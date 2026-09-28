"""
eval.bench — 离线对拍 harness：同一份话术语料跑两条产线并出可复现报告

两臂为什么必须严格对拍（eval/AGENTS.md ①）：
  - fast 臂：plan 单元用 key 引用预铸资产，allow_fallback=False（fail-closed）→ 期望每单元 hit；
  - slow 臂：**同一句话术**改用 text 表达（SAY_LIVE 自由文本），allow_fallback=True
              → 期望每单元 miss，reason == say_live_text；
  两臂共用**同一个 pack 对象**与**同一个 adapter**，否则音色/引擎差异会混进结论、数字不可比。

确定性 vs 时序的切分（eval/AGENTS.md ③.1）：
  - deterministic_metrics：不含挂钟时间的量（state_counts / hit_rate / precast_ratio /
    tts_calls / synthesized_chars）——同输入 + 同种子跑两次必须逐值相等（测试断言）；
  - timing_metrics：first_audio_ms 的分布（n / P50 / P99 / max / CI）——不同机器/负载下会变，
    所以报告口径只承诺「分布口径与样本量、CI 方法一致」，不承诺数值相等。

为什么分两臂：只测快路无法证明"零延迟"，只测慢路无法证明"省了多少"；
      只有同一语料、同一引擎、同一包对象下的两臂差值，才是可对外的结论。

为什么禁均值外推：首响延迟是厚尾分布（eval/AGENTS.md §①），
      均值会被极值拖走且不携带尾部信息，一律报 P50/P99 + 置信区间 + 样本量。

为什么固定 seed：bootstrap 区间是重采样得到的，种子不固定则同一份原始数据算出不同区间，
      报告无法复现（与 runtime 用 sha256 而非 hash() 的理由相同）。
"""

import argparse
import hashlib
import json
import os
import platform
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from assets.pack import AssetPackError, load_pack
from core.metrics_spec import (
    FALLBACK,
    FIRST_AUDIO_MS,
    HIT,
    HIT_RATE,
    KEY,
    MISS,
    PART,
    PACK_VERSION,
    PRECAST_RATIO,
    RATE,
    TURN_ID,
    VARIANT,
)
from core.protocol import VALID_RATES
from runtime import Executor, RuntimeMissError, SAMPLE_RATE

from .offline_tts import OfflineTts
# 文件级 I/O 与指纹摘要的统一实现（仅标准库；指纹写侧，见 eval/_io.py 的
# 「指纹咬合耦合」——report.check_raw_on_disk 是同一个函数的验侧）
from . import _io
from .report import (
    BenchReportError,
    assess_completeness,
    build_report,
    render_summary,
    write_report,
)
from .stats import (
    BOOTSTRAP_RESAMPLES,
    DEFAULT_REPEATS,
    DEFAULT_SEED,
    MIN_REPEATS,
    bootstrap_ci,
    percentile,
)

FAST_ARM: str = "fast"
SLOW_ARM: str = "slow"
_ARMS: Tuple[str, ...] = (FAST_ARM, SLOW_ARM)

# 语料单元里允许的句末标点（每条 text 只能是一句）
_SENTENCE_END: str = "。！？!?"


# ---------------------------------------------------------------------------
# 异常与配置
# ---------------------------------------------------------------------------
# BenchReportError 定义在 report.py（build_report 的硬校验需要它），此处 re-export。


@dataclass(frozen=True)
class BenchConfig:
    """一次对拍的配置。

    属性：
        pack:              AssetPack（assets.load_pack 装载的只读包）
        adapter:           注入的 TTS 适配器（真机 或 OfflineTts）
        corpus:            语料单位序列 {"key","text","rate","variant"}
        plan_id:           播报计划 ID 前缀（实际 plan_id = "{plan_id}-{臂名}"）
        repeats:           每臂采样次数（>= MIN_REPEATS）
        warmup_runs:       每臂预热次数（不计入样本，避免首跑冷读盘污染 P99）
        seed:              bootstrap 种子（固定 → 区间可复现）
        required_hit_rate: 命中率参考门槛（docs/01 判据 ε ≤ 1 - p^(1/n)）
        out_dir:           输出根目录（None → tempfile；音频写 audio/，原始数据写 raw/）
        corpus_path:       语料文件路径（追加：报告 corpus.path / sha256 的来源）
        corpus_meta:       语料文件元数据（追加：corpus_id 等）
        command:           可原样复现的完整命令行（追加）
    """

    pack: Any
    adapter: Any
    corpus: Tuple[Dict[str, Any], ...]
    plan_id: str = "bench"
    repeats: int = DEFAULT_REPEATS
    warmup_runs: int = 1
    seed: int = DEFAULT_SEED
    required_hit_rate: float = 0.987
    out_dir: Optional[Path] = None
    corpus_path: Optional[Path] = None
    corpus_meta: Optional[Dict[str, Any]] = None
    command: str = ""


# ---------------------------------------------------------------------------
# 语料装载与校验
# ---------------------------------------------------------------------------
def load_corpus(
    path: Any,
) -> Tuple[Tuple[Dict[str, Any], ...], Dict[str, Any]]:
    """读固定语料并校验，返回 (units tuple, 元数据 dict)。

    校验（全部 fail-closed，消息含具体值）：
      units 非空；key 非空且唯一；text 非空；rate ∈ slow/normal/fast；variant 是整数；
      每条 text 至多 1 个句末标点（与 compiler 源格式规矩一致，一条单元就是一句）。

    异常：
        BenchReportError: 文件不存在 / 非 JSON / 任一条校验不过
    """
    p = Path(path)
    if not p.is_file():
        raise BenchReportError(f"语料文件不存在: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchReportError(f"语料文件无法解析为 JSON: {p} ({exc})") from exc
    if not isinstance(raw, dict):
        raise BenchReportError(
            f"语料文件顶层必须是 JSON 对象，实际为 {type(raw).__name__}: {p}"
        )

    units_raw = raw.get("units")
    if not isinstance(units_raw, list) or len(units_raw) == 0:
        raise BenchReportError(
            f"语料文件 units 必须是非空列表，实际为 "
            f"{type(units_raw).__name__}（长度 {len(units_raw) if isinstance(units_raw, list) else 'N/A'}）: {p}"
        )

    units: List[Dict[str, Any]] = []
    seen: set = set()
    for i, unit in enumerate(units_raw):
        if not isinstance(unit, dict):
            raise BenchReportError(
                f"语料单元 #{i} 必须是对象，实际为 {type(unit).__name__}"
            )
        key = unit.get("key")
        if not isinstance(key, str) or not key:
            raise BenchReportError(
                f"语料单元 #{i} 的 key 必须是非空字符串，实际为 {key!r}"
            )
        if key in seen:
            raise BenchReportError(f"语料 key 重复: {key!r}（单元 #{i}）")
        seen.add(key)

        text = unit.get("text")
        if not isinstance(text, str) or not text:
            raise BenchReportError(
                f"语料单元 {key!r} 的 text 必须是非空字符串，实际为 {text!r}"
            )
        rate = unit.get("rate")
        if rate not in VALID_RATES:
            raise BenchReportError(
                f"语料单元 {key!r} 的 rate 必须是 {sorted(VALID_RATES)} 之一，"
                f"实际为 {rate!r}"
            )
        variant = unit.get("variant", 0)
        if not isinstance(variant, int) or isinstance(variant, bool):
            raise BenchReportError(
                f"语料单元 {key!r} 的 variant 必须是整数，实际为 {variant!r}"
            )
        ends = sum(1 for ch in text if ch in _SENTENCE_END)
        if ends > 1:
            raise BenchReportError(
                f"语料单元 {key!r} 的 text 含 {ends} 个句末标点"
                f"（每条必须是一句，与 compiler 源格式规矩一致）: {text!r}"
            )
        units.append({"key": key, "text": text, "rate": rate, "variant": variant})

    meta = {
        "corpus_id": raw.get("corpus_id"),
        "version": raw.get("version"),
        "notes": raw.get("notes"),
    }
    return tuple(units), meta


# ---------------------------------------------------------------------------
# 两臂 plan 构造（严格对拍）
# ---------------------------------------------------------------------------
def _fast_plan(corpus: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """快路 plan：用 key 引用预铸资产（SAY 原语）。"""
    return [
        {"key": u["key"], "rate": u["rate"], "variant": u["variant"]} for u in corpus
    ]


def _slow_plan(corpus: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """慢路 plan：同一句话术改用 text 表达（SAY_LIVE 自由文本，设计上就没有资产）。"""
    return [{"text": u["text"], "rate": u["rate"]} for u in corpus]


# ---------------------------------------------------------------------------
# 帧数与字符数推导
# ---------------------------------------------------------------------------
def _entry_frames(entry: Any) -> int:
    """包内条目时长（毫秒）换算成 16kHz 帧数。"""
    return int(round(entry.duration_ms * SAMPLE_RATE / 1000.0))


def _precast_frames(events: Sequence[Dict[str, Any]], pack: Any) -> int:
    """Σ 命中单元对应的包内音频帧数。

    按 (key, rate, variant) 匹配条目，只取 part_index 从 0 起连续的分片——
    与执行器的 part_index 升序探测语义一致（避免把跳号条目算进去）。
    """
    total = 0
    for event in events:
        if not event.get(HIT):
            continue
        entries = sorted(
            (
                e
                for e in pack.assets
                if e.key == event.get(KEY)
                and e.rate_key == event.get(RATE)
                and e.variant == event.get(VARIANT)
            ),
            key=lambda e: e.part_index,
        )
        part_index = 0
        for entry in entries:
            if entry.part_index != part_index:
                break
            total += _entry_frames(entry)
            part_index += 1
    return total


def _pack_text(pack: Any, key: Any, rate_key: Any, variant: Any) -> Optional[str]:
    """取包内某 key 的话术文本（降级时执行器合成的就是这条文本）。"""
    if not key:
        return None
    for entry in pack.assets:
        if entry.key == key and entry.rate_key == rate_key:
            if variant == "auto" or entry.variant == variant:
                return entry.text
    return None


def _synthesized_chars(
    plan_units: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    pack: Any,
) -> int:
    """推导值：Σ 非命中单元的文本长度 + Σ 槽值长度。

    注意：这是**推导值，不是 TTS 实测字符数**（报告 caliber 会标注），
    它只作单位会话成本的代理量。命中单元的文本完全不计（包内音频，零合成）。
    """
    by_part = {event.get(PART): event for event in events}
    total = 0
    for idx, unit in enumerate(plan_units):
        event = by_part.get(idx + 1)
        if event is not None and event.get(HIT):
            continue
        text = unit.get("text")
        if text is None:
            # key 单元未命中：执行器降级合成的是包内该条目的文本
            text = _pack_text(
                pack, unit.get("key"), unit.get("rate"), unit.get("variant")
            ) or str(unit.get("key") or "")
        total += len(text)
        # 槽值一律现场合成（runtime/AGENTS.md §①），无论主单元是否命中
        for value in (unit.get("slots") or {}).values():
            total += len(str(value))
    return total


# ---------------------------------------------------------------------------
# 单次执行 → 样本
# ---------------------------------------------------------------------------
def _make_sample(
    arm: str,
    index: int,
    turn_id: str,
    plan_units: Sequence[Dict[str, Any]],
    result: Any,
    pack: Any,
    units: int,
) -> Dict[str, Any]:
    """把一次 execute() 的结果折成一条原始样本。"""
    state_counts: Dict[str, int] = {HIT: 0, MISS: 0, FALLBACK: 0}
    for event in result.events:
        if event.get(HIT):
            state_counts[HIT] += 1
        elif event.get(MISS):
            state_counts[MISS] += 1
        elif event.get(FALLBACK):
            state_counts[FALLBACK] += 1

    precast = _precast_frames(result.events, pack)
    total_frames = int(round(result.total_duration_ms * SAMPLE_RATE / 1000.0))
    # WHY: 不得夹取——比值 > 1 说明包元数据不一致（分子 > 分母），如实写出并标 incomplete；
    #      分母为 0 时给 None（不得写 0.0，0.0 会被读成"没有任何预铸音频"）。
    if total_frames == 0:
        precast_ratio = None
    else:
        precast_ratio = round(precast / total_frames, 6)

    return {
        "index": index,
        "arm": arm,
        TURN_ID: turn_id,
        FIRST_AUDIO_MS: float(result.first_audio_ms),
        "tts_calls": int(result.tts_calls),
        "synthesized_chars": int(_synthesized_chars(plan_units, result.events, pack)),
        "total_duration_ms": int(result.total_duration_ms),
        "state_counts": state_counts,
        HIT_RATE: round(state_counts[HIT] / units, 6) if units else 0.0,
        PRECAST_RATIO: precast_ratio,
        "precast_frames": precast,
        "total_frames": total_frames,
    }


# ---------------------------------------------------------------------------
# 臂执行
# ---------------------------------------------------------------------------
@dataclass
class _ArmRun:
    """一条臂的原始结果（样本、事件、以及各类失败留痕）。"""

    name: str
    samples: List[Dict[str, Any]]
    events: List[Dict[str, Any]]
    miss_messages: List[str]
    error_messages: List[str]
    event_issues: List[str]


def _run_arm(arm: str, config: BenchConfig, audio_dir: Path) -> _ArmRun:
    """跑一条臂：warmup_runs 次预热（不计样本）+ repeats 次采样。

    纪律：
      - fail-closed 抛 RuntimeMissError → 捕获并留痕（原因含 key），**不得**改用
        allow_fallback=True 掩盖，也不得静默跳过；
      - 其他异常 → 该次不计入样本，异常类型与消息进留痕；
      - 事件数与语料单位数不符 / 事件缺 part → 该次不计入样本，留痕。
    """
    allow_fallback = arm != FAST_ARM
    plan_units = _fast_plan(config.corpus) if arm == FAST_ARM else _slow_plan(config.corpus)
    executor = Executor(config.pack, config.adapter, allow_fallback=allow_fallback)
    # plan_id 带臂名，便于事件溯源；turn_id 逐次变化
    plan_id = f"{config.plan_id}-{arm}"
    units = len(config.corpus)

    samples: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    miss_messages: List[str] = []
    error_messages: List[str] = []
    event_issues: List[str] = []

    total_runs = config.warmup_runs + config.repeats
    for i in range(total_runs):
        turn_id = f"{plan_id}-{i:04d}"
        out_path = audio_dir / f"{arm}_{i:04d}.wav"

        try:
            result = executor.execute(
                plan_units, plan_id=plan_id, turn_id=turn_id, out_path=out_path
            )
        except RuntimeMissError as exc:
            miss_messages.append(f"{turn_id}: {exc}")
            continue
        except Exception as exc:
            # WHY：必须留痕。语音链路的静默降级表现为"突然换了个声音"，事后无法定位，
            #      所以任何异常都不能被吞掉。
            error_messages.append(f"{turn_id}: {type(exc).__name__}: {exc}")
            continue

        if i < config.warmup_runs:
            continue

        # 事件流完整性：每个单元恰好一条事件，part 从 1 连续到 units
        if len(result.events) != units:
            event_issues.append(
                f"{turn_id}: 事件数 {len(result.events)} ≠ 语料单位数 {units}"
            )
            continue
        parts = [event.get(PART) for event in result.events]
        missing_parts = sorted(
            set(range(1, units + 1))
            - {int(p) for p in parts if isinstance(p, int)}
        )
        if missing_parts:
            event_issues.append(f"{turn_id}: 事件缺 part={missing_parts}")
            continue

        events.extend(result.events)
        samples.append(
            _make_sample(arm, i - config.warmup_runs, turn_id, plan_units, result,
                         config.pack, units)
        )

    return _ArmRun(
        name=arm,
        samples=samples,
        events=events,
        miss_messages=miss_messages,
        error_messages=error_messages,
        event_issues=event_issues,
    )


# ---------------------------------------------------------------------------
# 原始数据落盘（文件级 I/O 全部在 eval._io：实现只有一份）
# ---------------------------------------------------------------------------
# T21 起本文件不再持有 JSONL 读写与 sha256 的实现，一律经 `from . import _io` 调用。
# 为何收敛点必须是 eval/_io.py 而不是本文件：本模块为跑 harness 静态 import 了
#   assets/ 与 runtime/，helper 住在这里会把这些跨层依赖带给只需要标准库的
#   readback / report。
# 指纹咬合（bench 写 ↔ report 验）：下方 _build_raw_files 写 `{name}_sha256` 指纹，
#   eval/report.py 的 check_raw_on_disk 用**同一个** _io.sha256_of_file 重算比对；
#   两侧共享一份实现后，分块策略不可能各自漂移（docs/08 §8.7 欠账 1）。
# WHY 是文件字节 sha256 而不是内容语义哈希（T08b 审计 #5 留给 T10b 的破口）：
#   行数与 index 都在、只把 first_audio_ms 除以 1000 的「值级篡改」不改变任何计数结构，
#   语义哈希（按记录语义聚合）抓不到它；只有逐字节摘要才能反映「文件内容被换过」。
#   用 sha256 而不是内建 hash()：后者字符串种子随进程变化，同一文件两次跑会算出不同值。


def _build_raw_files(
    runs: Dict[str, _ArmRun], raw_dir: Path, work_dir: Path
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """写 4 份原始 JSONL，并回读核对（存在性 / 行数 / index 连续性）+ 记指纹。

    报告 raw 块是**追加式**的：既有 4 个「名字 → 相对路径」指针原样保留，
    再为每份文件追加 `{name}_sha256`（文件字节 sha256）与 `{name}_lines`（应有行数），
    供 eval.report.check_raw_on_disk 做值级核对（docs/08 §8.7 欠账 1）。

    返回：
        (raw 块指针+指纹, raw_checks)
    """
    pointers: Dict[str, Any] = {}
    checks: Dict[str, Dict[str, Any]] = {}

    for arm in _ARMS:
        run = runs[arm]
        files = {
            f"{arm}_samples": (raw_dir / f"{arm}_samples.jsonl", run.samples),
            f"{arm}_events": (
                raw_dir / f"{arm}_events.jsonl",
                [
                    {**event, "arm": arm, "index": seq}
                    for seq, event in enumerate(run.events)
                ],
            ),
        }
        for name, (path, records) in files.items():
            _io.write_jsonl(path, records)
            rel = path.relative_to(work_dir).as_posix()
            pointers[name] = rel
            # 追加指纹：路径键原样保留，新字段只加不改名（报告 schema 可追加不得改名）
            pointers[f"{name}_sha256"] = _io.sha256_of_file(path)
            pointers[f"{name}_lines"] = len(records)
            # 回读核对：落盘后立即验证，报告不引用自己写不出来的文件
            existing = _io.read_jsonl(path) if path.is_file() else []
            indices = [r["index"] for r in existing if isinstance(r.get("index"), int)]
            checks[rel] = {
                "exists": path.is_file(),
                "lines": len(existing),
                "expected_lines": len(records),
                "missing_indices": sorted(set(range(len(existing))) - set(indices)),
            }
    return pointers, checks


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
def _quant_summary(values: Sequence[float]) -> Dict[str, Any]:
    """n / P50 / max 三元组（0 样本时给 None，不编造点估计）。"""
    if not values:
        return {"n": 0, "p50": None, "max": None}
    return {
        "n": len(values),
        "p50": percentile(values, 0.5),
        "max": float(max(values)),
    }


def _deterministic_signature(sample: Dict[str, Any]) -> Tuple[Any, ...]:
    """一条样本的确定性指纹：同输入 + 同种子跑两次必须完全相同。"""
    return (
        tuple(sorted(sample["state_counts"].items())),
        sample[HIT_RATE],
        sample[PRECAST_RATIO],
        sample["tts_calls"],
        sample["synthesized_chars"],
    )


def _arm_aggregate(
    samples: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[int]]:
    """聚合一条臂的确定性指标，返回 (聚合块, 与首样本不一致的样本序号列表)。

    state_counts / hit_rate / precast_ratio 取首样本值（它们每次执行必相同，
    不一致即确定性被破坏 → 走 incomplete）；tts_calls / synthesized_chars 报分布。
    """
    if not samples:
        return (
            {
                "state_counts": {HIT: 0, MISS: 0, FALLBACK: 0},
                HIT_RATE: None,
                PRECAST_RATIO: None,
                "tts_calls": {"n": 0, "p50": None, "max": None},
                "synthesized_chars": {"n": 0, "p50": None, "max": None},
            },
            [],
        )
    canon = samples[0]
    sig0 = _deterministic_signature(canon)
    divergent = [
        i for i, sample in enumerate(samples) if _deterministic_signature(sample) != sig0
    ]
    return (
        {
            "state_counts": dict(canon["state_counts"]),
            HIT_RATE: canon[HIT_RATE],
            PRECAST_RATIO: canon[PRECAST_RATIO],
            "tts_calls": _quant_summary([s["tts_calls"] for s in samples]),
            "synthesized_chars": _quant_summary(
                [s["synthesized_chars"] for s in samples]
            ),
        },
        divergent,
    )


def _timing_block(samples: Sequence[Dict[str, Any]], seed: int) -> Dict[str, Any]:
    """first_audio_ms 的分布块：n / P50 / P99 / max / CI95 / unit / machine_dependent。

    machine_dependent 恒为 True——挂钟时间必然受机器与负载影响；
    离线替身适配器的问题由 timing_metrics_meaningful 单独标记。

    WHY: n < MIN_REPEATS 时 bootstrap_ci 会抛 ValueError（正确行为——样本不足不得出 CI），
         但 _timing_block 不应中止整轮对拍：样本不足的判定归 assess_completeness，
         此处返回 None 值让报告如实呈现"数据不够"而不是"整轮崩溃"。
    """
    if not samples:
        return {
            "n": 0,
            "p50": None,
            "p99": None,
            "max": None,
            "ci95_p50": None,
            "ci95_p99": None,
            "unit": "ms",
            "machine_dependent": True,
        }
    values = [sample[FIRST_AUDIO_MS] for sample in samples]
    n = len(values)
    if n < MIN_REPEATS:
        # WHY: 降级路径——n 不足时不给点估计与 CI（避免用稀疏样本编造统计量），
        #      但保留 n 让报告如实显示"实际样本数"。
        return {
            "n": n,
            "p50": None,
            "p99": None,
            "max": None,
            "ci95_p50": None,
            "ci95_p99": None,
            "unit": "ms",
            "machine_dependent": True,
        }
    return {
        "n": n,
        "p50": percentile(values, 0.5),
        "p99": percentile(values, 0.99),
        "max": float(max(values)),
        "ci95_p50": list(bootstrap_ci(values, 0.5, seed=seed)),
        "ci95_p99": list(bootstrap_ci(values, 0.99, seed=seed)),
        "unit": "ms",
        "machine_dependent": True,
    }


def _delta(fast_agg: Dict[str, Any], slow_agg: Dict[str, Any],
           fast_timing: Dict[str, Any], slow_timing: Dict[str, Any]) -> Dict[str, Any]:
    """差值 = 慢路 - 快路（正值表示慢路更慢/更贵）；任一侧无数据给 None，不编造。"""

    def _sub(key: str, fast: Dict[str, Any], slow: Dict[str, Any]) -> Optional[float]:
        fv, sv = fast.get(key), slow.get(key)
        if fv is None or sv is None:
            return None
        return round(float(sv) - float(fv), 6)

    return {
        f"{FIRST_AUDIO_MS}_p50": _sub("p50", fast_timing, slow_timing),
        f"{FIRST_AUDIO_MS}_p99": _sub("p99", fast_timing, slow_timing),
        "tts_calls_p50": _sub("p50", fast_agg["tts_calls"], slow_agg["tts_calls"]),
        "synthesized_chars_p50": _sub(
            "p50", fast_agg["synthesized_chars"], slow_agg["synthesized_chars"]
        ),
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def _assert_out_dir_outside_pack(out_dir: Any, pack_root: Any) -> None:
    """断言 out_dir 不落在资产包目录内，否则抛 BenchReportError（docs/08 §8.7 欠账 2）。

    WHY **必须在建工作目录之前**检查：一旦 `mkdir(audio_dir)` 执行，`audio/`、`raw/`
      就已经写进冻结的资产包目录——即使随后抛错，污染也已经发生（assets/AGENTS.md ②
      资产包是只读契约），回滚要人工删。先检查则完全不产生任何文件。
    WHY 用 resolve() 的绝对路径做子路径判定而不是字符串前缀比较：字符串比较会被
      `../` 逃逸与 `pack-evil` 这类同名前缀绕过。
    消息含两个路径（out 与 pack），调用方一眼能看出是哪条被拦下。
    """
    pack_abs = Path(pack_root).resolve()
    out_abs = Path(out_dir).resolve()
    if out_abs == pack_abs or pack_abs in out_abs.parents:
        raise BenchReportError(
            f"--out {out_dir} 落在资产包目录 {pack_root} 内"
            f"（解析后 out={out_abs}，pack={pack_abs}）——"
            f"对拍产物不得写进包目录（包是只读契约，assets/AGENTS.md ②）"
        )


def run_bench(config: BenchConfig) -> Dict[str, Any]:
    """跑两臂对拍并组装报告字典（**不写盘**）。

    流程：配置校验 → 建工作目录 → 两臂执行 → 原始 JSONL 落盘并回读核对
          → incomplete 判定 → 报告组装。

    异常：
        BenchReportError: repeats 不足 / corpus 为空 / plan_id 为空（此时不产生任何文件）
    """
    if not isinstance(config, BenchConfig):
        raise TypeError(
            f"run_bench: config 必须是 BenchConfig，实际为 {type(config).__name__}"
        )
    if not isinstance(config.repeats, int) or config.repeats < MIN_REPEATS:
        raise BenchReportError(
            f"--repeats {config.repeats} 小于下限 MIN_REPEATS={MIN_REPEATS}——"
            f"厚尾分布禁均值外推（eval/AGENTS.md §①），样本不足不得出报告"
        )
    if not isinstance(config.warmup_runs, int) or config.warmup_runs < 0:
        raise BenchReportError(
            f"--warmup {config.warmup_runs} 必须是非负整数"
        )
    if not config.plan_id:
        raise BenchReportError("--plan-id 不能为空（plan_id 参与事件溯源）")
    units = len(config.corpus)
    if units == 0:
        raise BenchReportError("corpus 为空：至少需要 1 个播报单元，实际为 0 个")

    # --out 越界拦截（docs/08 §8.7 欠账 2）：**必须在建目录之前**执行，
    # 否则 mkdir 已经污染冻结的包目录。包 root 取不到时（非 assets.load_pack 的产物）
    # 不拦——此时没有可保护的包目录。
    pack_root = getattr(config.pack, "root", None)
    if config.out_dir is not None and pack_root is not None:
        _assert_out_dir_outside_pack(config.out_dir, pack_root)

    # 工作目录：音频写 audio/，原始数据写 raw/（绝不写进包目录，包是只读契约）
    work_dir = (
        Path(config.out_dir)
        if config.out_dir
        else Path(tempfile.mkdtemp(prefix="vox-bench-"))
    )
    audio_dir = work_dir / "audio"
    raw_dir = work_dir / "raw"
    audio_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    runs = {arm: _run_arm(arm, config, audio_dir) for arm in _ARMS}
    pointers, raw_checks = _build_raw_files(runs, raw_dir, work_dir)

    aggregates: Dict[str, Any] = {}
    arm_stats: Dict[str, Dict[str, Any]] = {}
    for arm in _ARMS:
        run = runs[arm]
        aggregate, divergent = _arm_aggregate(run.samples)
        aggregates[arm] = aggregate
        arm_stats[arm] = {
            "samples": len(run.samples),
            "runtime_miss": list(run.miss_messages),
            "errors": list(run.error_messages),
            "event_issues": list(run.event_issues),
            "divergent_samples": divergent,
        }

    timing = {arm: _timing_block(runs[arm].samples, config.seed) for arm in _ARMS}
    incomplete, reasons = assess_completeness(
        repeats=config.repeats,
        corpus_units=units,
        arm_stats=arm_stats,
        raw_checks=raw_checks,
    )

    # WHY: precast_ratio > 1 说明包元数据不一致（分子 > 分母），
    #      分母为 0 说明输出总帧数为 0——都不能出结论，必须标 incomplete。
    #      不得夹取到 1.0，不得写 0.0（会被读成"没有任何预铸音频"）。
    for arm in _ARMS:
        run = runs[arm]
        if not run.samples:
            continue
        first_sample = run.samples[0]
        ratio = aggregates[arm].get(PRECAST_RATIO)
        total_f = first_sample.get("total_frames", 0)
        precast_f = first_sample.get("precast_frames", 0)
        if ratio is not None and ratio > 1.0:
            reasons.append(
                f"[{arm}] precast_ratio={ratio} > 1.0"
                f"（分子 precast_frames={precast_f} > 分母 total_frames={total_f}，"
                f"包元数据不一致）"
            )
            incomplete = True
        elif ratio is None and total_f == 0:
            reasons.append(
                f"[{arm}] precast_ratio=None（输出总帧数 total_frames=0，无法计算）"
            )
            incomplete = True

    adapter = config.adapter
    # WHY: 不依赖单一属性——忘记打 synthetic 标记的替身适配器会被误认为真机数据，
    #      导致时序数字被报告背书为真机（T08 卡 §224 明令禁止）。
    #      三重检查：
    #        ① adapter.synthetic is True（显式标记，如 OfflineTts）；
    #        ② 模块名以 eval. 开头（unittest discover 前缀场景）；
    #        ③ 源文件位于 eval 包目录下（unittest discover -s eval 无前缀场景）；
    #        ④ 模块不在 adapters/ 下且未显式声明 synthetic=False（第三方替身场景）。
    import os as _os
    import sys as _sys
    _eval_dir = _os.path.dirname(_os.path.abspath(__file__))
    _adapter_mod_name = type(adapter).__module__
    _adapter_mod_obj = _sys.modules.get(_adapter_mod_name)
    _adapter_file = getattr(_adapter_mod_obj, '__file__', '') or ''
    _adapter_dir = _os.path.dirname(_os.path.abspath(_adapter_file)) if _adapter_file else ''
    _in_eval_pkg = (
        _adapter_dir == _eval_dir
        or _adapter_dir.startswith(_eval_dir + _os.sep)
    )
    _in_adapters = _adapter_mod_name.startswith("adapters.")
    _has_explicit_synthetic = hasattr(adapter, "synthetic")
    synthetic = (
        getattr(adapter, "synthetic", False) is True
        or _adapter_mod_name.startswith("eval.")
        or _in_eval_pkg
        or (not _in_adapters and not _has_explicit_synthetic)
    )
    env = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "adapter": {
            "name": getattr(adapter, "name", type(adapter).__name__),
            "voice": getattr(adapter, "voice", None),
            "model_version": getattr(adapter, "model_version", None),
            "synthetic": synthetic,
        },
        "pack": {
            "pack_version": getattr(config.pack, "pack_version", None),
            "voice": getattr(config.pack, "voice", None),
            "model_version": getattr(config.pack, "model_version", None),
            "asset_count": len(getattr(config.pack, "assets", []) or []),
            "path": str(getattr(config.pack, "root", "")),
        },
    }

    corpus_info: Dict[str, Any] = {
        "path": str(config.corpus_path) if config.corpus_path else None,
        "corpus_id": (config.corpus_meta or {}).get("corpus_id"),
        "units": units,
        "sha256": None,
    }
    if config.corpus_path and Path(config.corpus_path).is_file():
        # 只取前 16 位：报告里做指纹对照用，不需要完整摘要
        corpus_info["sha256"] = hashlib.sha256(
            Path(config.corpus_path).read_bytes()
        ).hexdigest()[:16]

    observed_hit_rate = aggregates[FAST_ARM][HIT_RATE]
    reference_gate = {
        "required_hit_rate": config.required_hit_rate,
        "source": "docs/01 判据 ε ≤ 1 - p^(1/n)（p=0.9, n=8）",
        "observed_hit_rate": observed_hit_rate,
        "passed": bool(
            observed_hit_rate is not None
            and observed_hit_rate >= config.required_hit_rate
        ),
    }

    # deterministic_metrics 与 arms 内容一致：arms 是"全部指标"的入口，
    # deterministic_metrics 是"可逐值复现"承诺的显式面（后续 arms 若并入时序，
    # 二者不会混淆）。
    deterministic_metrics = {
        arm: {k: v for k, v in aggregates[arm].items()} for arm in _ARMS
    }

    return build_report(
        command=config.command,
        seed=config.seed,
        repeats=config.repeats,
        warmup_runs=config.warmup_runs,
        incomplete=incomplete,
        incomplete_reasons=reasons,
        env=env,
        corpus=corpus_info,
        arms=aggregates,
        delta=_delta(
            aggregates[FAST_ARM], aggregates[SLOW_ARM], timing[FAST_ARM], timing[SLOW_ARM]
        ),
        deterministic_metrics=deterministic_metrics,
        timing_metrics=timing,
        timing_metrics_meaningful=not synthetic,
        raw=pointers,
        reference_gate=reference_gate,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析 CLI 参数（argparse 自带未知参数/类型错误的非零退出）。"""
    parser = argparse.ArgumentParser(
        prog="python3 -m eval.bench",
        description="离线对拍 harness：同一语料跑快路（命中预铸资产）vs 慢路（全程现场合成），出可复现报告",
    )
    parser.add_argument("--pack", required=True, help="资产包根目录（含 manifest.json）")
    parser.add_argument("--corpus", required=True, help="固定语料 JSON 文件")
    parser.add_argument("--out", required=True, help="输出目录（报告 + audio/ + raw/）")
    parser.add_argument(
        "--adapter",
        default=None,
        help="适配器 '模块:类名'（如 adapters.tts_macsay:MacSayTts）；缺省用离线替身 OfflineTts",
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--required-hit-rate", type=float, default=0.987)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--plan-id", default="bench")
    return parser.parse_args(argv)


def _command_string(args: argparse.Namespace) -> str:
    """重建可原样复现的完整命令行（所有参数显式写出，不省默认值）。"""
    parts: List[str] = [
        "python3",
        "-m",
        "eval.bench",
        "--pack",
        str(args.pack),
        "--corpus",
        str(args.corpus),
        "--out",
        str(args.out),
    ]
    if args.adapter:
        parts += ["--adapter", args.adapter]
    parts += [
        "--repeats",
        str(args.repeats),
        "--seed",
        str(args.seed),
        "--required-hit-rate",
        str(args.required_hit_rate),
        "--warmup",
        str(args.warmup),
        "--plan-id",
        str(args.plan_id),
    ]
    return " ".join(shlex.quote(part) for part in parts)


def _resolve_adapter(spec: Optional[str], pack: Any) -> Any:
    """动态解析适配器（importlib）；缺省用 OfflineTts（voice/model_version 取包里的值）。

    WHY：eval/ 的静态 import 里不得出现 adapters/（层边界，适配器一律注入），
        所以 CLI 侧用 importlib 动态加载。
    """
    if not spec:
        return OfflineTts(
            voice=pack.voice, model_version=pack.model_version
        )
    if ":" not in spec:
        raise BenchReportError(
            f"--adapter 必须是 '模块:类名' 形式，实际为 {spec!r}"
        )
    module_name, _, class_name = spec.partition(":")
    try:
        import importlib

        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise BenchReportError(f"无法导入适配器模块 {module_name!r}: {exc}") from exc
    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise BenchReportError(
            f"模块 {module_name!r} 中没有类 {class_name!r}（实际 --adapter={spec!r}）"
        ) from exc
    try:
        adapter = cls()
    except Exception as exc:
        raise BenchReportError(
            f"适配器 {spec!r} 无法默认实例化: {type(exc).__name__}: {exc}"
        ) from exc
    for attr in ("synthesize", "voice", "model_version"):
        if not hasattr(adapter, attr):
            raise BenchReportError(
                f"适配器 {spec!r} 缺成员 {attr!r}（不符 TTS 接口契约）"
            )
    return adapter


# 需要在 CLI 顶层拦住的异常类型（其余异常属实现缺陷，直接 traceback）
_CLI_ERRORS = (
    BenchReportError,
    AssetPackError,
    OSError,
    ValueError,
    TypeError,
    ImportError,
    AttributeError,
    RuntimeError,
)


def run_bench_cli(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口：写盘 + 打印人读摘要，返回退出码。

    退出码：
        0 = 报告已写出（含 incomplete 的报告也会写出——incomplete 不阻止出报告）
        2 = 配置/参数错误（**不产生报告文件**）
    """
    args = _parse_args(argv)

    try:
        pack = load_pack(args.pack)
        corpus, meta = load_corpus(args.corpus)
        adapter = _resolve_adapter(args.adapter, pack)
        config = BenchConfig(
            pack=pack,
            adapter=adapter,
            corpus=corpus,
            plan_id=args.plan_id,
            repeats=args.repeats,
            warmup_runs=args.warmup,
            seed=args.seed,
            required_hit_rate=args.required_hit_rate,
            out_dir=Path(args.out),
            corpus_path=Path(args.corpus),
            corpus_meta=meta,
            command=_command_string(args),
        )
        report = run_bench(config)
    except _CLI_ERRORS as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    # 执行期失败导致样本不足：报告已写出，但退出码非 0。
    # WHY: 不得用"参数错误"的消息掩盖"执行期失败"的语义——
    #      退出码 2 与参数错误合流，但 stderr 消息必须说明是执行期失败。
    incomplete_from_bench = report["incomplete"]

    path = write_report(report, config.out_dir)
    print(render_summary(report))
    print(f"\n报告已写入: {path}")

    if incomplete_from_bench:
        for reason in report["incomplete_reasons"]:
            print(f"错误: {reason}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(run_bench_cli())
