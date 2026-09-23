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

from core.metrics_spec import FALLBACK, HIT, MISS, REASON_ASSET_EXPIRED
from core.protocol import CRITICAL_RATE, PlanUnit, parse_plan
from assets.fingerprint import fingerprint
from assets.pack import AssetEntry, AssetPackError, is_expired

from .audio import SAMPLE_RATE, AudioError, concat_wavs, read_wav, silence
from .duplex import DuplexParams, rate_distance
from .events import build_event, build_listen_event, build_unit_event

# 未命中/降级原因（取值照 docs/06 §6.2.7 ② 的表；这些是"值"，不是事件字段名）
REASON_KEY_NOT_PREBAKED = "key_not_prebaked"
REASON_SAY_LIVE_TEXT = "say_live_text"
REASON_FINGERPRINT_MISMATCH = "fingerprint_mismatch"
REASON_AUDIO_FILE_MISSING = "audio_file_missing"
REASON_ENGINE_MISMATCH = "engine_mismatch"
# T19：critical 单元缺关键信息档位，且回落档超出语速收敛带 → 不回落，按未命中处理。
# WHY 单独一个原因而不是复用 key_not_prebaked：那条语义是"整条 key 都没预铸"，
#     这里语义是"预铸了但没预铸 critical 要求的档位、且带内找不到可用档"，
#     两件事的修复动作不同（补铸 slow 档 vs 补铸整条话术）。
REASON_CRITICAL_RATE_OUT_OF_BAND = "critical_rate_out_of_band"
# T18：命中路径上的资产已过 invalid_at → 拒播，且**不受 allow_fallback 影响**。
# WHY 单独一个原因：key_not_prebaked 的语义是"整条 key 都没预铸"，而这里是
#     "预铸了但已经失效"——修复动作完全不同（补铸 vs 刷新内容/延长有效期），
#     共用原因码会让定位的人以为是"没铸"，从而做出错误的修复。
REASON_ASSET_EXPIRED = REASON_ASSET_EXPIRED  # 真源在 core.metrics_spec（只增）


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
    # 该单元实际使用的语速档（critical 回落时与 unit.rate 不同）
    rate_used: str = ""
    # 关键信息计划档位（非 critical 单元为空）
    requested_rate: str = ""
    # 是否发生了档位回落
    rate_fallback: bool = False


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
                 allow_fallback: bool = False, policy_stream: bool = False,
                 now=None):
        """初始化执行器并校验依赖对象。

        参数：
            policy_stream: 是否在事件流里追加双工策略事件（等待窗口 + 策略字段）。
                           默认 False = 保持"一单元一事件"的旧形状。
            now: 失效判定的时刻（T18）。None = 当前 UTC；显式传入（datetime 或
                 ISO 8601 字符串）用于评测/测试**重放**——不得依赖 sleep 等真实
                 时间流逝。判定与 assets 共用同一判据 `is_expired(entry, now)`。

        异常：
            TypeError: pack/adapter 缺必需成员、duplex 类型不对、
                       allow_fallback / policy_stream 非 bool
        """
        self._check_pack(pack)
        self._check_adapter(adapter)
        if not isinstance(allow_fallback, bool):
            raise TypeError(
                f"allow_fallback 必须是 bool，实际为 {allow_fallback!r}"
            )
        if not isinstance(policy_stream, bool):
            raise TypeError(
                f"policy_stream 必须是 bool，实际为 {policy_stream!r}"
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
        # T18：时钟注入。execute() 内的所有失效判定都用这一个值（单轮内自洽）。
        self.now = now
        # T19：双工策略事件（等待窗口 / backchannel_ok / barge_in / requires_confirm /
        # rate_fallback）是否进入事件流。
        # 为什么默认关闭（False）：既有事件流契约是"一个单元恰好一条事件"
        #     （eval/bench.py 用 len(events) == units 做完整性闸门，runtime/eval 的
        #     既有断言逐字节钉死这一形状）。默认开启会让既有下游按位置读取时
        #     读到等待窗口事件——那是"本该命中却换了路径"，属于静默降级。
        #     所以默认保持旧形状（只增字段 + 旧消费者零变化），新消费者显式 opt in。
        #     这是"不静默降级"的落点，不是偷懒。
        self.policy_stream = policy_stream

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

        # critical 超带是"更硬的 fail-closed"：即使 allow_fallback=True 也不允许
        # 用另一个档位现场合成播出去——那正是"本该关键信息慢说却跑成了常速"，
        # 属于事后无法定位的静默降级。所以这里无条件抛错、不写输出文件。
        for decision in decisions:
            if decision.reason == REASON_CRITICAL_RATE_OUT_OF_BAND:
                raise RuntimeMissError(self._abort_message(decision))

        # 过期资产同样是"更硬的 fail-closed"（T18）：即使 allow_fallback=True 也
        # 不允许把过期内容现场合成播出去——**播报过期事实比不播更糟**。
        for decision in decisions:
            if decision.reason == REASON_ASSET_EXPIRED:
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
                rate_key = decision.rate_used or decision.unit.rate

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

        # ③ 事件流：每个单元恰好一条（含未命中/降级），末尾追加等待窗口事件。
        # 单元时长用包内 manifest 的 duration_ms（离线可复现，不吃 wall-clock）。
        events: List[dict] = []
        cumulative_ms = 0
        if self.policy_stream:
            # 策略流形态：每单元事件带双工策略字段，末尾追加等待窗口事件。
            for idx, decision in enumerate(decisions):
                cumulative_ms += self._unit_duration_ms(decision)
                events.append(build_unit_event(
                    turn_id=turn_id,
                    plan_id=plan_id,
                    part=decision.part,
                    state=decision.state,
                    key=decision.unit.key,
                    rate=decision.rate_used or decision.unit.rate,
                    variant=decision.variant,
                    reason=decision.reason,
                    pack_version=self.pack.pack_version,
                    first_audio_ms=first_audio_ms,
                    patience_ms=self.duplex.patience_ms,
                    spoken_ms=cumulative_ms,
                    backchannel_ok=self._backchannel_ok(idx, cumulative_ms),
                    barge_in=self.duplex.barge_in,
                    requires_confirm=self._requires_confirm(
                        idx, decision, len(decisions)
                    ),
                    requested_rate=decision.requested_rate,
                    rate_fallback=decision.rate_fallback,
                ))

            # 等待窗口事件：播报完成后麦克风打开的时长（语义 = patience_ms）。
            # 只在事件流里体现，不产生任何音频。
            events.append(build_listen_event(
                turn_id=turn_id,
                plan_id=plan_id,
                listen_ms=self.duplex.patience_ms,
                pack_version=self.pack.pack_version,
            ))
        else:
            # 旧形状：一个单元恰好一条事件，字段集与既往逐字节一致。
            # WHY 仍走 build_event 而不是 build_unit_event：后者总会写入
            #     backchannel_ok / requires_confirm / rate_fallback（False）与
            #     requested_rate（""），旧消费者的字段集合断言会被判红——
            #     那正是"本该是旧形状却换了形状"的静默变化。
            for decision in decisions:
                events.append(build_event(
                    turn_id=turn_id,
                    plan_id=plan_id,
                    part=decision.part,
                    state=decision.state,
                    key=decision.unit.key,
                    rate=decision.rate_used or decision.unit.rate,
                    variant=decision.variant,
                    reason=decision.reason,
                    pack_version=self.pack.pack_version,
                    first_audio_ms=first_audio_ms,
                ))

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

        T18 追加：过期判定插在**引擎检查之后、包内查表之前**——与引擎一致性并列
        作为"命中路径不可用"的判据，但优先于 key_not_prebaked。
        """
        # 档位解析（T19）：critical 单元强制关键信息档；包内缺档时按收敛带判回落。
        # 非 critical 单元行为与既往完全一致——绝不顺手放宽。
        rate_key, requested_rate, rate_fallback, forced_miss_reason = (
            self._resolve_rate(unit, part)
        )

        # 变体解析（auto → 按 (turn_id, part) 稳定散列）
        variant = self._resolve_variant(
            unit.key, rate_key, turn_id, part, unit.variant
        )

        # ① SAY_LIVE：设计上就没有资产（critical 对自由文本无意义，直接沿用现状）
        if unit.key is None:
            return _UnitDecision(
                part=part, unit=unit, state=MISS,
                reason=REASON_SAY_LIVE_TEXT, variant=variant, text=unit.text,
                requested_rate=requested_rate, rate_fallback=rate_fallback,
            )

        # critical 单元缺档且带内无可回落档：按未命中处理，不静默换路。
        # 放在引擎检查之后，保证既有 engine_mismatch 优先级不变。
        if forced_miss_reason:
            return _UnitDecision(
                part=part, unit=unit, state=MISS,
                reason=forced_miss_reason, variant=variant, text=unit.key,
                requested_rate=requested_rate, rate_fallback=rate_fallback,
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
                requested_rate=requested_rate, rate_fallback=rate_fallback,
            )

        # ②b 过期判定（T18）：资产已过 invalid_at → 拒播，reason 明确为 asset_expired。
        # 插在引擎检查之后：engine_mismatch 表示"引擎换了"，优先级语义不变；
        # 插在查表之前：过期不是"没预铸"，不能用 key_not_prebaked 冒充。
        # 状态取 MISS 而非 FALLBACK：过期内容**不允许**现场合成播出（见 execute 的
        # 硬门），走 FALLBACK 会让事件语义与事实不符。
        expired_at = self._expired_at(unit.key, rate_key, variant)
        if expired_at is not None:
            return _UnitDecision(
                part=part, unit=unit, state=MISS,
                reason=REASON_ASSET_EXPIRED, variant=variant,
                text=expired_at, requested_rate=requested_rate,
                rate_fallback=rate_fallback,
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
                    text=unit.key, requested_rate=requested_rate,
                    rate_fallback=rate_fallback,
                )
            if state != HIT:
                # 包内有条目但不可用：降级时用它自己的文本现场合成
                return _UnitDecision(
                    part=part, unit=unit, state=FALLBACK, reason=reason,
                    variant=variant,
                    text=self._candidate_text(
                        unit.key, part_index, rate_key, variant
                    ) or unit.key,
                    requested_rate=requested_rate, rate_fallback=rate_fallback,
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
            entries=entries, rate_used=rate_key, requested_rate=requested_rate,
            rate_fallback=rate_fallback,
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
        # **now 必须与调用方同一个**（T18 遗留修复，docs/13 §五#22）：_decide_unit 先用
        # `self.now` 判过期（`_expired_at`）、确认没过期才走到这里；此处若用默认墙钟，
        # 两条判据就会在「注入的 now 未过期、墙上时钟已过期」时打架 → lookup 返回 None
        # → 撞下面那条 fail-closed。判据只有一份来源（`self.now`），不是一份半。
        entry = self.pack.lookup(
            key=key, part_index=part_index, rate_key=rate_key,
            variant=variant, expected_text=candidate.text, now=self.now,
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

    def _expired_at(self, key: str, rate_key: str,
                    variant: Optional[int]) -> Optional[str]:
        """取 (key, rate_key, variant) 的过期条目文本；未过期 / 无候选返回 None。

        判据**只有一份**：`assets.pack.is_expired(entry, now)`，与 `lookup` 共用。
        runtime 这边要自己判一次，是因为 `lookup` 只返回 None、不告诉你原因——
        而原因必须写进事件（红线：本该命中却换了路径必须留痕）。

        注意这里遍历的是 `self.pack.assets`（不是 `lookup`），因为 `_resolve_rate`
        已经按同一个身份把 rate / variant 解析好了；这里只补"它是否过期"这一维。
        """
        for entry in self.pack.assets:
            if (
                entry.key == key
                and entry.rate_key == rate_key
                and entry.variant == variant
                and is_expired(entry, self.now)
            ):
                return entry.text
        return None

    def _resolve_rate(self, unit: PlanUnit, part: int):
        """解析本单元的播报档位（T19：rate_band 的唯一消费点）。

        非 critical 单元：一律返回 unit.rate，行为与既往完全一致（不顺手放宽）。

        critical 单元（关键信息）：
            1. 包内有关键信息档（slow）资产 → 用它，零改动；
            2. 缺档但包内该 key 有其它档、且与目标档之差 ≤ rate_band
               → 用最近可用档（带内回落），事件留痕 rate_fallback；
            3. 缺档且带内无可回落档 → 返回一个"强制未命中原因"，
               由调用方按 miss 处理（fail-closed，绝不静默用别的档播出去）。

        返回：
            (rate_key, requested_rate, rate_fallback, forced_miss_reason)
            forced_miss_reason 为空字符串 = 无需强制未命中。
        """
        if not unit.critical or unit.key is None:
            return unit.rate, "", False, ""

        target = CRITICAL_RATE
        # 关键信息档可用 → 直接选它（此时单元计划档与计划档一致，无留痕需求）
        if self._has_assets(unit.key, target):
            return target, "", False, ""

        # 缺档：在该 key 可用档里找带内最近的
        available = self._available_rates(unit.key)
        in_band = [
            rate for rate in available
            if rate_distance(rate, target) <= self.duplex.rate_band
        ]
        if not in_band:
            # 带内无可用档 → 不回落（超出收敛带），按未命中 fail-closed
            return unit.rate, target, False, REASON_CRITICAL_RATE_OUT_OF_BAND

        best = min(in_band, key=lambda rate: rate_distance(rate, target))
        return best, target, True, ""

    def _has_assets(self, key: str, rate_key: str) -> bool:
        """包内是否存在 (key, rate_key) 的任意条目（不校验指纹/文件，仅判存在）。"""
        return any(entry.key == key and entry.rate_key == rate_key
                   for entry in self.pack.assets)

    def _available_rates(self, key: str):
        """该 key 在包内出现过的全部语速档（升序，去重）。"""
        return sorted({entry.rate_key for entry in self.pack.assets
                       if entry.key == key})

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

    def _unit_duration_ms(self, decision: _UnitDecision) -> int:
        """单个单元（含槽位）的累计时长，取包内 duration_ms 求和。

        口径：用 manifest 里的 duration_ms（预铸时长），不吃 wall-clock——
            离线产物生成器要可复现，backchannel 的判据输入必须是稳定值。
        """
        total = sum(entry.duration_ms for entry in decision.entries)
        # 槽位音频一律现场合成，manifest 里没有其时长；只记框架段时长。
        # WHY 不做估计：编造时长会让 backchannel 判据不可复现（比缺一项更糟）。
        return total

    def _backchannel_ok(self, index: int, cumulative_ms: int) -> bool:
        """判据：backchannel=on 且累计播报时长 ≥ patience_ms。

        挂在"累计已达耐心窗"的那条单元事件上（即卡内裁定的"之后"标记）；
        开关关闭时恒 False，不看时长。
        """
        if self.duplex.backchannel != "on":
            return False
        return cumulative_ms >= self.duplex.patience_ms

    def _requires_confirm(self, index: int, decision: _UnitDecision,
                          total: int) -> bool:
        """判据：barge_in=confirm 且本单元为终态。

        终态语义（卡内裁定）：命中 duplex.terminal_keys 的 key，或 plan 最后一个单元。
        barge_in=allow 时恒 False——allow 下任何单元都可被打断。
        """
        if self.duplex.barge_in != "confirm":
            return False
        key = decision.unit.key
        if key is not None and key in self.duplex.terminal_keys:
            return True
        return index == total - 1

    def _abort_message(self, decision: _UnitDecision) -> str:
        """生成 fail-closed 中止消息：必须含单元序号、key（或自由文本）与原因。

        消息里如实写出 allow_fallback 的实际取值——写错会让定位的人误以为
        "开了降级但没生效"，那比没报更糟。
        """
        if decision.unit.key is not None:
            ident = f"key={decision.unit.key!r}"
        else:
            ident = f"text={decision.unit.text!r}"
        if decision.reason == REASON_CRITICAL_RATE_OUT_OF_BAND:
            return (
                f"plan 单元 #{decision.part} 关键信息档位缺失（{ident}，"
                f"reason={decision.reason}，计划档=slow，实际可用档无带内可选）："
                f"rate_band={self.duplex.rate_band} 不足 → 中止整条 plan"
                f"（fail-closed：不写输出文件、不用其它档位合成播放；"
                f"该判定不受 allow_fallback={self.allow_fallback} 影响）"
            )
        if decision.reason == REASON_ASSET_EXPIRED:
            return (
                f"plan 单元 #{decision.part} 资产已过期（{ident}，"
                f"reason={decision.reason}）→ 中止整条 plan"
                f"（fail-closed：不写输出文件、不播其他话术；"
                f"播报过期内容比不播更糟，该判定不受 allow_fallback={self.allow_fallback} 影响）"
            )
        return (
            f"plan 单元 #{decision.part} 未命中（{ident}，"
            f"reason={decision.reason}）：allow_fallback={self.allow_fallback} → "
            f"中止整条 plan（fail-closed：不写输出文件、不播其他话术）"
        )
