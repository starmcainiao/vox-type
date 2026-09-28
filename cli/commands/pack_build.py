"""
cli.commands.pack_build — `vox pack build <pack_dir> --out <dir> [--adapter …] [--allow-partial] [--json]`

职责：把「话术 + 剧本」预铸成资产包（docs/08 §8.3 第 2 条），只做
     「解析参数 → 调各层公开 API → 打印 → 定退出码」。
不负责：不实现判定。四属性判据在 compiler.checks、合成与质检在 compiler.prebake、
     资产格式校验在 assets——CLI 一行都不重写。

调用链与退出码（docs/08 §8.2 / §8.3）：
  1. compiler.load_source                  → SourceError   → 2
  2. compiler.load_script + check_properties → 有违规      → 4（**先检后铸**，不得铸出一半）
  3. 适配器动态解析（importlib）             → 失败          → 2（**不回落**）
  4. compiler.prebake                      → PrebakeError  → 4（clean=False）
  5. clean=True                             →               → 0

WHY「先检后铸」：源不合规时预铸只会白烧 TTS 成本，还会产出一个注定被质检拒绝的半成品；
    正确行为是在烧任何一次合成之前就把结论给出来。
WHY「--out 不许落在包目录内」：产物写进业务源目录会污染冻结区与业务源，
    事后 diff 里分不清是源还是产物（docs/08 §8.5 欠账 2）。
"""

import argparse
from pathlib import Path
from typing import Any

from assets import validate_pack
from compiler import (
    PrebakeError,
    ScriptError,
    SourceError,
    check_properties,
    load_script,
    load_source,
    prebake,
)

from cli import commands
from cli.errors import EXIT_OK, EXIT_QUALITY, UsageError


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pack_dir", help="业务包源目录（packs/<业务>/）")
    parser.add_argument("--out", required=True, help="资产包输出目录（不得落在 pack_dir 内）")
    parser.add_argument(
        "--adapter",
        default=None,
        help=f"适配器 <模块>:<类名>，缺省 {commands.DEFAULT_ADAPTER_SPEC}",
    )
    parser.add_argument(
        "--allow-partial",
        dest="allow_partial",
        action="store_true",
        help="允许产出「部分包」（有失败/质检不过也写 manifest）",
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
    """执行 `vox pack build`，返回冻结退出码。"""
    pack_dir = commands.assert_dir(args.pack_dir, label="包源目录")

    # --out 越界拦截（docs/08 §8.5 欠账 2）：产物不许写进源目录。
    commands.assert_not_within(
        args.out,
        pack_dir,
        what="--out 输出目录",
        parent_label="包源目录",
    )
    out_path = Path(args.out)
    if out_path.is_file():
        raise UsageError(f"--out 输出目录不能是已存在的文件: {out_path}")

    # 统一的结果骨架：失败时缺的字段显式给 None / 空列表，
    # 避免「没跑到的字段悄悄消失」——JSON 契约保持稳定才可脚本化。
    payload: dict = {
        "command": "pack.build",
        "source_dir": str(pack_dir),
        "pack_dir": str(out_path),
        "total": None,
        "synthesized": None,
        "reused": None,
        "tts_calls": None,
        "failed": [],
        "quality_issues": [],
        "violations": [],
        "skipped": [],
        "validate_pack": [],
        "clean": False,
        "passed": False,
    }

    # 1. 源格式装载
    try:
        source = load_source(pack_dir)
    except SourceError as exc:
        raise UsageError(f"源格式校验失败: {exc}") from exc

    # 2. 先检后铸：剧本四属性不合规 → 直接 4，不得铸出一半
    try:
        script = load_script(pack_dir)
    except ScriptError as exc:
        raise UsageError(f"剧本源格式校验失败: {exc}") from exc

    result = check_properties(script, source, pack=None)
    payload["violations"] = commands.violations_to_dicts(result.violations)
    payload["skipped"] = list(result.skipped)

    if result.violations:
        summary = (
            f"pack build: 不通过（剧本四属性 {len(result.violations)} 条违规，"
            f"已在预铸前中止，未产出任何资产）"
        )
        commands.emit_result(payload, as_json=args.as_json, summary=summary)
        return EXIT_QUALITY

    # 3. 适配器动态解析：失败 → 2，绝不回落到默认引擎
    adapter = commands.resolve_adapter(args.adapter)

    # 4. 预铸（合成 / 质检 / 差量复用 / 写包全在 compiler.prebake 里）
    out_path.mkdir(parents=True, exist_ok=True)
    try:
        report = prebake(source, adapter, out_path, allow_partial=args.allow_partial)
    except PrebakeError as exc:
        # prebake fail-closed 时不写 manifest 但抛出 report；
        # 失败明细必须可取（e.report），否则「失败不静默」无从谈起。
        report = exc.report

    payload["total"] = report.total
    payload["synthesized"] = report.synthesized
    payload["reused"] = report.reused
    payload["tts_calls"] = report.tts_calls
    payload["failed"] = list(report.failed)
    payload["quality_issues"] = list(report.quality_issues)
    payload["clean"] = bool(report.clean)
    payload["passed"] = bool(report.clean)

    # 产物必须能过资产层校验；只在真的写出了 manifest 时才跑，
    # 否则 validate_pack 返回的「缺少 manifest.json」会被误读成产物不合规。
    if (out_path / "manifest.json").is_file():
        payload["validate_pack"] = list(validate_pack(out_path))

    if report.clean:
        summary = (
            f"pack build: 通过（total={report.total} synthesized={report.synthesized} "
            f"reused={report.reused} clean=True out={out_path}）"
        )
        commands.emit_result(payload, as_json=args.as_json, summary=summary)
        return EXIT_OK

    summary = (
        f"pack build: 不通过（clean=False：合成失败 {len(report.failed)} 条，"
        f"质检不过 {len(report.quality_issues)} 条；失败明细见 failed / quality_issues）"
    )
    commands.emit_result(payload, as_json=args.as_json, summary=summary)
    return EXIT_QUALITY
