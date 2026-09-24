"""
compiler.tests.test_ttl — 源的 ttl / invalid_at 校验与预铸折算（T18）

覆盖范围：
  1. 正例：源带 invalid_at（未来时间）→ load_source 通过、字段透传；
     铸包成功、manifest 落 invalid_at、lookup(now=失效前) 命中
  2. 互斥：ttl 与 invalid_at 同时给 → SourceError（消息含 key 与两个字段名）
  3. 非法值：ttl=-1 / ttl="3600" / ttl=0 / ttl=1.5 / ttl=True /
     invalid_at="not-a-time" / invalid_at 空串 → 各自 SourceError 且消息含实际值
  4. 折算：ttl=3600 → manifest.invalid_at == created_at + 3600s（容差 ≤ 2 秒，
     验收方可独立复算）；lookup(now=invalid_at 之前/之后) 行为相反
  5. 向后兼容：不带这两个字段的旧源 → manifest 条目里不出现失效字段（零变化）

纪律：时间断言**一律注入 now**，不用 sleep——评测要能重放。
"""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from assets import load_pack
from assets.pack import is_expired
from compiler.prebake import PrebakeError, prebake
from compiler.source import SourceError, load_source


# ---------------------------------------------------------------------------
# 辅助：TTS 替身 + 源装载（不依赖外部命令，可精确控制音频）
# ---------------------------------------------------------------------------
SR = 16000
AMP = 15000
TONE_SAMPLES = 8001      # 0.5 s
HEAD_ZERO = 80           # 5 ms
TAIL_ZERO = 320          # 20 ms


def _tone(amp: int, n: int, freq: int = 440):
    import array
    import math
    return array.array(
        "h", [int(amp * math.sin(2 * math.pi * freq * (i + 1) / SR)) for i in range(n)]
    )


def _clean_wav(path: Path, head_zero: int = HEAD_ZERO) -> None:
    import array
    import wave
    samples = (
        array.array("h", b"\x00\x00" * head_zero)
        + _tone(AMP, TONE_SAMPLES)
        + array.array("h", b"\x00\x00" * TAIL_ZERO)
    )
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(samples.tobytes())


class FakeTts:
    """产出可通过质检的干净 WAV 的 TTS 替身。"""

    name = "fake"
    model_version = "macos-say"
    voice = "Tingting"
    rate_map = {"slow": 150, "normal": 200, "fast": 300}

    def __init__(self):
        self.calls = []

    def synthesize(self, text: str, out_path, rate_key: str = "normal") -> None:
        self.calls.append((text, rate_key))
        _clean_wav(Path(out_path))


def _write_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _default_pack(**overrides) -> dict:
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


def _phrases(*entries) -> dict:
    """把一个或多个 phrase 字典包成 phrases.json。"""
    return {"phrases": list(entries)}


def _phrase(**overrides) -> dict:
    data = {
        "key": "greeting",
        "variants": ["您好，请问需要什么帮助"],
        "rates": ["normal"],
    }
    data.update(overrides)
    return data


class _Tmp(unittest.TestCase):
    """提供 tempfile 目录与清理。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="compiler_ttl_")
        self.tmp = Path(self._tmp)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.out = self.tmp / "out"
        self.out.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_source(self, pack=None, phrases=None) -> None:
        _write_json(self.src / "pack.json", pack if pack is not None else _default_pack())
        _write_json(self.src / "phrases.json", phrases if phrases is not None
                    else _phrases(_phrase()))

    def _manifest(self) -> dict:
        with open(self.out / "manifest.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def _entries(self) -> list:
        return self._manifest()["assets"]


# ============================================================
# 1. 源格式：正例与互斥
# ============================================================
class TestSourceTtl(_Tmp):
    """load_source 的失效字段校验（编译期第一道闸门）。"""

    def test_invalid_at_accepted_and_passthrough(self):
        """正例：invalid_at 是未来时间 → 通过，且字段透传到 PhraseSource。"""
        future = "2099-01-01T00:00:00Z"
        self._write_source(phrases=_phrases(_phrase(invalid_at=future)))
        source = load_source(self.src)
        self.assertEqual(len(source.phrases), 1)
        self.assertEqual(source.phrases[0].ttl, None)
        self.assertIsInstance(source.phrases[0].invalid_at, datetime)
        self.assertEqual(source.phrases[0].invalid_at.tzinfo, timezone.utc)

    def test_ttl_accepted_and_passthrough(self):
        self._write_source(phrases=_phrases(_phrase(ttl=3600)))
        source = load_source(self.src)
        self.assertEqual(source.phrases[0].ttl, 3600)
        self.assertIsNone(source.phrases[0].invalid_at)

    def test_legacy_source_without_fields_is_untouched(self):
        """向后兼容：不带这两个字段的旧源照常通过，字段为 None。"""
        self._write_source()
        source = load_source(self.src)
        self.assertIsNone(source.phrases[0].ttl)
        self.assertIsNone(source.phrases[0].invalid_at)

    def test_ttl_and_invalid_at_mutually_exclusive(self):
        """互斥：同时给 → SourceError，消息含 key 与两个字段名（不猜哪个优先）。"""
        self._write_source(phrases=_phrases(
            _phrase(ttl=3600, invalid_at="2099-01-01T00:00:00Z")
        ))
        with self.assertRaises(SourceError) as ctx:
            load_source(self.src)
        msg = str(ctx.exception)
        self.assertIn("ttl", msg)
        self.assertIn("invalid_at", msg)
        self.assertIn("互斥", msg)
        self.assertIn("greeting", msg)
        # 两个实际值都在消息里（便于定位是哪一条写错了）
        self.assertIn("3600", msg)
        self.assertIn("2099-01-01T00:00:00Z", msg)


class TestSourceTtlInvalidValues(_Tmp):
    """非法值必须各自报错，且消息含实际值（负例断言具体值）。"""

    def test_ttl_negative_rejected(self):
        self._write_source(phrases=_phrases(_phrase(ttl=-1)))
        with self.assertRaises(SourceError) as ctx:
            load_source(self.src)
        msg = str(ctx.exception)
        self.assertIn("ttl", msg)
        self.assertIn("-1", msg)

    def test_ttl_string_rejected(self):
        """字符串 "3600" 必须拒绝——放行会让话术提前失效（类型笔误）。"""
        self._write_source(phrases=_phrases(_phrase(ttl="3600")))
        with self.assertRaises(SourceError) as ctx:
            load_source(self.src)
        msg = str(ctx.exception)
        self.assertIn("ttl", msg)
        self.assertIn("'3600'", msg)

    def test_ttl_zero_rejected(self):
        self._write_source(phrases=_phrases(_phrase(ttl=0)))
        with self.assertRaises(SourceError) as ctx:
            load_source(self.src)
        self.assertIn("ttl", str(ctx.exception))
        self.assertIn("0", str(ctx.exception))

    def test_ttl_float_rejected(self):
        self._write_source(phrases=_phrases(_phrase(ttl=1.5)))
        with self.assertRaises(SourceError) as ctx:
            load_source(self.src)
        self.assertIn("ttl", str(ctx.exception))
        self.assertIn("1.5", str(ctx.exception))

    def test_ttl_boolean_rejected(self):
        """bool 是 int 的子类，必须显式排除——true 会被静默当成 1 秒。"""
        self._write_source(phrases=_phrases(_phrase(ttl=True)))
        with self.assertRaises(SourceError) as ctx:
            load_source(self.src)
        self.assertIn("ttl", str(ctx.exception))
        self.assertIn("True", str(ctx.exception))

    def test_invalid_at_unparseable_rejected(self):
        self._write_source(phrases=_phrases(_phrase(invalid_at="not-a-time")))
        with self.assertRaises(SourceError) as ctx:
            load_source(self.src)
        msg = str(ctx.exception)
        self.assertIn("invalid_at", msg)
        self.assertIn("not-a-time", msg)

    def test_invalid_at_empty_rejected(self):
        for bad in ("", None, 12345):
            with self.subTest(bad=bad):
                self._write_source(phrases=_phrases(_phrase(invalid_at=bad)))
                with self.assertRaises(SourceError) as ctx:
                    load_source(self.src)
                self.assertIn("invalid_at", str(ctx.exception))
                self.assertIn(repr(bad), str(ctx.exception))


# ============================================================
# 2. 预铸折算
# ============================================================
class TestPrebakeTtl(_Tmp):
    """ttl 在预铸时刻折算成 invalid_at 落进 manifest。"""

    def test_ttl_folded_into_invalid_at_within_tolerance(self):
        """验收标准 3：manifest.invalid_at == 预铸时刻 + 3600s（容差 ≤ 2 秒）。"""
        ttl_seconds = 3600
        self._write_source(phrases=_phrases(_phrase(ttl=ttl_seconds)))
        source = load_source(self.src)
        report = prebake(source, FakeTts(), self.out)
        self.assertTrue(report.clean, report.quality_issues)

        manifest = self._manifest()
        entry = self._entries()[0]
        self.assertIn("invalid_at", entry)
        self.assertEqual(entry["ttl"], ttl_seconds)   # 原值留痕供审计

        # 独立复算：created_at + ttl，容差 2 秒（两次取值之间会经过墙钟）
        created_at = datetime.fromisoformat(manifest["created_at"])
        invalid_at = datetime.fromisoformat(entry["invalid_at"])
        delta = (invalid_at - created_at).total_seconds()
        self.assertAlmostEqual(delta, ttl_seconds, delta=2.0)

    def test_lookup_behavior_flips_around_invalid_at(self):
        """验收标准 3 后半：lookup(now=invalid_at 之前/之后) 行为相反。"""
        self._write_source(phrases=_phrases(_phrase(ttl=3600)))
        prebake(load_source(self.src), FakeTts(), self.out)

        pack = load_pack(self.out)
        entry = pack.assets[0]
        self.assertIsInstance(entry.invalid_at, datetime)
        self.assertFalse(is_expired(entry, entry.invalid_at - timedelta(seconds=1)))
        self.assertTrue(is_expired(entry, entry.invalid_at + timedelta(seconds=1)))

        text = entry.text
        before = pack.lookup("greeting", 0, "normal", 0, text,
                             now=entry.invalid_at - timedelta(seconds=1))
        after = pack.lookup("greeting", 0, "normal", 0, text,
                            now=entry.invalid_at + timedelta(seconds=1))
        self.assertIsNotNone(before)
        self.assertIsNone(after)

    def test_source_invalid_at_written_verbatim(self):
        """正例（验收标准 1）：源带 invalid_at → 铸包成功、manifest 落该字段。"""
        future = "2099-01-01T00:00:00Z"
        self._write_source(phrases=_phrases(_phrase(invalid_at=future)))
        report = prebake(load_source(self.src), FakeTts(), self.out)
        self.assertTrue(report.clean, report.quality_issues)

        entry = self._entries()[0]
        # manifest 落的是规范化后的 UTC 时间戳（Z → +00:00），语义等价
        self.assertEqual(
            entry["invalid_at"], "2099-01-01T00:00:00+00:00"
        )
        self.assertNotIn("ttl", entry)          # 源没给 ttl，manifest 也不出现

        pack = load_pack(self.out)
        self.assertEqual(
            pack.assets[0].invalid_at,
            datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(is_expired(pack.assets[0], datetime(2098, 1, 1, tzinfo=timezone.utc)))

    def test_legacy_source_manifest_has_no_expiry_fields(self):
        """向后兼容（验收标准 4）：旧源铸出的 manifest 条目不含失效字段。"""
        self._write_source()
        prebake(load_source(self.src), FakeTts(), self.out)
        for entry in self._entries():
            self.assertNotIn("invalid_at", entry)
            self.assertNotIn("ttl", entry)
        pack = load_pack(self.out)
        self.assertIsNone(pack.assets[0].invalid_at)
        self.assertIsNone(pack.assets[0].ttl)
        self.assertFalse(is_expired(pack.assets[0]))

    def test_partial_failure_still_fail_closed(self):
        """带失效字段的源不改变既有 fail-closed 语义（不写 manifest）。"""
        self._write_source(phrases=_phrases(
            _phrase(key="greeting", variants=["您好，请问需要什么帮助"],
                    rates=["normal"], ttl=3600),
            _phrase(key="broken", variants=["这条会质检不过"], rates=["normal"],
                    ttl=3600),
        ))
        class BadTts(FakeTts):
            def synthesize(self, text, out_path, rate_key="normal"):
                if text.startswith("这条会"):
                    raise RuntimeError("模拟引擎故障")
                return super().synthesize(text, out_path, rate_key)

        with self.assertRaises(PrebakeError):
            prebake(load_source(self.src), BadTts(), self.out)
        self.assertFalse((self.out / "manifest.json").exists())


# ============================================================
# 3. 时间注入（反空转）
# ============================================================
class TestTimeInjection(_Tmp):
    """全部时间断言注入 now——不 sleep、不吃墙钟，评测可重放。"""

    def test_fixed_now_gives_stable_result(self):
        """同一个注入时刻反复判定，结论必须稳定（可重放）。"""
        self._write_source(phrases=_phrases(_phrase(invalid_at="2026-09-22T13:00:00Z")))
        prebake(load_source(self.src), FakeTts(), self.out)
        pack = load_pack(self.out)
        entry = pack.assets[0]

        frozen_now = datetime(2026, 9, 22, 14, 0, 0, tzinfo=timezone.utc)
        results = [is_expired(entry, frozen_now) for _ in range(50)]
        self.assertEqual(set(results), {True})

        entry_before = pack.assets[0]
        frozen_before = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual([is_expired(entry_before, frozen_before) for _ in range(50)],
                         [False] * 50)
