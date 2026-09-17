"""
cli.commands.run — `vox run <plan.json> --pack <built_pack_dir> [--adapter …] [--out <wav>] [--allow-fallback] [--duplex <json>] [--json]`

职责：执行一条播报计划（docs/08 §8.3 第 3 条），只做「解析 → 调用 → 打印 → 定退出码」。
不负责：不做命中判定（runtime.Executor）、不做降级判定、不算指标——
     事件字段名照 core.metrics_spec 常量，本文件不改名、不加工。

调用链与退出码：
  1. assets.load_pack(<pack>)            → AssetPackError → 2
  2. 读 plan JSON + core.parse_plan      → JSON/ProtocolError → 2
  3. runtime.DuplexParams(**--duplex)    → DuplexError   → 2
  4. runtime.Executor + execute          → RuntimeMissError → 5（fail-closed）
  5. 成功                                →                → 0

WHY `--allow-fallback` 缺省关闭：未命中且未显式开降级必须中止（docs/08 §8.2 的 5），
    因为「本该命中却换了路径」的静默降级在语音链路里表现为「突然换了个声音」，事后无法定位。
WHY `plan_id` / `turn_id` 由 CLI 缺省：plan 文件本身不带这两个 ID（协议层只定义单元），
    CLI 给稳定缺省值，保证同一 plan 两次执行的事件可对齐（variant='auto' 按
    (turn_id, part) 稳定散列，turn_id 变了会选到不同变体）。
"""

import argparse
import tempfile
from pathlib import Path
from typing import Any

from assets import AssetPackError, load_pack
from core import ProtocolError, parse_plan
from runtime import DuplexError, DuplexParams, Executor, RuntimeMissError

from cli import commands
from cli.errors import EXIT_FAIL_CLOSED, EXIT_OK, UsageError


# CLI 缺省的 plan / turn 标识（docs/08 §8.3 未定义命令行参数，故在此固定）
DEFAULT_PLAN_ID: str = "vox-cli"
DEFAULT_TURN_ID: int = 1


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plan", help="播报计划 JSON 文件路径（list[单元]）")
    parser.add_argument("--pack", required=True, help="已预铸资产包目录（含 manifest.json）")
    parser.add_argument(
        "--adapter",
        default=None,
        help=f"适配器 <模块>:<类名>，缺省 {commands.DEFAULT_ADAPTER_SPEC}",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="输出 WAV 路径（不得落在 --pack 包目录内）；缺省写到临时目录",
    )
    parser.add_argument(
        "--allow-fallback",
        dest="allow_fallback",
        action="store_true",
        help="允许未命中/降级走慢路并留痕（缺省关闭 = fail-closed）",
    )
    parser.add_argument(
        "--duplex",
        default=None,
        help='双工参数 JSON 对象，如 \'{"patience_ms": 1800}\'',
    )
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
# 命令主体
# ---------------------------------------------------------------------------
def run(args: Any) -> int:
    """执行 `vox run`，返回冻结退出码。"""
    pack_dir = commands.assert_dir(args.pack, label="--pack 包目录")

    # --out 越界拦截（docs/08 §8.5 欠账 2）：产物不许写进包目录（冻结区）。
    if args.out:
        commands.assert_not_within(
            args.out,
            pack_dir,
            what="--out 输出文件",
            parent_label="--pack 包目录",
        )

    # 1. 装载资产包（只读契约）
    try:
        pack = load_pack(pack_dir)
    except AssetPackError as exc:
        raise UsageError(f"资产包装载失败: {exc}") from exc

    # 2. 读 plan JSON + 协议校验（判定全在 core，CLI 不自己解析单元）
    raw_plan = commands.read_json_file(args.plan, label="plan 文件")
    try:
        plan = parse_plan(raw_plan)
    except ProtocolError as exc:
        raise UsageError(f"plan 协议校验失败: {exc}") from exc

    # 3. 双工参数（取值范围校验在 runtime.DuplexParams，CLI 不复制白名单）
    duplex = None
    if args.duplex:
        duplex_obj = commands.parse_json_arg(args.duplex, label="--duplex")
        if not isinstance(duplex_obj, dict):
            raise UsageError(
                f"--duplex 必须是 JSON 对象，实际类型为 {type(duplex_obj).__name__}"
            )
        try:
            duplex = DuplexParams(**duplex_obj)
        except DuplexError as exc:
            raise UsageError(f"--duplex 参数非法: {exc}") from exc

    # 4. 适配器动态解析（失败 → 2，不回落）
    adapter = commands.resolve_adapter(args.adapter)

    # 5. 输出路径：未给 --out 时落到临时目录（产物是文件，不写进仓库）
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = Path(tempfile.mkdtemp(prefix="vox-run-")) / f"{DEFAULT_PLAN_ID}.wav"

    # 6. 执行：命中判定 / 降级判定 / 拼接 / 事件流全在 runtime.Executor
    executor = Executor(
        pack,
        adapter,
        duplex=duplex,
        allow_fallback=args.allow_fallback,
    )

    try:
        result = executor.execute(
            plan,
            plan_id=DEFAULT_PLAN_ID,
            turn_id=DEFAULT_TURN_ID,
            out_path=out_path,
        )
    except RuntimeMissError as exc:
        # 业务语义中止（退出码 5），不是程序崩溃——措辞中性。
        # stderr 必带 key 与原因（exc 的原始消息里就有），便于定位是哪条话术拦下的。
        payload = {
            "command": "run",
            "pack_dir": str(pack_dir),
            "plan_id": DEFAULT_PLAN_ID,
            "turn_id": DEFAULT_TURN_ID,
            "hit_count": 0,
            "miss_count": 0,
            "fallback_count": 0,
            "tts_calls": 0,
            "first_audio_ms": 0,
            "total_duration_ms": 0,
            "out_path": None,
            "events": [],
            "aborted": str(exc),
        }
        summary = f"run: 已按 fail-closed 中止（{exc}）"
        commands.emit_result(payload, as_json=args.as_json, summary=summary)
        return EXIT_FAIL_CLOSED

    # 7. 成功：事件字段名照 core.metrics_spec，原样透出（不改名、不聚合）
    payload = {
        "command": "run",
        "pack_dir": str(pack_dir),
        "plan_id": DEFAULT_PLAN_ID,
        "turn_id": DEFAULT_TURN_ID,
        "hit_count": result.hit_count,
        "miss_count": result.miss_count,
        "fallback_count": result.fallback_count,
        "tts_calls": result.tts_calls,
        "first_audio_ms": result.first_audio_ms,
        "total_duration_ms": result.total_duration_ms,
        "out_path": str(result.output_path),
        "events": result.events,
    }

    out_abs = Path(result.output_path).resolve()
    summary = (
        f"run: 通过（hit={result.hit_count} miss={result.miss_count} "
        f"fallback={result.fallback_count} tts_calls={result.tts_calls} "
        f"first_audio_ms={result.first_audio_ms:.1f} out={out_abs}）"
    )
    commands.emit_result(payload, as_json=args.as_json, summary=summary)
    return EXIT_OK
