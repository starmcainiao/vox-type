"""
cli.main — 参数解析与分派（docs/08 §8.1 / §8.3）

职责：argparse 分派到三个命令，把各层异常统一翻译成冻结退出码。
     **本文件一行判定逻辑都不写**——命中判定 / 四属性 / 质检阈值 / 统计口径
     一律由对应层实现，CLI 只做「解析 → 调用 → 打印 → 定退出码」。
不负责：不实现 bench / verify 的内部逻辑（分别落在 cli/commands/bench.py 与
      verify.py，只调 eval/ 与 assets//compiler/ 的公开 API），不在这里 import 任何具体适配器。

退出码约定（docs/08 §8.2，逐条照做）：
  0 成功 / 2 用法与参数 / 3 运行期失败（stderr 必带异常类型名）/
  4 质检不通过 / 5 fail-closed 中止

WHY「3 必须打印异常类型名」：调用者只看类型名就能定位该查哪一层
    （TypeError/AttributeError → 适配器契约；AudioError → runtime/audio），
    把内部缺陷折叠成 2 会让人去查命令行参数，定位方向直接错掉。
WHY bench / verify 走各自的 handler 而不是 argparse 的 unrecognized：
    四个命令名已在根 AGENTS.md §四冻结，跑出来不能像「你打错了」；
    命令内的参数校验（如 bench 缺 --corpus / --out）由 argparse 报错 → 退出码 2。
"""

import argparse
import sys
import traceback

from cli.commands import (
    bench as bench_cmd,
    pack_build,
    pack_check,
    run as run_cmd,
    verify as verify_cmd,
)
from cli.errors import (
    CLIError,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USAGE,
    describe,
    format_runtime_line,
)


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """构造命令树：pack{check,build} / run / bench / verify。"""
    parser = argparse.ArgumentParser(
        prog="vox",
        description="vox — 预铸语音资产 CLI（薄壳：解析 → 调用各层公开 API → 打印 → 退出码）",
    )

    # 缺子命令 → argparse 自带 error → 退出码 2（不自己造错误分类）
    sub = parser.add_subparsers(dest="command", metavar="<命令>")
    sub.required = True

    # --- pack ---
    pack_p = sub.add_parser("pack", help="业务包子命令")
    pack_sub = pack_p.add_subparsers(dest="pack_command", metavar="<子命令>")
    pack_sub.required = True

    check_p = pack_sub.add_parser("check", help="只校验不预铸（源格式 + 剧本四属性 + 资产包）")
    pack_check.add_arguments(check_p)
    check_p.set_defaults(handler=pack_check.run)

    build_p = pack_sub.add_parser("build", help="预铸：话术 → 资产包（含质检报告）")
    pack_build.add_arguments(build_p)
    build_p.set_defaults(handler=pack_build.run)

    # --- run ---
    run_p = sub.add_parser("run", help="执行一条播报计划（快路；未命中默认 fail-closed）")
    run_cmd.add_arguments(run_p)
    run_p.set_defaults(handler=run_cmd.run)

    # --- bench / verify ---
    bench_p = sub.add_parser("bench", help="离线对拍：快路（命中资产）vs 慢路（现场合成），出可复现报告")
    bench_cmd.add_arguments(bench_p)
    bench_p.set_defaults(handler=bench_cmd.run)

    verify_p = sub.add_parser("verify", help="对任意产物跑质检（已铸包 / 包源 / eval 报告，只读）")
    verify_cmd.add_arguments(verify_p)
    verify_p.set_defaults(handler=verify_cmd.run)

    return parser


# ---------------------------------------------------------------------------
# 错误打印（docs/08 §8.2：不静默 + 3 必带类型名 + 4/5 措辞中性）
# ---------------------------------------------------------------------------
def _system_exit_code(exc: SystemExit) -> int:
    """把 argparse 的 sys.exit() 归一成冻结退出码。

    WHY 必须归一：argparse 的 error() / exit() 抛 SystemExit 而不是返回值，
        若直接放行，`main()` 在某些路径上会「抛异常」而不是「返回 int」——
        同一入口两种契约，调用者（bin/vox、python3 -m cli、单测）没法统一处理。
    注：argparse 已自行把用法说明写到 stderr，不违反「不静默」。
    """
    code = exc.code
    if code is None:
        return EXIT_OK
    if isinstance(code, int):
        return code
    return EXIT_USAGE


def _emit_conclusion_line(exit_code: int) -> None:
    """非零退出的「不静默」保险：确保 stderr 有一行结论说明。

    WHY 放在 main() 一处而不是各命令：命令函数只管「算出结果 + 打印产物」，
        「退出码 → 中文结论」的映射只有一处，就不会出现某个分支忘了写说明。
    注意：命令自身的摘要已经在正确的流里（--json 时摘要走 stderr），
        这里补的是**不带 --json** 的缺口——那时摘要在 stdout，stderr 会是空的。
    """
    sys.stderr.write(f"{describe(exit_code)}（退出码 {exit_code}）\n")
    sys.stderr.flush()


def _emit_error_line(exc: BaseException) -> None:
    """把一条错误写进 stderr（一行说明，任何非零退出都要有）。"""
    if isinstance(exc, CLIError):
        # 4 / 5 属预期内的业务结论：措辞用「不通过」/「已按 fail-closed 中止」
        sys.stderr.write(f"{describe(exc.exit_code)}: {exc.message}\n")
    else:
        # 3：运行期失败，必带 type(exc).__name__
        sys.stderr.write(f"{describe(EXIT_RUNTIME)}: {format_runtime_line(exc)}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    """CLI 入口：返回冻结退出码（0/2/3/4/5）。

    参数：
        argv: 命令行参数列表；None 时取 sys.argv[1:]（测试可直接传列表）

    返回：
        退出码整数——任何调用方式（bin/vox / python3 -m cli / 单测）都拿到 int。
    """
    parser = build_parser()

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse 的 error()/exit() 抛 SystemExit：归一成返回码（见 _system_exit_code）
        return _system_exit_code(exc)

    verbose = getattr(args, "verbose", False)

    try:
        rc = args.handler(args)
        # 业务结论（4 / 5）由命令函数返回码而不是抛异常：这里补上 stderr 结论行，
        # 保证「任何非零退出都有 stderr 说明」（不带 --json 时摘要在 stdout）。
        if rc not in (EXIT_OK, EXIT_USAGE):
            _emit_conclusion_line(rc)
        return rc
    except CLIError as exc:
        # 已分类的失败：按异常自带退出码返回
        _emit_error_line(exc)
        if verbose:
            traceback.print_exc()
        return exc.exit_code
    except SystemExit as exc:
        # 命令函数内部若调用 parser.exit()，同样归一成返回码
        return _system_exit_code(exc)
    except Exception as exc:  # noqa: BLE001 —— 这是「内部缺陷」的兜底网
        # WHY 必须兜底：未分类异常一律 3 而不是让它炸掉进程（那样退出码是 1，
        # 不在冻结集合里，脚本化调用无法区分「运行期失败」与「未知崩溃」）。
        _emit_error_line(exc)
        if verbose:
            traceback.print_exc()
        return EXIT_RUNTIME
