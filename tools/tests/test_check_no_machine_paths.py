"""
tools.tests.test_check_no_machine_paths — 绝对路径门禁在「根卷仓」上的前缀退化回归（T76）

为什么这一份测试必须在**本机**就能确定性复现缺陷
------------------------------------------------
缺陷只在**根卷** checkout 上出现：`os.path.ismount("/")` 为真，`_mount_ancestor`
从 checkout 目录一路向上，第一个命中的挂载点就是根 `/`；于是
`str("/").rstrip("/") + "/"` 恒等于 `"/"`，而 `"/"` 作前缀会命中每一行里的**每一个**
路径分隔符——CI 的 4a 关一次性产出上万条噪音，红到没人再看输出。

本机本仓落在外接卷上，退化不出来；但本机的系统临时目录**就在根卷上**，所以不必等
真机：把 `_mount_ancestor` 打成 `return_value=Path("/")` 就等价于 runner 的条件。

三条退化路径（`build_prefixes` 里的三份前缀装配），各有独立的守卫，也各有一条用例：
  1. volume-mount —— `_mount_ancestor` 返回 `Path("/")`   （本次事故的直接成因）
  2. machine-home —— `$HOME` 为 `"/"`                     （精简容器里真实存在）
  3. boot-tmp     —— `tempfile.gettempdir()` 为 `"/"`     （临时目录即根）

反空转
------
全部用例 import 产品的真实 `mod.build_prefixes` / `mod.scan_file` 直接调用，
**不在本文件里另写一份前缀装配逻辑**（否则测的是测试自己写的副本，缺陷照样漏）。
每条退化用例同时把**另外两路**打成良性值，从而断言的确实是本用例点名的那一条
守卫——不是「恰好另一路没退化」的巧合。每条负例另配一条正例，证明守卫只挡
`"/"` 而不挡真泄漏（那才是「不得改成干脆不扫」的护栏）。

源码里**不写任何绝对路径字面量**：良性家目录取 `Path.home()` 的运行时值，夹具路径
也同理由运行时值拼出。这样本文件自己也不含本机路径（不用 `scan-exempt` 开后门，
豁免是留给人审的口子，不是给判据自己消数字的）。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.check_no_machine_paths as mod

# 良性（非退化）取值：把不在本用例射程内的两路钉死，避免断言靠巧合成立
_BENIGN_HOME = str(Path.home())   # 运行时值：真实家目录，非 "/"
_BENIGN_TMP = "/tmp"              # 字面与 canonical 变体都不是 "/"
_BENIGN_ROOT = Path(tempfile.gettempdir())
_BENIGN_MOUNT = Path("/mnt/vox-data")   # 合成外接卷根：不落在任何通用兜底根上


def _prefix_strings(root=None):
    """跑产品的真实 `build_prefixes`，只回前缀字符串列表（断言点名的就是这些值）。"""
    return [prefix for _label, prefix, _cls in mod.build_prefixes(root or _BENIGN_ROOT)]


def _built(root=None):
    """跑产品的真实 `build_prefixes`，回完整三元组列表。"""
    return mod.build_prefixes(root or _BENIGN_ROOT)


class TestVolumeMountDegradation(unittest.TestCase):
    """volume-mount 一支：`_mount_ancestor` 在根卷上返回 `Path("/")`。"""

    def test_mount_at_root_does_not_produce_slash_prefix(self):
        """根卷 checkout 条件 → 前缀里不得出现 `"/"`（修复前此用例必红）。"""
        with patch.object(mod, "_mount_ancestor", return_value=Path("/")), \
             patch.dict(mod.os.environ, {"HOME": _BENIGN_HOME}), \
             patch.object(mod.tempfile, "gettempdir", return_value=_BENIGN_TMP):
            prefixes = _prefix_strings()
        self.assertNotIn("/", prefixes)

    def test_mount_at_real_volume_still_produces_volume_mount_prefix(self):
        """守卫只挡 `"/"`，不挡真挂载根：外接卷仍必须被扫（否则等于放宽判据）。"""
        with patch.object(mod, "_mount_ancestor", return_value=_BENIGN_MOUNT), \
             patch.dict(mod.os.environ, {"HOME": _BENIGN_HOME}), \
             patch.object(mod.tempfile, "gettempdir", return_value=_BENIGN_TMP):
            built = _built()
        hits = [(label, prefix, cls) for label, prefix, cls in built
                if label == "volume-mount"]
        self.assertEqual(
            hits,
            [("volume-mount", _BENIGN_MOUNT.as_posix().rstrip("/") + "/", "machine-path")],
        )


class TestHomeDegradation(unittest.TestCase):
    """machine-home 一支：`$HOME` 为 `"/"`（精简容器）。"""

    def test_home_at_root_does_not_produce_slash_prefix(self):
        """`HOME=/` → 前缀里不得出现 `"/"`。"""
        with patch.dict(mod.os.environ, {"HOME": "/"}), \
             patch.object(mod, "_mount_ancestor", return_value=None), \
             patch.object(mod.tempfile, "gettempdir", return_value=_BENIGN_TMP):
            prefixes = _prefix_strings()
        self.assertNotIn("/", prefixes)

    def test_home_at_real_dir_still_produces_machine_home_prefix(self):
        """正常家目录仍必须产出 machine-home 前缀（守卫不得连带把真泄漏放走）。"""
        with patch.dict(mod.os.environ, {"HOME": _BENIGN_HOME}), \
             patch.object(mod, "_mount_ancestor", return_value=None), \
             patch.object(mod.tempfile, "gettempdir", return_value=_BENIGN_TMP):
            built = _built()
        hits = [(label, prefix, cls) for label, prefix, cls in built
                if label == "machine-home"
                and prefix == _BENIGN_HOME.rstrip("/") + "/"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "machine-path")


class TestTmpDegradation(unittest.TestCase):
    """boot-tmp 一支：`tempfile.gettempdir()` 为 `"/"`。"""

    def test_tempdir_at_root_does_not_produce_slash_prefix(self):
        """临时目录即根 → 前缀里不得出现 `"/"`（含 realpath 变体）。"""
        with patch.object(mod.tempfile, "gettempdir", return_value="/"), \
             patch.dict(mod.os.environ, {"HOME": _BENIGN_HOME}), \
             patch.object(mod, "_mount_ancestor", return_value=None):
            prefixes = _prefix_strings()
        self.assertNotIn("/", prefixes)

    def test_tempdir_at_real_dir_still_produces_boot_tmp_prefix(self):
        """真实临时目录仍必须产出 boot-tmp 前缀（不许干脆不扫 boot-tmp）。

        只断言字面那条**必须在**、以及分级仍是 machine-path，不猜条数：
        canonical 变体是否与字面相同取决于本机符号链接。
        """
        with patch.object(mod.tempfile, "gettempdir", return_value="/tmp"), \
             patch.dict(mod.os.environ, {"HOME": _BENIGN_HOME}), \
             patch.object(mod, "_mount_ancestor", return_value=None):
            built = _built()
        boots = [(label, prefix, cls) for label, prefix, cls in built
                 if label == "boot-tmp"]
        self.assertIn(("boot-tmp", "/tmp/", "machine-path"), boots)
        self.assertTrue(all(cls == "machine-path" for _label, _p, cls in boots), boots)


class TestCriteriaNotWeakened(unittest.TestCase):
    """修复不许靠放宽判据变绿：分类、兜底根、豁免机制都得原样在。"""

    def test_both_classes_still_reported(self):
        """machine-path 与 root-literal 两类都还在（删掉一类等于掩盖真泄漏）。"""
        classes = sorted({cls for _label, _p, cls in _built()})
        self.assertEqual(classes, ["machine-path", "root-literal"])

    def test_root_prefixes_intact(self):
        """兜底根前缀一条没少：删改 ROOT_PREFIXES 同样是削弱判据。"""
        built = _built()
        got = {label for label, _p, cls in built if cls == "root-literal"}
        self.assertEqual(got, {label for label, _p in mod.ROOT_PREFIXES})

    def test_scan_exempt_marker_still_suppresses_hit(self):
        """`# scan-exempt:` 豁免仍生效：带标记 0 条，去掉标记照报 1 条。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "leaky.txt"
            prefixes = mod.build_prefixes(root)
            # 夹具路径由运行时家目录拼出，源码里不留绝对路径字面量
            fixture = Path.home().as_posix() + "/secrets.md"

            target.write_text(fixture + "\n", encoding="utf-8")
            hit, reason = mod.scan_file(target, root, prefixes)
            self.assertEqual(len(hit), 1)
            self.assertIn(hit[0]["prefix_class"], ("machine-path", "root-literal"))
            self.assertIsNone(reason)

            target.write_text(
                fixture + "  # scan-exempt: 合成夹具，仅验证豁免机制是否还在\n",
                encoding="utf-8",
            )
            hit2, reason2 = mod.scan_file(target, root, prefixes)
            self.assertEqual(hit2, [])
            self.assertIsNone(reason2)


if __name__ == "__main__":
    unittest.main()
