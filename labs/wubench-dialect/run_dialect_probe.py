"""
labs/wubench-dialect/run_dialect_probe.py — 方言域 ASR 能力边界实测（T15）

链路（严格止于 ASR，止于 CER）：
    Wu-Bench asr.parquet（吴语 RIFF/WAV 字节 + label 转写）
        → 固定 seed 抽样 100 条
        → 音频写系统临时目录 → adapters.asr_omlx.OmlxAsr.transcribe → 用完即删
        → eval.cer.normalize_for_cer + eval.cer.cer
        → eval.stats.percentile / bootstrap_ci
        → raw/samples.jsonl（逐条明细，含逐字原文，属 labs/**/raw/** 不进公开分发）
          report.json（汇总，不含任何语料逐字原文）

不做的事（范围裁定，见 README.md §范围裁定）：
  - 不做「提案 → 命中」实验：方言集没有「该命中哪个 key」的标注，不得自造命中；
  - 不做语义归一 / 同义替换 / 拼音近似——CER 口径一律走 eval/cer.py；
  - 不并发（oMLX 是本地服务，禁止压测）；不起停任何服务；不联网（除本机 oMLX 端点）。

依赖纪律：
  - import pyarrow **只出现在本 labs 目录内**；产品层（core/rules/compiler/assets/
    runtime/eval/cli/adapters/packs/tools）零 pyarrow 依赖；
  - 解释器必须为 ~/miniforge3/bin/python（3.12 + pyarrow；主解释器 3.14 无 pyarrow wheel）；
  - 产品代码经 PYTHONPATH=仓库根 import，不复制任何一份等价逻辑。

失败纪律（fail-closed）：
  - parquet 缺必需列 / 行数 0 → 抛错退出，不产出半个报告；
  - 时长 > 60 s → 跳过并留痕（skipped_too_long）；ASR 抛 AsrError → 留痕（asr_error）；
  - 跳过/失败不进 CER 统计分母，但都进 report 计数；
  - 脚本结尾自断言 n_valid + n_skipped + n_asr_fail == n_sampled，失败即非零退出。

用法（裸跑即可，无需 PYTHONPATH——main() 自带上仓库根）：
    ~/miniforge3/bin/python labs/wubench-dialect/run_dialect_probe.py
    ~/miniforge3/bin/python labs/wubench-dialect/run_dialect_probe.py --limit 20

落盘分流（防部分跑破坏性覆盖正式产物）：
    正式跑（无 --limit） → report.json / raw/samples.jsonl
    部分跑（--limit N） → report.limit{N}.json / raw/samples.limit{N}.jsonl
                          （env.limit=N、env.output_is_partial=true；不触碰正式产物）

退出码（契约，全部收口，不以裸 traceback 逃出）：
    0 成功 / 2 用法或参数错误 / 3 运行期失败（stderr 带异常类型名 + traceback）
    / 4 自断言不通过（含 sanity 与计数自断言）
"""

import argparse
import hashlib
import json
import os
import platform
import random
import struct
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 路径与固定常量（改这里 = 改口径，报告 caliber 需同步）
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
RAW_DIR = HERE / "raw"
RAW_SAMPLES = RAW_DIR / "samples.jsonl"
REPORT = HERE / "report.json"

# 语料（仓外，apache-2.0 公开集）；只读，绝不写回
PARQUET_PATH = Path(
    os.environ.get(
        "WUBENCH_PARQUET",
        "~/corpus/ASLP-lab__WenetSpeech-Wu-Bench/understanding/asr.parquet",
    )
)

REQUIRED_COLUMNS = ("utt_id", "audio", "label")

SAMPLE_SEED: int = 20260919          # 固定 seed（random.Random(20260919)）
N_SAMPLED: int = 100                 # 从 4851 行里抽 100 行
MAX_DURATION_SECONDS: float = 60.0   # 超过即跳过（本地服务不压测）
MAX_AUDIO_BYTES: int = 64 * 1024 * 1024  # 防御性上限：跳过而不写巨块进临时目录
SKIP_TOO_LONG = "skipped_too_long"
SKIP_TOO_LARGE = "skipped_too_large"
STATUS_OK = "ok"
STATUS_ASR_ERROR = "asr_error"
STATUS_SKIP = SKIP_TOO_LONG

# 统计口径：与 eval/stats.py 冻结常量对齐
BOOTSTRAP_SEED: int = 20260919
BOOTSTRAP_Q: float = 0.5
BOOTSTRAP_LEVEL: float = 0.95
BOOTSTRAP_RESAMPLES: int = 2000  # 显式传入 bootstrap_ci，报告字段与实际行为绑定

# scope 字段（范围裁定原文依据，随卡登记 docs/13 §七#1 与 00-索引）
SCOPE: Dict[str, Any] = {
    "link": "audio -> ASR -> normalize_for_cer -> CER（止于 CER）",
    "excluded": "提案 -> 命中 不在本卡范围",
    "excluded_reason": [
        "语义提案已实测否掉（docs/13 §八#1：top-1 11.8% vs 门槛 98.7%，差 8 倍）",
        "方言集没有「该命中哪个 key」的标注，第八批前置裁定 3 明令不得自造命中（00-索引 第八批）",
    ],
    "purpose": (
        "方言域 ASR 能力边界数字：方言用户进链路，ASR 这一环损失多少，"
        "为「预铸在方言场景的可行性」提供依据"
    ),
    "card": "docs/tasks/T15-labs-方言链路实测-WuBench吴语ASR-CER.md",
}


class FatalError(RuntimeError):
    """fail-closed 的硬失败：不产出半个报告。"""


# ---------------------------------------------------------------------------
# 1. sanity 自断言（调产品 API；断言必须能被打破）
# ---------------------------------------------------------------------------
def run_sanity() -> Dict[str, Any]:
    """用已知对照断言 CER 管道真的在工作。

    卡内硬要求 7：cer(normalize_for_cer("你好世界"), normalize_for_cer("你好时界")) == 0.25
    卡内反空转条款：同串对照必须为 0（若把对照换成「你好世界」vs「你好世界」必须为 0，
    证明断言不是恒真的）。两组对照任何一组不符即非零退出。
    """
    from eval.cer import cer, normalize_for_cer

    mismatch = cer(normalize_for_cer("你好世界"), normalize_for_cer("你好时界"))
    identity = cer(normalize_for_cer("你好世界"), normalize_for_cer("你好世界"))

    out = {
        "expected_mismatch": 0.25,
        "actual_mismatch": mismatch,
        "expected_identity": 0.0,
        "actual_identity": identity,
    }
    if abs(mismatch - 0.25) > 1e-12:
        raise FatalError(
            f"sanity 失败：「你好世界」vs「你好时界」CER 应为 0.25，实际 {mismatch!r}"
        )
    if abs(identity - 0.0) > 1e-12:
        raise FatalError(
            f"sanity 失败：同串对照 CER 应为 0.0，实际 {identity!r}——"
            f"说明 CER 管道失效，断言形同虚设"
        )
    return out


# ---------------------------------------------------------------------------
# 2. 音频时长解析（标准库 struct 读 WAV 头；只读元数据，不解析 PCM 内容）
# ---------------------------------------------------------------------------
def wav_duration_seconds(wav: bytes) -> float:
    """从 RIFF/WAVE 头解析时长（秒）。

    解析：RIFF header → 遍历 chunk 找 'fmt ' 与 'data'；
          duration = data_size / (sample_rate * channels * bits_per_sample / 8)。

    异常：非 RIFF / 无 fmt 或 data / 采样率 0 / 帧字节 0 → ValueError（fail-closed：
          时长未知的音频不进抽样分母也不静默按 0 处理）。
    """
    if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError(
            f"不是 RIFF/WAVE 容器（前 12 字节 {wav[:12]!r}），无法解析时长"
        )
    sample_rate = 0
    channels = 0
    bits_per_sample = 0
    data_size = 0

    off = 12
    end = len(wav) - 4  # 容许尾部 4 字节的偏差，按实际长度走
    while off + 8 <= len(wav):
        cid = wav[off : off + 4]
        size = struct.unpack("<I", wav[off + 4 : off + 8])[0]
        body = off + 8
        if cid == b"fmt ":
            if size >= 16:
                channels, _samp, sample_rate, _byte_rate, _blk, bits_per_sample = struct.unpack(
                    "<HHIIHH", wav[body : body + 16]
                )
        elif cid == b"data":
            data_size = min(size, len(wav) - body)
            break
        off = body + size + (size % 2)  # chunk 按偶数字节对齐

    if not sample_rate:
        raise ValueError("WAV 缺 fmt chunk 或采样率为 0，无法解析时长")
    if not channels or not bits_per_sample:
        raise ValueError("WAV fmt chunk 声道数/位深为 0，无法解析时长")
    if data_size == 0:
        raise ValueError("WAV 无 data chunk 或 data_size 为 0，无法解析时长")

    frame_bytes = channels * (bits_per_sample // 8)
    if frame_bytes == 0:
        raise ValueError("WAV 每帧字节数为 0，无法解析时长")
    return data_size / (sample_rate * frame_bytes)


# ---------------------------------------------------------------------------
# 3. 读 parquet + 抽样（顺序可复现）
# ---------------------------------------------------------------------------
def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            blk = fh.read(chunk)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def load_and_sample() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """读 parquet → fail-closed 校验 → 固定 seed 抽 100 行（按抽中顺序）。

    fail-closed：缺任一必需列 / 行数为 0 → FatalError，不产出半个报告。
    抽样：random.Random(20260919).sample(range(n_rows), 100)——
          只抽行索引，不读音频字节，因此 --limit 只影响跑多少条，不影响抽样顺序。
    """
    import pyarrow.parquet as pq  # noqa: PLC0415  — pyarrow 只允许出现在本 labs 目录

    if not PARQUET_PATH.is_file():
        raise FatalError(f"语料文件不存在: {PARQUET_PATH}")

    pf = pq.ParquetFile(PARQUET_PATH)
    schema_names = set(pf.schema_arrow.names)
    missing = [c for c in REQUIRED_COLUMNS if c not in schema_names]
    if missing:
        raise FatalError(
            f"parquet 缺必需列 {missing}（实际列 {sorted(schema_names)}）"
            f"——fail-closed，不产出半个报告"
        )

    n_rows = int(pf.metadata.num_rows)
    if n_rows == 0:
        raise FatalError(f"parquet 行数为 0（{PARQUET_PATH}）——fail-closed，不产出报告")

    cols = ["utt_id", "task", "audio", "label"]
    if "task" not in schema_names:
        cols.remove("task")
    rows = pq.read_table(PARQUET_PATH, columns=cols).to_pylist()

    if len(rows) != n_rows:
        raise FatalError(
            f"读取行数 {len(rows)} 与元数据行数 {n_rows} 不一致——fail-closed"
        )

    rng = random.Random(SAMPLE_SEED)
    picks = rng.sample(range(n_rows), N_SAMPLED)
    sampled: List[Dict[str, Any]] = []
    for idx in picks:
        r = rows[idx]
        sampled.append(
            {
                "row_index": idx,
                "utt_id": r["utt_id"],
                "task": r.get("task"),
                "audio": r["audio"],
                "label": r["label"],
            }
        )

    src = {
        "path": str(PARQUET_PATH),
        "sha256": sha256_file(PARQUET_PATH),
        "n_rows": n_rows,
        "columns": sorted(schema_names),
        "license": "Apache-2.0（公开语料）",
    }
    return sampled, src


# ---------------------------------------------------------------------------
# 4. 逐条跑：跳过 / ASR / CER，全部留痕
# ---------------------------------------------------------------------------
def probe(sampled: List[Dict[str, Any]], limit: Optional[int], asr: Any) -> List[Dict[str, Any]]:
    """顺序执行（禁止并发压测本地服务），每条都落 raw 行且带 status。"""
    from eval.cer import cer, normalize_for_cer

    n_run = len(sampled) if limit is None else min(limit, len(sampled))
    results: List[Dict[str, Any]] = []

    for i in range(n_run):
        s = sampled[i]
        utt_id = s["utt_id"]
        base: Dict[str, Any] = {"utt_id": utt_id, "task": s["task"]}

        # 4a. 时长解析：解析失败也留痕（非 0 字节畸形音频不算 ASR 失败）
        try:
            duration = wav_duration_seconds(s["audio"])
        except (ValueError, struct.error) as exc:
            base.update(
                {
                    "status": "skipped_bad_audio",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "audio_bytes": len(s["audio"]),
                    "duration_seconds": None,
                }
            )
            results.append(base)
            continue

        base["duration_seconds"] = round(duration, 4)

        # 4b. 过长 / 超大跳过（都留痕计数，不进 CER 分母）
        if duration > MAX_DURATION_SECONDS:
            base.update(
                {
                    "status": STATUS_SKIP,
                    "reason": f"音频时长 {duration:.3f}s > {MAX_DURATION_SECONDS:g}s 上限",
                    "audio_bytes": len(s["audio"]),
                }
            )
            results.append(base)
            continue
        if len(s["audio"]) > MAX_AUDIO_BYTES:
            base.update(
                {
                    "status": SKIP_TOO_LARGE,
                    "reason": f"音频 {len(s['audio'])} 字节 > {MAX_AUDIO_BYTES} 上限",
                    "audio_bytes": len(s["audio"]),
                }
            )
            results.append(base)
            continue

        # 4c. 音频写系统临时目录 → 转写 → 立即删除；仓库内绝不落音频字节
        tmp = tempfile.NamedTemporaryFile(
            prefix="wubench-", suffix=".wav", delete=False
        )
        tmp.write(s["audio"])
        tmp.close()
        tmp_path = Path(tmp.name)

        started = time.time()
        try:
            transcript = asr.transcribe(tmp_path)
        except Exception as exc:  # OmlxAsr 契约为 AsrError；其余异常也留痕不外逃
            base.update(
                {
                    "status": STATUS_ASR_ERROR,
                    "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "label": s["label"],
                    "audio_bytes": len(s["audio"]),
                    "asr_seconds": round(time.time() - started, 3),
                }
            )
            results.append(base)
            continue
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass  # 临时文件清理失败不阻塞；已在系统临时目录，不进仓库

        # 4d. CER：一律走 eval/cer.py，不做语义归一
        label = s["label"]
        try:
            cer_value = cer(normalize_for_cer(label), normalize_for_cer(transcript))
        except (ValueError, TypeError) as exc:
            # 归一后 reference 为空（label 纯标点/空白）→ 不是 ASR 失败，留痕跳过
            base.update(
                {
                    "status": "skipped_empty_reference",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "label": label,
                    "transcript": transcript,
                    "audio_bytes": len(s["audio"]),
                    "asr_seconds": round(time.time() - started, 3),
                }
            )
            results.append(base)
            continue

        base.update(
            {
                "status": STATUS_OK,
                "label": label,
                "transcript": transcript,
                "cer": round(cer_value, 6),
                "label_chars": len(normalize_for_cer(label)),
                "audio_bytes": len(s["audio"]),
                "asr_seconds": round(time.time() - started, 3),
            }
        )
        results.append(base)
        print(
            f"[{i + 1}/{n_run}] {utt_id} status={base['status']}"
            + (f" cer={base['cer']}" if "cer" in base else ""),
            flush=True,
        )

    return results


# ---------------------------------------------------------------------------
# 5. 汇总：计数 / CER 分布 / CI / 时长分布 / task 分桶
# ---------------------------------------------------------------------------
def summarize(results: List[Dict[str, Any]], src: Dict[str, Any]) -> Dict[str, Any]:
    from eval.stats import bootstrap_ci, percentile

    by_status: Dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    n_sampled = len(results)
    valid = [r for r in results if r["status"] == STATUS_OK]
    n_valid = len(valid)
    n_skipped = sum(v for k, v in by_status.items() if k.startswith("skipped_"))
    n_asr_fail = by_status.get(STATUS_ASR_ERROR, 0)

    cer_values = [r["cer"] for r in valid]
    durations = [r["duration_seconds"] for r in results if r["duration_seconds"] is not None]

    cer_block: Dict[str, Any]
    if n_valid > 0:
        cer_mean = sum(cer_values) / n_valid
        ci_low, ci_high = bootstrap_ci(
            cer_values,
            BOOTSTRAP_Q,
            seed=BOOTSTRAP_SEED,
            resamples=BOOTSTRAP_RESAMPLES,
            level=BOOTSTRAP_LEVEL,
        )
        cer_block = {
            "n": n_valid,
            "mean": round(cer_mean, 6),
            "P50": round(percentile(cer_values, 0.50), 6),
            "P95": round(percentile(cer_values, 0.95), 6),
            "P99": round(percentile(cer_values, 0.99), 6),
            "ci95_low": round(ci_low, 6),
            "ci95_high": round(ci_high, 6),
            "ci_estimand": "P50",
            "ci_method": "对 P50 的百分位 bootstrap 区间（eval.stats.bootstrap_ci）",
            "ci_bootstrap_seed": BOOTSTRAP_SEED,
            "ci_bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "ci_bootstrap_q": BOOTSTRAP_Q,
            "quantile_method": "linear（eval.stats.percentile）",
        }
    else:
        cer_block = {
            "n": 0,
            "mean": None,
            "P50": None,
            "P95": None,
            "P99": None,
            "ci95_low": None,
            "ci95_high": None,
            "note": "无有效样本，CI 无定义（不返回 0.0 冒充零错误）",
        }

    duration_block: Dict[str, Any]
    if durations:
        duration_block = {
            "n": len(durations),
            "P50": round(percentile(durations, 0.50), 4),
            "P95": round(percentile(durations, 0.95), 4),
            "max": round(max(durations), 4),
            "min": round(min(durations), 4),
            "max_duration_skip_threshold_seconds": MAX_DURATION_SECONDS,
        }
    else:
        duration_block = {"n": 0, "note": "无时长数据"}

    # 按 task 分桶（task 多于 1 类时才有意义；单类也照报，不隐藏口径）
    tasks = sorted({str(r["task"]) for r in results})
    task_buckets: Dict[str, Any] = {}
    for t in tasks:
        rows_t = [r for r in results if str(r["task"]) == t]
        ok_t = [r for r in rows_t if r["status"] == STATUS_OK]
        bucket: Dict[str, Any] = {
            "n": len(rows_t),
            "n_valid": len(ok_t),
            "n_skipped": sum(
                1 for r in rows_t if r["status"].startswith("skipped_")
            ),
            "n_asr_fail": sum(1 for r in rows_t if r["status"] == STATUS_ASR_ERROR),
        }
        if ok_t:
            vals = [r["cer"] for r in ok_t]
            bucket["cer_mean"] = round(sum(vals) / len(vals), 6)
            bucket["cer_P50"] = round(percentile(vals, 0.50), 6)
            bucket["cer_P95"] = round(percentile(vals, 0.95), 6)
        task_buckets[t] = bucket

    asr_seconds = [
        r["asr_seconds"]
        for r in results
        if r["status"] == STATUS_OK and r.get("asr_seconds") is not None
    ]

    return {
        "n_sampled": n_sampled,
        "n_valid": n_valid,
        "n_skipped": n_skipped,
        "n_asr_fail": n_asr_fail,
        "n_status_total": n_valid + n_skipped + n_asr_fail,
        "status_buckets": dict(sorted(by_status.items())),
        "cer": cer_block,
        "duration_seconds": duration_block,
        "asr_latency_seconds": {
            "n": len(asr_seconds),
            "P50": round(percentile(asr_seconds, 0.50), 3) if asr_seconds else None,
            "P95": round(percentile(asr_seconds, 0.95), 3) if asr_seconds else None,
            "max": round(max(asr_seconds), 3) if asr_seconds else None,
            "note": "仅统计 status==ok 的请求",
        },
        "task_buckets": task_buckets,
        "n_task_classes": len(tasks),
        "utt_ids": [r["utt_id"] for r in results],
        "source": src,
        "caliber": {
            "cer": "eval.cer.cer(normalize_for_cer(label), normalize_for_cer(transcript))"
            "——去标点与空白后逐字编辑距离 / 归一后 reference 字符数；不夹取上界；不做语义归一",
            "denominator": "仅 status==ok 的条数；跳过与失败不进 CER 分母",
            "sample": f"random.Random({SAMPLE_SEED}).sample(range({src['n_rows']}), {N_SAMPLED})",
            "duration": "标准库 struct 解析 RIFF/WAVE 头（只读元数据）",
            "concurrency": "顺序执行，不并发（本地 oMLX 服务，禁止压测）",
        },
    }


# ---------------------------------------------------------------------------
# 6. 落盘（limit 跑与正式跑分流，防止部分跑破坏性覆盖正式产物）
# ---------------------------------------------------------------------------
def output_paths(limit: Optional[int]) -> Tuple[Path, Path]:
    """返回 (raw 路径, report 路径)。

    正式跑（limit is None）写 raw/samples.jsonl 与 report.json；
    带 --limit 的部分跑一律写 raw/samples.limit{N}.jsonl 与 report.limit{N}.json——
    部分跑 rc 0 不代表"完整结果"，不得覆盖正式产物（T15 第二轮退回 P1）。
    """
    if limit is None:
        return RAW_SAMPLES, REPORT
    return RAW_DIR / f"samples.limit{limit}.jsonl", HERE / f"report.limit{limit}.json"


def write_raw(results: List[Dict[str, Any]], raw_path: Path) -> None:
    """逐条明细（含 label / 转写逐字原文）。

    按 docs/11 §11.7 属 labs/**/raw/**，含第三方语料逐字原文，不进公开分发。
    """
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_report(report: Dict[str, Any], report_path: Path) -> None:
    """汇总，不含任何语料逐字原文（可进公开分发）。"""
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 7. 自断言（防静默降级；失败即非零退出）
# ---------------------------------------------------------------------------
def self_assert(report: Dict[str, Any]) -> None:
    if report["n_valid"] + report["n_skipped"] + report["n_asr_fail"] != report["n_sampled"]:
        raise FatalError(
            f"计数自断言失败：n_valid({report['n_valid']}) + n_skipped({report['n_skipped']})"
            f" + n_asr_fail({report['n_asr_fail']}) != n_sampled({report['n_sampled']})"
        )
    if report["n_status_total"] != report["n_sampled"]:
        raise FatalError(
            f"status 桶之和 {report['n_status_total']} != n_sampled {report['n_sampled']}——"
            f"存在未分类行，留痕不完整"
        )
    n_valid = report["n_valid"]
    cer_n = report["cer"]["n"]
    if cer_n != n_valid:
        raise FatalError(f"CER 分母 {cer_n} != n_valid {n_valid}——分母口径漂移")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Wu-Bench 吴语方言域 ASR → CER 实测（T15；止于 CER，不做命中实验）"
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只跑前 N 条（不影响抽样顺序与 seed；用于快验）",
    )
    p.add_argument(
        "--base-url",
        default="http://127.0.0.1:10099",
        help="oMLX 端点（默认本机常驻服务）",
    )
    p.add_argument(
        "--model",
        default="Qwen3-ASR-0.6B-8bit",
        help="ASR 模型名（请求体 model 字段）",
    )
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    # sys.path 兜底（对齐 labs/ticket-source/run_e2e.py 先例）：
    # 验收命令是字面裸跑（无 PYTHONPATH），脚本必须自带上仓库根，不得依赖调用方环境。
    # 放在 parse_args 之后、任何产品模块 import（eval.cer / adapters.asr_omlx）之前——
    # 本脚本全部产品 import 都是函数内延迟导入，此位置即最早可达点。
    sys.path.insert(0, str(REPO_ROOT))

    args = parse_args(argv)

    # 解释器守卫：pyarrow 只有 ~/miniforge3/bin/python（3.12）有
    if sys.version_info[:2] != (3, 12):
        print(
            f"[FATAL] 解释器版本 {sys.version.split()[0]}：本脚本必须用 ~/miniforge3/bin/python"
            f"（3.12 + pyarrow；主解释器 3.14 无 pyarrow wheel）",
            file=sys.stderr,
        )
        return 2
    if args.limit is not None and (args.limit < 1 or not isinstance(args.limit, int)):
        print(f"[FATAL] --limit 必须 >= 1 的整数，实际 {args.limit!r}", file=sys.stderr)
        return 2

    # 退出码契约（本脚本 docstring 承诺的 {0,2,3,4}）：
    # sanity 与抽样之后的各阶段，非 FatalError 的异常（bootstrap_ci 的 ValueError
    # ——n_valid<MIN_REPEATS(20) 时；OmlxAsr 构造的 ValueError；tempfile 写盘 OSError；
    # 落盘 IOError…）统一收口为 rc 3 + [FATAL] + traceback，绝不以裸 traceback 逃出（rc 1）。
    try:
        # 1) sanity：先证明 CER 管道在工作，再花分钟级时间跑 ASR
        try:
            sanity = run_sanity()
        except FatalError as exc:
            print(f"[FATAL] {exc}", file=sys.stderr)
            return 4
        print(
            f"[sanity] 你好世界 vs 你好时界 → CER={sanity['actual_mismatch']}"
            f"（期望 {sanity['expected_mismatch']}）；同串对照 → CER={sanity['actual_identity']}"
            f"（期望 {sanity['expected_identity']}）",
            flush=True,
        )

        # 2) 读 + 抽样（fail-closed）
        try:
            sampled, src = load_and_sample()
        except FatalError as exc:
            print(f"[FATAL] {exc}", file=sys.stderr)
            return 3
        print(
            f"[src] {src['path']} sha256={src['sha256']} rows={src['n_rows']}"
            f" → 抽样 {len(sampled)} 条（seed={SAMPLE_SEED}，limit={args.limit}）",
            flush=True,
        )

        # 3) ASR 探针（顺序执行；构造期校验失败也在收口内）
        from adapters.asr_omlx.adapter import OmlxAsr

        asr = OmlxAsr(base_url=args.base_url, model=args.model)
        print(f"[asr] {asr.endpoint()} model={asr.model}", flush=True)

        started = time.time()
        results = probe(sampled, args.limit, asr)
        elapsed = time.time() - started

        # 4) 汇总 + 自断言 + 落盘（断言失败不写半个报告）
        try:
            report_body = summarize(results, src)
            self_assert(report_body)
        except FatalError as exc:
            print(f"[FATAL] {exc}", file=sys.stderr)
            return 4

        report = {
            "probe": "wubench-dialect-asr-cer",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scope": SCOPE,
            "env": {
                "python_version": sys.version.split()[0],
                "python_executable": sys.executable,
                "platform": platform.platform(),
                "sample_seed": SAMPLE_SEED,
                "asr_model": args.model,
                "asr_endpoint": asr.endpoint(),
                "asr_adapter": "adapters.asr_omlx.adapter.OmlxAsr",
                "cer_module": "eval.cer（normalize_for_cer + cer）",
                "stats_module": "eval.stats（percentile + bootstrap_ci）",
                "pyarrow": "仅本 labs 目录内使用；产品层零 pyarrow 依赖",
                "concurrency": "sequential",
                "elapsed_seconds": round(elapsed, 1),
            },
            "sanity": sanity,
            **report_body,
        }

        # 5) 落盘分流：正式跑写 report.json / raw/samples.jsonl；
        #    部分跑（--limit）写 report.limit{N}.json / raw/samples.limit{N}.jsonl，
        #    不覆盖正式产物（T15 第二轮退回 P1）。
        raw_path, report_path = output_paths(args.limit)
        if args.limit is not None:
            report["env"]["limit"] = args.limit
            report["env"]["output_is_partial"] = True
        else:
            report["env"]["limit"] = None

        write_raw(results, raw_path)
        write_report(report, report_path)

        print(
            f"[done] n_sampled={report['n_sampled']} n_valid={report['n_valid']}"
            f" n_skipped={report['n_skipped']} n_asr_fail={report['n_asr_fail']}"
            f" status_buckets={report['status_buckets']}",
            flush=True,
        )
        c = report["cer"]
        if c["mean"] is not None:
            print(
                f"[cer] mean={c['mean']} P50={c['P50']} P95={c['P95']} "
                f"CI95=[{c['ci95_low']}, {c['ci95_high']}]（n={c['n']}）",
                flush=True,
            )
        print(f"[raw] {raw_path}（{len(results)} 行）", flush=True)
        print(f"[report] {report_path}", flush=True)
        if args.limit is not None:
            print(
                f"[partial] limit={args.limit} 的部分跑产物，未触碰正式 "
                f"{REPORT.name} / {RAW_SAMPLES.name}",
                flush=True,
            )
        return 0
    except FatalError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"[FATAL] 运行期失败（{type(exc).__name__}）: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
