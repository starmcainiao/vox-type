"""
cli.commands.pack_check — `vox pack check <pack_dir> [--json]`

职责：只校验不预铸（docs/08 §8.3 第 1 条）——逐层往下调各层公开 API，
     把「四属性判据结果 + 资产包校验结果」原样汇报成退出码与 JSON。
不负责：不实现任何判据。命中判定、四属性、质检阈值、统计口径一律在对应层。

调用链（每步的异常都映射到 docs/08 §8.2 的退出码）：
  1. compiler.load_source  → SourceError   → 2
  2. compiler.load_script  → ScriptError   → 2
  3. compiler.check_properties(script, source, pack=None) → 四属性（判定全在 compiler）
  4. 若 <pack_dir> 有 manifest.json（指向已预铸包）→ assets.load_pack + assets.validate_pack

WHY `skipped` 必须如实透出：`pack=None` 时 C3c（已预铸）没有判定输入，
    既不能判过也不能判不过，只能记进 skipped。把它藏起来 = 「没检查」被当成「检查过了」，
    这正是仓库红线禁止的静默降级。
"""

import argparse
from pathlib import Path
from typing import Any, Optional

from assets import AssetPackError, load_pack, validate_pack
from compiler import (
    ScriptError,
    SourceError,
    check_properties,
    load_script,
    load_source,
)

from cli import commands
from cli.errors import EXIT_OK, EXIT_QUALITY, UsageError


# ---------------------------------------------------------------------------
# 参数（字段名与 docs/08 §8.3 一字不差）
# ---------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pack_dir", help="业务包目录（源目录，或含 manifest.json 的已预铸包目录）")
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
    """执行 `vox pack check`，返回冻结退出码。"""
    pack_dir = commands.assert_dir(args.pack_dir, label="包目录")

    has_manifest = (pack_dir / "manifest.json").is_file()

    # 已预铸包（有 manifest.json 但没有 pack.json）不带源文件：
    # 源格式与四属性判据**没有输入**，此时只做资产层校验，并把「跳过了哪一段」
    # 如实写进 skipped——不得当成「全部通过」（docs/08 §8.3 第 3 条红线）。
    built_only = has_manifest and not (pack_dir / "pack.json").is_file()

    violations = []
    skipped = []
    phrases_count: Optional[int] = 0
    units_count: Optional[int] = 0

    if built_only:
        skipped.append("source_and_script_unavailable")
        phrases_count = None
        units_count = None
    else:
        try:
            source = load_source(pack_dir)
        except SourceError as exc:
            raise UsageError(f"源格式校验失败: {exc}") from exc

        try:
            script = load_script(pack_dir)
        except ScriptError as exc:
            raise UsageError(f"剧本源格式校验失败: {exc}") from exc

        pack = None
        if has_manifest:
            try:
                pack = load_pack(pack_dir)
            except AssetPackError as exc:
                raise UsageError(f"资产包装载失败: {exc}") from exc

        # 四属性判定全部在 compiler.checks；CLI 只转发结果，一行判据都不写。
        result = check_properties(script, source, pack=pack)
        violations = commands.violations_to_dicts(result.violations)
        skipped = list(result.skipped)
        phrases_count = len(source.phrases)
        units_count = len(script.units)

    # 资产层校验只在「真的是包」时跑：源目录没有 manifest.json，
    # validate_pack 会返回「缺少 manifest.json」——那不是违规，不能计成不通过。
    pack_validate = list(validate_pack(pack_dir)) if has_manifest else []

    passed = not violations and not pack_validate
    payload = {
        "command": "pack.check",
        "pack_dir": str(pack_dir),
        "phrases": phrases_count,
        "units": units_count,
        "violations": violations,
        "skipped": skipped,
        "pack_validate": pack_validate,
        "passed": passed,
    }

    if passed:
        summary = (
            f"pack check: 通过（pack_dir={pack_dir} phrases={phrases_count} "
            f"units={units_count} pack_validate={len(pack_validate)} 条）"
            + (f" skipped={skipped}" if skipped else "")
        )
        commands.emit_result(payload, as_json=args.as_json, summary=summary)
        return EXIT_OK

    summary = (
        f"pack check: 不通过（violations={len(violations)} 条，"
        f"pack_validate={len(pack_validate)} 条）——pack_dir={pack_dir}"
    )
    commands.emit_result(payload, as_json=args.as_json, summary=summary)
    return EXIT_QUALITY
