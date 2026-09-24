"""
compiler.tests.test_source — 源格式装载器的正例 + 负例测试

覆盖范围：
  - load_source 正例：合法源能装载，PackSource 字段与结构正确
  - rates 缺省：phrase 未给 rates → 取 pack.json.rates
   - 负例（均断言异常类型 + 消息含 key 名或字段名）：
       缺 pack.json / 缺 phrases.json / pack.json 缺必填字段 / phrases 缺字段 /
       phrases 空列表 / key 空 / key 重复 / variants 空列表 / variant 空元素 /
       rates 含非法档位（phrase 级 + pack 级）/ variant 含两个句末标点 /
       未知字段（pack.json 的 duplx / phrases[0] 的 varient）
   - 未知字段的活体回归：locale + duplex 属业务侧合法字段，必须照常通过
"""

import json
import tempfile
import unittest
from pathlib import Path

from compiler.source import PackSource, SourceError, load_source


# ---------------------------------------------------------------------------
# 辅助：在 tempfile 里自造源（不留样例数据在仓库中）
# ---------------------------------------------------------------------------
def _write_json(path: Path, data: dict) -> None:
    """写 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _default_pack(**overrides) -> dict:
    """构造一份合法的 pack.json 字典。"""
    data = {
        "pack_id": "repair",
        "pack_version": "1",
        "protocol_version": "0.1",
        "ruleset_version": "v1",
        "voice": "Tingting",
        "model_version": "macos-say",
        "rates": ["normal", "slow"],
    }
    data.update(overrides)
    return data


def _default_phrases(**overrides) -> dict:
    """构造一份合法的 phrases.json 字典。"""
    data = {
        "phrases": [
            {
                "key": "greeting",
                "variants": ["您好，请问需要什么帮助"],
                "rates": ["slow"],
            }
        ]
    }
    data.update(overrides)
    return data


def _make_source(root: Path, pack=None, phrases=None) -> None:
    """在 root 下写出 pack.json + phrases.json。"""
    _write_json(root / "pack.json", pack if pack is not None else _default_pack())
    _write_json(root / "phrases.json", phrases if phrases is not None else _default_phrases())


class _TmpDirTestBase(unittest.TestCase):
    """提供 tempfile 目录与清理。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="compiler_source_")
        self.tmp_dir = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


# ============================================================
# 1. 正例
# ============================================================
class TestLoadSourceHappyPath(_TmpDirTestBase):
    """合法源必须能装载，且结构正确。"""

    def test_returns_pack_source_with_metadata(self):
        """装载结果必须是 PackSource，且包元数据字段与源一致。"""
        _make_source(self.tmp_dir)
        source = load_source(self.tmp_dir)

        self.assertIsInstance(source, PackSource)
        self.assertEqual(source.pack_id, "repair")
        self.assertEqual(source.pack_version, "1")
        self.assertEqual(source.protocol_version, "0.1")
        self.assertEqual(source.ruleset_version, "v1")
        self.assertEqual(source.voice, "Tingting")
        self.assertEqual(source.model_version, "macos-say")

    def test_rates_default_and_explicit(self):
        """rates 缺省时取 pack.json.rates；显式给出时用 phrase 自己的。"""
        phrases = {
            "phrases": [
                {"key": "a", "variants": ["第一条"]},              # 缺省 → pack.json.rates
                {"key": "b", "variants": ["第二条"], "rates": ["fast"]},
            ]
        }
        _make_source(self.tmp_dir, phrases=phrases)
        source = load_source(self.tmp_dir)

        self.assertEqual(source.rates, ("normal", "slow"))
        self.assertEqual(source.phrases[0].rates, ("normal", "slow"))
        self.assertEqual(source.phrases[1].rates, ("fast",))

    def test_variants_and_keys_preserved(self):
        """variants 与 key 必须原样保留，顺序不变。"""
        phrases = {
            "phrases": [
                {
                    "key": "greeting",
                    "variants": ["您好", "你好，请问有什么可以帮您"],
                    "rates": ["slow"],
                }
            ]
        }
        _make_source(self.tmp_dir, phrases=phrases)
        source = load_source(self.tmp_dir)

        self.assertEqual(len(source.phrases), 1)
        self.assertEqual(source.phrases[0].key, "greeting")
        self.assertEqual(
            source.phrases[0].variants, ("您好", "你好，请问有什么可以帮您")
        )

    def test_single_sentence_terminator_is_allowed(self):
        """含一个句末标点的 variant 是合法「一句」。"""
        phrases = {
            "phrases": [{"key": "k", "variants": ["好的，我先帮您查一下。"]}]
        }
        _make_source(self.tmp_dir, phrases=phrases)
        source = load_source(self.tmp_dir)
        self.assertEqual(source.phrases[0].variants, ("好的，我先帮您查一下。",))

    def test_source_is_frozen(self):
        """PackSource 必须不可变（frozen dataclass）。"""
        _make_source(self.tmp_dir)
        source = load_source(self.tmp_dir)
        with self.assertRaises(Exception):
            source.pack_id = "changed"


# ============================================================
# 2. 负例：文件缺失与字段缺失
# ============================================================
class TestMissingFiles(_TmpDirTestBase):
    """缺文件必须抛 SourceError，消息含文件名。"""

    def test_missing_pack_json(self):
        """缺 pack.json → SourceError，消息含 'pack.json'。"""
        _write_json(self.tmp_dir / "phrases.json", _default_phrases())
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("pack.json", str(ctx.exception))

    def test_missing_phrases_json(self):
        """缺 phrases.json → SourceError，消息含 'phrases.json'。"""
        _write_json(self.tmp_dir / "pack.json", _default_pack())
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("phrases.json", str(ctx.exception))

    def test_non_json_pack_file(self):
        """pack.json 不是合法 JSON → SourceError。"""
        (self.tmp_dir / "pack.json").write_text("{这不是JSON", encoding="utf-8")
        _write_json(self.tmp_dir / "phrases.json", _default_phrases())
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("pack.json", str(ctx.exception))


class TestMissingFields(_TmpDirTestBase):
    """缺必填字段必须抛 SourceError，消息含字段名。"""

    def test_pack_missing_voice(self):
        """pack.json 缺 voice → SourceError，消息含 'voice'。"""
        pack = _default_pack()
        pack.pop("voice")
        _make_source(self.tmp_dir, pack=pack)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("voice", str(ctx.exception))

    def test_pack_missing_pack_id(self):
        """pack.json 缺 pack_id → SourceError，消息含 'pack_id'。"""
        pack = _default_pack()
        pack.pop("pack_id")
        _make_source(self.tmp_dir, pack=pack)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("pack_id", str(ctx.exception))

    def test_phrases_missing_key_field(self):
        """phrases.json 缺 phrases 字段 → SourceError，消息含 'phrases'。"""
        _make_source(self.tmp_dir, phrases={})
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("phrases", str(ctx.exception))

    def test_phrases_empty_list(self):
        """phrases 为空列表 → SourceError，消息含 'phrases'。"""
        _make_source(self.tmp_dir, phrases={"phrases": []})
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("phrases", str(ctx.exception))


# ============================================================
# 3. 负例：key 规则
# ============================================================
class TestKeyRules(_TmpDirTestBase):
    """key 必须非空且在同一源内唯一。"""

    def test_empty_key(self):
        """空 key → SourceError，消息含 'key'。"""
        phrases = {"phrases": [{"key": "", "variants": ["您好"]}]}
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("key", str(ctx.exception))

    def test_missing_key(self):
        """完全不给 key → SourceError，消息含 'key'。"""
        phrases = {"phrases": [{"variants": ["您好"]}]}
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("key", str(ctx.exception))

    def test_duplicate_key(self):
        """重复 key → SourceError，消息含该 key 名（不许静默后者覆盖前者）。"""
        phrases = {
            "phrases": [
                {"key": "greeting", "variants": ["第一条"]},
                {"key": "greeting", "variants": ["第二条"]},
            ]
        }
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        msg = str(ctx.exception)
        self.assertIn("greeting", msg)
        self.assertIn("重复", msg)


# ============================================================
# 4. 负例：variants 规则
# ============================================================
class TestVariantRules(_TmpDirTestBase):
    """variants 必须是非空列表，元素非空且必须是一句。"""

    def test_empty_variants_list(self):
        """variants 为空列表 → SourceError，消息含 key 名。"""
        phrases = {"phrases": [{"key": "empty_v", "variants": []}]}
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("empty_v", str(ctx.exception))

    def test_empty_variant_element(self):
        """variant 元素为空字符串 → SourceError，消息含 key 名。"""
        phrases = {"phrases": [{"key": "empty_e", "variants": ["您好", ""]}]}
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("empty_e", str(ctx.exception))

    def test_variant_with_two_terminators(self):
        """variant 含两个句末标点 → SourceError，且消息含「拆成多个 key」。"""
        phrases = {
            "phrases": [
                {
                    "key": "too_long",
                    "variants": ["您好。请问需要什么帮助？"],
                }
            ]
        }
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        msg = str(ctx.exception)
        self.assertIn("too_long", msg)
        self.assertIn("拆成多个 key", msg)

    def test_variant_with_three_terminators(self):
        """variant 含三个句末标点 → SourceError（断言确实违规的输入）。"""
        phrases = {
            "phrases": [
                {"key": "very_long", "variants": ["第一句。第二句！第三句？"]}
            ]
        }
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("very_long", str(ctx.exception))

    def test_variant_with_mixed_ascii_terminators(self):
        """中英混合句末标点（! + ?）同样算两句 → SourceError。"""
        phrases = {"phrases": [{"key": "mix", "variants": ["真的吗?!"]}]}
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("mix", str(ctx.exception))


# ============================================================
# 5. 负例：rates 规则（非法档位不得回落）
# ============================================================
class TestRateRules(_TmpDirTestBase):
    """rates 只能取 VALID_RATES 里的值，未知档位必须报错且不得回落。"""

    def test_invalid_rate_in_phrase(self):
        """phrase 级 rates 含非法档位 → SourceError，消息含该档位值。"""
        phrases = {
            "phrases": [{"key": "bad_rate", "variants": ["您好"], "rates": ["turbo"]}]
        }
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        msg = str(ctx.exception)
        self.assertIn("turbo", msg)

    def test_invalid_rate_in_pack(self):
        """pack.json.rates 含非法档位 → SourceError，消息含该档位值。"""
        pack = _default_pack(rates=["normal", "whisper"])
        _make_source(self.tmp_dir, pack=pack)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("whisper", str(ctx.exception))

    def test_non_list_rates(self):
        """rates 不是列表 → SourceError。"""
        pack = _default_pack(rates="normal")
        _make_source(self.tmp_dir, pack=pack)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("rates", str(ctx.exception))

    def test_empty_rates_list(self):
        """rates 为空列表 → SourceError。"""
        pack = _default_pack(rates=[])
        _make_source(self.tmp_dir, pack=pack)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        self.assertIn("rates", str(ctx.exception))


# ============================================================
# 6. 负例：未知字段拒绝（docs/08 §8.5 欠账 1）
# ============================================================
class TestUnknownFields(_TmpDirTestBase):
    """未知字段必须抛 SourceError（typo 静默丢弃 = 静默降级），只增不减。"""

    def test_pack_unknown_field(self):
        """pack.json 塞 duplx → SourceError，消息含 'duplx' 与位置 'pack.json'。"""
        pack = _default_pack(duplx={})
        _make_source(self.tmp_dir, pack=pack)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        msg = str(ctx.exception)
        self.assertIn("duplx", msg)
        self.assertIn("pack.json", msg)
        self.assertIn("未知字段", msg)

    def test_phrases_unknown_field(self):
        """phrases[0] 塞 varient → SourceError，消息含 'varient' 与位置 'phrases[0]'。"""
        phrases = {
            "phrases": [
                {"key": "greeting", "variants": ["您好"], "varient": []},
            ]
        }
        _make_source(self.tmp_dir, phrases=phrases)
        with self.assertRaises(SourceError) as ctx:
            load_source(self.tmp_dir)
        msg = str(ctx.exception)
        self.assertIn("varient", msg)
        self.assertIn("phrases[0]", msg)
        self.assertIn("未知字段", msg)

    def test_pack_business_fields_locale_and_duplex_allowed(self):
        """locale + duplex 是业务侧合法字段（packs/repair 的活体回归用例），必须通过。"""
        pack = _default_pack(
            locale="zh-CN",
            duplex={
                "patience_ms": 900,
                "rate_band": 0.15,
                "backchannel": "on",
                "barge_in": "allow",
                "silence_pad_ms": 200,
                "slot_pad_ms": 80,
                "fade_ms": 5,
            },
        )
        _make_source(self.tmp_dir, pack=pack)
        # 只校验「存在性允许」，内部形状由 runtime.DuplexParams 负责——此处不应报错
        source = load_source(self.tmp_dir)
        self.assertEqual(source.pack_id, "repair")


if __name__ == "__main__":
    unittest.main()
