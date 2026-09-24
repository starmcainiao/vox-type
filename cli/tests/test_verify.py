"""
cli/tests/test_verify.py — `vox verify` 的验收用例（docs/08 §8.6）

覆盖：
  1. 已铸包目录 → kind=asset_pack / rc 0 / issues==[]
  2. 已铸包删掉一个 audio/*.wav → **rc 4（不得 2）** 且 issues 含缺失文件名
     （WHY：产物坏了是 verify 要**发现**的结论，不是"你参数用错了"；
      load_pack 对缺文件 fail-closed 抛错，但它本来就是 validate_pack 的 issue 之一）
  3. 包源目录（packs/repair 活体用例）→ kind=pack_source / rc 0 / violations==[]
  4. 剧本连续 4 次 error_retry → rc 4 且 violations 含 retry_unbounded
  5. 包源装载失败（rates 非法档）→ **rc 4** 且 issues 点明非法值（不是 2）
  6. eval 报告 → kind=eval_report / rc 0；把 raw 数值改掉（**行数不变**）→ rc 4 且 issues 含 sha256
  7. 不认识的产物（.txt）→ rc 2 且 stderr 列出三类支持形态

反空转：全部经 run_cli 调 cli.main.main；`--json` 断言真跑 json.loads；
      不在测试里复制判定逻辑——资产包走 assets、包源走 compiler、报告走 eval。
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cli.tests import (
    PACKS_REPAIR,
    TINY_SCRIPT_JSON,
    CliTestBase,
    assert_json_stdout,
    build_tiny_pack,
    make_tiny_pack,
    run_cli,
    write_json,
)

SUPPORTED_KINDS = ("asset_pack", "pack_source", "eval_report")


class VerifyBase(CliTestBase):
    """共享夹具：极小包源 + 它的已铸产物（预铸一次真机 say，类级复用）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._shared = Path(tempfile.mkdtemp(prefix="cli_verify_shared_"))
        cls.src = make_tiny_pack(cls._shared / "src")
        cls.pack_dir = cls._shared / "built"
        rc, _, err = build_tiny_pack(cls.src, cls.pack_dir)
        assert rc == 0, f"预铸夹具失败: {err}"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared, ignore_errors=True)
        super().tearDownClass()

    def verify(self, artifact, *, json_flag=True):
        argv = ["verify", str(artifact)]
        if json_flag:
            argv.append("--json")
        return run_cli(argv)


# ============================================================
# 1~2. 已铸包：正常 + 坏件（rc 4 而不是 2）
# ============================================================
class TestVerifyAssetPack(VerifyBase):
    def test_ok_pack_returns_0_with_no_issues(self):
        """已铸包正常 → kind=asset_pack / rc 0 / issues 空。"""
        rc, out, err = self.verify(self.pack_dir)

        self.assertEqual(rc, 0, err)
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["command"], "verify")
        self.assertEqual(payload["kind"], "asset_pack")
        self.assertIs(payload["passed"], True)
        self.assertEqual(payload["issues"], [])

    def test_missing_audio_returns_4_not_2(self):
        """删掉一个 audio/*.wav → **rc 4**（不是 2），issues 含缺失文件名。"""
        broken = self.tmp / "broken"
        shutil.copytree(self.pack_dir, broken)
        victims = sorted((broken / "audio").glob("*.wav"))
        self.assertTrue(victims, "夹具包应含音频")
        missing_name = victims[0].name
        victims[0].unlink()

        rc, out, err = self.verify(broken)

        self.assertEqual(rc, 4, f"坏包必须 rc 4（不得 2）；stderr={err}")
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["kind"], "asset_pack")
        self.assertIs(payload["passed"], False)
        self.assertTrue(payload["issues"], "issues 不得为空")
        self.assertTrue(
            any(missing_name in issue for issue in payload["issues"]),
            f"issues 应点名缺失文件 {missing_name}：{payload['issues']}",
        )


# ============================================================
# 3~5. 包源：正常 / 四属性违规 / 装载失败
# ============================================================
class TestVerifyPackSource(VerifyBase):
    def test_ok_source_returns_0(self):
        """包源正常（packs/repair 活体）→ kind=pack_source / rc 0 / violations 空。"""
        rc, out, err = self.verify(PACKS_REPAIR)

        self.assertEqual(rc, 0, err)
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["kind"], "pack_source")
        self.assertIs(payload["passed"], True)
        self.assertEqual(payload["violations"], [])
        # pack=None → C3c 必然跳过，且必须如实透出（跳过 ≠ 通过）
        self.assertEqual(payload["skipped"], ["key_not_prebaked"])

    def test_script_violation_returns_4(self):
        """剧本连续 4 次 error_retry → rc 4 且 violations 含 retry_unbounded。"""
        src = self.tmp / "viol"
        units = [
            {"key": "greeting", "rate": "normal"},
            {"key": "greeting", "rate": "normal"},
            {"key": "greeting", "rate": "normal"},
            {"key": "greeting", "rate": "normal"},   # 第 4 次 = 超过 max_retry(3)
            {"key": "closing", "rate": "normal"},
        ]
        make_tiny_pack(src, script={**TINY_SCRIPT_JSON, "units": units})

        rc, out, err = self.verify(src)

        self.assertEqual(rc, 4, err)
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["kind"], "pack_source")
        self.assertIs(payload["passed"], False)
        codes = [v["code"] for v in payload["violations"]]
        self.assertIn("retry_unbounded", codes)

    def test_source_load_failure_returns_4_not_2(self):
        """源装载失败（rates 非法档）→ **rc 4** 且 issues 点明非法值（不是 2）。"""
        src = self.tmp / "badsrc"
        make_tiny_pack(src)
        pack_json = json.loads((src / "pack.json").read_text(encoding="utf-8"))
        pack_json["rates"] = ["superfast"]
        write_json(src / "pack.json", pack_json)

        rc, out, err = self.verify(src)

        self.assertEqual(rc, 4, f"产物自身不合规必须 rc 4；stderr={err}")
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["kind"], "pack_source")
        self.assertIs(payload["passed"], False)
        joined = " ".join(payload["issues"])
        self.assertIn("superfast", joined)


# ============================================================
# 6. eval 报告：正常 + 值级篡改（T08b 遗留破口的回归）
# ============================================================
class TestVerifyEvalReport(VerifyBase):
    def _make_report(self, out_dir: Path) -> Path:
        """用产品 CLI 造一份 eval 报告（vox bench，离线替身适配器）。"""
        corpus = self.tmp / "corpus.json"
        write_json(
            corpus,
            {"corpus_id": "verify-corpus", "version": 1,
             "units": [{"key": "greeting", "text": "您好，请问需要什么帮助。",
                        "rate": "normal", "variant": 0},
                       {"key": "closing", "text": "感谢您的来电。",
                        "rate": "normal", "variant": 0}]},
        )
        rc, out, err = run_cli([
            "bench", str(self.pack_dir), "--corpus", str(corpus),
            "--out", str(out_dir), "--repeats", "20", "--json",
        ])
        self.assertEqual(rc, 0, err)
        return Path(assert_json_stdout(out, self)["report_path"])

    def test_ok_report_returns_0(self):
        """eval 报告正常 → kind=eval_report / rc 0 / issues 空。"""
        report = self._make_report(self.tmp / "bench-ok")

        rc, out, err = self.verify(report)

        self.assertEqual(rc, 0, err)
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["kind"], "eval_report")
        self.assertIs(payload["passed"], True)
        self.assertEqual(payload["issues"], [])

    def test_value_tampered_raw_returns_4(self):
        """改 raw 数值（行数与 index 不变）→ rc 4 且 issues 含 sha256。

        这是 T08b 明确留给 CLI 的破口：计数结构（行数/index）抓不住值级篡改，
        只有落盘时记下的文件指纹抓得住。
        """
        report_path = self._make_report(self.tmp / "bench-tamper")
        raw = report_path.parent / "raw" / "slow_samples.jsonl"
        rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(rows, "报告应含 slow 样本")
        for row in rows:
            row["first_audio_ms"] = row["first_audio_ms"] / 1000.0   # 只改值，不动行数
        raw.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

        rc, out, err = self.verify(report_path)

        self.assertEqual(rc, 4, f"值级篡改必须 rc 4；stderr={err}")
        payload = assert_json_stdout(out, self)
        self.assertEqual(payload["kind"], "eval_report")
        self.assertIs(payload["passed"], False)
        joined = " ".join(payload["issues"])
        self.assertIn("slow_samples.jsonl", joined)
        self.assertIn("sha256", joined)


# ============================================================
# 7. 不认识的产物 → rc 2（并列出三类支持形态）
# ============================================================
class TestVerifyUnknownArtifact(CliTestBase):
    def test_unknown_artifact_returns_2(self):
        txt = self.tmp / "notes.txt"
        txt.write_text("这不是产物", encoding="utf-8")

        rc, out, err = run_cli(["verify", str(txt), "--json"])

        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "", "rc 2 时 stdout 不应有 JSON")
        for kind in SUPPORTED_KINDS:
            self.assertIn(kind, err, f"stderr 必须列出支持的形态 {kind}")

    def test_missing_path_returns_2(self):
        rc, _, err = run_cli(["verify", str(self.tmp / "nope"), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("nope", err)


if __name__ == "__main__":
    unittest.main()
