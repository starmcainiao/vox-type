"""
cli.commands.bench — `vox bench <pack_dir> --corpus <corpus.json> --out <dir>
                       [--adapter …] [--repeats N] [--seed N]
                       [--required-hit-rate F] [--warmup N] [--json]`

职责：把「资产包 + 固定语料」送进 eval 的离线对拍 harness（docs/08 §8.6），
     只做「解析参数 → 调用 → 打印 → 定退出码」。
不负责：不实现任何对拍或统计口径。两臂构造、采样、分位数、bootstrap 置信区间、
     命中率与预铸时长占比、incomplete 判定、报告 schema 一律在 eval/ 里——
     CLI 一行统计代码都不写。

调用链与退出码（docs/08 §8.2 / §8.6）：
  1. assets.load_pack(<pack_dir>)            → AssetPackError → 2
  2. eval.bench.load_corpus(--corpus)        → BenchReportError → 2
  3. --out 越界（落在 --pack 内）             → UsageError   → 2
  4. eval.bench.run_bench(BenchConfig)       → BenchReportError → 2
     （repeats < MIN_REPEATS / corpus 为空 / out_dir 落进包目录都在这里被拦）
  5. eval.report.write_report                → 写 report.json（含 incomplete 也写）
  6. incomplete is True  → 4（「数据不完整」是结论不是崩溃，报告照样写出）
     incomplete is False → 继续看第 7 步
  7. reference_gate.passed is False → 4（T21 装牙齿：「命中率未达参考门槛」同为结论，
     报告与数字照旧如实写出，只是返回值如实反映它）
     passed is True → 0
  8. 其余执行期异常 → 3（stderr 由 cli.main 带上异常类型名）

WHY `--adapter` 缺省用离线替身而不是真机引擎（eval/AGENTS.md ③.4「离线可跑」）：
  对拍要跑 repeats × 2 臂 × 语料单位数 次执行，真机 `say` 在 CI / 无麦无网环境里
  既慢又不可用；替身适配器产出的时序数字由 eval 自己标记为
  timing_metrics_meaningful=False 并在摘要里打警告，不会被报告背书为真机数据。
WHY 不回落：显式给的 --adapter 解析失败一律 → 2，绝不换成默认引擎——
  「本该用 A 引擎却换了 B」在语音链路里表现为「突然换了个声音」，事后无法定位。
"""

import argparse
import shlex
import sys
from pathlib import Path
from typing import Any

from assets import AssetPackError, load_pack
# load_corpus 走 eval 的公开导出（eval.bench.load_corpus）；本模块的 re-export 由
# cli/__init__.py 与 cli/commands/__init__.py 的 __all__ 承担——公开面变更留痕。
from eval.bench import BenchConfig, BenchReportError, load_corpus, run_bench
from eval.offline_tts import OfflineTts
from eval.report import write_report
from eval.stats import DEFAULT_REPEATS, DEFAULT_SEED

from cli import commands
from cli.errors import EXIT_OK, EXIT_QUALITY, UsageError


# 缺省对拍适配器：离线替身（无参构造需要包的引擎元数据，故不经 importlib 解析）
DEFAULT_BENCH_ADAPTER_SPEC: str = "eval.offline_tts:OfflineTts"


# ---------------------------------------------------------------------------
# 参数（字段名与 docs/08 §8.6 一字不差）
# ---------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pack_dir", help="已预铸资产包目录（含 manifest.json）")
    parser.add_argument("--corpus", required=True, help="固定语料 JSON 文件")
    parser.add_argument("--out", required=True, help="输出目录（report.json + audio/ + raw/）")
    parser.add_argument(
        "--adapter",
        default=None,
        help=(
            f"适配器 <模块>:<类名>（须能零参实例化，如 {commands.DEFAULT_ADAPTER_SPEC}）；"
            f"缺省 {DEFAULT_BENCH_ADAPTER_SPEC}（离线替身，时序数字不被报告背书）"
        ),
    )
    # 缺省值取 eval 的公开常量（不在此处硬编码——口径的唯一来源是 eval/stats.py）
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                        help=f"每臂采样次数（缺省 {DEFAULT_REPEATS}；eval 层有下限拦截）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"bootstrap 种子（缺省 {DEFAULT_SEED}；固定 → 区间可复现）")
    parser.add_argument(
        "--required-hit-rate",
        dest="required_hit_rate",
        type=float,
        default=0.987,
        help="命中率参考门槛（写入报告 reference_gate；未达 → 退出码 4）",
    )
    parser.add_argument("--warmup", type=int, default=1, help="每臂预热次数（不计入样本）")
    parser.add_argument("--plan-id", dest="plan_id", default="vox-bench", help="播报计划 ID 前缀")
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 只输出纯 JSON（人读摘要走 stderr）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="出错时打印完整 traceback（docs/08 §8.2）",
    )


# ---------------------------------------------------------------------------
# 复现命令重建（报告 command 字段要能原样重跑）
# ---------------------------------------------------------------------------
def _command_string(args: Any) -> str:
    """重建可原样复现的完整命令行（所有参数显式写出，不省默认值）。"""
    parts: list = ["vox", "bench", str(args.pack_dir), "--corpus", str(args.corpus),
                   "--out", str(args.out), "--repeats", str(args.repeats),
                   "--seed", str(args.seed), "--required-hit-rate", str(args.required_hit_rate),
                   "--warmup", str(args.warmup), "--plan-id", str(args.plan_id)]
    if args.adapter:
        parts += ["--adapter", args.adapter]
    return " ".join(shlex.quote(part) for part in parts)


# ---------------------------------------------------------------------------
# 命令主体
# ---------------------------------------------------------------------------
def run(args: Any) -> int:
    """执行 `vox bench`，返回冻结退出码。"""
    pack_dir = commands.assert_dir(args.pack_dir, label="--pack 资产包目录")

    # --out 越界拦截（docs/08 §8.6 / §8.7）：产物不许写进资产包目录（只读契约）。
    # eval.run_bench 里也有一道同名的检查，这里是 CLI 层的第一道——两者都必须报 2。
    commands.assert_not_within(
        args.out,
        pack_dir,
        what="--out 输出目录",
        parent_label="--pack 资产包目录",
    )
    out_dir = Path(args.out)
    if out_dir.is_file():
        raise UsageError(f"--out 输出目录不能是已存在的文件: {out_dir}")

    # 1. 装载资产包（只读契约）
    try:
        pack = load_pack(pack_dir)
    except AssetPackError as exc:
        raise UsageError(f"资产包装载失败: {exc}") from exc

    # 2. 语料装载与校验（判定全在 eval.load_corpus，CLI 不复制它的规矩）
    try:
        corpus, meta = load_corpus(args.corpus)
    except BenchReportError as exc:
        raise UsageError(f"语料校验失败: {exc}") from exc

    # 3. 适配器：缺省离线替身；显式给出则动态解析（失败 → 2，不回落）
    if args.adapter:
        adapter = commands.resolve_adapter(args.adapter)
    else:
        # 替身构造需要与包一致的引擎元数据，否则运行时会按 engine_mismatch 降级
        adapter = OfflineTts(voice=pack.voice, model_version=pack.model_version)

    # 4. 对拍：采样、聚合、incomplete 判定全在 eval.run_bench
    config = BenchConfig(
        pack=pack,
        adapter=adapter,
        corpus=corpus,
        plan_id=args.plan_id,
        repeats=args.repeats,
        warmup_runs=args.warmup,
        seed=args.seed,
        required_hit_rate=args.required_hit_rate,
        out_dir=out_dir,
        corpus_path=Path(args.corpus),
        corpus_meta=meta,
        command=_command_string(args),
    )
    try:
        report = run_bench(config)
    except BenchReportError as exc:
        # 参数/配置类失败（含 repeats 低于下限、out_dir 落进包目录）→ 2，且不产出任何文件
        raise UsageError(f"对拍配置错误: {exc}") from exc

    # 5. 落盘：含 incomplete 的报告照样写出（不得用「数据不够」掩盖失败，也不得丢弃原始数据）
    report_path = write_report(report, out_dir)

    # 6. 打印：`arms` 直接透传报告里的同名块（**不得改名、不得重算**）——
    #    统计口径是 eval 的契约，CLI 重算一次就等于第二份口径，两边必然漂移。
    gate = report.get("reference_gate") or {}
    payload: dict = {
        "command": "bench",
        "report_path": str(report_path),
        "incomplete": report["incomplete"],
        "repeats": report["repeats"],
        "seed": report["seed"],
        "arms": report["arms"],
        "timing_metrics_meaningful": report["timing_metrics_meaningful"],
        # gate 结论直接透传报告里的 reference_gate（不得改名、不得重算）——
        # 「数字如实」与「退出码反映结论」是两件事，两者都要有。
        "reference_gate": {
            "required_hit_rate": gate.get("required_hit_rate"),
            "observed_hit_rate": gate.get("observed_hit_rate"),
            "passed": bool(gate.get("passed")),
        },
    }

    if report["incomplete"]:
        reasons = report["incomplete_reasons"]
        summary = (
            f"bench: 不通过（数据不完整 {len(reasons)} 项，报告已写出: {report_path}）"
        )
        commands.emit_result(payload, as_json=args.as_json, summary=summary)
        # 逐条原因走 stderr：「不静默」红线（docs/08 §8.2），且 --json 时 stdout 必须留给纯 JSON
        for reason in reasons:
            sys.stderr.write(f"数据不完整: {reason}\n")
        sys.stderr.flush()
        return EXIT_QUALITY

    summary = (
        f"bench: 通过（repeats={report['repeats']} seed={report['seed']} "
        f"arms={','.join(sorted(report['arms']))} 报告已写入: {report_path}）"
    )
    if not report["timing_metrics_meaningful"]:
        summary += "（替身适配器：时序数字不得对外引用）"
    commands.emit_result(payload, as_json=args.as_json, summary=summary)

    # 7. reference_gate 结论 → 退出码（T21 装牙齿）
    # gate 是「命中率是否达到参考门槛」的业务结论，与 incomplete 同一性质：
    # 结论如实写在报告里，退出码也如实反映它——不得「算出不过却返回 0」。
    # 数字不变、报告不变，变的只是返回值（docs/08 §8.2 的 EXIT_QUALITY=4）。
    if not payload["reference_gate"]["passed"]:
        g = payload["reference_gate"]
        sys.stderr.write(
            f"reference_gate 不通过: observed_hit_rate={g['observed_hit_rate']} "
            f"< required_hit_rate={g['required_hit_rate']}（报告已写出: {report_path}）\n"
        )
        sys.stderr.flush()
        return EXIT_QUALITY
    return EXIT_OK
