"""
cli/tests/test_pack_check.py — `vox pack check` 的验收用例

覆盖范围：
  - packs/repair 源目录：rc 0 + passed=True + skipped=["key_not_prebaked"] + phrases=16 + units=17
  - 已预铸包目录：rc 0 + pack_validate=[]（源/剧本判据无输入，如实记进 skipped）
  - 含违规剧本：rc 4 + passed=False + violations 非空且四字段齐全
  - 缺目录 / 源非法 JSON / 缺 script.json：rc 2（stderr 含可定位信息）
  - --json 的 stdout/stderr 分工（stdout 真跑 json.loads，不带 --json 时反之）

反空转：全部经 `run_cli` 调 `cli.main.main`，不断言「含某个字符串」代替解析。
"""

import json
import unittest

from cli.tests import (
    PACKS_REPAIR,
    CliTestBase,
    TINY_PACK_JSON,
    TINY_SCRIPT_BAD_EXIT_JSON,
    assert_json_stdout,
    assert_not_json,
    build_tiny_pack,
    make_tiny_pack,
    run_cli,
    write_json,
)


# ============================================================
# 1. packs/repair 端到端（活体回归用例，只读）
# ============================================================
class TestPackCheckRepair(CliTestBase):
    """docs/08 §8.3 第 1 条的验收口径：packs/repair 源目录必须全绿。"""

    def test_check_repair_source_passes(self):
        """rc 0，passed=True，skipped 如实透出 key_not_prebaked（pack=None）。"""
        rc, out, err = run_cli(["pack", "check", str(PACKS_REPAIR), "--json"])

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["command"], "pack.check")
        self.assertIs(payload["passed"], True)
        self.assertEqual(payload["violations"], [])
        self.assertEqual(payload["skipped"], ["key_not_prebaked"])
        self.assertEqual(payload["pack_validate"], [])
        self.assertEqual(payload["phrases"], 16)
        self.assertEqual(payload["units"], 17)
        self.assertEqual(payload["pack_dir"], str(PACKS_REPAIR))

    def test_check_repair_summary_goes_to_stderr_with_json(self):
        """--json 时人读摘要走 stderr（stdout 保持纯 JSON）。"""
        rc, out, err = run_cli(["pack", "check", str(PACKS_REPAIR), "--json"])

        self.assertEqual(rc, 0)
        assert_json_stdout(out, self)          # stdout 是 JSON
        self.assertIn("pack check", err)        # 摘要在 stderr

    def test_check_repair_without_json_flag(self):
        """不带 --json：stdout 是人读摘要（非 JSON），JSON 走 stderr。"""
        rc, out, err = run_cli(["pack", "check", str(PACKS_REPAIR)])

        self.assertEqual(rc, 0)
        self.assertIn("pack check", out)
        assert_not_json(out, self)
        self.assertEqual(json.loads(err)["passed"], True)


# ============================================================
# 2. 已预铸包目录（含 manifest.json）
# ============================================================
class TestPackCheckBuiltPack(CliTestBase):
    """指向已预铸包时只做资产层校验，并把跳过的判据如实写进 skipped。"""

    def test_check_built_pack_passes(self):
        """rc 0 且 pack_validate=[]；源/剧本判据无输入 → skipped 非空。"""
        src = make_tiny_pack(self.tmp / "src")
        built = self.tmp / "built"
        rc, out, _ = build_tiny_pack(src, built)
        self.assertEqual(rc, 0, "预铸极小包应当成功（夹具前提）")

        rc, out, err = run_cli(["pack", "check", str(built), "--json"])

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["pack_validate"], [])
        self.assertIs(payload["passed"], True)
        self.assertEqual(payload["violations"], [])
        # 已预铸包不带源文件：不得静默当成「全部通过」
        self.assertEqual(payload["skipped"], ["source_and_script_unavailable"])

    def test_check_broken_manifest_reports_issue(self):
        """manifest.json 缺必填字段 → pack_validate 非空 → rc 4（不静默）。"""
        broken = self.tmp / "broken_pack"
        broken.mkdir(parents=True)
        write_json(broken / "manifest.json", {"pack_id": "x"})

        rc, out, _ = run_cli(["pack", "check", str(broken), "--json"])

        self.assertEqual(rc, 4)
        payload = assert_json_stdout(out, self)
        self.assertIs(payload["passed"], False)
        self.assertGreaterEqual(len(payload["pack_validate"]), 1)
        joined = "; ".join(payload["pack_validate"])
        self.assertIn("缺少必填字段", joined)
        self.assertIn("pack_version", joined)


# ============================================================
# 3. 退出码 4：剧本四属性违规
# ============================================================
class TestPackCheckQualityFailure(CliTestBase):
    """有违规 → rc 4，violations 字段名照 docs/08 §8.3（code/unit_index/key/message）。"""

    def test_check_violating_script_returns_4(self):
        """末单元不是终态 → no_exit，rc 4 且 passed=False。"""
        src = make_tiny_pack(self.tmp / "src", script=TINY_SCRIPT_BAD_EXIT_JSON)
        rc, out, err = run_cli(["pack", "check", str(src), "--json"])

        self.assertEqual(rc, 4)
        payload = assert_json_stdout(out, self)
        self.assertIs(payload["passed"], False)
        self.assertGreaterEqual(len(payload["violations"]), 1)
        codes = {v["code"] for v in payload["violations"]}
        self.assertIn("no_exit", codes)
        # 字段名一字不改
        for violation in payload["violations"]:
            self.assertEqual(sorted(violation.keys()), ["code", "key", "message", "unit_index"])
        self.assertIn("pack check: 不通过", err)

    def test_check_source_and_script_counts(self):
        """phrases / units 计数取自各层装载结果，不是 CLI 自己数的。"""
        src = make_tiny_pack(self.tmp / "src")
        rc, out, _ = run_cli(["pack", "check", str(src), "--json"])

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["phrases"], 2)
        self.assertEqual(payload["units"], 2)


# ============================================================
# 4. 退出码 2：源格式 / 路径 / JSON 解析失败
# ============================================================
class TestPackCheckUsageErrors(CliTestBase):
    """配置期错误一律 rc 2，stderr 必带可定位信息。"""

    def test_check_missing_dir_returns_2(self):
        """路径不存在 → rc 2，stderr 含该路径。"""
        missing = self.tmp / "does_not_exist"
        rc, _, err = run_cli(["pack", "check", str(missing), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn(str(missing), err)

    def test_check_malformed_pack_json_returns_2(self):
        """pack.json 不是合法 JSON → SourceError → rc 2。"""
        src = self.tmp / "src"
        make_tiny_pack(src)
        (src / "pack.json").write_text("{这不是JSON", encoding="utf-8")

        rc, _, err = run_cli(["pack", "check", str(src), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("pack.json", err)
        self.assertIn("源格式校验失败", err)

    def test_check_missing_script_returns_2(self):
        """缺 script.json → ScriptError → rc 2（不静默当成无剧本）。"""
        src = self.tmp / "src"
        make_tiny_pack(src)
        (src / "script.json").unlink()

        rc, _, err = run_cli(["pack", "check", str(src), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("script.json", err)

    def test_check_unknown_source_field_returns_2(self):
        """pack.json 未知字段（docs/08 §8.5 欠账 1）→ rc 2 且消息含该字段名。"""
        src = self.tmp / "src"
        bad_pack = dict(TINY_PACK_JSON)
        bad_pack["duplx"] = {}
        make_tiny_pack(src, pack=bad_pack)

        rc, _, err = run_cli(["pack", "check", str(src), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("duplx", err)
        self.assertIn("未知字段", err)


if __name__ == "__main__":
    unittest.main()
