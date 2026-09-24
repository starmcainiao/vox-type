"""
assets.tests.test_ttl — TTL / invalid_at 失效判定（T18）

覆盖范围：
  1. is_expired 纯函数：无 invalid_at 恒 False（旧包永不过期）；`now >= invalid_at`
     → True；恰好等于失效时刻也算过期；`now` 一律注入（datetime 与 ISO 字符串两种形态）
  2. load_pack：invalid_at 落进 AssetEntry（UTC datetime）；ttl 原值留痕；
     坏值（非字符串 / 不可解析 / 空串）→ AssetPackError 且消息含实际值；
     ttl 非正整数（含 0 / 负数 / 字符串 / 布尔）→ AssetPackError 且消息含实际值
  3. **保险丝在资产层**：lookup(now=过期后) 返回 None；lookup(now=失效前) 命中；
     模块级 lookup(now=) 与 AssetPack.lookup(now=) 同形
  4. 向后兼容：无这两个字段的旧包，lookup() 默认参数与传 now= 都照常命中
  5. validate_pack：坏失效字段进问题列表（不抛错、口径与 load_pack 一致）

纪律：时间断言**一律注入 now**，不用 sleep——评测要能重放。
"""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from assets.fingerprint import fingerprint
from assets.pack import (
    AssetPack,
    AssetEntry,
    AssetPackError,
    is_expired,
    load_pack,
    lookup,
    parse_invalid_at,
    validate_pack,
)


# ---------------------------------------------------------------------------
# 辅助：手搓资产包（与 test_pack.py 同一套路，独立一份避免耦合）
# ---------------------------------------------------------------------------
VOICE = "zh-CN-Xiaoxiao"
MODEL = "v1.0"
TEXT = "你好，欢迎使用语音系统"

# 固定的判定基准时刻：所有用例围绕它构造 invalid_at，绝不吃墙钟
BASE = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
BASE_STR = "2026-09-22T12:00:00+00:00"
# 失效时刻：基准后 1 小时
EXPIRES_STR = "2026-09-22T13:00:00Z"          # Z 结尾，覆盖归一化路径
EXPIRES_BEFORE = "2026-09-22T12:59:00+00:00"
EXPIRES_AT = "2026-09-22T13:00:00+00:00"
EXPIRES_AFTER = "2026-09-22T13:01:00+00:00"


def _make_pack(tmp_dir: Path, assets_overrides=None, manifest_overrides=None) -> Path:
    """在 tmp_dir 写一个最小合法资产包，返回包根。

    assets_overrides:  附加到资产条目上的字段（如 {"invalid_at": ..., "ttl": ...}）
    manifest_overrides: 附加到 manifest 顶层的字段
    """
    root = Path(tmp_dir)
    audio_dir = root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_file = audio_dir / "greeting.wav"
    audio_file.write_bytes(b"RIFF fake wav data")

    fp = fingerprint(text=TEXT, voice=VOICE, rate_value="normal", model_version=MODEL)
    asset = {
        "key": "greeting",
        "part_index": 0,
        "rate_key": "normal",
        "variant": 0,
        "text": TEXT,
        "fingerprint": fp,
        "path": "audio/greeting.wav",
        "duration_ms": 2000,
    }
    if assets_overrides:
        asset.update(assets_overrides)

    manifest = {
        "pack_id": "test-pack",
        "pack_version": "1.0.0",
        "protocol_version": "1.0",
        "ruleset_version": "1.0",
        "voice": VOICE,
        "model_version": MODEL,
        "created_at": BASE_STR,
        "assets": [asset],
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


class _Tmp(unittest.TestCase):
    """提供临时目录。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="assets_ttl_")
        self.tmp = Path(self._tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ============================================================
# 1. is_expired 纯函数
# ============================================================
def _entry(invalid_at=None, ttl=None) -> AssetEntry:
    """构造一条最小资产条目（判定只看 invalid_at）。"""
    return AssetEntry(
        key="greeting", part_index=0, rate_key="normal", variant=0,
        text=TEXT, fingerprint="0" * 16, path="audio/greeting.wav",
        duration_ms=100, ttl=ttl, invalid_at=invalid_at,
    )


class TestIsExpired(_Tmp):
    """is_expired 是唯一判据——纯函数，可直接单测，不含任何 I/O。"""

    def test_entry_without_invalid_at_never_expires(self):
        """无 invalid_at = 永不过期（旧包/旧源的唯一锚点）。"""
        self.assertFalse(is_expired(_entry()))
        # 无论注入什么时刻都不过期
        self.assertFalse(is_expired(_entry(), BASE))
        self.assertFalse(is_expired(_entry(), BASE + timedelta(days=3650)))
        self.assertFalse(is_expired(_entry(), now=None))

    def test_ttl_alone_does_not_expire(self):
        """ttl 只是审计留痕——判定只看 invalid_at（折算已由 compiler 完成）。"""
        self.assertFalse(is_expired(_entry(ttl=3600), BASE))
        self.assertFalse(is_expired(_entry(ttl=3600), BASE + timedelta(days=3650)))

    def test_expired_when_now_after_invalid_at(self):
        expires = datetime(2026, 9, 22, 13, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(is_expired(_entry(expires), BASE))
        self.assertTrue(is_expired(_entry(expires), BASE + timedelta(hours=1, minutes=1)))

    def test_expires_exactly_at_invalid_at(self):
        """语义冻结：now >= invalid_at → 过期（恰好等于也算过期）。"""
        expires = datetime(2026, 9, 22, 13, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_expired(_entry(expires), expires))

    def test_now_injectable_as_datetime_and_iso_string(self):
        """now 支持 datetime 与 ISO 8601 字符串两种形态（评测重放要求）。"""
        expires = datetime(2026, 9, 22, 13, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(is_expired(_entry(expires), EXPIRES_BEFORE))
        self.assertTrue(is_expired(_entry(expires), EXPIRES_AFTER))
        # datetime 形态给出同样的结论
        self.assertFalse(is_expired(_entry(expires), BASE))
        self.assertTrue(is_expired(_entry(expires), BASE + timedelta(hours=2)))

    def test_naive_string_treated_as_utc(self):
        """naive 字符串按 UTC 解释（与 manifest 时间戳同口径）。"""
        expires = datetime(2026, 9, 22, 13, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_expired(_entry(expires), "2026-09-22T13:30:00"))

    def test_parse_invalid_at_rejects_bad_values(self):
        """非法 invalid_at 必须抛 AssetPackError 且消息含实际值。"""
        for bad in ("not-a-time", "", "   ", 12345, None, ["2026-01-01"]):
            with self.subTest(bad=bad):
                with self.assertRaises(AssetPackError) as ctx:
                    parse_invalid_at(bad)
                self.assertIn("invalid_at", str(ctx.exception))
                self.assertIn(repr(bad), str(ctx.exception))

    def test_parse_invalid_at_accepts_z_suffix(self):
        """Z 结尾（UTC）可解析，且与 +00:00 同值。"""
        self.assertEqual(
            parse_invalid_at("2026-09-22T13:00:00Z"),
            parse_invalid_at("2026-09-22T13:00:00+00:00"),
        )


# ============================================================
# 2. load_pack：字段落盘 + 校验
# ============================================================
class TestLoadPackTtl(_Tmp):
    """manifest 里的失效字段必须被正确装载并校验。"""

    def test_invalid_at_loaded_as_utc_datetime(self):
        root = _make_pack(self.tmp, {"invalid_at": EXPIRES_STR})
        pack = load_pack(root)
        entry = pack.assets[0]
        self.assertIsInstance(entry.invalid_at, datetime)
        self.assertEqual(
            entry.invalid_at,
            datetime(2026, 9, 22, 13, 0, 0, tzinfo=timezone.utc),
        )

    def test_ttl_preserved_for_audit(self):
        root = _make_pack(
            self.tmp, {"ttl": 3600, "invalid_at": EXPIRES_STR}
        )
        self.assertEqual(load_pack(root).assets[0].ttl, 3600)

    def test_legacy_pack_without_fields_loads_and_never_expires(self):
        """向后兼容：无这两个字段的旧包照常装载，且永不判定为过期。"""
        pack = load_pack(_make_pack(self.tmp))
        entry = pack.assets[0]
        self.assertIsNone(entry.invalid_at)
        self.assertIsNone(entry.ttl)
        self.assertFalse(is_expired(entry, BASE))

    def test_bad_invalid_at_raises_with_actual_value(self):
        """非法 invalid_at → AssetPackError，消息含字段名与实际值。"""
        for bad in ("not-a-time", "", 12345):
            with self.subTest(bad=bad):
                root = _make_pack(self.tmp, {"invalid_at": bad})
                with self.assertRaises(AssetPackError) as ctx:
                    load_pack(root)
                self.assertIn("invalid_at", str(ctx.exception))
                self.assertIn(repr(bad), str(ctx.exception))

    def test_bad_ttl_raises_with_actual_value(self):
        """ttl 必须是正整数：0 / 负数 / 字符串 / 布尔全部拒绝。"""
        for bad in (0, -1, 1.5, "3600", True, None, []):
            with self.subTest(bad=bad):
                root = _make_pack(self.tmp, {"ttl": bad})
                with self.assertRaises(AssetPackError) as ctx:
                    load_pack(root)
                self.assertIn("ttl", str(ctx.exception))
                self.assertIn(repr(bad), str(ctx.exception))


class TestValidatePackTtl(_Tmp):
    """validate_pack 的口径必须与 load_pack 一致（共用同一校验函数）。"""

    def test_bad_expiry_fields_reported_not_raised(self):
        root = _make_pack(
            self.tmp, {"invalid_at": "not-a-time", "ttl": -1}
        )
        issues = validate_pack(root)
        self.assertTrue(any("invalid_at" in i for i in issues), issues)
        self.assertTrue(any("ttl" in i for i in issues), issues)

    def test_good_expiry_fields_no_issues(self):
        root = _make_pack(
            self.tmp, {"ttl": 3600, "invalid_at": EXPIRES_STR}
        )
        self.assertEqual(validate_pack(root), [])

    def test_legacy_pack_no_issues(self):
        self.assertEqual(validate_pack(_make_pack(self.tmp)), [])


# ============================================================
# 3. 保险丝在资产层：lookup(now=)
# ============================================================
class TestLookupExpiryFuse(_Tmp):
    """过期条目对任何消费方（含不经 runtime 的直接调用）都必须取不出来。"""

    def test_lookup_returns_none_after_expiry(self):
        root = _make_pack(self.tmp, {"invalid_at": EXPIRES_STR})
        pack = load_pack(root)
        # 注入失效前时刻取条目：**不能走默认 now（=墙钟）**——写死的失效时刻一旦被墙钟
        # 越过，这条「失效前能取到」的断言就必然转红（日历炸弹，2026-09-23 CI 三档全红即由此）。
        entry = self._find(pack, now=EXPIRES_BEFORE)
        self.assertIsNotNone(entry)          # 失效前能取到
        self.assertIsNone(self._find(pack, now=EXPIRES_AFTER))

    def test_lookup_hit_before_expiry(self):
        root = _make_pack(self.tmp, {"invalid_at": EXPIRES_STR})
        pack = load_pack(root)
        self.assertIsNotNone(self._find(pack, now=EXPIRES_BEFORE))

    def test_lookup_boundary_at_exact_expiry_returns_none(self):
        """now == invalid_at → 已过期 → None（语义与 is_expired 一致）。"""
        root = _make_pack(self.tmp, {"invalid_at": EXPIRES_STR})
        pack = load_pack(root)
        self.assertIsNone(self._find(pack, now=EXPIRES_AT))

    def test_module_level_lookup_also_fuses(self):
        """模块级 lookup(now=) 与 AssetPack.lookup(now=) 同形（保险丝不分入口）。"""
        root = _make_pack(self.tmp, {"invalid_at": EXPIRES_STR})
        pack = load_pack(root)
        self.assertIsNotNone(lookup(
            pack, "greeting", 0, "normal", 0, TEXT, now=EXPIRES_BEFORE))
        self.assertIsNone(lookup(
            pack, "greeting", 0, "normal", 0, TEXT, now=EXPIRES_AFTER))

    def test_legacy_pack_lookup_unchanged_with_and_without_now(self):
        """旧包无失效字段：默认参数与显式 now= 都照常命中（既有行为零变化）。"""
        pack = load_pack(_make_pack(self.tmp))
        self.assertIsNotNone(self._find(pack))
        self.assertIsNotNone(self._find(pack, now=BASE + timedelta(days=3650)))

    def test_fuse_is_orthogonal_to_fingerprint_check(self):
        """过期判定只加一条失败条件：未过期时指纹不符仍返回 None。"""
        root = _make_pack(self.tmp, {"invalid_at": EXPIRES_STR})
        pack = load_pack(root)
        other = "完全不同的另一句话"
        self.assertIsNone(
            pack.lookup("greeting", 0, "normal", 0, other, now=EXPIRES_BEFORE))

    def _find(self, pack, now=None):
        """按 now 取条目（None = 默认路径）。"""
        if now is None:
            return pack.lookup("greeting", 0, "normal", 0, TEXT)
        return pack.lookup("greeting", 0, "normal", 0, TEXT, now=now)


# ============================================================
# 4. 直接调 pack.lookup（不经 runtime）——消费方自动受保护
# ============================================================
class TestConsumerProtection(_Tmp):
    """验收标准 5：直接调 pack.lookup(now=<过期后>) 返回 None。"""

    def test_direct_lookup_after_expiry_returns_none(self):
        """这条用例只调 assets 层——模拟 adapters/framework_kefu 的钩子。"""
        root = _make_pack(self.tmp, {"invalid_at": EXPIRES_STR})
        pack = load_pack(root)

        # 未过期：命中
        before = pack.lookup("greeting", 0, "normal", 0, TEXT, now=EXPIRES_BEFORE)
        self.assertIsNotNone(before)
        self.assertEqual(before.key, "greeting")

        # 过期：None（同形于未命中，消费方零改动即受保护）
        after = pack.lookup("greeting", 0, "normal", 0, TEXT, now=EXPIRES_AFTER)
        self.assertIsNone(after)

    def test_consumer_sees_no_expired_entry_at_all(self):
        """遍历式消费方（enumerate pack.assets 后自行判断）也能拿到同一判据。"""
        pack = load_pack(_make_pack(self.tmp, {"invalid_at": EXPIRES_STR}))
        self.assertTrue(is_expired(pack.assets[0], EXPIRES_AFTER))
        self.assertFalse(is_expired(pack.assets[0], EXPIRES_BEFORE))
