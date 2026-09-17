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


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """逐行读 JSONL；坏行记为 {"__bad_line__": 行号} 而不是抛错（报告要如实呈现损坏）。"""
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except ValueError:
                value = {"__bad_line__": line_no}
            records.append(value if isinstance(value, dict) else {"__not_dict__": line_no})
    return records


def check_raw_on_disk(report: Dict[str, Any], out_dir: Any) -> List[str]:
    """核对 out_dir 下 report['raw'] 指向的 JSONL：存在性 / 行数 / index 连续性。

    WHY：报告落盘时再核一次磁盘状态，防事后抽改原始数据却仍声称 complete。
    """
    reasons: List[str] = []
    out = Path(out_dir)
    expected = _expected_raw_counts(report)

    for name, rel in sorted((report.get("raw") or {}).items()):
        path = out / rel
        if not path.is_file():
            reasons.append(f"{rel} 缺失（原始数据不存在，报告不得用估算值填补）")
            continue
        records = _read_jsonl(path)
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
    if raw:
        lines.append("原始数据: " + ", ".join(f"{k}={v}" for k, v in sorted(raw.items())))

    lines.append("可复现: " + str(report.get("caliber", {}).get("reproducibility")))
    if report.get("command"):
        lines.append(f"复现命令: {report['command']}")
    return "\n".join(lines)
