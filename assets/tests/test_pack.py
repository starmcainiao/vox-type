"""
assets.tests.test_pack — pack 模块的正例 + 负例测试

覆盖范围：
  - load_pack 正例：合法包能正常装载
  - load_pack 负例：坏包必拒（缺 manifest / 缺必填字段 / 指纹不匹配 / 文件不存在）
  - lookup 语义：指纹不匹配 → 返回 None（不抛错、不过期）
  - validate_pack：问题列表准确性
  - 用户数据黑名单：manifest / 资产条目中含 slots/phone/user_utterance → 拒绝
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from assets.fingerprint import fingerprint
from assets.pack import (
    AssetPack,
    AssetEntry,
    AssetPackError,
    load_pack,
    lookup,
    validate_pack,
)


# ---------------------------------------------------------------------------
# 辅助：自造合法资产包（用 tempfile，不留样例数据在仓库中）
# ---------------------------------------------------------------------------
def _create_valid_pack(tmp_dir: Path) -> None:
    """在 tmp_dir 下创建一个合法的资产包（含 manifest.json + 音频文件）。

    包内容：
      - pack_id: test-pack
      - 1 条资产：key=greeting, part_index=0, rate_key=normal, variant=0
    """
    text = "你好，欢迎使用语音系统"
    voice = "zh-CN-Xiaoxiao"
    rate_key = "normal"
    model_version = "v1.0"

    # 创建音频目录并写入假音频文件
    audio_dir = tmp_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_file = audio_dir / "greeting.wav"
    audio_file.write_bytes(b"RIFF fake wav data")

    # 计算指纹
    fp = fingerprint(text=text, voice=voice, rate_value=rate_key, model_version=model_version)

    manifest = {
        "pack_id": "test-pack",
        "pack_version": "1.0.0",
        "protocol_version": "1.0",
        "ruleset_version": "1.0",
        "voice": voice,
        "model_version": model_version,
        "created_at": "2026-01-01T00:00:00Z",
        "assets": [
            {
                "key": "greeting",
                "part_index": 0,
                "rate_key": rate_key,
                "variant": 0,
                "text": text,
                "fingerprint": fp,
                "path": "audio/greeting.wav",
                "duration_ms": 2000,
            }
        ],
    }

    manifest_path = tmp_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _create_manifest(tmp_dir: Path, manifest_dict: dict) -> None:
    """在 tmp_dir 下写入指定的 manifest.json。"""
    manifest_path = tmp_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_dict, f, ensure_ascii=False, indent=2)


# ============================================================
# 1. load_pack — 正例
# ============================================================
class TestLoadPackPositive(unittest.TestCase):
    """合法资产包必须能正常装载。"""

    def test_load_valid_pack(self):
        """装载合法包，字段值正确。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            pack = load_pack(root)

            self.assertEqual(pack.pack_id, "test-pack")
            self.assertEqual(pack.pack_version, "1.0.0")
            self.assertEqual(pack.protocol_version, "1.0")
            self.assertEqual(pack.ruleset_version, "1.0")
            self.assertEqual(pack.voice, "zh-CN-Xiaoxiao")
            self.assertEqual(pack.model_version, "v1.0")
            self.assertEqual(len(pack.assets), 1)

            entry = pack.assets[0]
            self.assertEqual(entry.key, "greeting")
            self.assertEqual(entry.part_index, 0)
            self.assertEqual(entry.rate_key, "normal")
            self.assertEqual(entry.variant, 0)
            self.assertEqual(entry.text, "你好，欢迎使用语音系统")
            self.assertEqual(entry.duration_ms, 2000)

    def test_load_pack_sets_root(self):
        """装载后 pack.root 指向包根目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            pack = load_pack(root)
            self.assertEqual(pack.root, root)


# ============================================================
# 2. load_pack — 负例（坏包必拒）
# ============================================================
class TestLoadPackNegative(unittest.TestCase):
    """坏包必须被 load_pack 拒绝，抛出 AssetPackError。"""

    def test_missing_manifest(self):
        """负例 1：缺 manifest.json → AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("manifest.json", str(ctx.exception))

    def test_invalid_json_syntax(self):
        """负例 2：manifest.json 语法错误 → AssetPackError 且消息含 JSON 错误。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text("{invalid json")
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("JSON", str(ctx.exception))

    def test_missing_required_field(self):
        """负例 3：manifest 缺必填字段 → AssetPackError 且消息含缺失字段名。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_manifest(root, {
                "pack_id": "test",
                # 缺 pack_version, protocol_version, ...
            })
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("pack_version", str(ctx.exception))

    def test_fingerprint_mismatch(self):
        """负例 4：资产条目指纹与重算不符 → AssetPackError 且消息含指纹值。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 创建合法包后篡改指纹
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["fingerprint"] = "0000000000000000"  # 篡改指纹
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("指纹不匹配", str(ctx.exception))

    def test_missing_audio_file(self):
        """负例 5：资产 path 指向不存在的文件 → AssetPackError 且消息含路径。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            # 删除音频文件
            (root / "audio" / "greeting.wav").unlink()
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("音频文件不存在", str(ctx.exception))

    def test_assets_not_list(self):
        """负例 6：assets 不是列表 → AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_manifest(root, {
                "pack_id": "test",
                "pack_version": "1.0",
                "protocol_version": "1.0",
                "ruleset_version": "1.0",
                "voice": "v",
                "model_version": "m",
                "created_at": "2026-01-01T00:00:00Z",
                "assets": "not a list",
            })
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("assets", str(ctx.exception))

    def test_empty_assets(self):
        """负例 7：assets 为空列表 → AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_manifest(root, {
                "pack_id": "test",
                "pack_version": "1.0",
                "protocol_version": "1.0",
                "ruleset_version": "1.0",
                "voice": "v",
                "model_version": "m",
                "created_at": "2026-01-01T00:00:00Z",
                "assets": [],
            })
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("assets", str(ctx.exception))

    def test_invalid_rate_key(self):
        """负例 8：rate_key 非法值 → AssetPackError 且消息含非法值。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            # 篡改 rate_key
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["rate_key"] = "turbo"
            # 重算指纹以通过指纹校验
            manifest["assets"][0]["fingerprint"] = fingerprint(
                text=manifest["assets"][0]["text"],
                voice=manifest["voice"],
                rate_value="turbo",
                model_version=manifest["model_version"],
            )
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("turbo", str(ctx.exception))

    def test_non_dict_manifest(self):
        """负例 9：manifest.json 内容不是字典 → AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text('"just a string"')
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("JSON 对象", str(ctx.exception))


# ============================================================
# 3. lookup — 语义测试
# ============================================================
class TestLookup(unittest.TestCase):
    """lookup 语义：指纹不匹配 / 文件不存在 → 返回 None。"""

    def _get_pack(self) -> tuple:
        """创建合法包并返回 (pack, entry)。"""
        tmp = tempfile.mkdtemp()
        # T04d：mkdtemp 必须必然清理（unittest 正式机制，覆盖断言失败路径）
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = Path(tmp)
        _create_valid_pack(root)
        pack = load_pack(root)
        entry = pack.assets[0]
        return pack, entry

    def test_lookup_match(self):
        """正例：所有条件匹配 → 返回 AssetEntry。"""
        pack, entry = self._get_pack()
        result = lookup(
            pack=pack,
            key="greeting",
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="你好，欢迎使用语音系统",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.key, "greeting")

    def test_lookup_wrong_key(self):
        """key 不匹配 → 返回 None。"""
        pack, _ = self._get_pack()
        result = lookup(
            pack=pack,
            key="nonexistent",  # key 不匹配
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="你好，欢迎使用语音系统",
        )
        self.assertIsNone(result)

    def test_lookup_fingerprint_mismatch(self):
        """指纹不匹配 → 返回 None（不抛错、不过期）。"""
        pack, _ = self._get_pack()
        result = lookup(
            pack=pack,
            key="greeting",
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="已修改的文本",  # 文本变了 → 指纹不匹配
        )
        self.assertIsNone(result)

    def test_lookup_file_not_exists(self):
        """音频文件不存在 → 返回 None。"""
        pack, entry = self._get_pack()
        # 删除音频文件
        audio_path = pack.root / entry.path
        audio_path.unlink()
        result = lookup(
            pack=pack,
            key="greeting",
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="你好，欢迎使用语音系统",
        )
        self.assertIsNone(result)


# ============================================================
# 4. validate_pack — 正例
# ============================================================
class TestValidatePackPositive(unittest.TestCase):
    """合法包 validate_pack 返回空列表。"""

    def test_valid_pack_returns_empty_list(self):
        """合法包 → 空问题列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            issues = validate_pack(root)
            self.assertEqual(issues, [])


# ============================================================
# 5. validate_pack — 负例
# ============================================================
class TestValidatePackNegative(unittest.TestCase):
    """坏包 validate_pack 返回非空问题列表。"""

    def test_missing_manifest(self):
        """缺 manifest.json → 返回含问题的列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issues = validate_pack(root)
            self.assertNotEqual(issues, [])
            self.assertIn("manifest.json", issues[0])

    def test_invalid_json(self):
        """manifest.json 不是合法 JSON → 返回非空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text("{bad json")
            issues = validate_pack(root)
            self.assertNotEqual(issues, [])
            self.assertIn("JSON", issues[0])

    def test_fingerprint_mismatch_detected(self):
        """指纹不匹配 → 问题列表中包含指纹不匹配信息。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            # 篡改指纹
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["fingerprint"] = "0000000000000000"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            issues = validate_pack(root)
            self.assertTrue(
                any("指纹不匹配" in issue for issue in issues),
                f"validate_pack 未检测到指纹不匹配: {issues}",
            )

    def test_missing_audio_detected(self):
        """音频文件不存在 → 问题列表中包含文件不存在信息。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            (root / "audio" / "greeting.wav").unlink()
            issues = validate_pack(root)
            self.assertTrue(
                any("音频文件不存在" in issue for issue in issues),
                f"validate_pack 未检测到音频文件不存在: {issues}",
            )


# ============================================================
# 6. 用户数据黑名单检查
# ============================================================
class TestUserDataBlacklist(unittest.TestCase):
    """包内不得含用户数据字段（slots/phone/user_utterance）。

    load_pack 和 validate_pack 都必须检查并拒绝。
    """

    def test_load_pack_rejects_slots_in_manifest(self):
        """load_pack：manifest 中含 slots 字段 → AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            # 在 manifest 中注入 slots 字段
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["slots"] = {"name": "小明"}
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("slots", str(ctx.exception))

    def test_load_pack_rejects_phone_in_manifest(self):
        """load_pack：manifest 中含 phone 字段 → AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["phone"] = "13800138000"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("phone", str(ctx.exception))

    def test_load_pack_rejects_user_utterance_in_manifest(self):
        """load_pack：manifest 中含 user_utterance 字段 → AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["user_utterance"] = "用户说的话"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("user_utterance", str(ctx.exception))

    def test_load_pack_rejects_slots_in_asset_entry(self):
        """load_pack：资产条目中含 slots 字段 → AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["slots"] = {"name": "小明"}
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("slots", str(ctx.exception))

    def test_validate_pack_rejects_slots_in_manifest(self):
        """validate_pack：manifest 中含 slots 字段 → 问题列表非空。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["slots"] = {"name": "小明"}
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            issues = validate_pack(root)
            self.assertTrue(
                any("slots" in issue for issue in issues),
                f"validate_pack 未检测到 slots 字段: {issues}",
            )

    def test_validate_pack_rejects_phone_in_asset(self):
        """validate_pack：资产条目中含 phone 字段 → 问题列表非空。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["phone"] = "13800138000"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            issues = validate_pack(root)
            self.assertTrue(
                any("phone" in issue for issue in issues),
                f"validate_pack 未检测到 phone 字段: {issues}",
            )


# ============================================================
# 7. 产品 API 一致性（反空转条款）
# ============================================================
class TestAPIConsistency(unittest.TestCase):
    """确保测试调用的是产品 API，而非测试内重写逻辑。"""

    def test_fingerprint_is_imported_from_assets(self):
        """fingerprint 函数是从 assets.fingerprint 导入的产品 API。"""
        from assets import fingerprint as api_fingerprint
        from assets.fingerprint import fingerprint as core_fingerprint
        self.assertIs(api_fingerprint, core_fingerprint)

    def test_load_pack_is_imported_from_assets(self):
        """load_pack 函数是从 assets.pack 导入的产品 API。"""
        from assets import load_pack as api_load_pack
        from assets.pack import load_pack as core_load_pack
        self.assertIs(api_load_pack, core_load_pack)

    def test_lookup_is_imported_from_assets(self):
        """lookup 函数是从 assets.pack 导入的产品 API。"""
        from assets import lookup as api_lookup
        from assets.pack import lookup as core_lookup
        self.assertIs(api_lookup, core_lookup)

    def test_validate_pack_is_imported_from_assets(self):
        """validate_pack 函数是从 assets.pack 导入的产品 API。"""
        from assets import validate_pack as api_validate
        from assets.pack import validate_pack as core_validate
        self.assertIs(api_validate, core_validate)


# ============================================================
# 8. 口径一致性负例（条目级 voice/model_version 不再被允许）
# ============================================================
class TestFingerprintCaliberConsistency(unittest.TestCase):
    """条目自带不同 voice 时，重算指纹与包内指纹不符 → 拒绝（fail-closed）。"""

    def test_entry_level_voice_rejected_by_load_pack(self):
        """load_pack：条目自带不同 voice → AssetPackError 且消息含指纹不匹配。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            # 篡改：给条目注入一个与 manifest 不同的 voice
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            different_voice = "zh-CN-Yunxi"
            manifest["assets"][0]["voice"] = different_voice
            # 重算指纹——用条目级 voice（模拟编译器按条目级 voice 出包）
            fp = fingerprint(
                text=manifest["assets"][0]["text"],
                voice=different_voice,
                rate_value=manifest["assets"][0]["rate_key"],
                model_version=manifest["model_version"],
            )
            manifest["assets"][0]["fingerprint"] = fp
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("指纹不匹配", str(ctx.exception))

    def test_entry_level_voice_rejected_by_validate_pack(self):
        """validate_pack：条目自带不同 voice → 问题列表非空且含指纹不匹配。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            different_voice = "zh-CN-Yunxi"
            manifest["assets"][0]["voice"] = different_voice
            fp = fingerprint(
                text=manifest["assets"][0]["text"],
                voice=different_voice,
                rate_value=manifest["assets"][0]["rate_key"],
                model_version=manifest["model_version"],
            )
            manifest["assets"][0]["fingerprint"] = fp
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            issues = validate_pack(root)
            self.assertTrue(
                any("指纹不匹配" in issue for issue in issues),
                f"validate_pack 未检测到条目级 voice 导致的指纹不匹配: {issues}",
            )

    def test_entry_level_model_version_rejected_by_load_pack(self):
        """load_pack：条目自带不同 model_version → AssetPackError 且消息含指纹不匹配。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            different_version = "v2.0"
            manifest["assets"][0]["model_version"] = different_version
            fp = fingerprint(
                text=manifest["assets"][0]["text"],
                voice=manifest["voice"],
                rate_value=manifest["assets"][0]["rate_key"],
                model_version=different_version,
            )
            manifest["assets"][0]["fingerprint"] = fp
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            self.assertIn("指纹不匹配", str(ctx.exception))


# ============================================================
# 9. 方法可用性正例（AssetPack.lookup 方法）
# ============================================================
class TestAssetPackMethodLookup(unittest.TestCase):
    """AssetPack.lookup 方法与模块级 lookup 行为一致。"""

    def _get_pack(self) -> tuple:
        """创建合法包并返回 (pack, entry)。"""
        tmp = tempfile.mkdtemp()
        # T04d：mkdtemp 必须必然清理（unittest 正式机制，覆盖断言失败路径）
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = Path(tmp)
        _create_valid_pack(root)
        pack = load_pack(root)
        entry = pack.assets[0]
        return pack, entry

    def test_method_lookup_hit(self):
        """正例：pack.lookup(...) 命中 → 返回 AssetEntry。"""
        pack, _ = self._get_pack()
        result = pack.lookup(
            key="greeting",
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="你好，欢迎使用语音系统",
        )
        self.assertIsNotNone(result)
        self.assertIsInstance(result, AssetEntry)
        self.assertEqual(result.key, "greeting")

    def test_method_lookup_text_changed(self):
        """expected_text 改一个字 → pack.lookup 返回 None。"""
        pack, _ = self._get_pack()
        result = pack.lookup(
            key="greeting",
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="你好，欢迎使用语音系统！",  # 多了一个感叹号
        )
        self.assertIsNone(result)

    def test_method_lookup_audio_deleted(self):
        """删掉音频文件 → pack.lookup 返回 None。"""
        pack, entry = self._get_pack()
        audio_path = pack.root / entry.path
        audio_path.unlink()
        result = pack.lookup(
            key="greeting",
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="你好，欢迎使用语音系统",
        )
        self.assertIsNone(result)

    def test_method_matches_module_level_lookup(self):
        """AssetPack.lookup 与 assets.pack.lookup 行为一致。"""
        pack, _ = self._get_pack()
        args = dict(
            key="greeting",
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="你好，欢迎使用语音系统",
        )
        method_result = pack.lookup(**args)
        module_result = lookup(pack, **args)
        self.assertEqual(method_result, module_result)

    def test_method_matches_module_level_lookup_miss(self):
        """未命中时两者都返回 None。"""
        pack, _ = self._get_pack()
        args = dict(
            key="nonexistent",
            part_index=0,
            rate_key="normal",
            variant=0,
            expected_text="你好，欢迎使用语音系统",
        )
        method_result = pack.lookup(**args)
        module_result = lookup(pack, **args)
        self.assertIsNone(method_result)
        self.assertIsNone(module_result)


# ============================================================
# 10. 缺陷 1：重复身份条目校验（T04c）
# ============================================================
class TestDuplicateIdentity(unittest.TestCase):
    """重复 (key, part_index, rate_key, variant) 身份必须被拒绝。

    load_pack → AssetPackError（消息含冲突 key 与坐标）
    validate_pack → 返回非空问题列表
    """

    def _create_duplicate_pack(self, root: Path) -> None:
        """在 root 下创建含两条同一身份条目的合法包。"""
        text_old = "旧话术"
        text_new = "新话术"
        voice = "Tingting"
        rate_key = "normal"
        model_version = "macos-say"

        audio_dir = root / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        for name in ("old", "new"):
            (audio_dir / f"{name}.wav").write_bytes(b"\x00\x00" * 80)

        def entry(t, p):
            return {
                "key": "greet", "part_index": 0, "rate_key": rate_key,
                "variant": 0, "text": t,
                "fingerprint": fingerprint(t, voice, rate_key, model_version),
                "path": p, "duration_ms": 100,
            }

        manifest = {
            "pack_id": "p", "pack_version": "1",
            "protocol_version": "0.1", "ruleset_version": "v1",
            "voice": voice, "model_version": model_version,
            "created_at": "t",
            "assets": [
                entry(text_old, "audio/old.wav"),
                entry(text_new, "audio/new.wav"),
            ],
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

    def test_load_pack_rejects_duplicate_identity(self):
        """load_pack 遇到重复身份 → AssetPackError，消息含冲突 key 与坐标。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_duplicate_pack(root)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            msg = str(ctx.exception)
            self.assertIn("重复", msg)
            self.assertIn("greet", msg)

    def test_validate_pack_detects_duplicate_identity(self):
        """validate_pack 检测重复身份 → 问题列表非空且含"重复"。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_duplicate_pack(root)
            issues = validate_pack(root)
            self.assertTrue(
                any("重复" in issue for issue in issues),
                f"validate_pack 未检测到重复身份: {issues}",
            )

    def test_load_pack_accepts_unique_identities(self):
        """身份唯一的合法包 → load_pack 正常装载。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            pack = load_pack(root)
            self.assertEqual(len(pack.assets), 1)
            self.assertEqual(pack.assets[0].key, "greeting")


# ============================================================
# 11. 缺陷 2：validate_pack 对非法输入返回问题列表而非抛异常（T04c）
# ============================================================
class TestValidatePackRobustness(unittest.TestCase):
    """validate_pack 对非法输入必须返回问题列表，不得抛异常。"""

    def test_validate_pack_returns_list_for_non_utf8_manifest(self):
        """非 UTF-8 编码的 manifest → validate_pack 返回问题列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_bytes(
                '{"pack_id":"中文"}'.encode("gb18030")
            )
            issues = validate_pack(root)
            self.assertIsInstance(issues, list)
            self.assertTrue(len(issues) > 0)
            self.assertTrue(
                any("编码" in issue or "UTF" in issue for issue in issues),
                f"validate_pack 未报告编码错误: {issues}",
            )

    def test_load_pack_raises_for_non_utf8_manifest(self):
        """非 UTF-8 编码的 manifest → load_pack 抛 AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_bytes(
                '{"pack_id":"中文"}'.encode("gb18030")
            )
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            msg = str(ctx.exception)
            self.assertTrue(
                "编码" in msg or "UTF" in msg,
                f"AssetPackError 消息未提及编码问题: {msg}",
            )

    def test_validate_pack_returns_list_for_non_string_path(self):
        """资产条目 path 非字符串 → validate_pack 返回问题列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["path"] = 12345  # 非字符串
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            issues = validate_pack(root)
            self.assertIsInstance(issues, list)
            self.assertTrue(len(issues) > 0)
            self.assertTrue(
                any("path" in issue.lower() or "字符串" in issue for issue in issues),
                f"validate_pack 未报告 path 类型错误: {issues}",
            )

    def test_load_pack_raises_for_non_string_path(self):
        """资产条目 path 非字符串 → load_pack 抛 AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["path"] = 12345  # 非字符串
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            msg = str(ctx.exception)
            self.assertIn("path", msg)
            self.assertIn("字符串", msg)


# ============================================================
# 12. 缺陷 3：path 必须在包根内（T04c）
# ============================================================
class TestAssetPathBoundary(unittest.TestCase):
    """path 必须是相对路径且规范化后仍落在包根内。"""

    def test_load_pack_rejects_absolute_path(self):
        """path 为绝对路径 → load_pack 抛 AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["path"] = "/etc/hosts"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            msg = str(ctx.exception)
            self.assertIn("相对路径", msg)
            self.assertIn("/etc/hosts", msg)

    def test_validate_pack_rejects_absolute_path(self):
        """path 为绝对路径 → validate_pack 返回非空问题列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["path"] = "/etc/hosts"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            issues = validate_pack(root)
            self.assertTrue(
                any("相对路径" in issue or "绝对路径" in issue for issue in issues),
                f"validate_pack 未检测到绝对路径: {issues}",
            )

    def test_load_pack_rejects_escape_path(self):
        """path 含 .. 逃逸 → load_pack 抛 AssetPackError。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["path"] = "../../../etc/hosts"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with self.assertRaises(AssetPackError) as ctx:
                load_pack(root)
            msg = str(ctx.exception)
            self.assertIn("逃逸", msg)

    def test_validate_pack_rejects_escape_path(self):
        """path 含 .. 逃逸 → validate_pack 返回非空问题列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            manifest_path = root / "manifest.json"
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["assets"][0]["path"] = "../../../etc/hosts"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            issues = validate_pack(root)
            self.assertTrue(
                any("逃逸" in issue for issue in issues),
                f"validate_pack 未检测到路径逃逸: {issues}",
            )

    def test_load_pack_accepts_valid_relative_path(self):
        """正常相对路径（如 audio/a.wav）→ load_pack 正常装载。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            pack = load_pack(root)
            self.assertEqual(pack.assets[0].path, "audio/greeting.wav")

    def test_validate_pack_accepts_valid_relative_path(self):
        """正常相对路径 → validate_pack 返回空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _create_valid_pack(root)
            issues = validate_pack(root)
            self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
