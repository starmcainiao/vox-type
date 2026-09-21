"""
adapters.framework_kefu.bridge — KefuBridge：一轮 = ASR → brain → 命中判定 → 播放/慢路 + 事件

职责（docs/10 §10.1 的那个接缝）：给定"这一轮要说什么"的表示，判断能不能用预铸资产
      立刻变成声音；能就零合成播放，不能就按旁路开关 fail-closed 或走慢路并留痕。
不负责：不决定"该说什么"（brain 的事）、不做 ASR（kefu worker 的事）、
      不做语义/模糊匹配、不解释业务话术。

WHY 这个适配器比 tts-* 厚（adapters/AGENTS.md §② 的 ≤150 行只约束 tts-*）：
    它是 `framework-*` 那一类——命中判定的口径（docs/10 §10.2）就钉在接缝这一层，
    判定与"该走哪条路"无法拆到引擎里。判定本身只用了包内查表与归一化逐字相等。

命中判定只允许两种（docs/10 §10.2 裁定 1）：
    key 档：pack.lookup(key, …) 直查（含指纹与音频存在性校验）
    文本档：normalize_text(回复文本) 与包内该 key 各 variant 的归一化文本逐字相等
    禁止语义/模糊/embedding/编辑距离/去语气词/去标点匹配——那要模型且不可复核，
    会静默播"差不多但不是这句"的话（红线）。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import time
from runtime import (
    REASON_ENGINE_MISMATCH,
    REASON_KEY_NOT_PREBAKED,
    RuntimeMissError,
)
from runtime import build_event
from core import FALLBACK, HIT, MISS
from runtime import Executor
from assets import AssetEntry

from .normalize import normalize_text
from . import hit_query
from .hit_query import (
    MODE_KEY,
    MODE_TEXT,
    REASON_TEXT_NOT_PREBAKED,
)


# ---------------------------------------------------------------------------
# 事件字段：只加不改（docs/10 §10.4）
# ---------------------------------------------------------------------------
# 三态与 reason/key/part/rate/variant/first_audio_ms/pack_version 全部引 core.metrics_spec，
# 本层不自造；下面这一个是唯一新增字段（docs/10 §10.4 明确点名 match_mode 允许"加"）。
MATCH_MODE = "match_mode"

# 两种"上游表示形态"与文本档原因码的真源在 .hit_query（T29 下沉：那里是
# 最底层判定模块，被本模块导入；本模块只转发导出，取值与语义不变）。


class BridgeError(Exception):
    """本层用法错误（输入表示冲突、缺依赖等）——与"未命中"区分开。

    未命中抛 runtime.RuntimeMissError（fail-closed）；本异常是"这次调用本身就不成立"，
    两者都不能静默通过，但语义不同，脚本按类型分流。
    """


@dataclass(frozen=True)
class BridgeResult:
    """一轮的产物。

    属性：
        state:         "hit" | "miss" | "fallback"
        match_mode:    "key" | "text"（未命中/降级也保留，说明本轮上游给的表示形态）
        audio_path:    本轮产出的音频（fail-closed 时不会走到这里）
        tts_calls:     本轮真调用慢路合成器的次数（命中必须为 0）
        live_text:     本轮"要说什么"（上游自由文本，或 key 对应的包内文本）
        first_audio_ms: 从"开始产出音频"到首帧可播放的墙钟（毫秒）。
                        口径对齐 docs/09 §三 的段级数字（只算读包/合成+拼接，
                        不含 ASR 与 brain 那段），这样命中与慢路才可比
        events:        事件列表（字段名一律引 core.metrics_spec，可带 MATCH_MODE）
    """

    state: str
    match_mode: str
    audio_path: Path
    tts_calls: int
    live_text: str
    first_audio_ms: float
    events: List[dict]


class _ClientTtsAdapter:
    """把只实现了 synthesize_live(text) -> (bytes, id) 的慢路合成器包成 runtime 适配器契约。

    runtime.Executor 只认 synthesize/voice/model_version，kefu worker 给的是
    (bytes, engine)。这一层只做形状转换，不加任何判定。
    """

    def __init__(self, src):
        self.src = src
        self.voice = src.voice
        self.model_version = src.model_version

    def synthesize(self, text: str, out_path, rate_key: str = "normal") -> None:
        wav, _ = self.src.synthesize_live(text)
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(wav)


class KefuBridge:
    """预铸包 ↔ kefu 真链路的接缝。

    属性：
        pack:           只读资产包（assets.AssetPack）
        live_tts:       慢路合成器（runtime 适配器契约，或带 synthesize_live 的对象）
        allow_fallback: False = fail-closed（默认）；True = 允许走慢路并出事件
        duplex:         双工参数（透传给 runtime.Executor）
        client:         KefuClient 或具备 ask_brain/transcribe 的对象（key 档可省）
        rate:           key 档缺省语速
    """

    def __init__(
        self,
        pack,
        *,
        live_tts,
        allow_fallback: bool = False,
        duplex=None,
        client=None,
        rate: str = "normal",
    ):
        """校验依赖对象并装配执行器。

        异常：
            BridgeError: pack / live_tts 缺必需成员、allow_fallback 非 bool、
                         client 不是可用的 brain/ASR 提供物
        """
        missing = [n for n in ("assets", "voice", "model_version", "pack_version", "lookup")
                   if not hasattr(pack, n)]
        if missing:
            raise BridgeError(
                f"pack 缺少必需成员 {missing}（实际类型 {type(pack).__name__}）"
            )

        if not isinstance(allow_fallback, bool):
            raise BridgeError(f"allow_fallback 必须是 bool，实际为 {allow_fallback!r}")

        self.pack = pack
        self.allow_fallback = allow_fallback
        self.duplex = duplex
        self.rate = rate
        self.client = client
        self.live_tts = self._wrap_live_tts(live_tts)

        # 命中路径恒用 allow_fallback=False 的执行器：本层已经判定过命中，
        # 执行器若不同意必须抛 RuntimeMissError（fail-closed），不许悄悄换路
        self._executor = Executor(
            pack, self.live_tts, duplex=duplex, allow_fallback=False
        )
        self._text_index_cache: Optional[dict] = None

    # ------------------------------------------------------------------
    # 依赖装配
    # ------------------------------------------------------------------
    @staticmethod
    def _wrap_live_tts(live_tts):
        """统一成 runtime 适配器契约；不接受"既不能合成也没有音色声明"的对象。

        WHY 必须要求 voice / model_version：这两者是引擎一致性检查的输入，
            缺了就无法判断"能不能播包内音频"，只能猜——猜就是静默降级。
        """
        if hasattr(live_tts, "synthesize"):
            missing = [n for n in ("voice", "model_version") if not hasattr(live_tts, n)]
            if missing:
                raise BridgeError(
                    f"live_tts 缺少必需成员 {missing}（实际类型 {type(live_tts).__name__}）"
                )
            return live_tts
        if hasattr(live_tts, "synthesize_live"):
            missing = [n for n in ("voice", "model_version") if not hasattr(live_tts, n)]
            if missing:
                raise BridgeError(
                    f"live_tts 缺少必需成员 {missing}（synthesize_live 档同样要声明音色）"
                )
            return _ClientTtsAdapter(live_tts)
        raise BridgeError(
            f"live_tts 既没有 synthesize 也没有 synthesize_live（实际类型 "
            f"{type(live_tts).__name__}）——拒绝装配"
        )

    def _text_index(self) -> dict:
        """建"归一化文本 → 条目列表"索引（惰性一次；包是只读，缓存安全）。

        缓存逻辑留在本层（缓存字段与惰性判断不迁移），索引构建委托 hit_query。
        """
        if self._text_index_cache is None:
            self._text_index_cache = hit_query.build_text_index(self.pack)
        return self._text_index_cache

    # ------------------------------------------------------------------
    # 一轮
    # ------------------------------------------------------------------
    def run_turn(self, *, session_id: str, out_path,
                 wav_bytes=None, text: Optional[str] = None,
                 key: Optional[str] = None) -> BridgeResult:
        """跑一轮：ASR → brain → 命中判定 → 播放/慢路 + 事件。

        参数：
            session_id: 会话轮次标识（进事件的 turn_id）
            out_path:   本轮音频输出路径
            wav_bytes:  用户音频（走 client.transcribe 识别）
            text:       上游已转写的用户文本（跳过 ASR，直接问 brain）
            key:        上游直接给的话术 key（脚本驱动档，跳过 brain）

        返回：
            BridgeResult

        异常：
            BridgeError:      输入表示不是恰好一种 / 缺 brain 或 ASR 依赖 / 判定与执行器冲突
            RuntimeMissError: 未命中且 allow_fallback=False（fail-closed：不落盘、不调慢路）
        """
        if not isinstance(session_id, str) or not session_id.strip():
            raise BridgeError(f"session_id 必须是非空字符串，实际为 {session_id!r}")

        given = sum(1 for v in (wav_bytes, text, key) if v is not None)
        if given == 0:
            raise BridgeError(
                "run_turn 需要 wav_bytes / text / key 之一——禁止空轮"
            )
        if given > 1:
            raise BridgeError(
                f"run_turn 只能给一种输入表示（wav_bytes / text / key），实际给了 {given} 种"
                f"——禁止猜哪一个是用户想要的"
            )

        out_path = Path(out_path)
        plan_id = f"kefu-{session_id}"
        turn_id = session_id

        # ① ASR 段：有音频就识别，否则用上游给的文本
        if wav_bytes is not None:
            if self.client is None:
                raise BridgeError(
                    "给了 wav_bytes 但没有注入 client，无法做 ASR——禁止静默跳过识别"
                )
            user_text = self.client.transcribe(wav_bytes)
        else:
            user_text = text          # key 档这里为 None

        # ② 取"这一轮要说什么"
        if key is not None:
            mode = MODE_KEY
            live_text = self._text_for_key(key)
        else:
            mode = MODE_TEXT
            if self.client is None:
                raise BridgeError(
                    "自由文本档必须注入 client（需要 brain 给出本轮要说什么）"
                )
            live_text = self.client.ask_brain(session_id, user_text)

        # ③ 引擎一致性前置检查（docs/06 §6.2.3）
        # WHY 放在命中判定之前、且不设 allow_fallback 门槛：音色/模型版本不一致时
        #      "一律"不播包内音频（docs/10 §10.4），播了就是红线里那个
        #      "突然换了个声音"，事后无法定位。它自带 fallback 事件，不属于静默降级。
        if (
            self.pack.voice != self.live_tts.voice
            or self.pack.model_version != self.live_tts.model_version
        ):
            return self._slow_path(
                mode, key, live_text, out_path, plan_id, turn_id,
                state=FALLBACK, reason=REASON_ENGINE_MISMATCH,
            )

        # ④ 命中判定（只允许 key 直查与归一化逐字相等两种）
        if key is not None:
            entry = self._lookup_key(key)
            miss_reason = REASON_KEY_NOT_PREBAKED
        else:
            norm = normalize_text(live_text) if isinstance(live_text, str) else ""
            if not norm:
                # 空回复没有可播内容，按未命中处理（不猜、不凑）
                entry, miss_reason = None, REASON_TEXT_NOT_PREBAKED
            else:
                entry = self._pick_by_text(norm)
                miss_reason = REASON_TEXT_NOT_PREBAKED

        # ⑤ 命中 → 交给 runtime.Executor 播包（零慢路调用）
        if entry is not None:
            return self._play_pack(
                entry, mode, live_text, out_path, plan_id, turn_id
            )

        # ⑥ 未命中 → fail-closed 或显式降级
        if not self.allow_fallback:
            raise RuntimeMissError(self._miss_message(mode, key, live_text, miss_reason))
        return self._slow_path(
            mode, key, live_text, out_path, plan_id, turn_id,
            state=MISS, reason=miss_reason,
        )

    # ------------------------------------------------------------------
    # 判定内部件
    # ------------------------------------------------------------------
    def _lookup_key(self, key: str) -> Optional[AssetEntry]:
        """key 档命中判定（委托 hit_query.lookup_key；口径见其 docstring）。"""
        return hit_query.lookup_key(self.pack, key, self.rate)

    def _pick_by_text(self, norm: str) -> Optional[AssetEntry]:
        """文本档命中判定（委托 hit_query.pick_by_text；口径见其 docstring）。"""
        return hit_query.pick_by_text(self.pack, norm, self.rate)

    def _confirm(self, cand: AssetEntry) -> Optional[AssetEntry]:
        """用 pack.lookup 复核一次（委托 hit_query.confirm）。"""
        return hit_query.confirm(self.pack, cand)

    def _text_for_key(self, key: str) -> str:
        """key 档的 live_text（委托 hit_query.text_for_key；口径见其 docstring）。"""
        return hit_query.text_for_key(self.pack, key, self.rate)

    # ------------------------------------------------------------------
    # 两条出路
    # ------------------------------------------------------------------
    def _play_pack(self, entry, mode: str, live_text: str, out_path: Path,
                   plan_id: str, turn_id: str) -> BridgeResult:
        """命中 → 交 runtime.Executor 播包内音频（快路）。"""
        plan = [{"key": entry.key, "rate": entry.rate_key, "variant": entry.variant}]
        result = self._executor.execute(
            plan, plan_id=plan_id, turn_id=turn_id, out_path=out_path
        )

        # 本层判定为命中，执行器必须也命中且零慢路调用；不一致就是"本该命中却换了路径"，
        # 必须报错留痕（executor 的 fail-closed 通常已先抛 RuntimeMissError，这里是双保险）
        if result.hit_count != 1 or result.miss_count + result.fallback_count != 0:
            raise BridgeError(
                f"判定与执行器结论冲突（key={entry.key!r}）：命中判定通过但执行器得到 "
                f"hit={result.hit_count} miss={result.miss_count} fallback={result.fallback_count}"
                f"——中止本轮（fail-closed）"
            )
        if result.tts_calls != 0:
            raise BridgeError(
                f"命中路径出现了 {result.tts_calls} 次慢路调用（key={entry.key!r}）"
                f"——命中必须零调用"
            )

        event = dict(result.events[0])
        event[MATCH_MODE] = mode
        return BridgeResult(
            state=HIT,
            match_mode=mode,
            audio_path=Path(result.output_path),
            tts_calls=result.tts_calls,
            # key 档报包内原文（命中就是播它）；文本档报上游原话（保留未命中原因的可读线索）
            live_text=entry.text if mode == MODE_KEY else live_text,
            first_audio_ms=result.first_audio_ms,
            events=[event],
        )

    def _slow_path(self, mode: str, key, live_text: str, out_path: Path,
                   plan_id: str, turn_id: str, *, state: str, reason: str) -> BridgeResult:
        """慢路：直接调注入的慢路合成器，并出事件（未命中/降级都走这里）。

        WHY 不借 runtime.Executor 产事件：executor 的 SAY_LIVE 单元把原因记成
            say_live_text，而本层必须区分 text_not_prebaked / key_not_prebaked
            （T13 预铸准入要吃这个分布）。复用 executor 的事件会记错原因码，
            等于把"为什么没命中"这条线索丢掉。
        """
        started = time.perf_counter()
        self.live_tts.synthesize(live_text, out_path, self.rate)
        first_audio_ms = (time.perf_counter() - started) * 1000.0

        event = build_event(
            turn_id=turn_id,
            plan_id=plan_id,
            part=1,
            state=state,
            key=None if mode == MODE_TEXT else key,
            rate=self.rate,
            variant=None,
            reason=reason,
            pack_version=self.pack.pack_version,
            first_audio_ms=first_audio_ms,
        )
        event[MATCH_MODE] = mode
        return BridgeResult(
            state=state,
            match_mode=mode,
            audio_path=out_path,
            tts_calls=1,
            live_text=live_text,
            first_audio_ms=first_audio_ms,
            events=[event],
        )

    @staticmethod
    def _miss_message(mode: str, key, live_text, reason: str) -> str:
        """fail-closed 中止消息：必须含哪一轮、什么表示、什么原因码。"""
        if mode == MODE_KEY:
            ident = f"key={key!r}"
        else:
            ident = f"text={str(live_text)[:40]!r}"
        return (
            f"未命中（{ident}，reason={reason}）：allow_fallback=False → 中止本轮"
            f"（fail-closed：不写输出文件、不调用慢路合成器、不播替代音频）"
        )
