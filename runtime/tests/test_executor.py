"""
runtime.tests.test_executor — 执行器最小闭环：命中判定 / 槽位 / fail-closed / 事件流 / 拼接

覆盖范围（对应 T07 验收标准）：
  1. 命中即零调用：全命中且无槽位 → tts_calls == 0，输出时长 ≈ 各段之和 + 静音垫
  2. 命中 + 槽位：tts_calls == 1（只有槽值现场合成），且包目录内不含槽值字符串
  3. fail-closed：未命中且 allow_fallback=False → 抛 RuntimeMissError、不写输出文件
  4. 三种未命中原因可区分：key_not_prebaked / fingerprint_mismatch / audio_file_missing
  5. 引擎一致性：音色/模型版本不一致 → engine_mismatch，且不得播包内音频
  6. 事件字段：每单元恰好一条、part 从 1 递增、键名全部来自 core.metrics_spec
  7. variant="auto"：同 turn 稳定、跨 turn 覆盖多变体、事件里是整数
  8. 拼接与 click：段间确有 silence_pad_ms 静音，max_sample_jump 不超阈值
  9. SAY_LIVE：miss / say_live_text
 10. 真实 say 端到端（say 不可用时 skip）

纪律：
  - 资产包全部在 tempfile 手搓（wave 造音频 + assets.fingerprint 算指纹 + 写 manifest.json）
  - 不得 import compiler/ 或 rules/（runtime/AGENTS.md §⑤ 层边界）
  - 负例必须喂真的违规输入，并断言异常类型 + 消息含关键值
"""

import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

import core.metrics_spec as spec
from assets.fingerprint import fingerprint
from assets.pack import load_pack
from core.protocol import ProtocolError, parse_plan

from runtime.audio import SAMPLE_RATE, max_sample_jump, read_wav
from runtime.duplex import DuplexParams
from runtime.executor import (
    REASON_AUDIO_FILE_MISSING,
    REASON_ENGINE_MISMATCH,
    REASON_FINGERPRINT_MISMATCH,
    REASON_KEY_NOT_PREBAKED,
    REASON_SAY_LIVE_TEXT,
    ExecutionResult,
    Executor,
    RuntimeMissError,
)


SR = SAMPLE_RATE          # 16000
PACK_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 辅助：手搓 WAV / 资产包 / 假适配器（不依赖 compiler/）
# ---------------------------------------------------------------------------
def _write_wav(path, samples):
    """写 16kHz/单声道/16-bit WAV。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(samples.tobytes())
    return path


def _level(level, ms=100):
    """固定电平样本：不同电平用于判别"实际播的是哪条音频"。"""
    return array("h", [level]) * int(round(ms * SR / 1000))


def make_pack(tmp, entries, *, voice="Tingting", model_version="macos-say",
              pack_version="1.0.0", pack_id="test-pack"):
    """手搓合法资产包并装载：写音频 + 按 assets 格式写 manifest.json。

    entries 每项：{key, text, rate_key, variant, path, level, ms, part_index?, duration_ms?}
        level/ms 决定落盘音频的电平与时长（测试用，用于判别播放来源）。
    """
    root = Path(tmp)
    assets = []
    for entry in entries:
        _write_wav(root / entry["path"], _level(entry.get("level", 1000), entry.get("ms", 100)))
        assets.append({
            "key": entry["key"],
            "part_index": entry.get("part_index", 0),
            "rate_key": entry["rate_key"],
            "variant": entry["variant"],
            "text": entry["text"],
            "fingerprint": fingerprint(
                text=entry["text"], voice=voice,
                rate_value=entry["rate_key"], model_version=model_version,
            ),
            "path": entry["path"],
            "duration_ms": entry.get("duration_ms", entry.get("ms", 100)),
        })
    manifest = {
        "pack_id": pack_id,
        "pack_version": pack_version,
        "protocol_version": "1.0",
        "ruleset_version": "1.0",
        "voice": voice,
        "model_version": model_version,
        "created_at": "2026-01-01T00:00:00Z",
        "assets": assets,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return load_pack(root)


class FakeTts:
    """假 TTS 适配器：不依赖 say，产出固定电平的可判别音频并记录调用。"""

    name = "fake"
    model_version = "macos-say"
    voice = "Tingting"
    rate_map = {"slow": 150, "normal": 200, "fast": 300}

    def __init__(self, level=500, ms=60):
        self.level = level
        self.ms = ms
        self.calls = []          # [(text, rate_key)]，用于核对"哪些文本被合成了"

    def rate_value(self, rate_key):
        """语义档位 → 物理语速值（未知档位报错，不回落）。"""
        if rate_key not in self.rate_map:
            raise ValueError(f"未知语速档位: {rate_key!r}")
        return self.rate_map[rate_key]

    def synthesize(self, text, out_path, rate_key="normal"):
        """合成一段固定电平音频并落盘。"""
        self.calls.append((text, rate_key))
        _write_wav(Path(out_path), _level(self.level, self.ms))


def default_entries():
    """两条不同电平的预铸话术（level 20000 / 10000，各 100ms）。"""
    return [
        {"key": "greeting", "text": "您好，请问需要什么帮助", "rate_key": "normal",
         "variant": 0, "path": "audio/greeting_v0.wav", "level": 20000, "ms": 100},
        {"key": "goodbye", "text": "感谢您的来电，祝您生活愉快", "rate_key": "normal",
         "variant": 0, "path": "audio/goodbye_v0.wav", "level": 10000, "ms": 100},
    ]


def hit_plan():
    """两条都命中的 plan（原始 dict 形式，走 parse_plan）。"""
    return [{"key": "greeting"}, {"key": "goodbye"}]


# ---------------------------------------------------------------------------
# 1. 命中即零调用
# ---------------------------------------------------------------------------
class TestHitZeroTts(unittest.TestCase):
    """全命中且无槽位：TTS 调用次数必须为 0，输出时长 ≈ 各段之和 + 静音垫。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.pack = make_pack(self.pack_root, default_entries())
        self.tts = FakeTts()
        self.out = Path(self.tmp.name) / "out.wav"

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_hit_no_slots_zero_tts_calls(self):
        """命中路径零 TTS 调用（runtime/AGENTS.md §③.1）。"""
        result = Executor(self.pack, self.tts).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(result.tts_calls, 0, "命中路径不得调用 TTS")
        self.assertEqual(self.tts.calls, [])
        self.assertEqual(result.hit_count, 2)
        self.assertEqual(result.miss_count, 0)
        self.assertEqual(result.fallback_count, 0)

    def test_output_exists_and_wav_format_valid(self):
        """输出 WAV 必须存在且为 16kHz/单声道/16-bit。"""
        Executor(self.pack, self.tts).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertTrue(self.out.exists())
        with wave.open(str(self.out), "rb") as wf:
            self.assertEqual(wf.getframerate(), SR)
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)

    def test_duration_is_segments_plus_pads(self):
        """总时长 ≈ 各段之和 + 静音垫：100 + 100 + 200 = 400ms。"""
        result = Executor(self.pack, self.tts).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(result.total_duration_ms, 400)
        samples, _ = read_wav(self.out)
        self.assertEqual(len(samples), 400 * SR // 1000)

    def test_hit_plays_pack_audio(self):
        """命中必须播包内音频（用电平判别：20000 与 10000）。"""
        Executor(self.pack, self.tts).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        samples, _ = read_wav(self.out)
        pad_samples = 200 * SR // 1000            # silence_pad_ms=200
        first_len = 100 * SR // 1000              # 100ms
        # 跳过淡入区，取各段中部的电平
        self.assertEqual(samples[first_len // 2], 20000, "第一段应为 greeting 的包内音频")
        self.assertEqual(
            samples[first_len + pad_samples + first_len // 2], 10000,
            "第二段应为 goodbye 的包内音频",
        )

    def test_hit_with_slot_still_allowed_fail_closed(self):
        """命中 + 槽位：allow_fallback=False 也不报错（槽位合成不算未命中）。"""
        plan = [{"key": "greeting", "slots": {"name": "王五"}}]
        result = Executor(self.pack, self.tts).execute(
            plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(result.hit_count, 1)
        self.assertEqual(result.tts_calls, 1, "只有槽值会调用 TTS")
        self.assertEqual(self.tts.calls, [("王五", "normal")])

    def test_result_is_execution_result(self):
        """返回值类型与关键字段齐全；首音频延迟从 execute() 进入起算且为正。"""
        result = Executor(self.pack, self.tts).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertIsInstance(result, ExecutionResult)
        self.assertIsInstance(result.first_audio_ms, float)
        self.assertGreater(
            result.first_audio_ms, 0.0,
            "命中路径也要读包内音频，首音频延迟应为正数",
        )
        self.assertIsInstance(result.total_duration_ms, int)
        self.assertGreater(result.total_duration_ms, 0)
        self.assertLess(
            result.first_audio_ms, result.total_duration_ms + 5000,
            "首音频延迟不应离谱地超过整段时长（计时口径异常检测）",
        )
        self.assertEqual(result.output_path, self.out)


# ---------------------------------------------------------------------------
# 2. 槽位：一律现场合成、不入包
# ---------------------------------------------------------------------------
class TestSlots(unittest.TestCase):
    """槽值只出现在现场合成的音频里，包目录内不得出现（docs/06 §6.2.6）。"""

    SLOT_VALUE = "赵钱孙李"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.pack = make_pack(self.pack_root, default_entries())
        self.tts = FakeTts()
        self.out = Path(self.tmp.name) / "out.wav"

    def tearDown(self):
        self.tmp.cleanup()

    def _plan(self, slots):
        return [{"key": "greeting", "slots": slots}]

    def test_hit_with_one_slot_calls_tts_once(self):
        """带 1 个槽位的命中单元 → tts_calls == 1（只有槽值现场合成）。"""
        result = Executor(self.pack, self.tts).execute(
            self._plan({"name": self.SLOT_VALUE}),
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(result.tts_calls, 1)
        self.assertEqual(result.hit_count, 1)
        self.assertEqual(self.tts.calls, [(self.SLOT_VALUE, "normal")])

    def test_slot_value_not_in_pack_dir(self):
        """包目录内任何文件都不得含槽值字符串（我 grep 包目录核对）。"""
        Executor(self.pack, self.tts).execute(
            self._plan({"name": self.SLOT_VALUE}),
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        needle = self.SLOT_VALUE.encode("utf-8")
        for path in sorted(self.pack_root.rglob("*")):
            if path.is_file():
                self.assertNotIn(
                    needle, path.read_bytes(),
                    f"槽值不应出现在包内文件 {path.name}",
                )

    def test_slot_pads_surround_slot_audio(self):
        """槽值前后必须各垫 slot_pad_ms（80ms = 1280 样本）静音。"""
        Executor(self.pack, self.tts).execute(
            self._plan({"name": self.SLOT_VALUE}),
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        samples, _ = read_wav(self.out)
        frame_len = 100 * SR // 1000                 # 框架音频 100ms
        slot_pad = 80 * SR // 1000                   # slot_pad_ms=80
        pad_before = samples[frame_len:frame_len + slot_pad]
        self.assertTrue(all(s == 0 for s in pad_before), "槽值前应有静音垫")
        pad_after_start = frame_len + slot_pad + 60 * SR // 1000   # + 槽音频 60ms
        pad_after = samples[pad_after_start:pad_after_start + slot_pad]
        self.assertTrue(all(s == 0 for s in pad_after), "槽值后应有静音垫")

    def test_multiple_slots_each_synthesized(self):
        """多个槽位各合成一次，且按字典顺序垫停顿。"""
        slots = {"a": "甲", "b": "乙"}
        result = Executor(self.pack, self.tts).execute(
            self._plan(slots), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(result.tts_calls, 2)
        self.assertEqual(self.tts.calls, [("甲", "normal"), ("乙", "normal")])


# ---------------------------------------------------------------------------
# 3. fail-closed
# ---------------------------------------------------------------------------
class TestFailClosed(unittest.TestCase):
    """未命中且未开启降级 → 抛错中止，不写输出文件、不播别的话术。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.pack = make_pack(self.pack_root, default_entries())
        self.tts = FakeTts()
        self.out = Path(self.tmp.name) / "out.wav"
        self.missing_plan = [{"key": "not_in_pack"}]

    def tearDown(self):
        self.tmp.cleanup()

    def test_miss_raises_runtime_miss_error(self):
        """引用包内不存在的 key → 抛 RuntimeMissError，消息含该 key 与原因。"""
        with self.assertRaises(RuntimeMissError) as ctx:
            Executor(self.pack, self.tts).execute(
                self.missing_plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )
        message = str(ctx.exception)
        self.assertIn("not_in_pack", message, "消息必须含缺失的 key")
        self.assertIn(REASON_KEY_NOT_PREBAKED, message, "消息必须含该单元的原因")

    def test_no_output_file_written_on_abort(self):
        """中止时绝不写输出文件（不留半截产物）。"""
        with self.assertRaises(RuntimeMissError):
            Executor(self.pack, self.tts).execute(
                self.missing_plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )
        self.assertFalse(self.out.exists(), "fail-closed 中止后不得存在输出文件")

    def test_no_tts_called_on_abort(self):
        """中止时不得调用 TTS 播别的话术。"""
        with self.assertRaises(RuntimeMissError):
            Executor(self.pack, self.tts).execute(
                self.missing_plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )
        self.assertEqual(self.tts.calls, [])

    def test_say_live_also_fail_closed_by_default(self):
        """SAY_LIVE 单元默认也是 miss，allow_fallback=False 时必须抛错。"""
        with self.assertRaises(RuntimeMissError) as ctx:
            Executor(self.pack, self.tts).execute(
                [{"text": "现场说一句"}],
                plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )
        self.assertIn(REASON_SAY_LIVE_TEXT, str(ctx.exception))

    def test_same_plan_succeeds_when_fallback_allowed(self):
        """同一 plan 在 allow_fallback=True 下正常产出且事件含 miss。"""
        result = Executor(self.pack, self.tts, allow_fallback=True).execute(
            self.missing_plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertTrue(self.out.exists())
        self.assertEqual(result.miss_count, 1)
        self.assertIs(result.events[0][spec.MISS], True)
        self.assertEqual(result.events[0][spec.REASON], REASON_KEY_NOT_PREBAKED)
        self.assertEqual(result.tts_calls, 1, "降级单元走现场合成")

    def test_later_miss_aborts_whole_plan(self):
        """第二个单元未命中也要中止整条 plan（不能播掉前面再报错）。"""
        plan = [{"key": "greeting"}, {"key": "not_in_pack"}]
        with self.assertRaises(RuntimeMissError):
            Executor(self.pack, self.tts).execute(
                plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )
        self.assertFalse(self.out.exists())
        self.assertEqual(self.tts.calls, [], "中止前不得有任何合成调用")


# ---------------------------------------------------------------------------
# 4. 三种未命中原因必须可区分（docs/06 §6.2.2）
# ---------------------------------------------------------------------------
class TestMissReasonsDistinguishable(unittest.TestCase):
    """三种未命中语义不同，事件 reason 必须分别可断言。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.tts = FakeTts()
        self.out = Path(self.tmp.name) / "out.wav"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, pack, plan=None):
        """以 allow_fallback=True 执行并返回结果（否则拿不到 miss/fallback 事件）。"""
        return Executor(pack, self.tts, allow_fallback=True).execute(
            plan or hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )

    def test_key_not_prebaked_is_miss(self):
        """包内无该组合 → miss + key_not_prebaked。"""
        pack = make_pack(self.pack_root, default_entries())
        result = self._run(pack, [{"key": "no_such_key"}])
        event = result.events[0]
        self.assertIs(event[spec.MISS], True)
        self.assertNotIn(spec.FALLBACK, event)
        self.assertEqual(event[spec.REASON], REASON_KEY_NOT_PREBAKED)
        self.assertEqual(result.miss_count, 1)
        self.assertEqual(result.fallback_count, 0)

    def test_fingerprint_mismatch_is_fallback(self):
        """包内条目文本被改过（指纹不符）→ fallback + fingerprint_mismatch。"""
        pack = make_pack(self.pack_root, default_entries())
        pack.assets[0].text = "被改过的话术"        # 模拟"话术改了但资产没重铸"
        result = self._run(pack)
        event = result.events[0]
        self.assertIs(event[spec.FALLBACK], True)
        self.assertNotIn(spec.HIT, event)
        self.assertNotIn(spec.MISS, event)
        self.assertEqual(event[spec.REASON], REASON_FINGERPRINT_MISMATCH)
        self.assertEqual(result.fallback_count, 1)
        self.assertEqual(result.hit_count, 1, "第二条（goodbye）未受损，仍应命中")
        self.assertEqual(result.miss_count, 0)

    def test_fingerprint_mismatch_does_not_play_pack_audio(self):
        """指纹不符时必须现场合成，不得播可能已过期的包内音频。"""
        pack = make_pack(
            self.pack_root,
            [{"key": "greeting", "text": "您好", "rate_key": "normal", "variant": 0,
              "path": "audio/g.wav", "level": 30000, "ms": 100}],
        )
        pack.assets[0].text = "被改过的话术"
        tts = FakeTts(level=500)
        Executor(pack, tts, allow_fallback=True).execute(
            [{"key": "greeting"}], plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        samples, _ = read_wav(self.out)
        self.assertLessEqual(
            max(abs(s) for s in samples), 500,
            "指纹不符时不得播包内音频（包内电平 30000）",
        )
        self.assertEqual(tts.calls, [("被改过的话术", "normal")])

    def test_audio_file_missing_is_fallback(self):
        """包内条目音频文件删掉 → fallback + audio_file_missing。"""
        pack = make_pack(self.pack_root, default_entries())
        (self.pack_root / "audio/greeting_v0.wav").unlink()
        result = self._run(pack)
        event = result.events[0]
        self.assertIs(event[spec.FALLBACK], True)
        self.assertEqual(event[spec.REASON], REASON_AUDIO_FILE_MISSING)
        self.assertEqual(result.fallback_count, 1)

    def test_reasons_are_three_distinct_values(self):
        """三种原因必须是三个不同的字符串（不许合并成笼统"未命中"）。"""
        self.assertEqual(
            len({REASON_KEY_NOT_PREBAKED, REASON_FINGERPRINT_MISMATCH,
                 REASON_AUDIO_FILE_MISSING}),
            3,
        )


# ---------------------------------------------------------------------------
# 5. 引擎一致性（docs/06 §6.2.3）
# ---------------------------------------------------------------------------
class TestEngineConsistency(unittest.TestCase):
    """包与适配器的音色/模型版本不一致 → 不得播包内音频。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.out = Path(self.tmp.name) / "out.wav"

    def tearDown(self):
        self.tmp.cleanup()

    def test_voice_mismatch_is_fallback(self):
        """音色不一致 → fallback + engine_mismatch。"""
        pack = make_pack(self.pack_root, default_entries(), voice="Meijia")
        tts = FakeTts(level=500)
        result = Executor(pack, tts, allow_fallback=True).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        event = result.events[0]
        self.assertIs(event[spec.FALLBACK], True)
        self.assertEqual(event[spec.REASON], REASON_ENGINE_MISMATCH)
        self.assertEqual(result.fallback_count, 2)
        self.assertEqual(result.hit_count, 0)

    def test_voice_mismatch_does_not_play_pack_audio(self):
        """必须播现场合成的音频，而不是包内那条（用电平判别：包 30000 / 合成 500）。"""
        pack = make_pack(
            self.pack_root,
            [{"key": "greeting", "text": "您好", "rate_key": "normal", "variant": 0,
              "path": "audio/g.wav", "level": 30000, "ms": 100}],
            voice="Meijia",
        )
        tts = FakeTts(level=500)
        Executor(pack, tts, allow_fallback=True).execute(
            [{"key": "greeting"}], plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        samples, _ = read_wav(self.out)
        peak = max(abs(s) for s in samples)
        self.assertLessEqual(peak, 500, "输出不得来自包内音频（包内电平为 30000）")
        self.assertGreater(peak, 0, "仍应产出可用音频")

    def test_model_version_mismatch_is_fallback(self):
        """模型版本不一致 → fallback + engine_mismatch。"""
        pack = make_pack(self.pack_root, default_entries(), model_version="v2.0")
        result = Executor(pack, FakeTts(), allow_fallback=True).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(result.events[0][spec.REASON], REASON_ENGINE_MISMATCH)

    def test_engine_check_wins_over_hit(self):
        """引擎不一致时不得因为"本来能命中"就播包内音频。"""
        pack = make_pack(self.pack_root, default_entries(), voice="Meijia")
        with self.assertRaises(RuntimeMissError) as ctx:
            Executor(pack, FakeTts()).execute(
                hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )
        self.assertIn(REASON_ENGINE_MISMATCH, str(ctx.exception))


# ---------------------------------------------------------------------------
# 6. 事件流契约
# ---------------------------------------------------------------------------
class TestEventStream(unittest.TestCase):
    """每个单元恰好一条事件、part 从 1 递增、键名全部来自 metrics_spec。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.pack = make_pack(
            self.pack_root,
            default_entries() + [
                {"key": "confirm", "text": "请确认您的信息", "rate_key": "normal",
                 "variant": 0, "path": "audio/confirm_v0.wav", "level": 8000, "ms": 100},
            ],
        )
        self.tts = FakeTts()
        self.out = Path(self.tmp.name) / "out.wav"

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_event_per_unit(self):
        """3 个单元 → 恰好 3 条事件，不多不少。"""
        plan = hit_plan() + [{"key": "confirm"}]
        result = Executor(self.pack, self.tts).execute(
            plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(len(result.events), 3)

    def test_part_starts_at_one_and_increments(self):
        """part 必须从 1 递增，且与单元顺序一致。"""
        plan = hit_plan() + [{"key": "confirm"}]
        result = Executor(self.pack, self.tts).execute(
            plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        parts = [event[spec.PART] for event in result.events]
        self.assertEqual(parts, [1, 2, 3])

    def test_event_keys_all_from_metric_spec(self):
        """任何事件键都不允许自造（grep 核对无字面量字符串键）。"""
        result = Executor(self.pack, self.tts).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        for event in result.events:
            unknown = set(event.keys()) - spec.METRIC_FIELDS
            self.assertEqual(unknown, set(), f"事件出现未知键: {sorted(unknown)}")

    def test_event_records_key_rate_turn_and_pack(self):
        """事件必须带 key / rate / turn_id / plan_id / pack_version。"""
        pack_version = "9.9.9"
        self.pack = make_pack(
            Path(self.tmp.name) / "pack2", default_entries(), pack_version=pack_version,
        )
        result = Executor(self.pack, self.tts).execute(
            hit_plan(), plan_id="plan-7", turn_id="turn-7", out_path=self.out,
        )
        self.assertEqual(result.events[0][spec.KEY], "greeting")
        self.assertEqual(result.events[1][spec.KEY], "goodbye")
        self.assertEqual(result.events[0][spec.RATE], "normal")
        self.assertEqual(result.events[0][spec.TURN_ID], "turn-7")
        self.assertEqual(result.events[0][spec.PLAN_ID], "plan-7")
        self.assertEqual(result.events[0][spec.PACK_VERSION], pack_version)

    def test_first_audio_ms_present_in_every_event(self):
        """每个事件都带 first_audio_ms（plan 级口径，便于按事件聚合）。"""
        result = Executor(self.pack, self.tts).execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        for event in result.events:
            self.assertIsInstance(event[spec.FIRST_AUDIO_MS], float)
            self.assertGreaterEqual(event[spec.FIRST_AUDIO_MS], 0.0)

    def test_accepts_parsed_plan_units(self):
        """也接受已解析的 PlanUnit 列表（不只原始 dict）。"""
        plan = parse_plan(hit_plan())
        result = Executor(self.pack, self.tts).execute(
            plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(result.hit_count, 2)
        self.assertEqual(result.tts_calls, 0)
        self.assertTrue(self.out.exists())

    def test_empty_plan_rejected_by_core(self):
        """空 plan 由 core 拒绝——执行器不另起一套隐性约定。"""
        with self.assertRaises(ProtocolError) as ctx:
            Executor(self.pack, self.tts).execute(
                [], plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )
        self.assertIn("空", str(ctx.exception))

    def test_unit_without_key_or_text_rejected_by_core(self):
        """单元既无 key 也无 text 必须由 core 拒绝（不得静默当成空单元）。"""
        with self.assertRaises(ProtocolError):
            Executor(self.pack, self.tts).execute(
                [{"rate": "normal"}],
                plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )

    def test_illegal_rate_rejected_by_core(self):
        """非法 rate 必须由 core 拒绝（不回落成 normal）。"""
        with self.assertRaises(ProtocolError) as ctx:
            Executor(self.pack, self.tts).execute(
                [{"key": "greeting", "rate": "turbo"}],
                plan_id="plan-1", turn_id="turn-1", out_path=self.out,
            )
        self.assertIn("turbo", str(ctx.exception))

    def test_say_live_is_miss_with_reason(self):
        """SAY_LIVE 单元 → miss + say_live_text，key 为 None。"""
        result = Executor(self.pack, self.tts, allow_fallback=True).execute(
            [{"text": "现场说一句"}],
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        event = result.events[0]
        self.assertIs(event[spec.MISS], True)
        self.assertEqual(event[spec.REASON], REASON_SAY_LIVE_TEXT)
        self.assertIsNone(event[spec.KEY])
        self.assertIsNone(event[spec.VARIANT])
        self.assertEqual(result.tts_calls, 1)
        self.assertEqual(self.tts.calls, [("现场说一句", "normal")])

    def test_mixed_plan_emits_events_for_all_states(self):
        """混合 plan：命中 / 未命中 / SAY_LIVE 各一条事件，状态互斥。"""
        result = Executor(self.pack, self.tts, allow_fallback=True).execute(
            [{"key": "greeting"}, {"key": "nope"}, {"text": "现场说一句"}],
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        states = [
            next(s for s in (spec.HIT, spec.MISS, spec.FALLBACK) if s in event)
            for event in result.events
        ]
        self.assertEqual(states, [spec.HIT, spec.MISS, spec.MISS])
        self.assertEqual(result.hit_count, 1)
        self.assertEqual(result.miss_count, 2)
        self.assertEqual(result.tts_calls, 2)


# ---------------------------------------------------------------------------
# 7. variant="auto" 稳定散列（docs/06 §6.2.7 ③）
# ---------------------------------------------------------------------------
class TestVariantAuto(unittest.TestCase):
    """auto 变体按 (turn_id, part) 稳定散列：同轮可复现，跨轮会变。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        # 同一 key 两个变体：文本相同、电平不同（20000 / 15000）以便判别实际播了哪条
        self.pack = make_pack(
            self.pack_root,
            [
                {"key": "greeting", "text": "您好，请问需要什么帮助", "rate_key": "normal",
                 "variant": 0, "path": "audio/greeting_v0.wav", "level": 20000, "ms": 100},
                {"key": "greeting", "text": "您好，请问需要什么帮助", "rate_key": "normal",
                 "variant": 1, "path": "audio/greeting_v1.wav", "level": 15000, "ms": 100},
            ],
        )
        self.tts = FakeTts()
        self.out = Path(self.tmp.name) / "out.wav"

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_turn_and_part_selects_same_variant(self):
        """同一 (turn_id, part) 两次执行必须选到同一 variant（评测要能重放）。"""
        plan = [{"key": "greeting"}]
        first = Executor(self.pack, self.tts).execute(
            plan, plan_id="p", turn_id="turn-42", out_path=self.out,
        )
        second = Executor(self.pack, self.tts).execute(
            plan, plan_id="p", turn_id="turn-42", out_path=self.out,
        )
        self.assertEqual(
            first.events[0][spec.VARIANT], second.events[0][spec.VARIANT],
            "同一 turn 同一单元必须可复现",
        )

    def test_batch_of_turns_covers_both_variants(self):
        """一批不同 turn_id 必须覆盖到包内两个变体（落实"防复读机"）。"""
        plan = [{"key": "greeting"}]
        seen = set()
        for index in range(40):
            result = Executor(self.pack, self.tts).execute(
                plan, plan_id="p", turn_id=f"turn-{index}", out_path=self.out,
            )
            seen.add(result.events[0][spec.VARIANT])
        self.assertEqual(seen, {0, 1}, f"40 个 turn_id 应覆盖两个变体，实际 {sorted(seen)}")

    def test_event_variant_is_int_not_auto(self):
        """事件里的 variant 必须是整数，不得是字符串 'auto'。"""
        result = Executor(self.pack, self.tts).execute(
            [{"key": "greeting"}], plan_id="p", turn_id="turn-1", out_path=self.out,
        )
        variant = result.events[0][spec.VARIANT]
        self.assertIsInstance(variant, int)
        self.assertNotIsInstance(variant, bool)
        self.assertNotEqual(variant, "auto")

    def test_selected_variant_matches_played_audio(self):
        """事件记录的 variant 必须与实际播放的音频一致（用电平判别）。"""
        plan = [{"key": "greeting"}]
        for index in range(20):
            result = Executor(self.pack, self.tts).execute(
                plan, plan_id="p", turn_id=f"turn-{index}", out_path=self.out,
            )
            variant = result.events[0][spec.VARIANT]
            samples, _ = read_wav(self.out)
            expected_level = 20000 if variant == 0 else 15000
            self.assertEqual(
                samples[500], expected_level,
                f"variant={variant} 应播放电平 {expected_level} 的音频",
            )

    def test_explicit_variant_is_respected(self):
        """显式指定 variant 时必须原样使用，不走散列。"""
        result = Executor(self.pack, self.tts).execute(
            [{"key": "greeting", "variant": 1}],
            plan_id="p", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(result.events[0][spec.VARIANT], 1)
        samples, _ = read_wav(self.out)
        self.assertEqual(samples[500], 15000)

    def test_missing_variant_is_key_not_prebaked(self):
        """显式指定包内不存在的 variant → miss + key_not_prebaked。"""
        result = Executor(self.pack, self.tts, allow_fallback=True).execute(
            [{"key": "greeting", "variant": 9}],
            plan_id="p", turn_id="turn-1", out_path=self.out,
        )
        event = result.events[0]
        self.assertIs(event[spec.MISS], True)
        self.assertEqual(event[spec.REASON], REASON_KEY_NOT_PREBAKED)
        self.assertEqual(event[spec.VARIANT], 9)


# ---------------------------------------------------------------------------
# 8. 拼接与 click 判据
# ---------------------------------------------------------------------------
class TestConcatenationAndClicks(unittest.TestCase):
    """段间确有 silence_pad_ms 静音；拼接边界无 click。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.pack = make_pack(self.pack_root, default_entries())
        self.tts = FakeTts()
        self.out = Path(self.tmp.name) / "out.wav"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, duplex=None):
        executor = Executor(self.pack, self.tts, duplex=duplex)
        return executor.execute(
            hit_plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )

    def test_silence_pad_between_units(self):
        """单元之间必须确有 silence_pad_ms（200ms = 3200 样本）静音。"""
        self._run()
        samples, _ = read_wav(self.out)
        first_len = 100 * SR // 1000
        pad = 200 * SR // 1000
        region = samples[first_len:first_len + pad]
        self.assertEqual(len(region), pad)
        self.assertTrue(all(s == 0 for s in region), "段间静音垫必须全零")

    def test_silence_pad_exact_sample_count_when_fade_off(self):
        """关掉淡入淡出时，静音垫的零样本长度必须精确等于 pad 样本数。"""
        self._run(DuplexParams(fade_ms=0))
        samples, _ = read_wav(self.out)
        first_len = 100 * SR // 1000
        pad = 200 * SR // 1000
        run = 0
        for value in samples[first_len:]:
            if value != 0:
                break
            run += 1
        self.assertEqual(run, pad, "静音垫长度必须精确等于 silence_pad_ms 换算的样本数")

    def test_max_sample_jump_within_threshold(self):
        """拼接产物上的峰值跳变不得超过测试给定阈值。"""
        threshold = 100
        self._run(DuplexParams(fade_ms=50))
        samples, _ = read_wav(self.out)
        self.assertLessEqual(max_sample_jump(samples), threshold)

    def test_fade_off_exceeds_threshold(self):
        """关掉淡入淡出则必然超阈——证明上一条的通过是靠 fade_ms 生效。"""
        threshold = 100
        self._run(DuplexParams(fade_ms=0))
        samples, _ = read_wav(self.out)
        self.assertGreater(max_sample_jump(samples), threshold)

    def test_custom_pad_applied(self):
        """silence_pad_ms 参数化生效（300ms 垫子）。"""
        self._run(DuplexParams(silence_pad_ms=300, fade_ms=0))
        samples, _ = read_wav(self.out)
        first_len = 100 * SR // 1000
        pad = 300 * SR // 1000
        self.assertTrue(all(s == 0 for s in samples[first_len:first_len + pad]))


# ---------------------------------------------------------------------------
# 9. 构造与层边界自检
# ---------------------------------------------------------------------------
class FakePack:
    """最小合法资产包替身（用于构造校验测试，不写磁盘）。"""

    assets = []
    voice = "Tingting"
    model_version = "macos-say"
    pack_version = "1.0"
    root = Path("/tmp")

    def lookup(self, *args, **kwargs):
        return None


class TestConstructionAndLayerBoundary(unittest.TestCase):
    """执行器构造校验 + runtime 不得 import compiler/ 或 rules/。"""

    def test_rejects_pack_without_lookup(self):
        """缺 lookup 成员的 pack 必须被拒（TypeError，消息含缺失成员）。"""
        with self.assertRaises(TypeError) as ctx:
            Executor(object(), FakeTts())
        self.assertIn("lookup", str(ctx.exception))

    def test_rejects_adapter_without_synthesize(self):
        """缺 synthesize 的 adapter 必须被拒。"""
        class EmptyAdapter:
            voice = "Tingting"
            model_version = "macos-say"

        with self.assertRaises(TypeError) as ctx:
            Executor(FakePack(), EmptyAdapter())
        self.assertIn("synthesize", str(ctx.exception))

    def test_rejects_non_bool_allow_fallback(self):
        """allow_fallback 必须是 bool（禁止 "yes" 字符串之类绕过）。"""
        with self.assertRaises(TypeError) as ctx:
            Executor(FakePack(), FakeTts(), allow_fallback="yes")
        self.assertIn("allow_fallback", str(ctx.exception))

    def test_rejects_non_duplex_params(self):
        """duplex 必须是 DuplexParams 实例（不允许裸字典）。"""
        with self.assertRaises(TypeError) as ctx:
            Executor(FakePack(), FakeTts(), duplex={"patience_ms": 900})
        self.assertIn("DuplexParams", str(ctx.exception))

    def test_default_duplex_is_used_when_omitted(self):
        """不传 duplex 时必须落到 DuplexParams.default()。"""
        executor = Executor(FakePack(), FakeTts())
        self.assertEqual(executor.duplex.patience_ms, 900)

    def test_explicit_duplex_is_honored(self):
        """传入 DuplexParams 时必须原样生效（慢思考档）。"""
        executor = Executor(FakePack(), FakeTts(), duplex=DuplexParams.slow_thinking())
        self.assertEqual(executor.duplex.patience_ms, 1800)

    def test_no_compiler_or_rules_import_in_runtime(self):
        """层边界硬约束：runtime 源码与测试都不得 import compiler/ 或 rules/。"""
        for path in list(PACK_DIR.glob("*.py")) + list((PACK_DIR / "tests").glob("*.py")):
            with self.subTest(file=path.name):
                source = path.read_text(encoding="utf-8")
                for line in source.splitlines():
                    stripped = line.strip()
                    if not (stripped.startswith("import ") or stripped.startswith("from ")):
                        continue
                    for forbidden in ("compiler", "rules"):
                        self.assertNotIn(
                            forbidden, stripped,
                            f"{path.name} 违规 import: {stripped}",
                        )


# ---------------------------------------------------------------------------
# 10. 真实 say 端到端（say 不可用时 skip）
# ---------------------------------------------------------------------------
def _available_say_voice():
    """找本机可用的 say 音色；默认 Tingting 未必装机，找不到返回 None。"""
    if not shutil.which("say"):
        return None
    try:
        listing = subprocess.check_output(["say", "-v", "?"], text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line for line in listing.splitlines() if line.strip()]
    names = [line.split()[0] for line in lines]
    if "Tingting" in names:
        return "Tingting"
    for line in lines:                       # 优先中文音色
        if "\tzh_" in line:
            return line.split()[0]
    return names[0] if names else None


_REAL_VOICE = _available_say_voice()


@unittest.skipUnless(_REAL_VOICE, "macOS say 命令或可用音色不可用，跳过真实合成用例")
class TestRealMacSayFallback(unittest.TestCase):
    """真适配器端到端：SAY_LIVE 走现场合成并产出合规 WAV。"""

    def test_say_live_synthesized_with_real_tts(self):
        """真实 say 合成成功，事件为 miss/say_live_text，输出格式合规。"""
        from adapters.tts_macsay.adapter import MacSayTts

        class RealTts(MacSayTts):
            """真实 say 适配器，音色改成本机实际可用的值。"""
            voice = _REAL_VOICE

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pack"
            root.mkdir(parents=True)
            pack = make_pack(
                root,
                [{"key": "greeting", "text": "您好", "rate_key": "normal",
                  "variant": 0, "path": "audio/g.wav", "level": 1000, "ms": 100}],
                voice=_REAL_VOICE,
            )
            out = Path(tmp) / "out.wav"
            result = Executor(pack, RealTts(), allow_fallback=True).execute(
                [{"text": "真实合成一句"}],
                plan_id="plan-1", turn_id="turn-1", out_path=out,
            )
            self.assertEqual(result.miss_count, 1)
            self.assertEqual(result.events[0][spec.REASON], REASON_SAY_LIVE_TEXT)
            self.assertEqual(result.tts_calls, 1)
            self.assertTrue(out.exists())
            samples, framerate = read_wav(out)
            self.assertEqual(framerate, SR)
            self.assertGreater(len(samples), 0)


if __name__ == "__main__":
    unittest.main()
