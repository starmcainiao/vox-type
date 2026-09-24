"""tools/tests/test_baseline_literal.py — 逐字档基线：variant 索引构建（T12c 修 2）。

**卡内判据（验收 3）**：两条不同 variant 归一化后相同 → **抛错**，且消息含
该归一化 key 与**两条原文**。此前的 `setdefault` 让后写的那条静默丢失、命中归属到
先写 variant 的 key——等于把两条不同的预铸话术当成一条算命中，基线数字口径失真。

全部**离线**：phrases 与 golden 都写在临时目录；归一化复用产品自己的
`adapters.framework_kefu.normalize.normalize_text`（卡内禁止测试里另写一份归一化逻辑）。
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.corpus_fetch.baseline_literal import BaselineError, measure  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_pack(tmp: Path, phrases) -> Path:
    """把 phrases 写成包内格式：{"phrases": [{"key": ..., "variants": [...]}]}。"""
    pack = tmp / "packs" / "fixture-biz"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "phrases.json").write_text(
        json.dumps({"phrases": phrases}, ensure_ascii=False), encoding="utf-8"
    )
    return pack


def _golden_line(assistant: str, user: str = "请问怎么办理", expect_key="greeting_inbound") -> str:
    return json.dumps(
        {
            "case_id": "fin-cs-00001",
            "assistant_reply_free": assistant,
            "user_utterance": user,
            "expect_key": expect_key,
        },
        ensure_ascii=False,
    )


class VariantCollisionTests(unittest.TestCase):
    """T12c 修 2：归一化后碰撞必须响亮失败——不允许静默取其一。"""

    def setUp(self):
        self.tmpdir = Path("/tmp/vox-baseline-collision")
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)
        self.tmpdir.mkdir()
        self.golden = self.tmpdir / "golden.jsonl"
        self.golden.write_text(_golden_line("随便一句话\n") + "\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_normalized_collision_raises_with_key_and_both_originals(self):
        """空格差异归一化后相同 → 抛错，消息含归一化 key 与**两条原文**。"""
        phrases = [
            {"key": "greeting_inbound", "variants": ["您好 欢迎致电客服中心", "您好欢迎致电客服中心"]},
        ]
        pack = _write_pack(self.tmpdir, phrases)
        with self.assertRaises(BaselineError) as cm:
            measure(pack, self.golden, REPO_ROOT)

        msg = str(cm.exception)
        self.assertIn("您好 欢迎致电客服中心", msg)   # 先出现的那条原文（含空格）
        self.assertIn("您好欢迎致电客服中心", msg)    # 本次冲突的那条原文（无空格）
        self.assertIn("您好欢迎致电客服中心", msg)    # 以及归一化后的 key
        self.assertIn("greeting_inbound", msg)
        self.assertIn("碰撞", msg)
        self.assertIn("不允许静默取其一", msg)

    def test_collision_across_two_different_keys(self):
        """跨 key 的碰撞同样要报（不只是同一个 phrase 内的重复），且消息点名两个 key。"""
        phrases = [
            {"key": "greeting_inbound", "variants": ["您好，这里是 Ａ客服中心"]},
            {"key": "verify_identity", "variants": ["您好，这里是A客服中心"]},
        ]
        pack = _write_pack(self.tmpdir, phrases)
        with self.assertRaises(BaselineError) as cm:
            measure(pack, self.golden, REPO_ROOT)

        msg = str(cm.exception)
        self.assertIn("您好，这里是 Ａ客服中心", msg)   # 先出现的那条原文（全角 Ａ + 空格）
        self.assertIn("您好，这里是A客服中心", msg)     # 本次冲突的那条原文（半角 A）
        self.assertIn("greeting_inbound", msg)
        self.assertIn("verify_identity", msg)
        # 两条原文都必须出现——只报一条会让维护者不知道该去重哪一条
        self.assertEqual(msg.count("您好，这里是"), 2)   # 两条原文各 1 次（归一化 key 里空格已被去掉）

    def test_distinct_variants_after_normalization_build_the_index(self):
        """对照：归一化后**不同**的 variants 正常建索引，命中判定不受影响。"""
        phrases = [
            {"key": "greeting_inbound", "variants": ["您好，欢迎致电客服中心。"]},
        ]
        pack = _write_pack(self.tmpdir, phrases)
        g = self.tmpdir / "golden2.jsonl"
        g.write_text(_golden_line("您好，欢迎致电客服中心。") + "\n", encoding="utf-8")

        result = measure(pack, g, REPO_ROOT)

        self.assertEqual(result["n_cases"], 1)
        self.assertEqual(result["hits_assistant_free_text"], 1)
        self.assertEqual(result["cases"][0]["hit_assistant_key"], "greeting_inbound")
