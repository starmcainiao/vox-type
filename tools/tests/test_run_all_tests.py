"""
tools.tests.test_run_all_tests — 全量跑批入口的验收用例（T48）

覆盖：
  1. 汇总口径分项：ran/skipped/executed/failed/failures/errors 逐项加总，
     且 `executed = ran - skipped`（不是「ran 减一切」——失败用例仍在 executed 里，失败数单列）
  2. **失败不被掩盖**：任一根 failed > 0 即进 `failed_roots`（跑批铁律的最小单元）
  3. 仓根入 sys.path：幂等，且能补回 `python3 -m` 的 cwd 副作用
  4. 单根发现：临时根里的必红用例必须被判成 failed（真跑 unittest，不 mock 计数）
  5. 端到端必红：真实起子进程跑一个必红临时根 → 退出码非 0 且输出点名该根
  6. 端到端全绿：真实跑一个仓内真根（rules）→ 退出码 0 且汇总行 failed=0

反空转：不复制计数逻辑去断言「我以为的口径」，而是直接调被验函数；
临时根用 `tempfile` 造在仓外（不往仓里写文件），端到端两条都真起子进程。
"""

import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.run_all_tests import (
    REPO_ROOT,
    _discover_and_run,
    _ensure_repo_root_on_syspath,
    _tally,
    main,
)


# 一个必红的临时测试根：内容是 unittest 真用例，不做任何 mock
_FAILING_ROOT = '''"""临时根：必红（用来验证失败不会被掩盖）。"""

import unittest


class TestAlwaysRed(unittest.TestCase):
    def test_red(self):
        self.assertTrue(False, "注入的失败")
'''

# 一个必绿的临时测试根
_GREEN_ROOT = '''"""临时根：必绿。"""

import unittest


class TestAlwaysGreen(unittest.TestCase):
    def test_green(self):
        self.assertTrue(True)
'''


def _make_root(tmpdir: str, filename: str, source: str) -> str:
    """把一段测试模块源码落成临时根（**独立子目录**），返回该子目录的绝对路径。

    参数：
      tmpdir    临时目录
      filename  测试文件名（须以 `test` 开头才会被 discover 收）
      source    测试模块源码
    返回：
      临时根的绝对路径（目录）
    """
    root = Path(tmpdir) / Path(filename).stem
    root.mkdir()
    (root / filename).write_text(source, encoding="utf-8")
    return str(root)


def _counters(root: str, ran: int, skipped: int, failures: int, errors: int) -> dict:
    """造一条与 `_discover_and_run` 同形的计数字典（只测加总，不测发现）。"""
    return {
        "root": root,
        "ran": ran,
        "skipped": skipped,
        "failures": failures,
        "errors": errors,
        "failed": failures + errors,
        "discovery_error": None,
    }


class TestTally(unittest.TestCase):
    """汇总口径：分项加总 + 失败根点名。"""

    def test_items_are_summed_separately(self):
        """分项逐项相加，不是把 ran/skipped/failed 合成一个总数。"""
        summary = _tally([
            _counters("core", 62, 0, 0, 0),
            _counters("packs", 35, 24, 0, 0),
        ])
        self.assertEqual(summary["ran"], 97)
        self.assertEqual(summary["skipped"], 24)
        self.assertEqual(summary["executed"], 73)  # 97 - 24
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["roots"], 2)

    def test_executed_keeps_failed_cases_and_failed_is_separate(self):
        """executed 含失败用例；失败数单列，不许从 executed 里抹掉（那正是 1,547 悬案的成因）。"""
        summary = _tally([_counters("core", 10, 2, 3, 1)])
        self.assertEqual(summary["executed"], 8)  # 10 - 2（失败的 4 条仍在里面）
        self.assertEqual(summary["failed"], 4)   # failures 3 + errors 1
        self.assertEqual(summary["failures"], 3)
        self.assertEqual(summary["errors"], 1)

    def test_failed_roots_named(self):
        """任一根红即进 failed_roots（逐个点名，不许只给总数）。"""
        summary = _tally([
            _counters("core", 62, 0, 0, 0),
            _counters("tools", 159, 0, 1, 0),
            _counters("eval", 239, 0, 0, 2),
        ])
        self.assertEqual(summary["failed_roots"], ["tools", "eval"])

    def test_all_green_has_no_failed_roots(self):
        self.assertEqual(_tally([_counters("core", 1, 0, 0, 0)])["failed_roots"], [])


class TestSysPathBootstrap(unittest.TestCase):
    """仓根入 sys.path（补回 `python3 -m unittest` 的 cwd 副作用）。"""

    def test_repo_root_is_on_sys_path_after_call(self):
        import sys

        before = list(sys.path)
        try:
            _ensure_repo_root_on_syspath()
            self.assertIn(str(REPO_ROOT), sys.path)
            first = sys.path.count(str(REPO_ROOT))
            _ensure_repo_root_on_syspath()  # 幂等：再调一次不重复插入
            self.assertEqual(sys.path.count(str(REPO_ROOT)), first)
        finally:
            sys.path[:] = before

    def test_repo_root_is_actual_repo_root(self):
        """仓根自推正确：不是 tools/（脚本所在目录），否则产品包全部 import 失败。"""
        self.assertTrue((REPO_ROOT / "core" / "protocol.py").is_file())
        self.assertTrue((REPO_ROOT / "tools" / "run_all_tests.py").is_file())


class TestDiscoverAndRun(unittest.TestCase):
    """单根发现：临时根，真跑 unittest。"""

    def test_failing_root_is_counted_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_root(tmp, "test_red_root.py", _FAILING_ROOT)
            counters = _discover_and_run(root, 0)
        self.assertEqual(counters["ran"], 1)
        self.assertEqual(counters["failed"], 1)
        self.assertEqual(counters["failures"], 1)
        self.assertIsNone(counters["discovery_error"])

    def test_green_root_is_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_root(tmp, "test_green_root.py", _GREEN_ROOT)
            counters = _discover_and_run(root, 0)
        self.assertEqual(counters["ran"], 1)
        self.assertEqual(counters["skipped"], 0)
        self.assertEqual(counters["failed"], 0)

    def test_missing_root_is_counted_as_error_not_silently_zero(self):
        """根不存在 / 不是目录必须记成 error，不许静默按 0 条全绿（实测：discover 自己不抛）。"""
        counters = _discover_and_run("no_such_test_root_xyz", 0)
        self.assertEqual(counters["failed"], 1)
        self.assertEqual(counters["errors"], 1)
        self.assertIn("测试根不存在", counters["discovery_error"])

    def test_file_instead_of_dir_is_counted_as_error(self):
        """根指向一个文件（打错根名的另一种形态）同样必须记 error。"""
        with tempfile.TemporaryDirectory() as tmp:
            a_file = Path(tmp) / "not_a_dir.py"
            a_file.write_text("x = 1\n", encoding="utf-8")
            counters = _discover_and_run(str(a_file), 0)
        self.assertEqual(counters["failed"], 1)
        self.assertIn("测试根不是目录", counters["discovery_error"])


class TestMainEndToEnd(unittest.TestCase):
    """端到端：真起子进程，验退出码与点名（跑批铁律）。"""

    def _run_main(self, roots):
        """调 `main` 并抓 stdout，返回 (退出码, 输出文本)。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--root", *roots])
        return code, buf.getvalue()

    def test_failing_root_yields_nonzero_exit_and_names_it(self):
        """必红根 → 退出码非 0 且输出点名该根（不被其他根的绿掩盖）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_root(tmp, "test_red_root.py", _FAILING_ROOT)
            code, out = self._run_main([root])
        self.assertEqual(code, 1)
        self.assertIn("FAILED roots: " + root, out)
        self.assertIn("failed=1", out)

    def test_real_root_green_yields_zero_exit(self):
        """真实仓内根（rules，21 条）→ 退出码 0，汇总行分项齐全。"""
        code, out = self._run_main(["rules"])
        self.assertEqual(code, 0)
        m = re.search(
            r"ran=(\d+) skipped=(\d+) executed=(\d+) failed=0 failures=0 errors=0 roots=1",
            out,
        )
        self.assertIsNotNone(m, out)
        self.assertEqual(int(m.group(1)) - int(m.group(2)), int(m.group(3)))
        self.assertIn("FAILED roots: (none)", out)


if __name__ == "__main__":
    unittest.main()
