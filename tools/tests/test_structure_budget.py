"""
tools/tests/test_structure_budget.py — 结构预算检查的验收用例

覆盖：
  1. 可执行行口径：注释与 docstring 不计、缩进层不计、测试目录不计；
     回归：`NL` 与 `NEWLINE` 不是同一个东西（用错会把口径反转成「数空行」）
  2. 阈值只从各层 AGENTS.md 读：未登记 → 该层不参与判定（不是 0 也不是 150）
  3. 超限且未登记豁免 → 违规 + 非 0 退出（判红）
  4. 已登记豁免 → 合规（判绿）
  5. 台账与快照与实际行数一致（渲染出的数字必须能反查到磁盘）
  6. tokenize 失败不得静默按 0 处理

反空转：全部用 `tempfile` 造临时仓库跑 `check()` / `main()`，不 mock 行数、
不在测试里复制 tokenize 逻辑。判红用例真跑一遍再还原。
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.structure_budget.check import (
    check,
    executable_line_count,
    load_budgets,
    load_exemptions,
    main,
    render_ledger,
    scan_layer,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


SMALL_PY = '''"""模块 docstring，这一行与下面两行都不计。"""


def f(x):
    """函数 docstring，也不计。"""
    # 注释行，不计
    return x + 1


class C:
    def m(self):
        return self


Y = [
    1,
    2,
    3,
]
'''


# 60 个函数 × 2 行 = 120，加 SMALL_PY 的 11 行 ≈ 131 > 150 需要更长的量
BIG_PY = SMALL_PY + "\n" + "".join(
    f"def g{i}(x):\n    return x + {i}\n" for i in range(120)
)


def _mkrepo(tmp: Path, *, budgets: dict, files: dict) -> Path:
    """造一个最小仓库骨架：<tmp>/<layer>/AGENTS.md + 若干 .py。"""
    for layer in budgets:
        d = tmp / layer
        d.mkdir(parents=True, exist_ok=True)
        if budgets[layer] is not None:
            (d / "AGENTS.md").write_text(
                f"# {layer}/ AGENTS\n\n结构预算：可执行行数阈值 <= {budgets[layer]}\n",
                encoding="utf-8",
            )
    for rel, body in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp


class TestExecutableLineCount(unittest.TestCase):
    """口径：注释与 docstring 不计，缩进层不计。"""

    def test_comment_and_docstring_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.py"
            p.write_text(SMALL_PY, encoding="utf-8")
            # 手算（NEWLINE 口径 = 逻辑语句数）：module docstring 与 f 的 docstring
            # 不计；f 的定义+return = 2；C 类定义+方法定义+return = 3；
            # Y 的列表字面量是**一条**语句 = 1（括号内的续行不算独立行）。
            self.assertEqual(
                executable_line_count(p), 8,
                "注释 / docstring 被计入，或括号内续行被当成独立行",
            )

    def test_indentation_levels_not_counted(self):
        """缩进层（INDENT/DEDENT）不是物理行，不得各算一行。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.py"
            p.write_text("if True:\n    x = 1\n", encoding="utf-8")
            self.assertEqual(executable_line_count(p), 2)

    def test_newline_not_nl(self):
        """回归：`NL` 只在视觉空白行才发——用它会把「可执行行」数成「空行数」。

        这是本卡实测踩过的坑：`a = 1\nb = 2\n` 的 NEWLINE 是 2、NL 是 0。
        本测试钉住的是「纯代码行文件的行数 = 物理代码行数」，用 NL 口径必然红。
        """
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.py"
            body = "".join(f"v{i} = {i}\n" for i in range(20))
            p.write_text(body, encoding="utf-8")
            self.assertEqual(executable_line_count(p), 20)

    def test_blank_line_only_file_is_zero(self):
        """全空行文件必须是 0——反过来验证不是「数一切换行」。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.py"
            p.write_text("\n\n\n", encoding="utf-8")
            self.assertEqual(executable_line_count(p), 0)

    def test_tokenize_failure_raises_not_zero(self):
        """tokenize 跑不完必须抛错——按 0 行处理就是静默降级。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.py"
            p.write_text("def f(:\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                executable_line_count(p)


class TestBudgetSource(unittest.TestCase):
    """阈值只能来自各层 AGENTS.md；未登记 → 不参与判定。"""

    def test_registered_layer_has_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            _mkrepo(Path(td), budgets={"core": 150}, files={"core/a.py": SMALL_PY})
            self.assertEqual(load_budgets(Path(td))["core"], 150)

    def test_unregistered_layer_is_none_not_guessed(self):
        with tempfile.TemporaryDirectory() as td:
            _mkrepo(Path(td), budgets={"core": None}, files={"core/a.py": SMALL_PY})
            budgets = load_budgets(Path(td))
            self.assertIsNone(budgets["core"])
            # 未登记 → 就算文件再长也不产生违规（不猜阈值、不放宽阈值）
            violations, _, _ = check(Path(td))
            self.assertEqual(violations, [])


class TestViolations(unittest.TestCase):
    """超限 + 未登记豁免 → 判红；登记后 → 判绿。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        _mkrepo(self.tmp, budgets={"core": 150, "eval": 150}, files={})

    def tearDown(self):
        self._td.cleanup()

    def _add(self, rel, body):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def test_over_limit_without_exemption_is_violation(self):
        self._add("core/big.py", BIG_PY)
        violations, _, _ = check(self.tmp, exemptions=[])
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v["path"], "core/big.py")
        self.assertGreater(v["actual"], v["limit"])
        self.assertEqual(v["layer"], "core")

    def test_under_limit_is_not_violation(self):
        self._add("core/small.py", SMALL_PY)
        violations, _, _ = check(self.tmp, exemptions=[])
        self.assertEqual(violations, [])

    def test_registered_exemption_is_compliant(self):
        self._add("core/big.py", BIG_PY)
        violations, _, _ = check(
            self.tmp,
            exemptions=[{"layer": "core", "file": "big.py", "actual": None, "reason": "r"}],
        )
        self.assertEqual(violations, [])

    def test_exemption_key_is_layer_scoped(self):
        """同名的 big.py 在不同层不能互相豁免。"""
        self._add("core/big.py", BIG_PY)
        self._add("eval/big.py", BIG_PY)
        violations, _, _ = check(
            self.tmp,
            exemptions=[{"layer": "core", "file": "big.py", "actual": None, "reason": "r"}],
        )
        self.assertEqual([v["path"] for v in violations], ["eval/big.py"])

    def test_tests_and_pycache_ignored(self):
        self._add("core/tests/big_test.py", BIG_PY)
        self._add("core/__pycache__/big.cpython-314.py", BIG_PY)
        violations, rows, _ = check(self.tmp, exemptions=[])
        self.assertEqual(violations, [])
        self.assertEqual([r["path"] for r in rows], [])


class TestMain(unittest.TestCase):
    """入口：退出码 + JSON 输出。"""

    def test_exit_1_on_violation(self):
        with tempfile.TemporaryDirectory() as td:
            root = _mkrepo(Path(td), budgets={"core": 150}, files={"core/big.py": BIG_PY})
            (root / "tools" / "structure_budget").mkdir(parents=True, exist_ok=True)
            rc = main(["--root", str(root), "--json", "--no-write"])
            self.assertEqual(rc, 1)

    def test_exit_0_when_compliant(self):
        with tempfile.TemporaryDirectory() as td:
            root = _mkrepo(Path(td), budgets={"core": 150}, files={"core/small.py": SMALL_PY})
            (root / "tools" / "structure_budget").mkdir(parents=True, exist_ok=True)
            rc = main(["--root", str(root), "--json", "--no-write"])
            self.assertEqual(rc, 0)

    def test_exit_2_when_not_repo_root(self):
        with tempfile.TemporaryDirectory() as td:
            rc = main(["--root", td, "--json", "--no-write"])
            self.assertEqual(rc, 2)


class TestRealRepo(unittest.TestCase):
    """对真仓库跑一遍：阈值读到了、行数与快照一致、当前无违规。"""

    def test_real_repo_budgets_read_from_agents_md(self):
        budgets = load_budgets(REPO_ROOT)
        # 三层的机器可读标记必须被读到（不是脚本自己写的 150）
        for layer in ("core", "adapters", "eval"):
            self.assertEqual(budgets[layer], 150, f"{layer} 的阈值没从 AGENTS.md 读到")

    def test_real_repo_has_no_violations(self):
        violations, rows, _ = check(REPO_ROOT)
        self.assertEqual(
            violations,
            [],
            "真仓库出现违规——要么先登记豁免，要么先拆文件",
        )
        self.assertGreater(len(rows), 40)

    def test_ledger_numbers_match_disk(self):
        """台账渲染出的每个数字必须能反查到磁盘上的实际行数。"""
        exemptions = load_exemptions(REPO_ROOT)
        violations, rows, budgets = check(REPO_ROOT)
        ledger = render_ledger(REPO_ROOT, rows, budgets, violations, exemptions)
        for row in rows:
            self.assertIn(
                f"`{row['path']}` | {row['executable_lines']} |",
                ledger,
                f"{row['path']} 的行数没进台账",
            )
        # 台账里写的阈值必须等于从 AGENTS.md 读到的
        for layer in ("core", "adapters", "eval"):
            self.assertIn(f"`{layer}` | 150 |", ledger)

    def test_exemptions_round_trip_is_stable(self):
        """渲染出的豁免块必须能被自己的解析器读回（否则下一轮豁免就失效）。"""
        exemptions = load_exemptions(REPO_ROOT)
        self.assertGreaterEqual(len(exemptions), 5)
        _, rows, budgets = check(REPO_ROOT)
        ledger = render_ledger(REPO_ROOT, rows, budgets, [], exemptions)
        import re

        body = re.search(
            r"<!-- BEGIN exemptions -->(.*?)<!-- END exemptions -->", ledger, re.S
        ).group(1)
        # 把渲染结果写进临时台账，再读回来，条目必须一致
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tools" / "structure_budget").mkdir(parents=True)
            block = re.search(
                r"<!-- BEGIN exemptions -->.*?<!-- END exemptions -->",
                ledger,
                re.S,
            ).group(0)
            (root / "tools" / "structure_budget" / "LEDGER.md").write_text(
                block, encoding="utf-8"
            )
            reread = load_exemptions(root)
            self.assertEqual(
                sorted((e["layer"], e["file"]) for e in reread),
                sorted((e["layer"], e["file"]) for e in exemptions),
            )


if __name__ == "__main__":
    unittest.main()
