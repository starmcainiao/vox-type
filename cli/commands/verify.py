"""
cli.commands.verify — `vox verify <artifact> [--json]`

职责：对**一份产物**做只读质检（docs/08 §8.6 的「按产物类型分派」表），
     只做「识别形态 → 调对应层公开 API → 打印 → 定退出码」。
不负责：不实现任何判据，也不修复、不写盘。

分派顺序（逐条照 §8.6 的表，**不猜**）：
  1. 目录且有 manifest.json                     → assets.load_pack + assets.validate_pack
                                                   kind="asset_pack"，issues = validate_pack 的返回
  2. 目录且有 pack.json 与 phrases.json          → compiler.load_source + load_script
                                                   + check_properties(script, source, pack=None)
                                                   kind="pack_source"，violations = 四属性返回
  3. 文件且 JSON 顶层 schema_version ==
     "vox-eval-report/1"                          → eval.report.check_raw_on_disk
                                                   （完整性核对 + raw 指纹核对）
                                                   kind="eval_report"，issues = 缺失/不符项
  4. 其它                                       → 退出码 2，stderr 说明支持哪三类

退出码：0 = 三类都过 / 4 = 有 issues 或 violations /
        2 = **仅限**产物类型不认识、路径不存在（docs/08 §8.6）。

WHY 装载失败也是 4 而不是 2（2026-09-17 实测钉死）：删掉已铸包里的一个 audio/*.wav 后，
  assets.load_pack 会抛 AssetPackError（fail-closed）——但「产物坏了」是 verify 要**发现**
  的结论（assets.validate_pack 本来就把「音频文件不存在」当 issue 返回），
  不是「你参数用错了」。把它映射成 2 会让人去查命令行，而包是真的坏了。
WHY 只调公开 API 而不自己判：命中判定 / 四属性 / 质检阈值 / 报告口径分属 assets /
  compiler / eval 三层，CLI 重写一份就是第二份口径——两份口径必然漂移，
  漂移的那一份会悄悄让「不通过」变成「通过」，这正是红线禁止的静默降级。
WHY 只读：verify 是复查工具，一旦它修过产物，被复查的对象就不再是当时那一份，
  结论无法回溯（白盒溯源的前提是产物不变）。
"""

import argparse
import sys
from pathlib import Path
from typing import Any

from assets import AssetPackError, load_pack, validate_pack
from compiler import (
    ScriptError,
    SourceError,
    check_properties,
    load_script,
    load_source,
)
from eval.report import SCHEMA_VERSION, check_raw_on_disk

from cli import commands
from cli.errors import EXIT_OK, EXIT_QUALITY, UsageError


# 支持的三类产物形态（退出码 2 的说明文案要原样列出它）
SUPPORTED_KINDS = ("asset_pack", "pack_source", "eval_report")


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", help="产物路径：已铸包目录 / 包源目录 / eval 报告 JSON")
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
# 统一结果骨架（未涉及的键给空列表，JSON 契约保持稳定）
# ---------------------------------------------------------------------------
def _payload(kind: str) -> dict:
    return {
        "command": "verify",
        "kind": kind,
        "passed": False,
        "issues": [],
        "violations": [],
        # skipped 非 §8.6 表内的字段，但必须如实透出：pack=None 时 C3c（已预铸）
        # 没有判定输入，把它藏起来等于「没检查」被当成「检查过了」。
        "skipped": [],
    }


# ---------------------------------------------------------------------------
# 三条分派路径
# ---------------------------------------------------------------------------
def _verify_asset_pack(pack_dir: Path, args: Any) -> int:
    """已铸包目录：assets.validate_pack 收 issues + assets.load_pack 试装载（只读）。

    WHY 先 validate_pack、再 load_pack（2026-09-17 实测钉死）：
        `load_pack` 对"条目引用的音频文件不存在"是 fail-closed 抛 `AssetPackError`，
        而这件事**本来就是 `validate_pack` 的 issue 之一**——"产物坏了"是 verify 要
        **发现**的结论，不是"你参数用错了"。若先 load 并把异常当用法错误（rc 2），
        脚本化调用方会去查命令行，而包是真的坏了。所以两条都收：issues 里既有
        validate_pack 的返回，也有 load_pack 的异常消息（去重后合并）。
    """
    issues = list(validate_pack(pack_dir))
    try:
        load_pack(pack_dir)
    except AssetPackError as exc:
        # 装载失败并入 issues（不抛 UsageError）：它是"产物不合格"的一种 → 返回 4
        message = str(exc)
        if message not in issues:
            issues.append(message)

    payload = _payload("asset_pack")
    payload["issues"] = issues
    payload["passed"] = not issues
    if payload["passed"]:
        summary = f"verify: 通过（kind=asset_pack pack_dir={pack_dir}）"
        commands.emit_result(payload, as_json=args.as_json, summary=summary)
        return EXIT_OK
    summary = (
        f"verify: 不通过（kind=asset_pack issues={len(issues)} 条，pack_dir={pack_dir}）"
    )
    commands.emit_result(payload, as_json=args.as_json, summary=summary)
    return EXIT_QUALITY


def _fail_pack_source(src_dir: Path, args: Any, *, kind_label: str, message: str) -> int:
    """包源自检失败（装载期）：如实报 kind=pack_source / passed=False / rc 4。

    消息进 `issues`（装载失败不是四属性的 `Violation`，不塞进 violations——两块语义不同：
    violations = 四属性判据的返回；issues = "产物读不动/不合格"的说明）。
    """
    payload = _payload("pack_source")
    payload["issues"] = [f"{kind_label}: {message}"]
    summary = f"verify: 不通过（kind=pack_source {kind_label}，src={src_dir}）"
    commands.emit_result(payload, as_json=args.as_json, summary=summary)
    return EXIT_QUALITY


def _verify_pack_source(src_dir: Path, args: Any) -> int:
    """包源目录：compiler 三件套。四属性判据全在 compiler.checks，这里只透传结果。

    WHY 源/剧本装载失败返回 4（不是 2）（2026-09-17 与 docs/08 §8.6 对齐）：
        产物**自身不合规**（`SourceError`/`ScriptError`）正是 verify 要发现的结论；
        rc 2 只留给"类型不认识 / 路径不存在"这类**调用方**的问题——否则脚本化调用方
        会把"这个包源写错了"误读成"我命令敲错了"。
    """
    try:
        source = load_source(src_dir)
    except SourceError as exc:
        return _fail_pack_source(
            src_dir, args, kind_label="源格式校验失败", message=str(exc)
        )
    try:
        script = load_script(src_dir)
    except ScriptError as exc:
        return _fail_pack_source(
            src_dir, args, kind_label="剧本源格式校验失败", message=str(exc)
        )

    # pack=None：包源目录没有 manifest.json，C3c 无判定输入 → 记入 skipped
    result = check_properties(script, source, pack=None)
    payload = _payload("pack_source")
    payload["violations"] = commands.violations_to_dicts(result.violations)
    payload["skipped"] = list(result.skipped)
    payload["passed"] = not result.violations
    if payload["passed"]:
        summary = (
            f"verify: 通过（kind=pack_source src={src_dir} "
            f"phrases={len(source.phrases)} units={len(script.units)}"
        )
        if payload["skipped"]:
            summary += f" skipped={payload['skipped']}"
        summary += "）"
        commands.emit_result(payload, as_json=args.as_json, summary=summary)
        return EXIT_OK
    summary = (
        f"verify: 不通过（kind=pack_source violations={len(result.violations)} 条，"
        f"src={src_dir}）"
    )
    for violation in payload["violations"]:
        _emit_issue(violation["message"])
    commands.emit_result(payload, as_json=args.as_json, summary=summary)
    return EXIT_QUALITY


def _verify_eval_report(report_path: Path, args: Any) -> int:
    """eval 报告：只调 eval.report.check_raw_on_disk（完整性 + raw 指纹核对）。

    raw 指针是相对路径，基准目录就是报告文件所在目录。
    """
    report = commands.read_json_file(report_path, label="eval 报告")
    issues = list(check_raw_on_disk(report, report_path.parent))

    payload = _payload("eval_report")
    payload["issues"] = issues
    payload["passed"] = not issues
    if payload["passed"]:
        summary = f"verify: 通过（kind=eval_report report={report_path}）"
        commands.emit_result(payload, as_json=args.as_json, summary=summary)
        return EXIT_OK
    summary = (
        f"verify: 不通过（kind=eval_report issues={len(issues)} 条，report={report_path}）"
    )
    for issue in issues:
        _emit_issue(issue)
    commands.emit_result(payload, as_json=args.as_json, summary=summary)
    return EXIT_QUALITY


def _emit_issue(text: str) -> None:
    """把一条 issue / violation 写进 stderr（不静默；--json 时 stdout 留给纯 JSON）。"""
    sys.stderr.write(f"问题: {text}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# 命令主体：形态识别 + 分派
# ---------------------------------------------------------------------------
def run(args: Any) -> int:
    """执行 `vox verify`，返回冻结退出码。"""
    artifact = Path(args.artifact)
    if not artifact.exists():
        raise UsageError(f"产物不存在: {artifact}")

    if artifact.is_dir():
        # 判据顺序照 §8.6：先看 manifest.json（已铸包），再看 pack.json + phrases.json（包源）
        if (artifact / "manifest.json").is_file():
            return _verify_asset_pack(artifact, args)
        if (artifact / "pack.json").is_file() and (artifact / "phrases.json").is_file():
            return _verify_pack_source(artifact, args)
        raise UsageError(
            f"无法识别的产物: {artifact}（目录内既无 manifest.json，"
            f"也无 pack.json + phrases.json）"
            f"——vox verify 支持三类产物: {', '.join(SUPPORTED_KINDS)}"
        )

    # 文件：只有 schema_version 对得上的 JSON 才是 eval 报告，其余一律 2（不猜）
    try:
        candidate = commands.read_json_file(artifact, label="产物 JSON")
    except UsageError as exc:
        raise UsageError(
            f"无法识别的产物: {artifact}（{exc.message}）"
            f"——vox verify 支持三类产物: {', '.join(SUPPORTED_KINDS)}"
        ) from exc
    if not isinstance(candidate, dict) or candidate.get("schema_version") != SCHEMA_VERSION:
        actual = (
            candidate.get("schema_version")
            if isinstance(candidate, dict)
            else type(candidate).__name__
        )
        raise UsageError(
            f"无法识别的产物: {artifact}（schema_version={actual!r}，"
            f"期望 {SCHEMA_VERSION!r}）"
            f"——vox verify 支持三类产物: {', '.join(SUPPORTED_KINDS)}"
        )
    return _verify_eval_report(artifact, args)
