#!/usr/bin/env python3
"""
tools/run_all_tests.py — 「跑全量测试」的**单一真源**（T48）

为什么需要它（三个可复现事实）：
  1. 仓内十一个测试根的 `tests/` 包名**全部叫 `tests`**，放在同一个解释器里必然撞名
     （`rules/ adapters/ tools/` 三根的父目录没有 `__init__.py`，unittest 会把它们
     统一导成 `tests.test_xxx`，后一根拿到的是前一根的模块）。
     → 故**每根一个独立子进程**：进程隔离是唯一不污染任何测试文件的隔离方式。
  2. `.github/workflows/tests.yml` 与 `CONTRIBUTING.md` 各抄了一份 `for` 循环，两份会漂移。
     → 本脚本是那一份循环的**唯一副本**，两边都调它。
  3. 对外宣称的测试数没有机器出处（README/docs 写 1,547，而任何实跑口径都给不出它）。
     → 本脚本打印**分项**汇总行，数字从此有出处；口径写在 `--help` 与 README 里。

口径（分项，不合并成一个总数）：
  ran      = 所有根 `result.testsRun` 之和（含 skip、含失败用例）
  skipped  = 所有根 `result.skipped` 条数之和（`@unittest.skip` 的用例）
  executed = ran - skipped（**含**失败用例；失败数单列在 failed/failures/errors）
  failed   = failures + errors（断言失败 + 运行期 error + 收集/发现异常）
  failures = 断言失败条数（`result.failures`）
  errors   = 运行期 error 条数（`result.errors`）+ 发现/导入异常（记 1）

退出码：
  0  全部根全绿
  1  至少一根有失败/错误（含发现异常）——**逐个点名失败根**
  2  用法或参数错误（argparse 自带）

铁律（AGENTS.md「跑批铁律」）：失败会被产物数量掩盖。
故任一根 `failed > 0` 都**必须**非零退出，且汇总里逐个点名，不允许「总数看着还行」蒙混过关。

零第三方依赖：只用标准库。`python3 -m pytest` 在不在都不影响本脚本
（conftest.py 只服务 pytest；全量口径走 unittest，与 pytest 无关）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unittest
from pathlib import Path

# 仓根（tools/run_all_tests.py 的上上级）
REPO_ROOT = Path(__file__).resolve().parents[1]

# 全量测试根：**顺序与 .github/workflows/tests.yml 的那份循环一致**（T49 会让 CI 改调本脚本）
TEST_ROOTS = (
    "core",
    "rules",
    "assets",
    "adapters",
    "compiler",
    "runtime",
    "eval",
    "cli",
    "tools",
    "trigger",
    "packs",
)

# 子进程回报用的哨兵前缀：只认这一行，unittest 自己打的任何文字都不参与解析
_SENTINEL = "__VOX_TEST_RESULT__ "


def _ensure_repo_root_on_syspath() -> None:
    """把仓根放进 `sys.path`（幂等）。

    为什么需要：CI 那边是 `python3 -m unittest discover`，`-m` 会把 **cwd** 放进
    `sys.path[0]`，测试文件里的 `from core.protocol import parse_plan` /
    `from tools.xxx import yyy` 靠的就是它。本脚本以 `python3 tools/run_all_tests.py`
    方式启动时 `sys.path[0]` 是 `tools/`（脚本所在目录）而不是 cwd，
    仓根不在路径上 → 产品包全部 import 失败。
    这一行就是**显式补回 `-m` 的那个副作用**，不是额外行为。
    """
    repo_root = str(REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def _resolve_root_dir(root: str) -> str:
    """把根名解析成仓内的绝对目录，并**显式校验它存在且是目录**。

    为什么必须校验（实测出来的坑）：`TestLoader.discover(start_dir=<不存在或非目录>)`
    **不抛异常**，而是返回空 suite → ran=0、failed=0、退出码 0。
    也就是「根名打错一个字母 → 这一根静默按 0 条全绿」，
    正是 `AGENTS.md` 禁止的静默降级（且在跑批场景下**失败会被 0 条掩盖**）。
    故这里先拦一道，拦不住就让 `_discover_and_run` 记成 error 并点名。

    参数：
      root 测试根名（相对仓根）或绝对路径
    返回：
      绝对目录路径字符串
    抛：
      NotADirectoryError / FileNotFoundError（目录不存在或不是目录）
    """
    path = Path(root)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"测试根不存在: {root}（解析为 {path}）")
    if not path.is_dir():
        raise NotADirectoryError(f"测试根不是目录: {root}（解析为 {path}）")
    return str(path)


def _discover_and_run(root: str, verbosity: int) -> dict:
    """在**当前进程**内发现并跑完一个测试根，返回机器可读的结果字典。

    与 `python3 -m unittest discover -s <root>` 等价（同一个 `TestLoader.discover`
    + `TextTestRunner`，同样把 top_level_dir 插进 `sys.path`），
    区别有两处，都是为了**堵静默降级**：
      一是先 `_resolve_root_dir` 校验根确实存在（`discover` 自己不校验，见该函数 docstring）；
      二是本函数拿得到 `TestResult` 对象，不必正则去解析 `Ran N tests` 文本。

    参数：
      root      测试根目录名（相对仓根）或绝对路径
      verbosity unittest 输出详细度（0/1/2）
    返回：
      计数字典（ran/skipped/failures/errors/failed/discovery_error）
    """
    # 发现阶段就炸的（根不存在、import 失败、包名冲突…）也算 error：记 1，不静默跳过
    try:
        start_dir = _resolve_root_dir(root)
        suite = unittest.TestLoader().discover(
            start_dir=start_dir, top_level_dir=None
        )
    except Exception as exc:  # noqa: BLE001 - 发现期任何异常都不得吞掉（禁止静默降级）
        return {
            "root": root,
            "ran": 0,
            "skipped": 0,
            "failures": 0,
            "errors": 1,
            "failed": 1,
            "discovery_error": f"{type(exc).__name__}: {exc}",
        }

    # 结果流走 stderr（与 unittest 自身一致），stdout 只留给哨兵行，父进程才好解析
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=verbosity).run(suite)
    skipped = len(result.skipped)
    failures = len(result.failures)
    errors = len(result.errors)
    return {
        "root": root,
        "ran": result.testsRun,
        "skipped": skipped,
        "failures": failures,
        "errors": errors,
        "failed": failures + errors,
        "discovery_error": None,
    }


def _run_child(root: str, verbosity: int) -> int:
    """子进程入口：跑一个根，把计数以哨兵行写到 stdout，然后按结果返回退出码。

    参数：
      root      测试根目录名
      verbosity unittest 输出详细度
    返回：
      进程退出码（0 全绿 / 1 有失败）
    """
    _ensure_repo_root_on_syspath()
    counters = _discover_and_run(root, verbosity)
    sys.stdout.write(_SENTINEL + json.dumps(counters, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 1 if counters["failed"] else 0


def _run_root_in_subprocess(root: str, verbosity: int) -> dict:
    """在一个**独立子进程**里跑一个根，并解析它的哨兵行。

    为什么要子进程：十一个根的 `tests` 包名全撞（见模块 docstring），
    同一解释器内 `sys.modules` 会串味，第二个根会静默拿到第一个根的模块。
    进程隔离是唯一不改动任何测试文件的隔离方式。

    哨兵行缺失（子进程被信号杀死、解释器起不来）同样算失败并点名，
    不允许「没回报就当过了」。

    参数：
      root      测试根目录名
      verbosity 子进程内 unittest 的输出详细度
    返回：
      计数字典；解析不到时用带 failed=1 的兜底字典
    """
    cmd = [sys.executable, str(Path(__file__).resolve()), "--_child", root]
    if verbosity:
        cmd += ["--verbosity", str(verbosity)]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),  # 产品包按绝对路径 import（from core.protocol import ...），cwd 必须是仓根
        stdout=subprocess.PIPE,  # 哨兵行
        stderr=None,  # 测试进度/失败详情直接继承终端，实时可见
        text=True,
    )
    payload = None
    with proc.stdout:  # 显式关闭，避免 ResourceWarning（leak 会让跑批本身变成噪声源）
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith(_SENTINEL):
                payload = line[len(_SENTINEL):]
    proc.wait()

    if payload is None:
        return {
            "root": root,
            "ran": 0,
            "skipped": 0,
            "failures": 0,
            "errors": 1,
            "failed": 1,
            "discovery_error": f"子进程未回报计数行（returncode={proc.returncode}）",
        }
    counters = json.loads(payload)
    # 进程退出码与计数**互相交叉校验**：任一为红都算红（计数被篡改/漏报不许蒙混过关）
    if proc.returncode != 0 and counters["failed"] == 0:
        counters["failed"] = 1
        counters["errors"] += 1
        counters["discovery_error"] = counters["discovery_error"] or (
            f"子进程退出码 {proc.returncode} 但计数报 failed=0"
        )
    return counters


def _tally(per_root: list[dict]) -> dict:
    """把逐根计数加总成分项汇总。

    参数：
      per_root  `_run_root_in_subprocess` 逐根返回的计数字典列表
    返回：
      分项汇总字典（ran/skipped/executed/failed/failures/errors/roots/failed_roots）
    """
    ran = sum(r["ran"] for r in per_root)
    skipped = sum(r["skipped"] for r in per_root)
    failures = sum(r["failures"] for r in per_root)
    errors = sum(r["errors"] for r in per_root)
    failed_roots = [r["root"] for r in per_root if r["failed"] > 0]
    return {
        "ran": ran,
        "skipped": skipped,
        # executed = ran - skipped（**含**失败用例，失败数单列，不许从 executed 里抹掉）
        "executed": ran - skipped,
        "failed": failures + errors,
        "failures": failures,
        "errors": errors,
        "roots": len(per_root),
        "failed_roots": failed_roots,
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口：逐根跑 → 打印分项汇总 → 按断言给退出码。

    参数：
      argv      命令行参数（None = 取 `sys.argv[1:]`）
    返回：
      进程退出码（0 全绿 / 1 有失败根 / 2 参数错误）
    """
    parser = argparse.ArgumentParser(
        prog="run_all_tests.py",
        description="跑全量测试（单一真源）：逐根 unittest discover，打印分项汇总，非零退出即有失败根。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "口径：ran=所有根 testsRun 之和（含 skip 与失败用例）；"
            "skipped=@unittest.skip 条数；executed=ran-skipped；"
            "failed=failures+errors（含发现期异常）。\n"
            "退出码：0 全绿 / 1 有失败根 / 2 参数错误。"
        ),
    )
    parser.add_argument("--root", action="append", default=None,
                        help="只跑指定根（可重复）；缺省跑全部 11 根")
    parser.add_argument("--json", action="store_true",
                        help="额外把逐根明细与汇总以 JSON 打到 stdout（机器出处用）")
    parser.add_argument("-v", "--verbosity", action="count", default=0,
                        help="-v 逐用例名；-vv 同 unittest 的更详细")
    # 内部子进程入口（父进程调用，不面向使用者）
    parser.add_argument("--_child", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args._child is not None:
        return _run_child(args._child, args.verbosity)

    roots = tuple(args.root) if args.root else TEST_ROOTS

    per_root: list[dict] = []
    for root in roots:
        print(f"== {root} ==", flush=True)
        per_root.append(_run_root_in_subprocess(root, args.verbosity))

    summary = _tally(per_root)
    # 末行汇总：**分项**打印，不要只给一个总数（1,547 那个悬案正是「只给总数」的产物）
    print("== 汇总 ==")
    print(
        f"ran={summary['ran']} skipped={summary['skipped']} "
        f"executed={summary['executed']} failed={summary['failed']} "
        f"failures={summary['failures']} errors={summary['errors']} "
        f"roots={summary['roots']}"
    )
    if summary["failed_roots"]:
        # 逐个点名失败根：失败根必须可定位，不能被「其他根都绿」淹没
        print("FAILED roots: " + ", ".join(summary["failed_roots"]))
        for r in per_root:
            if r["failed"] and r.get("discovery_error"):
                print(f"  [{r['root']}] {r['discovery_error']}")
    else:
        print("FAILED roots: (none)")

    if args.json:
        print(json.dumps({"summary": summary, "per_root": per_root},
                         ensure_ascii=False, indent=2))

    # 跑批铁律：任一根红 → 非零退出
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
