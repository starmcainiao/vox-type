"""
eval.report — 报告组装（JSON + 人读摘要）

职责：把 harness 采集到的数据组装成冻结 schema 的报告字典；落盘；渲染人读摘要；
      以及"不得美化"的两道闸：incomplete 判定（assess_completeness）与
      落盘前重新核对磁盘上的原始 JSONL（write_report）。
不负责：不采样、不跑执行器（bench/）、不做统计（stats/）、不定义指标字段名（core.metrics_spec）。

层边界（eval/AGENTS.md ⑤）：只依赖 core/（字段常量）+ 标准库。

字段名纪律：hit_rate / precast_ratio / first_audio_ms 等指标键一律引用 core.metrics_spec 的常量，
      本文件内不出现这些字符串字面量作为字典键（改了常量 = 破坏性变更，测试必须失败）。
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.metrics_spec import (
    FALLBACK,
    FIRST_AUDIO_MS,
    HIT,
    HIT_RATE,
    MISS,
    PRECAST_RATIO,
)

from .stats import BOOTSTRAP_RESAMPLES, MIN_REPEATS, QUANTILE_METHOD
# 文件级 I/O 与指纹摘要的统一实现（仅标准库）：本模块是 raw 指纹的**验侧**，
# 与 eval/bench.py 的写侧共用同一个函数，见 eval/_io.py 的「指纹咬合耦合」
from . import _io


class BenchReportError(Exception):
    """harness 配置/语料/报告组装错误。

    消息必须包含导致失败的具体值（路径、数值、key、字段名），不允许吞掉上下文。
    抛此异常时不得产生报告文件（不写盘，避免留下半成品）。
    """


# 报告 schema 版本：字段名冻结，新增只能追加
SCHEMA_VERSION: str = "vox-eval-report/1"

# 顶层必填字段（build_report 保证齐备，缺项 = 报告不可比对）
REQUIRED_TOP_LEVEL: Tuple[str, ...] = (
    "schema_version",
    "generated_at",
    "command",
    "seed",
    "repeats",
    "warmup_runs",
    "incomplete",
    "incomplete_reasons",
    "env",
    "corpus",
    "caliber",
    "arms",
    "delta",
    "deterministic_metrics",
    "timing_metrics",
    "timing_metrics_meaningful",
    "raw",
    "reference_gate",
)

# ---------------------------------------------------------------------------
# 口径说明（公式 + 设计依据，逐条写进报告 caliber）
# ---------------------------------------------------------------------------
CALIBER: Dict[str, Any] = {
    HIT_RATE: (
        f"{HIT_RATE} = 命中单元数 / 总单元数（取值 0.0~1.0）。"
        "设计依据：core.metrics_spec.HIT_RATE；docs/01 判据 ε ≤ 1 - p^(1/n)"
        "（8 轮通话要 90% 顺畅 → 单轮命中率 ≥98.7%）"
    ),
    PRECAST_RATIO: (
        f"{PRECAST_RATIO} = Σ(命中单元对应包内音频帧数) ÷ 输出音频总帧数。"
        "分母由 ExecutionResult.total_duration_ms 换算成帧，**含句间静音垫**，"
        "所以全命中也不等于 1.0（默认 silence_pad_ms=200，12 个单元有 11 段静音垫）。"
        "设计依据：core.metrics_spec.PRECAST_RATIO；docs/05 §5.1 预铸时长占比"
    ),
    "precast_ratio_denominator_note": (
        "PRECAST_RATIO 的分母**含**句间静音垫，因此全命中时也不等于 1.0："
        "12 个单元有 11 段 silence_pad_ms=200 的静音垫（默认双工参数）。"
        "若要「预铸语音时长占全部语音时长」的口径，只能**追加**新字段，"
        "不得改动 PRECAST_RATIO 的定义（改口径 = 破坏性变更）"
    ),
    "tts_calls": (
        "tts_calls = ExecutionResult.tts_calls（运行时**实测**计数，只计成功调用；"
        "纯命中且无槽位时为 0）。设计依据：runtime/AGENTS.md §③.1 命中即零调用"
    ),
    "synthesized_chars": (
        "synthesized_chars = Σ(非命中单元的文本长度) + Σ(槽值长度)。"
        "**推导值，不是 TTS 实测字符数**，仅作单位会话成本的代理量。"
        "设计依据：docs/05 §5.1 单位会话成本口径"
    ),
    FIRST_AUDIO_MS: (
        f"{FIRST_AUDIO_MS} = ExecutionResult.first_audio_ms（运行时**实测**："
        "从 execute() 进入到首段音频就绪的毫秒数）。"
        "设计依据：docs/05 §5.1 首响延迟 P50/P99（对标业界 sub-400ms）"
    ),
    "state_counts": (
        "state_counts = 单条 plan 内逐单元事件计数，键为 core.metrics_spec 的 "
        "hit/miss/fallback 常量。设计依据：runtime/AGENTS.md §④ 事件字段。"
        "报告取首样本值；样本间不一致即标 incomplete（确定性被破坏）"
    ),
    "quantile_method": QUANTILE_METHOD,
    "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
    "min_repeats": MIN_REPEATS,
    "statistics": (
        "厚尾分布禁均值外推（eval/AGENTS.md §①）：一律报 P50/P99 + 置信区间 + 样本量。"
        "n<100 时 P99 的置信区间必然很宽，报告必须如实呈现，不得只看点估计"
    ),
    "reproducibility": (
        "deterministic_metrics：同输入 + 同种子跑两次必须逐值相等（这是可复现的硬承诺，"
        "改算法或改常量会破坏它）；"
        "timing_metrics：只保证分布口径、样本量与 CI 方法一致，"
        "数值受机器/负载影响，不得断言相等；"
        "bootstrap 用 random.Random(seed) 独立实例，固定 seed 必得同一区间"
    ),
    "delta": (
        "delta = 慢路 - 快路（正值表示慢路更慢/更贵）。"
        "first_audio_ms 两个分位的 delta > 0 才说明快路确实更快"
    ),
}


def _utc_now_iso() -> str:
    """当前 UTC 时间戳（ISO 8601，带 Z）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 1. incomplete 判定（不得美化，eval/AGENTS.md §③.3）
# ---------------------------------------------------------------------------
def assess_completeness(
    *,
    repeats: int,
    corpus_units: int,
    arm_stats: Dict[str, Dict[str, Any]],
    raw_checks: Dict[str, Dict[str, Any]],
) -> Tuple[bool, List[str]]:
    """逐条判定报告是否不完整，返回 (incomplete, 原因列表)。

    参数：
        repeats:      每臂应有样本数
        corpus_units: 语料单位数（保留参数：单次执行应产生等量事件，用于消息文案）
        arm_stats:    {臂名: {"samples": int, "runtime_miss": [msg,...],
                              "errors": [msg,...], "event_issues": [msg,...],
                              "divergent_samples": [index,...]}}
        raw_checks:   {"raw/fast_samples.jsonl": {"exists": bool, "lines": int,
                                                   "expected_lines": int,
                                                   "missing_indices": [int,...]}}

    返回：
        (True, [原因,...]) 或 (False, [])
    """
    reasons: List[str] = []

    for arm in sorted(arm_stats):
        stats = arm_stats[arm]
        got = int(stats.get("samples", 0))

        # ① 样本数不足
        if got < repeats:
            reasons.append(
                f"[{arm}] 有效样本数 {got} < repeats {repeats}"
                f"（缺 {repeats - got} 条，样本不足不得出结论）"
            )

        # ② fail-closed 被触发 / 执行抛异常
        for msg in stats.get("runtime_miss", []):
            reasons.append(f"[{arm}] 快路 fail-closed 触发 RuntimeMissError: {msg}")
        for msg in stats.get("errors", []):
            reasons.append(f"[{arm}] 执行抛异常，该次不计入样本: {msg}")

        # ③ 事件流不完整（事件数 ≠ 语料单位数 / 事件缺 part）
        for issue in stats.get("event_issues", []):
            reasons.append(
                f"[{arm}] 事件流不完整: {issue}（每个单元必须恰好一条事件）"
            )

        # ④ 确定性被破坏：同输入必得同结果
        divergent: List[int] = [int(i) for i in stats.get("divergent_samples", [])]
        if divergent:
            reasons.append(
                f"[{arm}] 确定性指标在样本间不一致: sample_index={divergent}"
                f"（同输入 + 同种子必须逐值相等）"
            )

    # ⑤ 原始数据文件
    for rel in sorted(raw_checks):
        check = raw_checks[rel]
        if not check.get("exists"):
            reasons.append(f"{rel} 缺失（原始数据未落盘，不得用估算值填补）")
            continue
        lines = int(check.get("lines", 0))
        want = check.get("expected_lines")
        if want is not None and lines != int(want):
            reasons.append(
                f"{rel} 行数 {lines} ≠ 期望 {want}（差 {abs(int(want) - lines)} 条）"
            )
        missing = sorted(int(i) for i in check.get("missing_indices", []))
        if missing:
            reasons.append(f"{rel} 缺 index={missing}（样本/事件编号不连续）")

    return bool(reasons), reasons


# ---------------------------------------------------------------------------
# 2. 报告组装
# ---------------------------------------------------------------------------
def build_report(
    *,
    generated_at: Optional[str] = None,
    command: str = "",
    seed: int,
    repeats: int,
    warmup_runs: int,
    incomplete: bool,
    incomplete_reasons: Sequence[str],
    env: Dict[str, Any],
    corpus: Dict[str, Any],
    arms: Dict[str, Any],
    delta: Dict[str, Any],
    deterministic_metrics: Dict[str, Any],
    timing_metrics: Dict[str, Any],
    timing_metrics_meaningful: bool,
    raw: Dict[str, str],
    reference_gate: Dict[str, Any],
) -> Dict[str, Any]:
    """组装报告字典（不写盘）。字段名冻结，可追加不得改名或缺项。

    异常：
        TypeError:  incomplete 非 bool / incomplete_reasons 非序列 / 关键块不是字典
        ValueError: incomplete=True 却无原因（不得美化）；incomplete=False 却有原因；
                    arms 为空
    """
    if not isinstance(incomplete, bool):
        raise TypeError(
            f"build_report.incomplete: 必须是 bool，实际为 {incomplete!r}"
        )
    if not isinstance(incomplete_reasons, (list, tuple)):
        raise TypeError(
            "build_report.incomplete_reasons: 必须是列表或元组，"
            f"实际为 {type(incomplete_reasons).__name__}"
        )
    # 不得美化：不完整就必须给原因；完整就不能有残留原因
    if incomplete and not incomplete_reasons:
        raise ValueError(
            "build_report: incomplete=True 但 incomplete_reasons 为空——"
            "必须写明缺什么、哪个臂、哪个单位（不得美化，eval/AGENTS.md §③.3）"
        )
    if not incomplete and incomplete_reasons:
        raise ValueError(
            "build_report: incomplete=False 但 incomplete_reasons 非空——"
            "存在原因却声称完整，属于美化"
        )
    if not isinstance(arms, dict) or not arms:
        raise ValueError(f"build_report.arms: 必须是非空字典，实际为 {arms!r}")
    if not isinstance(timing_metrics, dict) or not timing_metrics:
        raise ValueError("build_report.timing_metrics: 必须是非空字典")
    if not isinstance(timing_metrics_meaningful, bool):
        raise TypeError(
            "build_report.timing_metrics_meaningful: 必须是 bool，"
            f"实际为 {timing_metrics_meaningful!r}"
        )
    # WHY: 替身适配器（synthetic=True）的时序数字是人工构造的，
    #      不得被报告背书为真机数据（T08 卡 §224 明令禁止）。
    #      校验必须落在函数体内——只在测试里断言等于把红线挪开。
    synthetic = env.get("adapter", {}).get("synthetic", False)
    if synthetic is True and timing_metrics_meaningful is not False:
        raise BenchReportError(
            f"build_report: env.adapter.synthetic={synthetic} 但 "
            f"timing_metrics_meaningful={timing_metrics_meaningful}——"
            f"替身适配器的时序数字不得被报告背书为真机数据"
        )

    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at if generated_at is not None else _utc_now_iso(),
        "command": command,
        "seed": seed,
        "repeats": repeats,
        "warmup_runs": warmup_runs,
        "incomplete": incomplete,
        "incomplete_reasons": list(incomplete_reasons),
        "env": dict(env) if isinstance(env, dict) else {},
        "corpus": dict(corpus) if isinstance(corpus, dict) else {},
        "caliber": dict(CALIBER),
        "arms": {arm: dict(block) for arm, block in arms.items()},
        "delta": dict(delta) if isinstance(delta, dict) else {},
        "deterministic_metrics": {
            arm: dict(block) for arm, block in (deterministic_metrics or {}).items()
        },
        "timing_metrics": {
            arm: dict(block) for arm, block in timing_metrics.items()
        },
        "timing_metrics_meaningful": bool(timing_metrics_meaningful),
        "raw": dict(raw) if isinstance(raw, dict) else {},
        "reference_gate": dict(reference_gate) if isinstance(reference_gate, dict) else {},
    }
    return report


# ---------------------------------------------------------------------------
# 3. 原始 JSONL 核对
# ---------------------------------------------------------------------------
def _expected_raw_counts(report: Dict[str, Any]) -> Dict[str, int]:
    """从 arms 反推每份原始 JSONL 应有的行数（无需额外字段，arms 里已带样本数与事件计数）。

    - samples：等于样本数（arms[arm]['tts_calls']['n']）；
    - events ：每次执行产生「单臂 state_counts 之和」条事件，跨样本累加，
               所以总数 = state_counts 之和 × 样本数。
    """
    expected: Dict[str, int] = {}
    for arm, block in (report.get("arms") or {}).items():
        counts = block.get("state_counts") or {}
        samples = int((block.get("tts_calls") or {}).get("n", 0))
        expected[f"{arm}_samples"] = samples
        expected[f"{arm}_events"] = int(sum(int(v) for v in counts.values())) * samples if counts else 0
    return expected


# 文件级 I/O（read_jsonl / sha256_of_file）在 eval._io：实现只有一份。
# 指纹咬合（bench 写 ↔ report 验）：eval/bench.py 落 raw 时写 `{name}_sha256` 指纹，
#   本模块 check_raw_on_disk 用**同一个** _io.sha256_of_file 重算并逐文件比对。
#   两侧共享一份实现后，分块策略不可能各自漂移——否则会出现「自己写的指纹自己验不过」
#   或「该抓的值级篡改抓不到」（docs/08 §8.7 欠账 1，T08b 审计 #5 留的破口）。
# WHY 是文件字节摘要而不是内容语义摘要：值级篡改（把 first_audio_ms 全部除以 1000）
#   不改变行数与 index，任何按计数/结构聚合的比对都抓不到它；只有逐字节摘要会变。

# ---------------------------------------------------------------------------
# 3b. 原始数据指纹（追加式字段，docs/08 §8.7 欠账 1）
# ---------------------------------------------------------------------------
# raw 块的指纹元数据键后缀：`{name}_sha256` / `{name}_lines`。
# 命名与既有 `{arm}_samples` / `{arm}_events` 路径键并列，只追加不改名。
_SHA256_SUFFIX: str = "_sha256"
_LINES_SUFFIX: str = "_lines"


def _raw_fingerprints(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从报告 raw 块提取每份原始数据的指纹：{name: {"sha256": …, "lines": …}}。

    老报告（T08/T08b 产出的，raw 块只有路径键）→ 返回空 dict；
    调用方据此退回原有的行数/index 核对，**不得**因缺指纹而报错（向后兼容）。
    """
    fingerprints: Dict[str, Dict[str, Any]] = {}
    for key, value in (report.get("raw") or {}).items():
        if not isinstance(key, str):
            continue
        if key.endswith(_SHA256_SUFFIX):
            fingerprints.setdefault(key[: -len(_SHA256_SUFFIX)], {})["sha256"] = value
        elif key.endswith(_LINES_SUFFIX):
            fingerprints.setdefault(key[: -len(_LINES_SUFFIX)], {})["lines"] = value
    return fingerprints


def check_raw_on_disk(report: Dict[str, Any], out_dir: Any) -> List[str]:
    """核对 out_dir 下 report['raw'] 指向的 JSONL：存在性 / 行数 / index 连续性 / 指纹。

    WHY：报告落盘时再核一次磁盘状态，防事后抽改原始数据却仍声称 complete。

    指纹核对（docs/08 §8.7 欠账 1，T08b 审计 #5 留的破口）：raw 块里带
      `{name}_sha256` / `{name}_lines` 时，逐文件比对**文件字节 sha256** 与行数——
      这样「行数与 index 都在、只把数值改掉」的值级篡改才可见（计数结构抓不到它）。
    老报告兼容：不带指纹的老报告（T08/T08b 产的）**沿用**原有行数/index 核对，
      不得因缺指纹而报错——报告 schema 是追加式的，旧报告必须仍能被 verify。
    """
    reasons: List[str] = []
    out = Path(out_dir)
    expected = _expected_raw_counts(report)
    fingerprints = _raw_fingerprints(report)

    for name, rel in sorted((report.get("raw") or {}).items()):
        # 指纹元数据键（*_sha256 / *_lines）不是文件指针，跳过；它们在下文参与核对。
        # 老报告没有这些键，循环行为与改造前完全一致。
        if not isinstance(rel, str) or name.endswith((_SHA256_SUFFIX, _LINES_SUFFIX)):
            continue
        path = out / rel
        if not path.is_file():
            reasons.append(f"{rel} 缺失（原始数据不存在，报告不得用估算值填补）")
            continue
        records = _io.read_jsonl(path)
        want = expected.get(name)
        if want is not None and len(records) != want:
            reasons.append(
                f"{rel} 行数 {len(records)} ≠ 期望 {want}"
                f"（差 {abs(want - len(records))} 条）"
            )
        bad = [r for r in records if "__bad_line__" in r or "__not_dict__" in r]
        if bad:
            reasons.append(
                f"{rel} 有 {len(bad)} 行无法解析（行号："
                f"{[r.get('__bad_line__', r.get('__not_dict__')) for r in bad]}）"
            )
        indices = [
            r["index"] for r in records
            if isinstance(r.get("index"), int)
        ]
        missing = sorted(set(range(len(records))) - set(indices))
        if missing:
            reasons.append(f"{rel} 缺 index={missing}（样本/事件编号不连续）")

        # 指纹核对：报告里带了指纹才做；不带则上面的行数/index 逻辑就是全部核对。
        fp = fingerprints.get(name) or {}
        if fp:
            want_hash = fp.get("sha256")
            if isinstance(want_hash, str) and want_hash:
                actual_hash = _io.sha256_of_file(path)
                if actual_hash != want_hash:
                    # 消息含文件名 + 期望/实际，「点名单个文件」是验收硬要求
                    reasons.append(
                        f"{rel} sha256 不符（期望 {want_hash}，实际 {actual_hash}）"
                        f"——原始数据被改过（值级篡改，计数结构抓不到它）"
                    )
            want_lines = fp.get("lines")
            if want_lines is not None and len(records) != int(want_lines):
                reasons.append(
                    f"{rel} 行数 {len(records)} ≠ 指纹记录 {int(want_lines)}"
                    f"（差 {abs(int(want_lines) - len(records))} 条）"
                )
    return reasons


def write_report(report: Dict[str, Any], out_dir: Any) -> Path:
    """写 report.json 到 out_dir，返回路径。

    落盘前用 check_raw_on_disk 重新核对磁盘上的原始 JSONL：
    若发现缺失/行数不符/index 不连续，则把 report['incomplete'] 置 True 并追加原因
    （原地更新传入的 report 字典，使返回值与磁盘内容一致）。

    异常：
        OSError: 目录创建或写文件失败
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    reasons = check_raw_on_disk(report, out)
    if reasons:
        report["incomplete"] = True
        report["incomplete_reasons"] = list(report.get("incomplete_reasons", [])) + reasons
    path = out / "report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 4. 人读摘要
# ---------------------------------------------------------------------------
def _fmt_interval(block: Optional[Dict[str, Any]]) -> str:
    """把 timing 块的 P50/P99 与 CI 渲染成一行。"""
    if not block or block.get("p50") is None:
        return "无有效样本"
    ci50 = block.get("ci95_p50")
    ci99 = block.get("ci95_p99")
    return (
        f"n={block.get('n')} P50={block['p50']:.3f}ms (CI95 {ci50[0]:.3f}~{ci50[1]:.3f}) "
        f"P99={block['p99']:.3f}ms (CI95 {ci99[0]:.3f}~{ci99[1]:.3f}) max={block['max']:.3f}ms"
    )


def render_summary(report: Dict[str, Any]) -> str:
    """渲染人读摘要（CLI 打印用）。

    规则：
      - incomplete=True → 第一行必须以 [INCOMPLETE] 开头并列出原因；
      - timing_metrics_meaningful=False → 必须有一行显式警告（替身适配器不产生真实合成耗时）；
      - 摘要里同时给样本量与置信区间，禁止只给点估计。
    """
    lines: List[str] = []

    if report.get("incomplete"):
        reasons: List[str] = list(report.get("incomplete_reasons", []))
        lines.append(
            f"[INCOMPLETE] 本报告数据不完整（{len(reasons)} 项原因），"
            f"结论不得对外引用："
        )
        for i, reason in enumerate(reasons, 1):
            lines.append(f"  {i}. {reason}")
    else:
        lines.append("[COMPLETE] 两臂样本齐备；deterministic_metrics 可逐值复现")

    meaningful = bool(report.get("timing_metrics_meaningful", True))
    if not meaningful:
        lines.append(
            "[警告] adapter.synthetic=True（离线替身适配器）：时序数字由字符数推算，"
            "不代表真实合成耗时，**不得对外引用**（不得美化，eval/AGENTS.md §③.3）"
        )

    arms = report.get("arms") or {}
    timing = report.get("timing_metrics") or {}
    lines.append("")
    lines.append(
        f"样本量 repeats={report.get('repeats')} warmup_runs={report.get('warmup_runs')} "
        f"seed={report.get('seed')} 分位口径={report.get('caliber', {}).get('quantile_method')}"
    )
    for arm in sorted(arms):
        block = arms[arm]
        counts = block.get("state_counts") or {}
        lines.append(
            f"[{arm}] hit={counts.get(HIT, 0)} miss={counts.get(MISS, 0)} "
            f"fallback={counts.get(FALLBACK, 0)} "
            f"{HIT_RATE}={block.get(HIT_RATE)} "
            f"{PRECAST_RATIO}={block.get(PRECAST_RATIO)} "
            f"tts_calls_p50={(block.get('tts_calls') or {}).get('p50')} "
            f"synthesized_chars_p50={(block.get('synthesized_chars') or {}).get('p50')}"
        )
        lines.append(f"[{arm}] 时序: {_fmt_interval(timing.get(arm))}")

    delta = report.get("delta") or {}
    if delta:
        lines.append(
            "delta(慢路-快路): "
            + ", ".join(f"{k}={v}" for k, v in delta.items())
        )

    gate = report.get("reference_gate") or {}
    if gate:
        lines.append(
            f"门槛 {gate.get('required_hit_rate')}（{gate.get('source')}）："
            f"实测 {gate.get('observed_hit_rate')} → "
            f"{'通过' if gate.get('passed') else '未通过'}"
        )

    raw = report.get("raw") or {}
    # 人读摘要只列文件指针；*_sha256 / *_lines 是机器核对用的指纹元数据（64 位摘要），
    # 印出来只会把摘要撑成噪声——需要核对时读 report.json 的 raw 块即可。
    pointers = {
        k: v for k, v in raw.items()
        if isinstance(v, str) and not k.endswith((_SHA256_SUFFIX, _LINES_SUFFIX))
    }
    if pointers:
        lines.append("原始数据: " + ", ".join(f"{k}={v}" for k, v in sorted(pointers.items())))

    lines.append("可复现: " + str(report.get("caliber", {}).get("reproducibility")))
    if report.get("command"):
        lines.append(f"复现命令: {report['command']}")
    return "\n".join(lines)
