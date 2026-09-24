"""
eval.tests.test_bench — 离线对拍 harness 端到端（真实执行器 + 真实资产包 + 真实语料）

与 test_stats / test_report 不同，本文件不许用假数据搭报告：包用 OfflineTts 真合成、
真写 WAV、真算指纹、真 load_pack 装载，两臂经 runtime.Executor 真执行，
再校验产物（report.json / raw/*.jsonl / 音频目录 / CLI 退出码）。

覆盖（对应 T08 验收 1~10 与反空转条款）：
  1. 固定语料契约：12 单位 / key 唯一 / 每单位一句 / rate 三档各 ≥2 / 无个人数据痕迹
  2. 两臂严格对拍：同一 pack 对象 + 同一 adapter 对象，fast 全 hit、slow 全 miss
  3. fast 臂：tts_calls 恒 0、synthesized_chars 恒 0、precast_ratio 按口径独立重算一致
  4. slow 臂：tts_calls = 单元数、synthesized_chars = Σ 文本长度、reason = say_live_text
  5. 事件流契约：字段齐备（全取自 core.metrics_spec）、part 从 1 连续、pack_version 可溯源
  6. 可复现：同配置跑两次 deterministic_metrics 逐值相等；timing 只给分布口径不给相等承诺
  7. raw JSONL：行数 = repeats（样本）/ repeats×units（事件），index 连续
  8. 不得美化：抽掉一行原始数据 → incomplete=True 且原因含文件名
  9. 负面：repeats < MIN_REPEATS → BenchReportError 且不产生任何文件
 10. 负面：包缺某个 key → incomplete=True 且原因含该 key（fail-closed 留痕）
 11. CLI：退出码 2（不写报告）/ 0（写出 report.json + raw/ + audio/）
 12. 人读摘要：替身适配器必出显式警告行；不完整报告首行必为 [INCOMPLETE]

纪律：全部走产品 API（run_bench / run_bench_cli / load_corpus / OfflineTts），
      不在测试里重实现算法；负例断言异常类型 + 消息含关键值。
"""

import contextlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path

from assets.fingerprint import fingerprint
from assets.pack import load_pack
from core.metrics_spec import (
    FALLBACK,
    FIRST_AUDIO_MS,
    HIT,
    HIT_RATE,
    KEY,
    MISS,
    PACK_VERSION,
    PART,
    PLAN_ID,
    PRECAST_RATIO,
    RATE,
    REASON,
    TS,
    TURN_ID,
    VARIANT,
)
from core.protocol import VALID_RATES
from runtime import REASON_SAY_LIVE_TEXT, SAMPLE_RATE

from eval.bench import (
    FAST_ARM,
    SLOW_ARM,
    BenchConfig,
    BenchReportError,
    load_corpus,
    run_bench,
    run_bench_cli,
)
from eval.offline_tts import OfflineTts
from eval.report import CALIBER, check_raw_on_disk, render_summary, write_report

# 语料文件位置：测试文件在 eval/tests/，语料在 eval/corpus/
CORPUS_FILE = Path(__file__).resolve().parents[1] / "corpus" / "demo_broadcast.json"

VOICE = "Tingting"
MODEL_VERSION = "macos-say"
PACK_VERSION_VALUE = "1.0.0"
REPEATS = 20  # 恰好在 MIN_REPEATS 下限：既满足样本量口径，又让测试跑得动
PACK_CREATED_AT = "2026-09-17T00:00:00Z"

# 事件流必需字段（键名全部来自 core.metrics_spec 常量，不允许 harness 自造）
EVENT_REQUIRED_KEYS = (TS, TURN_ID, PLAN_ID, PART, KEY, RATE, VARIANT, REASON, PACK_VERSION)

# caliber 里 PRECAST_RATIO 口径的补充说明键（前缀取自常量，不复制字面量）
DENOM_NOTE_KEY = PRECAST_RATIO + "_denominator_note"


# ---------------------------------------------------------------------------
# 夹具：真实合成 + 真实指纹 + 真实 manifest
# ---------------------------------------------------------------------------
def build_pack(root: Path, units, skip_keys=(), ms_per_char=10.0, adapter=None) -> object:
    """在 root 下真合成音频、真算指纹、写 manifest.json 并 load_pack 装载。

    skip_keys 里的 key 不出资产条目（用于"包缺 key"的负面用例）。
    ms_per_char 调小只为让纯 Python 替身适配器跑得快，不改变任何口径。
    adapter 可选：自定义 TTS 适配器（用于测试特殊场景，如 0 帧音频）。
    """
    audio_dir = root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    tts = adapter if adapter is not None else OfflineTts(voice=VOICE, model_version=MODEL_VERSION, ms_per_char=ms_per_char)

    assets = []
    for unit in units:
        key = unit["key"]
        if key in skip_keys:
            continue
        wav = audio_dir / f"{key}_{unit['rate']}.wav"
        tts.synthesize(unit["text"], wav, unit["rate"])
        with wave.open(str(wav), "rb") as wf:
            frames = wf.getnframes()
        assets.append({
            "key": key,
            "part_index": 0,
            "rate_key": unit["rate"],
            "variant": unit["variant"],
            "text": unit["text"],
            "fingerprint": fingerprint(unit["text"], VOICE, unit["rate"], MODEL_VERSION),
            "path": f"audio/{key}_{unit['rate']}.wav",
            "duration_ms": int(round(frames * 1000 / SAMPLE_RATE)),
        })

    manifest = {
        "pack_id": "bench-pack",
        "pack_version": PACK_VERSION_VALUE,
        "protocol_version": "1.0",
        "ruleset_version": "1.0",
        "voice": VOICE,
        "model_version": MODEL_VERSION,
        "created_at": PACK_CREATED_AT,
        "assets": assets,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return load_pack(root)


def make_config(pack, units, out_dir, *, repeats=REPEATS, warmup_runs=0,
                adapter=None, corpus=None, plan_id="bench",
                corpus_path=None, corpus_meta=None):
    """构造一份默认配置（离线替身适配器 + 最小 warmup）。"""
    return BenchConfig(
        pack=pack,
        adapter=adapter if adapter is not None else OfflineTts(
            voice=VOICE, model_version=MODEL_VERSION, ms_per_char=10.0
        ),
        corpus=tuple(corpus if corpus is not None else units),
        plan_id=plan_id,
        repeats=repeats,
        warmup_runs=warmup_runs,
        out_dir=out_dir,
        corpus_path=corpus_path,
        corpus_meta=corpus_meta,
    )


def read_jsonl(path: Path) -> list:
    """读一份 JSONL（测试侧读取工具，与 harness 内部实现无关）。"""
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                out.append(json.loads(stripped))
    return out


def deterministic_raw_part(raw: dict) -> dict:
    """取 raw 块里跨运行可比的子集：路径指针 + `*_lines` 行数。

    排除 `*_sha256`（文件字节摘要）：raw 文件含 first_audio_ms / total_duration_ms /
    ts 等挂钟字段，字节必然随运行变化——这与 timing_metrics.machine_dependent=True
    是同一个事实，不是本次改动引入的不确定性。
    """
    return {k: v for k, v in raw.items() if not k.endswith("_sha256")}


class FlakyOfflineTts:
    """模拟执行期失败的适配器：第一次 synthesize 抛异常，后续正常。

    用于测试"某一次 execute() 抛错 → 仍产出报告"的路径。
    类变量 _calls 跨实例共享（模拟真实场景中全局状态的适配器）。
    """
    name = "flaky-adapter"
    voice = VOICE
    model_version = MODEL_VERSION
    _calls = 0

    def __init__(self, ms_per_char=10.0):
        self._tts = OfflineTts(voice=VOICE, model_version=MODEL_VERSION, ms_per_char=ms_per_char)

    def synthesize(self, text, out_path, rate_key="normal"):
        FlakyOfflineTts._calls += 1
        if FlakyOfflineTts._calls == 1:
            raise RuntimeError("注入的失败")
        return self._tts.synthesize(text, out_path, rate_key)

    @classmethod
    def reset(cls):
        cls._calls = 0


class FakeAdapterNoSynthetic:
    """没有 synthetic 属性的假适配器（模拟忘记打标记的第三方适配器）。

    本类定义在 eval/tests/test_bench.py 内，__module__ 以 eval. 开头，
    因此 bench.py 的模块路径检查会将其识别为替身。
    """
    name = "fake-no-synthetic"

    def __init__(self, voice, model_version, ms_per_char=10.0):
        self.voice = voice
        self.model_version = model_version
        self._tts = OfflineTts(voice=voice, model_version=model_version, ms_per_char=ms_per_char)

    def synthesize(self, text, out_path, rate_key="normal"):
        return self._tts.synthesize(text, out_path, rate_key)


class BenchCase(unittest.TestCase):
    """共享夹具基类：一个包 + 一份语料，全套用例复用（避免每条用例重铸 12 段音频）。"""

    @classmethod
    def setUpClass(cls):
        cls._work = Path(tempfile.mkdtemp(prefix="vox-bench-test-"))
        cls.units, cls.meta = load_corpus(CORPUS_FILE)
        cls.pack_root = cls._work / "pack"
        cls.pack_root.mkdir()
        cls.pack = build_pack(cls.pack_root, cls.units)
        cls.adapter = OfflineTts(voice=VOICE, model_version=MODEL_VERSION, ms_per_char=10.0)
        cls.total_chars = sum(len(u["text"]) for u in cls.units)
        cls.out_dirs = []

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls._work, ignore_errors=True)

    def fresh_out_dir(self) -> Path:
        self.out_dirs.append(f"out-{len(self.out_dirs)}")
        return self._work / self.out_dirs[-1]

    def bench(self, out_dir=None, **kwargs):
        """跑一次对拍，返回 (report, config)。"""
        out_dir = out_dir if out_dir is not None else self.fresh_out_dir()
        config = make_config(self.pack, self.units, out_dir, **kwargs)
        return run_bench(config), config


# ---------------------------------------------------------------------------
# 1. 固定语料契约
# ---------------------------------------------------------------------------
class CorpusContractTest(BenchCase):
    """固定语料本身必须可审计——否则报告数字没有可对照的基线。"""

    def test_units_count_and_rate_bands(self):
        units = self.units
        self.assertEqual(len(units), 12, "固定语料应为 12 个播报单位")
        counts = {rate: sum(1 for u in units if u["rate"] == rate) for rate in VALID_RATES}
        for rate, n in counts.items():
            self.assertGreaterEqual(
                n, 2, f"语速档位 {rate} 至少 2 个单位才能报档位差异，实际为 {n}"
            )

    def test_keys_unique_and_nonempty(self):
        keys = [u["key"] for u in self.units]
        self.assertEqual(len(keys), len(set(keys)), "语料 key 必须唯一")
        for key in keys:
            self.assertTrue(key and key.strip() == key)

    def test_one_sentence_per_unit(self):
        """每条 text 至多一个句末标点——一条单元就是一句（与 compiler 源格式规矩一致）。"""
        for unit in self.units:
            ends = sum(1 for ch in unit["text"] if ch in "。！？!?")
            self.assertLessEqual(ends, 1, f"{unit['key']} 含 {ends} 个句末标点")
            self.assertGreater(len(unit["text"]), 0)
            self.assertIsInstance(unit["variant"], int)
            self.assertNotIsInstance(unit["variant"], bool)

    def test_no_personal_data(self):
        """语料是对外可引用的 demo 话术，不得含个人数据（本层唯一的敏感数据红线）。"""
        blob = json.dumps(self.units, ensure_ascii=False).lower()
        for forbidden in ("138", "139", "@", "身份证", "手机号", "abc123"):
            self.assertNotIn(forbidden, blob, f"语料疑似含个人数据痕迹: {forbidden}")

    def test_corpus_meta(self):
        self.assertEqual(self.meta["corpus_id"], "demo-broadcast-v1")
        self.assertEqual(self.meta["version"], 1)

    def test_load_corpus_missing_file(self):
        with self.assertRaises(BenchReportError) as ctx:
            load_corpus(self._work / "no-such-corpus.json")
        self.assertIn("no-such-corpus.json", str(ctx.exception))

    def test_load_corpus_rejects_duplicate_key(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"units": [
                {"key": "dup", "text": "第一句。", "rate": "normal", "variant": 0},
                {"key": "dup", "text": "第二句。", "rate": "slow", "variant": 0},
            ]}, f)
            path = Path(f.name)
        try:
            with self.assertRaises(BenchReportError) as ctx:
                load_corpus(path)
            self.assertIn("'dup'", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_load_corpus_rejects_two_sentences(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"units": [
                {"key": "two", "text": "第一句。第二句。", "rate": "normal", "variant": 0},
            ]}, f)
            path = Path(f.name)
        try:
            with self.assertRaises(BenchReportError) as ctx:
                load_corpus(path)
            self.assertIn("two", str(ctx.exception))
            self.assertIn("2", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_load_corpus_rejects_bad_rate(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"units": [
                {"key": "bad", "text": "一句话。", "rate": "turbo", "variant": 0},
            ]}, f)
            path = Path(f.name)
        try:
            with self.assertRaises(BenchReportError) as ctx:
                load_corpus(path)
            self.assertIn("turbo", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_load_corpus_rejects_empty_units(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"units": []}, f)
            path = Path(f.name)
        try:
            with self.assertRaises(BenchReportError) as ctx:
                load_corpus(path)
            self.assertIn("units", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 2~4. 两臂语义
# ---------------------------------------------------------------------------
class FastArmSemanticsTest(BenchCase):
    """快路：全部单元命中预铸资产 → 零 TTS、零合成字符。"""

    def test_all_units_hit(self):
        report, _ = self.bench()
        fast = report["arms"][FAST_ARM]
        self.assertEqual(fast["state_counts"][HIT], 12)
        self.assertEqual(fast["state_counts"][MISS], 0)
        self.assertEqual(fast["state_counts"][FALLBACK], 0)
        self.assertEqual(fast[HIT_RATE], 1.0)

    def test_zero_tts_calls_and_chars(self):
        report, _ = self.bench()
        fast = report["arms"][FAST_ARM]
        self.assertEqual(fast["tts_calls"]["n"], REPEATS)
        self.assertEqual(fast["tts_calls"]["p50"], 0.0)
        self.assertEqual(fast["tts_calls"]["max"], 0.0)
        self.assertEqual(fast["synthesized_chars"]["p50"], 0.0)
        self.assertEqual(fast["synthesized_chars"]["max"], 0.0)

    def test_precast_ratio_matches_caliber(self):
        """precast_ratio 按报告口径独立重算一次，必须逐值一致。

        注意：分母含句间静音垫，所以全命中时也**不**等于 1.0——口径已写明，不得美化。
        """
        report, config = self.bench()
        precast = sum(
            int(round(entry.duration_ms * SAMPLE_RATE / 1000.0)) for entry in self.pack.assets
        )
        samples = read_jsonl(config.out_dir / "raw" / f"{FAST_ARM}_samples.jsonl")
        self.assertEqual(len(samples), REPEATS)
        total_frames = samples[0]["total_frames"]
        expected = round(precast / total_frames, 6)
        observed = report["arms"][FAST_ARM][PRECAST_RATIO]
        self.assertEqual(observed, expected, "precast_ratio 与口径公式不一致")
        self.assertGreater(observed, 0.0)
        self.assertLess(
            observed, 1.0, "分母含 11 段句间静音垫，包内音频帧数不可能等于输出总帧数"
        )

    def test_precast_ratio_caliber_documented(self):
        """口径必须写明分母含静音垫——这是"数字好看"与"口径诚实"的分界线。"""
        self.assertIn("静音垫", CALIBER[PRECAST_RATIO])
        self.assertIn("不等于 1.0", CALIBER[DENOM_NOTE_KEY])

    def test_deterministic_metrics_equals_arms(self):
        report, _ = self.bench()
        self.assertEqual(report["deterministic_metrics"], report["arms"])


class SlowArmSemanticsTest(BenchCase):
    """慢路：同一句话术改用自由文本 → 设计上全部未命中，逐句现场合成。"""

    def test_all_units_miss_with_reason(self):
        report, _ = self.bench()
        slow = report["arms"][SLOW_ARM]
        self.assertEqual(slow["state_counts"][MISS], 12)
        self.assertEqual(slow["state_counts"][HIT], 0)
        self.assertEqual(slow[HIT_RATE], 0.0)
        self.assertEqual(slow[PRECAST_RATIO], 0.0)

    def test_tts_calls_equal_unit_count(self):
        report, _ = self.bench()
        slow = report["arms"][SLOW_ARM]
        self.assertEqual(slow["tts_calls"]["p50"], float(len(self.units)))
        self.assertEqual(slow["tts_calls"]["max"], float(len(self.units)))

    def test_synthesized_chars_equal_text_lengths(self):
        report, _ = self.bench()
        slow = report["arms"][SLOW_ARM]
        self.assertEqual(slow["synthesized_chars"]["p50"], float(self.total_chars))
        self.assertEqual(slow["synthesized_chars"]["max"], float(self.total_chars))

    def test_delta_sign_and_magnitude(self):
        """delta = 慢路 - 快路：TTS 调用数与合成字符数的差值应等于慢路全量。"""
        report, _ = self.bench()
        delta = report["delta"]
        self.assertEqual(delta["tts_calls_p50"], float(len(self.units)))
        self.assertEqual(delta["synthesized_chars_p50"], float(self.total_chars))
        self.assertGreater(delta["tts_calls_p50"], 0.0)
        self.assertGreater(delta["synthesized_chars_p50"], 0.0)

    def test_reference_gate_passes_on_fast_arm(self):
        report, _ = self.bench()
        gate = report["reference_gate"]
        self.assertEqual(gate["observed_hit_rate"], 1.0)
        self.assertTrue(gate["passed"])
        self.assertIn("ε", gate["source"])


# ---------------------------------------------------------------------------
# 5. 事件流契约
# ---------------------------------------------------------------------------
class EventContractTest(BenchCase):
    """事件必须来自 runtime（不是 harness 自己造的），字段名一律来自 core.metrics_spec。"""

    def test_event_fields_complete(self):
        report, config = self.bench()
        events = read_jsonl(config.out_dir / "raw" / f"{FAST_ARM}_events.jsonl")
        self.assertGreater(len(events), 0)
        for event in events:
            for key in EVENT_REQUIRED_KEYS:
                self.assertIn(key, event, f"事件缺字段 {key}")
            states = [s for s in (HIT, MISS, FALLBACK) if event.get(s)]
            self.assertEqual(
                len(states), 1,
                f"每条事件必须恰好标记一个三态，实际标记 {states}（事件里三态键的值是状态名）",
            )

    def test_parts_contiguous_per_turn(self):
        """每个 turn_id 恰好有 units 条事件，part 从 1 连续到 units。"""
        report, config = self.bench()
        units = len(self.units)
        for arm in (FAST_ARM, SLOW_ARM):
            events = read_jsonl(config.out_dir / "raw" / f"{arm}_events.jsonl")
            self.assertEqual(len(events), REPEATS * units)
            by_turn = {}
            for event in events:
                by_turn.setdefault(event[TURN_ID], []).append(event)
            self.assertGreater(len(by_turn), 0)
            for turn_id, group in by_turn.items():
                self.assertEqual(
                    len(group), units,
                    f"{turn_id} 事件数 {len(group)} ≠ 语料单位数 {units}",
                )
                self.assertEqual(
                    sorted(event[PART] for event in group), list(range(1, units + 1)),
                    f"{turn_id} 的 part 不是 1..{units}",
                )

    def test_pack_version_and_plan_id_traced(self):
        report, config = self.bench()
        for arm in (FAST_ARM, SLOW_ARM):
            for event in read_jsonl(config.out_dir / "raw" / f"{arm}_events.jsonl"):
                self.assertEqual(event[PACK_VERSION], PACK_VERSION_VALUE)
                self.assertEqual(event[PLAN_ID], f"bench-{arm}")

    def test_slow_arm_reason_is_say_live_text(self):
        report, config = self.bench()
        events = read_jsonl(config.out_dir / "raw" / f"{SLOW_ARM}_events.jsonl")
        self.assertEqual(len(events), REPEATS * len(self.units))
        for event in events:
            self.assertEqual(event[REASON], REASON_SAY_LIVE_TEXT)
            self.assertTrue(event[MISS])
            self.assertIsNone(event[KEY], "自由文本单元不应带资产 key")
            self.assertNotIn(HIT, event)

    def test_fast_arm_reason_empty_and_hit(self):
        report, config = self.bench()
        events = read_jsonl(config.out_dir / "raw" / f"{FAST_ARM}_events.jsonl")
        for event in events:
            self.assertTrue(event[HIT])
            self.assertEqual(event[REASON], "")
            self.assertIsInstance(event[KEY], str)
            self.assertEqual(event[VARIANT], 0)
            self.assertIn(event[RATE], VALID_RATES)


# ---------------------------------------------------------------------------
# 6. 可复现
# ---------------------------------------------------------------------------
class DeterminismTest(BenchCase):
    """同配置跑两次：确定性指标逐值相等，时序只报分布不承诺数值相等。"""

    def test_deterministic_metrics_repeatable(self):
        first, _ = self.bench()
        second, _ = self.bench()
        self.assertEqual(first["deterministic_metrics"], second["deterministic_metrics"],
                         "同输入 + 同种子必须逐值相等（可复现的硬承诺）")
        self.assertEqual(first["arms"], second["arms"])
        self.assertEqual(first["incomplete"], False)
        self.assertEqual(first["incomplete_reasons"], [])

    def test_report_identities_repeatable(self):
        first, _ = self.bench()
        second, _ = self.bench()
        for key in ("schema_version", "seed", "repeats", "warmup_runs", "command",
                    "caliber", "corpus"):
            self.assertEqual(first[key], second[key], f"{key} 两次运行不一致")
        # raw 只比对确定性子集（路径指针 + 行数）：*_sha256 是文件字节摘要，
        # 而 raw 里的 first_audio_ms / total_duration_ms / ts 都是挂钟字段
        # （与 timing_metrics.machine_dependent=True 同源），跨运行必然不同。
        self.assertEqual(
            deterministic_raw_part(first["raw"]), deterministic_raw_part(second["raw"]),
            "raw 的路径指针与行数两次运行不一致",
        )
        self.assertEqual(first["env"]["adapter"], second["env"]["adapter"])
        self.assertEqual(first["env"]["pack"], second["env"]["pack"])

    def test_timing_block_shape_only(self):
        """时序块必须有分布口径（n / P50 / P99 / max / CI / unit / machine_dependent）。
        挂钟时间受机器负载影响，这里只校验形状与内部一致性，不校验数值。"""
        report, _ = self.bench()
        for arm in (FAST_ARM, SLOW_ARM):
            block = report["timing_metrics"][arm]
            for key in ("n", "p50", "p99", "max", "ci95_p50", "ci95_p99",
                        "unit", "machine_dependent"):
                self.assertIn(key, block)
            self.assertEqual(block["n"], REPEATS)
            self.assertEqual(block["unit"], "ms")
            self.assertIs(block["machine_dependent"], True)
            self.assertLessEqual(block["p50"], block["p99"])
            self.assertLessEqual(block["p99"], block["max"])
            self.assertEqual(len(block["ci95_p50"]), 2)
            self.assertLessEqual(block["ci95_p50"][0], block["ci95_p50"][1])
            self.assertLessEqual(block["ci95_p99"][0], block["ci95_p99"][1])

    def test_fast_arm_not_slower_than_slow(self):
        """快路命中预铸资产、不合成任何音频，首音频延迟不应比慢路大。

        这是不等式断言（口径只承诺分布方法可复现，不承诺数值相等），
        主要用来防两臂被调错的回归——慢路必然多 12 次现场合成。
        """
        report, _ = self.bench()
        fast = report["timing_metrics"][FAST_ARM]
        slow = report["timing_metrics"][SLOW_ARM]
        self.assertLess(fast["p50"], slow["p50"])
        self.assertGreater(report["delta"][f"{FIRST_AUDIO_MS}_p50"], 0.0)

    def test_env_and_corpus_provenance(self):
        """报告必须带可审计的环境与语料指纹（否则数字无法复现）。"""
        report, _ = self.bench(corpus_path=CORPUS_FILE, corpus_meta=self.meta)
        env = report["env"]
        self.assertEqual(env["adapter"]["name"], "offline-synthetic")
        self.assertIs(env["adapter"]["synthetic"], True)
        self.assertEqual(env["pack"]["pack_version"], PACK_VERSION_VALUE)
        self.assertEqual(env["pack"]["asset_count"], len(self.pack.assets))
        self.assertTrue(env["python"], "报告必须记录 Python 版本")
        self.assertTrue(env["platform"])
        corpus = report["corpus"]
        self.assertEqual(corpus["units"], len(self.units))
        self.assertEqual(corpus["path"], str(CORPUS_FILE))
        self.assertEqual(corpus["corpus_id"], "demo-broadcast-v1")
        self.assertEqual(len(corpus["sha256"]), 16)

    def test_corpus_fingerprint_without_path_is_none(self):
        """没给语料文件路径时 sha256 必须是 None——不得编造指纹（不得美化）。"""
        report, _ = self.bench()
        self.assertIsNone(report["corpus"]["sha256"])
        self.assertIsNone(report["corpus"]["path"])


# ---------------------------------------------------------------------------
# 7~8. 原始数据落盘与核对
# ---------------------------------------------------------------------------
class RawIntegrityTest(BenchCase):
    """原始 JSONL 是报告的底气：行数、index、落盘都要真核对。"""

    def test_raw_file_line_counts(self):
        report, config = self.bench()
        for arm in (FAST_ARM, SLOW_ARM):
            samples = read_jsonl(config.out_dir / "raw" / f"{arm}_samples.jsonl")
            events = read_jsonl(config.out_dir / "raw" / f"{arm}_events.jsonl")
            self.assertEqual(len(samples), REPEATS)
            self.assertEqual(len(events), REPEATS * len(self.units))
        # raw 块是追加式的：既有 4 个路径指针 + 每份文件追加 sha256/lines 指纹
        # （docs/08 §8.7 欠账 1）——断言完整键集，防止字段被改名或缺项
        self.assertEqual(
            set(report["raw"].keys()),
            {
                "fast_samples", "fast_events", "slow_samples", "slow_events",
                "fast_samples_sha256", "fast_samples_lines",
                "fast_events_sha256", "fast_events_lines",
                "slow_samples_sha256", "slow_samples_lines",
                "slow_events_sha256", "slow_events_lines",
            },
        )
        for key in ("fast_samples", "fast_events", "slow_samples", "slow_events"):
            rel = report["raw"][key]
            self.assertTrue((config.out_dir / rel).is_file(), f"{key} → {rel} 未落盘")

    def test_sample_index_continuous(self):
        report, config = self.bench()
        for arm in (FAST_ARM, SLOW_ARM):
            samples = read_jsonl(config.out_dir / "raw" / f"{arm}_samples.jsonl")
            self.assertEqual([s["index"] for s in samples], list(range(REPEATS)))
            for sample in samples:
                self.assertEqual(sample["arm"], arm)
                for key in (FIRST_AUDIO_MS, HIT_RATE, PRECAST_RATIO, TURN_ID,
                            "tts_calls", "synthesized_chars", "total_duration_ms",
                            "state_counts"):
                    self.assertIn(key, sample)

    def test_run_bench_does_not_write_report(self):
        """run_bench 只组装报告字典；report.json 由 CLI/write_report 写出（职责分离）。"""
        report, config = self.bench()
        self.assertFalse((config.out_dir / "report.json").exists())
        self.assertTrue((config.out_dir / "audio").is_dir())
        self.assertTrue((config.out_dir / "raw").is_dir())

    def test_warmup_runs_excluded_from_samples(self):
        """预热轮不计入样本（避免首跑冷读盘污染 P99），样本数仍等于 repeats。"""
        report, config = self.bench(warmup_runs=2)
        for arm in (FAST_ARM, SLOW_ARM):
            samples = read_jsonl(config.out_dir / "raw" / f"{arm}_samples.jsonl")
            self.assertEqual(len(samples), REPEATS)
        self.assertEqual(report["warmup_runs"], 2)

    def test_tampered_raw_data_marks_report_incomplete(self):
        """抽掉一行原始数据后重新写报告 → incomplete=True，原因含文件名（不得美化）。"""
        report, config = self.bench()
        first_path = write_report(json.loads(json.dumps(report)), config.out_dir)
        written = json.loads(first_path.read_text(encoding="utf-8"))
        self.assertFalse(written["incomplete"])

        target = config.out_dir / "raw" / f"{FAST_ARM}_samples.jsonl"
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), REPEATS)
        target.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")

        fresh = json.loads(json.dumps(report))
        second_path = write_report(fresh, config.out_dir)
        rewritten = json.loads(second_path.read_text(encoding="utf-8"))
        self.assertTrue(rewritten["incomplete"], "原始数据被抽行后报告仍声称完整")
        blob = " ".join(rewritten["incomplete_reasons"])
        self.assertIn(f"{FAST_ARM}_samples.jsonl", blob)
        self.assertIn("行数", blob)
        self.assertEqual(first_path, second_path)

    def test_raw_fingerprints_written_and_match_disk(self):
        """落盘后每份 raw 都追加 sha256（文件字节摘要）与 lines；既有路径键原样保留。

        WHY 指纹必须是文件字节 sha256：值级篡改不改行数也不改 index，
        只有逐字节摘要才能反映「文件内容被换过」（docs/08 §8.7 欠账 1）。
        """
        import hashlib

        report, config = self.bench()
        raw = report["raw"]
        expected_lines = {
            f"{FAST_ARM}_samples": REPEATS,
            f"{FAST_ARM}_events": REPEATS * len(self.units),
            f"{SLOW_ARM}_samples": REPEATS,
            f"{SLOW_ARM}_events": REPEATS * len(self.units),
        }
        for name, want_lines in expected_lines.items():
            path = config.out_dir / raw[name]
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(len(raw[f"{name}_sha256"]), 64, f"{name} 指纹长度不是 64")
            self.assertEqual(raw[f"{name}_sha256"], actual_hash, f"{name} 指纹与磁盘字节不符")
            self.assertEqual(raw[f"{name}_lines"], want_lines, f"{name} 行数记录不符")

    def test_value_tampering_detected_by_fingerprint(self):
        """核心回归：行数与 index 不动、只把 first_audio_ms 除以 1000 → 必须被抓出。

        这正是 T08b 审计 #5 留下的破口：旧的行数/index 核对对此完全失明。
        测试真的改了文件字节（不是模拟文件缺失）。
        """
        report, config = self.bench()
        target = config.out_dir / "raw" / f"{SLOW_ARM}_samples.jsonl"
        records = read_jsonl(target)
        for record in records:
            record[FIRST_AUDIO_MS] = record[FIRST_AUDIO_MS] / 1000.0
        with target.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        reasons = check_raw_on_disk(json.loads(json.dumps(report)), config.out_dir)
        self.assertTrue(
            any(f"{SLOW_ARM}_samples.jsonl" in r and "sha256" in r for r in reasons),
            f"值级篡改未被指纹核对拦下，reasons={reasons}",
        )
        self.assertEqual(len(read_jsonl(target)), REPEATS, "篡改不应改变行数")


# ---------------------------------------------------------------------------
# 11b. --out 越界拦截（docs/08 §8.7 欠账 2）
# ---------------------------------------------------------------------------
class OutDirGuardTest(BenchCase):
    """对拍产物不得写进资产包目录——检查必须在建工作目录之前完成。"""

    def test_out_dir_inside_pack_rejected(self):
        """out_dir 落在包目录内 → BenchReportError，且消息含两个路径、不产生任何文件。"""
        nested = self.pack_root / "sub"
        with self.assertRaises(BenchReportError) as ctx:
            run_bench(make_config(self.pack, self.units, nested))
        msg = str(ctx.exception)
        self.assertIn(str(nested), msg, "消息必须含 out_dir 路径")
        self.assertIn(str(self.pack_root), msg, "消息必须含包目录路径")
        self.assertFalse(nested.exists(), "抛错前不得建目录（否则包目录已被污染）")
        self.assertEqual(
            sorted(p.name for p in self.pack_root.iterdir()),
            ["audio", "manifest.json"],
            "包目录内容不得因越界的 --out 而改变",
        )

    def test_out_dir_escape_via_dotdot_rejected(self):
        """带 .. 的相对路径解析后落在包内 → 同样拦下（字符串前缀比较会漏掉它）。"""
        escaped = self.pack_root / "audio" / ".." / "nested"
        with self.assertRaises(BenchReportError) as ctx:
            run_bench(make_config(self.pack, self.units, escaped))
        self.assertIn(str(escaped), str(ctx.exception))
        self.assertFalse((self.pack_root / "nested").exists())

    def test_out_dir_outside_pack_allowed(self):
        """包外的正常输出目录不受影响（不得把防护做成误杀）。"""
        out_dir = self.fresh_out_dir()
        report, config = self.bench(out_dir)
        self.assertTrue((config.out_dir / "raw").is_dir())
        self.assertTrue((config.out_dir / "audio").is_dir())
        self.assertFalse(report["incomplete"])


# ---------------------------------------------------------------------------
# 12. 人读摘要
# ---------------------------------------------------------------------------
class SummaryTest(BenchCase):
    """替身适配器的时序数字不得对外引用——摘要里必须有显式警告。"""

    def test_synthetic_adapter_warning(self):
        report, _ = self.bench()
        self.assertIs(report["timing_metrics_meaningful"], False)
        summary = render_summary(report)
        self.assertIn("[警告]", summary)
        self.assertIn("不得对外引用", summary)
        self.assertIn("offline-synthetic", json.dumps(report["env"], ensure_ascii=False))

    def test_complete_report_first_line(self):
        report, _ = self.bench()
        summary = render_summary(report)
        self.assertTrue(summary.startswith("[COMPLETE]"))
        self.assertNotIn("[INCOMPLETE]", summary)
        self.assertIn("CI95", summary)
        self.assertIn("可复现:", summary)

    def test_incomplete_report_first_line(self):
        report, _ = self.bench()
        report["incomplete"] = True
        report["incomplete_reasons"] = ["[fast] 有效样本数 19 < repeats 20"]
        summary = render_summary(report)
        self.assertTrue(summary.startswith("[INCOMPLETE]"))
        self.assertIn("有效样本数 19 < repeats 20", summary)

    def test_synthetic_marker_without_attribute(self):
        """没有 synthetic 属性的假适配器（__module__ 以 eval. 开头）→ meaningful=False 且摘要含警告。

        WHY: 忘记打标记的替身适配器不得被报告背书为真机数据。
        """
        adapter = FakeAdapterNoSynthetic(voice=VOICE, model_version=MODEL_VERSION)
        config = make_config(self.pack, self.units, self.fresh_out_dir(), adapter=adapter)
        report = run_bench(config)

        self.assertIs(report["timing_metrics_meaningful"], False,
                      "eval.* 模块下的适配器必须被识别为替身")
        self.assertTrue(report["env"]["adapter"]["synthetic"],
                        "模块路径检查应将其识别为替身（synthetic=True）")
        summary = render_summary(report)
        self.assertIn("[警告]", summary)


# ---------------------------------------------------------------------------
# 9~10. 负面用例
# ---------------------------------------------------------------------------
class NegativeTest(BenchCase):
    """配置错误必须中止且不产生文件；fail-closed 必须留痕。"""

    def test_repeats_below_floor_raises_without_files(self):
        out_dir = self.fresh_out_dir()
        self.assertFalse(out_dir.exists())
        with self.assertRaises(BenchReportError) as ctx:
            self.bench(out_dir=out_dir, repeats=5)
        msg = str(ctx.exception)
        self.assertIn("5", msg)
        self.assertIn("20", msg)
        self.assertFalse(out_dir.exists(), "低于样本量下限不得产生任何输出文件")

    def test_missing_pack_key_is_reported(self):
        """包缺某个 key → 快路 fail-closed → incomplete=True 且原因点名该 key。"""
        missing = self.units[1]["key"]
        pack_root = self._work / "pack-incomplete"
        pack_root.mkdir()
        pack = build_pack(pack_root, self.units, skip_keys={missing})
        config = make_config(pack, self.units, self.fresh_out_dir())
        report = run_bench(config)
        self.assertTrue(report["incomplete"], "包缺 key 时报告必须标 incomplete")
        blob = " ".join(report["incomplete_reasons"])
        self.assertIn(missing, blob, "原因必须点名缺失的 key（不得只说'样本不足'）")
        self.assertIn(FAST_ARM, blob)
        self.assertEqual(report["arms"][FAST_ARM]["state_counts"][HIT], 0)
        # 慢路不受影响：自由文本从不依赖资产
        self.assertEqual(report["arms"][SLOW_ARM]["state_counts"][MISS], len(self.units))
        self.assertTrue(render_summary(report).startswith("[INCOMPLETE]"))

    def test_empty_corpus_raises(self):
        out_dir = self.fresh_out_dir()
        with self.assertRaises(BenchReportError) as ctx:
            self.bench(out_dir=out_dir, corpus=())
        self.assertIn("0", str(ctx.exception))
        self.assertFalse(out_dir.exists())

    def test_empty_plan_id_raises(self):
        out_dir = self.fresh_out_dir()
        with self.assertRaises(BenchReportError) as ctx:
            run_bench(make_config(self.pack, self.units, out_dir, plan_id=""))
        self.assertIn("plan_id", str(ctx.exception))
        self.assertFalse(out_dir.exists())

    def test_wrong_config_type_raises(self):
        with self.assertRaises(TypeError) as ctx:
            run_bench({"pack": self.pack, "corpus": ()})
        self.assertIn("BenchConfig", str(ctx.exception))

    def test_unknown_adapter_spec_raises(self):
        from eval.bench import _resolve_adapter

        with self.assertRaises(BenchReportError) as ctx:
            _resolve_adapter("no_such_module_xyz:None", self.pack)
        self.assertIn("no_such_module_xyz", str(ctx.exception))

    def test_adapter_spec_without_colon_raises(self):
        from eval.bench import _resolve_adapter

        with self.assertRaises(BenchReportError) as ctx:
            _resolve_adapter("eval.offline_tts", self.pack)
        self.assertIn("模块:类名", str(ctx.exception))

    def test_default_adapter_is_offline_tts(self):
        """CLI 缺省适配器必须是离线替身（离线可跑的验收前提）。"""
        from eval.bench import _resolve_adapter

        adapter = _resolve_adapter(None, self.pack)
        self.assertIsInstance(adapter, OfflineTts)
        self.assertEqual(adapter.voice, VOICE)
        self.assertEqual(adapter.model_version, MODEL_VERSION)

    def test_bad_adapter_fails_closed(self):
        """适配器形状不符契约 → 执行器拒收（不得静默降级成假绿报告）。"""
        class Bare:
            pass

        with self.assertRaises(TypeError) as ctx:
            run_bench(make_config(self.pack, self.units, self.fresh_out_dir(),
                                  adapter=Bare()))
        self.assertIn("synthesize", str(ctx.exception))
        self.assertIn("Bare", str(ctx.exception))

    def test_partial_failure_produces_report(self):
        """某一臂一次 execute() 抛错 → 仍产出报告、incomplete is True、原因含 turn_id 与异常消息。

        WHY: _timing_block 在 n < MIN_REPEATS 时不得抛错中止整轮对拍，
             样本不足的判定归 assess_completeness。
        """
        FlakyOfflineTts.reset()
        adapter = FlakyOfflineTts()
        config = make_config(self.pack, self.units, self.fresh_out_dir(), adapter=adapter)
        report = run_bench(config)

        self.assertTrue(report["incomplete"], "执行期失败导致样本不足时报告必须标 incomplete")
        blob = " ".join(report["incomplete_reasons"])
        self.assertIn("注入的失败", blob, "原因必须含异常消息原文")
        self.assertIn("bench-slow-0000", blob, "原因必须含失败的 turn_id")
        self.assertEqual(report["timing_metrics"][SLOW_ARM]["n"], REPEATS - 1)
        self.assertIsNone(report["timing_metrics"][SLOW_ARM]["p50"],
                          "n < MIN_REPEATS 时 p50 必须是 None（不是 0.0）")
        self.assertTrue(render_summary(report).startswith("[INCOMPLETE]"))

    def test_precast_ratio_over_one_marks_incomplete(self):
        """包元数据不一致（duration_ms 篡改使分子 > 分母）→ 比值 > 1 如实写出且 incomplete=true。

        WHY: 不得夹取到 1.0——异常数据被静默改写成"满分"属于美化。
        """
        pack_root = self._work / "pack-tampered"
        pack_root.mkdir()
        pack = build_pack(pack_root, self.units)
        manifest = json.loads((pack_root / "manifest.json").read_text(encoding="utf-8"))
        for a in manifest["assets"]:
            a["duration_ms"] *= 3
        (pack_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        tampered_pack = load_pack(pack_root)
        config = make_config(tampered_pack, self.units, self.fresh_out_dir())
        report = run_bench(config)

        self.assertTrue(report["incomplete"])
        ratio = report["arms"][FAST_ARM][PRECAST_RATIO]
        self.assertIsNotNone(ratio, "比值 > 1 时不得写 None")
        self.assertGreater(ratio, 1.0, f"比值 > 1 应如实写出，实际为 {ratio}")
        blob = " ".join(report["incomplete_reasons"])
        self.assertIn("precast_ratio", blob)
        self.assertIn("包元数据不一致", blob)

    def test_precast_ratio_zero_denominator_gives_none(self):
        """总帧数为 0 时 precast_ratio 必须是 None（不得写 0.0）。

        WHY: 0.0 会被读成"没有任何预铸音频"，但真实情况是"无法计算"。
        此用例直接测试 _make_sample（产品 API 无法触发 0 帧场景：
        runtime 拒读 0 帧 WAV，属正常防御行为）。
        """
        from eval.bench import _make_sample

        class MockResult:
            """模拟 total_duration_ms=0 的执行结果。"""
            events = []
            total_duration_ms = 0
            first_audio_ms = 0.0
            tts_calls = 0

        sample = _make_sample(
            arm=FAST_ARM,
            index=0,
            turn_id="test-fast-0000",
            plan_units=self.units,
            result=MockResult(),
            pack=self.pack,
            units=len(self.units),
        )
        self.assertIsNone(sample[PRECAST_RATIO], "总帧数为 0 时 precast_ratio 必须是 None")
        self.assertEqual(sample["total_frames"], 0)
        self.assertEqual(sample["precast_frames"], 0)


# ---------------------------------------------------------------------------
# 11. CLI
# ---------------------------------------------------------------------------
class CliTest(BenchCase):
    """CLI 退出码与产物：2 = 配置错且不写报告；0 = report.json + raw/ + audio/ 齐备。"""

    def _cli_argv(self, out_dir, repeats=REPEATS):
        return [
            "--pack", str(self.pack_root),
            "--corpus", str(CORPUS_FILE),
            "--out", str(out_dir),
            "--repeats", str(repeats),
            "--warmup", "0",
        ]

    def test_cli_invalid_repeats_exits_2_without_report(self):
        out_dir = self.fresh_out_dir()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = run_bench_cli(self._cli_argv(out_dir, repeats=5))
        self.assertEqual(code, 2)
        self.assertIn("5", err.getvalue())
        self.assertIn("20", err.getvalue())
        self.assertFalse(out_dir.exists(), "参数错误不得产生报告文件")

    def test_cli_happy_path_exits_0(self):
        out_dir = self.fresh_out_dir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = run_bench_cli(self._cli_argv(out_dir))
        self.assertEqual(code, 0)
        self.assertTrue(out_dir.is_dir())
        self.assertTrue((out_dir / "report.json").exists())
        for arm in (FAST_ARM, SLOW_ARM):
            self.assertTrue((out_dir / "raw" / f"{arm}_samples.jsonl").exists())
            self.assertTrue((out_dir / "raw" / f"{arm}_events.jsonl").exists())
        self.assertGreater(len(list((out_dir / "audio").glob("*.wav"))), 0)
        summary = out.getvalue()
        self.assertIn("[警告]", summary)
        self.assertIn("报告已写入", summary)

    def test_cli_report_matches_run_bench(self):
        """落盘的 report.json 必须与 run_bench 在确定性面上逐值一致（CLI 不得改口径）。

        不比对 timing_metrics / delta 里的时序数值——那些是挂钟时间，
        两次运行本来就该不同（口径只承诺分布方法可复现）。
        """
        out_dir = self.fresh_out_dir()
        reference = run_bench(make_config(self.pack, self.units, out_dir))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = run_bench_cli(self._cli_argv(out_dir))
        self.assertEqual(code, 0)
        written = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        for key in ("schema_version", "seed", "repeats", "warmup_runs", "incomplete",
                    "incomplete_reasons", "deterministic_metrics", "arms", "caliber",
                    "reference_gate", "timing_metrics_meaningful"):
            self.assertEqual(written[key], reference[key],
                             f"{key} 与 run_bench 返回值不一致（CLI 改动了口径）")
        # raw 只比确定性子集：reference 与 CLI 是两次独立运行，raw 里的挂钟字段不同，
        # 字节 sha256 自然不同（路径与行数必须逐值一致）。
        self.assertEqual(
            deterministic_raw_part(written["raw"]), deterministic_raw_part(reference["raw"]),
            "raw 的路径与行数与 run_bench 返回值不一致（CLI 改动了口径）",
        )
        self.assertEqual(written["timing_metrics"]["fast"]["n"],
                         reference["timing_metrics"]["fast"]["n"])
        command = written["command"]
        self.assertTrue(command.startswith("python3 -m eval.bench"))
        for flag in ("--pack", "--corpus", "--out", "--repeats", "--seed", "--plan-id"):
            self.assertIn(flag, command, f"复现命令缺 {flag}")

    def test_cli_missing_pack_exits_2(self):
        out_dir = self.fresh_out_dir()
        argv = self._cli_argv(out_dir)
        argv[argv.index(str(self.pack_root))] = str(self._work / "no-such-pack")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = run_bench_cli(argv)
        self.assertEqual(code, 2)
        self.assertIn("no-such-pack", err.getvalue())
        self.assertFalse(out_dir.exists())

    def test_cli_missing_args_exits_2(self):
        """argparse 的必填参数缺失必须非零退出（不得默默用默认值假装跑通）。"""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                run_bench_cli([])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--pack", err.getvalue())


if __name__ == "__main__":
    unittest.main()
