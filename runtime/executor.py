"""
runtime.executor — 执行器：命中判定 → 取资产/现场合成 → 拼接 → 事件流

职责：把一条 plan 变成 16kHz/单声道/16-bit WAV + 逐单元事件流。
      命中路径零 TTS 调用；未命中默认 fail-closed（抛 RuntimeMissError 且绝不写输出文件）。
不负责：不做语义分析/意图识别（判定只用确定性路由 + 包内查表），不实现 TTS，不改资产包。

判定语义与事件字段见 docs/06 §6.2（§6.2.7 为本卡专用补定）与 runtime/AGENTS.md §③④。
层边界（runtime/AGENTS.md §⑤）：只依赖 core/（协议与字段常量）、assets/（只读）、
    adapters/（经接口注入）；不得 import compiler/ 或 rules/。
"""

import hashlib
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.metrics_spec import FALLBACK, HIT, MISS
from core.protocol import PlanUnit, parse_plan
from assets.fingerprint import fingerprint
from assets.pack import AssetEntry, AssetPackError

from .audio import SAMPLE_RATE, AudioError, concat_wavs, read_wav, silence
from .duplex import DuplexParams
from .events import build_event

# 未命中/降级原因（取值照 docs/06 §6.2.7 ② 的表；这些是"值"，不是事件字段名）
REASON_KEY_NOT_PREBAKED = "key_not_prebaked"
REASON_SAY_LIVE_TEXT = "say_live_text"
REASON_FINGERPRINT_MISMATCH = "fingerprint_mismatch"
REASON_AUDIO_FILE_MISSING = "audio_file_missing"
REASON_ENGINE_MISMATCH = "engine_mismatch"


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class RuntimeMissError(Exception):
    """fail-closed：plan 出现未命中/降级而 allow_fallback 未开启。

    消息必须含 key 与该单元的原因，便于定位是哪条话术把整条 plan 拦下来的。
    抛出即中止整条 plan：不写输出文件、不播其他话术。
    """


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------
@dataclass
class ExecutionResult:
    """一次 execute() 的产物。

    属性：
        events:           逐单元事件列表（每个单元恰好一条，含未命中/降级）
        output_path:      输出 WAV 路径
        hit_count:        命中单元数
        miss_count:       未命中单元数
        fallback_count:   降级单元数
        tts_calls:        本次执行真正调用 TTS 的次数（纯命中无槽位时为 0）
        first_audio_ms:   首音频延迟（毫秒）
        total_duration_ms: 输出音频总时长（毫秒）
    """

    events: List[dict]
    output_path: Optional[Path]
    hit_count: int
    miss_count: int
    fallback_count: int
    tts_calls: int
    first_audio_ms: float
    total_duration_ms: int


@dataclass
class _UnitDecision:
    """判定阶段的中间产物：一个 plan 单元该走哪条路。

    entries 非空 = 命中（播包内音频，零 TTS）；
    text 非空 = 现场合成（SAY_LIVE 或降级单元）。
    """

    part: int
    unit: PlanUnit
    state: str
    reason: str
    variant: Optional[int]
    entries: List[AssetEntry] = field(default_factory=list)
    text: Optional[str] = None


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------
class Executor:
    """执行器：一条 plan → WAV + 事件流。

    属性：
        pack:           只读资产包（AssetPack）
        adapter:        TTS 适配器（经接口注入，只认 synthesize/voice/model_version）
        duplex:         双工参数（缺省 DuplexParams.default()）
        allow_fallback: False = fail-closed（默认）；True = 允许降级走慢路并留痕
    """

    def __init__(self, pack, adapter, *, duplex: Optional[DuplexParams] = None,
                 allow_fallback: bool = False):
        """初始化执行器并校验依赖对象。

        异常：
            TypeError: pack/adapter 缺必需成员、duplex 类型不对、allow_fallback 非 bool
        """
        self._check_pack(pack)
        self._check_adapter(adapter)
        if not isinstance(allow_fallback, bool):
            raise TypeError(
                f"allow_fallback 必须是 bool，实际为 {allow_fallback!r}"
            )

        params = DuplexParams.default() if duplex is None else duplex
        if not isinstance(params, DuplexParams):
            raise TypeError(
                f"duplex 必须是 DuplexParams 实例，实际为 {type(params).__name__}"
            )

        self.pack = pack
        self.adapter = adapter
        self.duplex = params
        self.allow_fallback = allow_fallback

    @staticmethod
    def _check_pack(pack) -> None:
        """校验资产包具备只读契约要求的成员。"""
        missing = [
            name for name in ("assets", "voice", "model_version", "pack_version",
                              "root", "lookup")
            if not hasattr(pack, name)
        ]
        if missing:
            raise TypeError(
                f"pack 缺少必需成员: {missing}（实际类型 {type(pack).__name__}）"
            )

    @staticmethod
    def _check_adapter(adapter) -> None:
        """校验适配器具备 TTS 接口契约要求的成员。"""
        missing = [name for name in ("synthesize", "voice", "model_version")
                   if not hasattr(adapter, name)]
        if missing:
            raise TypeError(
                f"adapter 缺少必需成员: {missing}（实际类型 {type(adapter).__name__}）"
            )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def execute(self, plan, *, plan_id, turn_id, out_path) -> ExecutionResult:
        """执行一条 plan：判定 → 取资产/现场合成 → 拼接写盘 → 产出事件流。

        参数：
            plan:      原始 plan（list[dict]）或已解析的 list[PlanUnit]
            plan_id:   播报计划 ID
            turn_id:   会话轮次 ID（参与 variant auto 的稳定散列）
            out_path:  输出 WAV 路径

        返回：
            ExecutionResult

        异常：
            ProtocolError:    plan 协议不合法
            RuntimeMissError: 出现 miss/fallback 且 allow_fallback=False（fail-closed，不写文件）
            AudioError:       音频格式/读写失败
        """
        out_path = Path(out_path)
        started = time.perf_counter()      # 首音频延迟计时起点：execute() 进入即开始
        units = self._coerce_plan(plan)

        # ① 判定阶段：逐单元定三态与原因，此刻不调用 TTS、不写任何文件
        decisions = [
            self._decide_unit(unit, idx + 1, turn_id)
            for idx, unit in enumerate(units)
        ]

        # fail-closed：判定全部完成、尚未合成/落盘，此时中止才不会留下半截输出
        if not self.allow_fallback:
            for decision in decisions:
                if decision.state != HIT:
                    raise RuntimeMissError(self._abort_message(decision))

        # ② 合成与拼接阶段
        segments: List = []
        tts_calls = 0
        first_audio_at = None
        seq = 0

        def synth(text: str, rate_key: str):
            """现场合成一段音频并读回样本；tts_calls 只计成功调用。"""
            nonlocal tts_calls, seq
            seq += 1
            target = tmpdir / f"live_{seq:03d}.wav"
            self.adapter.synthesize(text, target, rate_key)
            tts_calls += 1
            return read_wav(target)[0]

        with tempfile.TemporaryDirectory(prefix="vox-exec-") as tmp:
            tmpdir = Path(tmp)

            for idx, decision in enumerate(decisions):
                rate_key = decision.unit.rate

                if decision.entries:
                    # 命中：直接读包内音频（part_index 升序），零 TTS 调用
                    for entry in decision.entries:
                        samples, _ = read_wav(self.pack.root / entry.path)
                        if first_audio_at is None:
                            first_audio_at = time.perf_counter()
                        segments.append(samples)
                else:
                    # SAY_LIVE / 降级：现场合成
                    samples = synth(decision.text, rate_key)
                    if first_audio_at is None:
                        first_audio_at = time.perf_counter()
                    segments.append(samples)

                # 槽位：槽值一律现场合成（docs/06 §6.2.5），前后垫 slot_pad_ms 微停顿。
                # WHY：命中路径因此允许有 TTS 调用（每槽一次），但只有槽位会调——
                #      这不算破"命中零调用"，包内音频本身仍是预铸的。
                for slot_value in decision.unit.slots.values():
                    segments.append(silence(self.duplex.slot_pad_ms, SAMPLE_RATE))
                    segments.append(synth(slot_value, rate_key))
                    segments.append(silence(self.duplex.slot_pad_ms, SAMPLE_RATE))

                # 单元之间的句间静音垫
                if idx < len(decisions) - 1:
                    segments.append(silence(self.duplex.silence_pad_ms, SAMPLE_RATE))

            # 每段施 fade_ms 线性淡入淡出后写盘（docs/06 §6.2.7 ④）
            concat_wavs(
                segments, out_path,
                framerate=SAMPLE_RATE, fade_ms=self.duplex.fade_ms,
            )

        total_samples = sum(len(s) for s in segments)
        first_audio_ms = (
            (first_audio_at - started) * 1000 if first_audio_at else 0.0
        )
        total_duration_ms = int(round(total_samples * 1000 / SAMPLE_RATE))

        # ③ 事件流：每个单元恰好一条，含未命中/降级
        events = [
            build_event(
                turn_id=turn_id,
                plan_id=plan_id,
                part=decision.part,
                state=decision.state,
                key=decision.unit.key,
                rate=decision.unit.rate,
                variant=decision.variant,
                reason=decision.reason,
                pack_version=self.pack.pack_version,
                first_audio_ms=first_audio_ms,
            )
            for decision in decisions
        ]

        return ExecutionResult(
            events=events,
            output_path=out_path,
            hit_count=sum(1 for d in decisions if d.state == HIT),
            miss_count=sum(1 for d in decisions if d.state == MISS),
            fallback_count=sum(1 for d in decisions if d.state == FALLBACK),
            tts_calls=tts_calls,
            first_audio_ms=first_audio_ms,
            total_duration_ms=total_duration_ms,
        )

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_plan(plan) -> List[PlanUnit]:
        """把输入 plan 统一成 PlanUnit 列表；原始 list 交给 core 校验。"""
        if plan and all(isinstance(u, PlanUnit) for u in plan):
            return list(plan)
        return parse_plan(plan)      # 空 plan / 原始 dict 列表都走 core 的标准报错

    def _decide_unit(self, unit: PlanUnit, part: int, turn_id) -> _UnitDecision:
        """判定单个 plan 单元的三态、原因与音频来源（只做确定性路由 + 包内查表）。

        判定顺序照 docs/06 §6.2.7 ②：
            1. SAY_LIVE（自由文本）→ miss / say_live_text
            2. 引擎一致性（音色/模型版本）→ fallback / engine_mismatch
            3. 包内查表：无候选 → miss / key_not_prebaked；
               指纹不符 → fallback / fingerprint_mismatch；
               音频缺失 → fallback / audio_file_missing；否则 hit
        """
        rate_key = unit.rate

        # 变体解析（auto → 按 (turn_id, part) 稳定散列）
        variant = self._resolve_variant(
            unit.key, rate_key, turn_id, part, unit.variant
        )

        # ① SAY_LIVE：设计上就没有资产
        if unit.key is None:
            return _UnitDecision(
                part=part, unit=unit, state=MISS,
                reason=REASON_SAY_LIVE_TEXT, variant=variant, text=unit.text,
            )

        # ② 引擎一致性前置检查（docs/06 §6.2.3）
        # WHY：音色/模型版本不一致时绝不能播包内音频——那正是红线举的
        #      "突然换了个声音"，事后无法定位。此检查必须在任何 lookup 之前。
        if (
            self.pack.voice != self.adapter.voice
            or self.pack.model_version != self.adapter.model_version
        ):
            return _UnitDecision(
                part=part, unit=unit, state=FALLBACK,
                reason=REASON_ENGINE_MISMATCH, variant=variant,
                text=self._candidate_text(unit.key, 0, rate_key, variant) or unit.key,
            )

        # ③ part_index 升序探测（0,1,2…；本轮包只产 0，但要对未来兼容）
        entries: List[AssetEntry] = []
        part_index = 0
        while True:
            state, reason, entry = self._diagnose(
                unit.key, part_index, rate_key, variant
            )
            if entry is None:
                # 只可能发生在 part_index == 0：包内根本没有这个组合
                return _UnitDecision(
                    part=part, unit=unit, state=MISS,
                    reason=REASON_KEY_NOT_PREBAKED, variant=variant,
                    text=unit.key,
                )
            if state != HIT:
                # 包内有条目但不可用：降级时用它自己的文本现场合成
                return _UnitDecision(
                    part=part, unit=unit, state=FALLBACK, reason=reason,
                    variant=variant,
                    text=self._candidate_text(
                        unit.key, part_index, rate_key, variant
                    ) or unit.key,
                )
            entries.append(entry)
            part_index += 1
            # 下一个分片没有候选就结束探测（包是有限集合，循环必然终止）
            if self._find_candidate(unit.key, part_index, rate_key, variant) is None:
                break

        if self.pack.root is None:
            raise AssetPackError(
                "资产包缺少根目录（root=None），无法定位音频文件——拒绝播放"
            )

        return _UnitDecision(
            part=part, unit=unit, state=HIT, reason="", variant=variant,
            entries=entries,
        )

    def _diagnose(self, key: str, part_index: int, rate_key: str,
                  variant: Optional[int]):
        """诊断单个 (key, part_index, rate, variant) 组合，返回 (state, reason, entry)。

        WHY：assets.AssetPack.lookup 按契约一律返回 None，不告诉我们为什么。
            三种未命中语义不同（没预铸 / 过期 / 包损坏），必须自行判定并写进事件，
            这正是红线"本该命中却换了路径必须留痕"的落点。

        返回：
            (MISS, key_not_prebaked, None)            包内无候选
            (FALLBACK, fingerprint_mismatch, entry)   指纹不符（过期资产）
            (FALLBACK, audio_file_missing, entry)     指纹匹配但音频缺失（包损坏）
            (HIT, "", entry)                          可播放
        """
        candidate = self._find_candidate(key, part_index, rate_key, variant)
        if candidate is None:
            return MISS, REASON_KEY_NOT_PREBAKED, None

        # 用包内存的文本重算指纹，与包内指纹比对（语音/模型版本一律取 manifest 级）
        expected_fp = fingerprint(
            text=candidate.text,
            voice=self.pack.voice,
            rate_value=rate_key,
            model_version=self.pack.model_version,
        )
        if candidate.fingerprint != expected_fp:
            return FALLBACK, REASON_FINGERPRINT_MISMATCH, candidate

        # 音频文件存在性（root 为 None 时 lookup 也不校验，这里保持一致）
        if self.pack.root is not None and not (self.pack.root / candidate.path).exists():
            return FALLBACK, REASON_AUDIO_FILE_MISSING, candidate

        # 通过资产层公开 API 取条目（每层只认上一层产物）
        entry = self.pack.lookup(
            key=key, part_index=part_index, rate_key=rate_key,
            variant=variant, expected_text=candidate.text,
        )
        if entry is None:
            # 诊断与 lookup 结论冲突 = 包在并发中被改动，fail-closed，绝不静默换路
            raise AssetPackError(
                f"资产包判定不一致: key={key!r} part_index={part_index} "
                f"rate={rate_key} variant={variant!r}——诊断通过但 lookup 返回 None"
            )
        return HIT, "", entry

    def _find_candidate(self, key: str, part_index: int, rate_key: str,
                        variant: Optional[int]) -> Optional[AssetEntry]:
        """按 (key, part_index, rate_key, variant) 在包内取候选条目（不做指纹/文件校验）。"""
        for entry in self.pack.assets:
            if (
                entry.key == key
                and entry.part_index == part_index
                and entry.rate_key == rate_key
                and entry.variant == variant
            ):
                return entry
        return None

    def _candidate_text(self, key: str, part_index: int, rate_key: str,
                        variant: Optional[int]) -> Optional[str]:
        """取候选条目的文本，供降级时的现场合成使用；无候选返回 None。"""
        entry = self._find_candidate(key, part_index, rate_key, variant)
        return entry.text if entry is not None else None

    def _resolve_variant(self, key: Optional[str], rate_key: str, turn_id,
                         part: int, requested) -> Optional[int]:
        """解析 variant：'auto' 按 (turn_id, part) 稳定散列在包内可用变体里选。

        口径（docs/06 §6.2.7 ③）：同一轮次同一单元可复现（评测要能重放），
            跨轮次会变化（落实"防复读机"）。

        WHY：用 sha256 而不是内建 hash()——hash() 的字符串种子随进程变化
            （PYTHONHASHSEED），不可复现，评测重放会失效。

        返回：
            显式整数直接透传；'auto' 且包内有可用变体 → 解析出的整数；
            自由文本单元或包内无任何可用变体 → None（事件里记 None，不编造值）。
        """
        if key is None:
            return None
        if requested != "auto":
            return requested

        available = sorted(
            {
                entry.variant
                for entry in self.pack.assets
                if entry.key == key and entry.rate_key == rate_key
            }
        )
        if not available:
            return None

        digest = hashlib.sha256(
            f"{turn_id}\x00{part}".encode("utf-8")
        ).hexdigest()
        return available[int(digest[:16], 16) % len(available)]

    @staticmethod
    def _abort_message(decision: _UnitDecision) -> str:
        """生成 fail-closed 中止消息：必须含单元序号、key（或自由文本）与原因。"""
        if decision.unit.key is not None:
            ident = f"key={decision.unit.key!r}"
        else:
            ident = f"text={decision.unit.text!r}"
        return (
            f"plan 单元 #{decision.part} 未命中（{ident}，"
            f"reason={decision.reason}）：allow_fallback=False → "
            f"中止整条 plan（fail-closed：不写输出文件、不播其他话术）"
        )
