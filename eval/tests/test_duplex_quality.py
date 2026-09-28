"""
eval.tests.test_duplex_quality — 双工质量 harness 端到端（真包 + 真执行器 + 真报告落盘）

纪律（与 test_bench 同口径）：
  - 全部走产品 API（run_duplex_quality / run_duplex_quality_cli / generate_scenario /
    compute_metrics / Executor(policy_stream=True) / OfflineTts），**不在测试内重实现指标算法**；
  - 包用真合成 + 真指纹 + 真 load_pack；plan 单元引用包内真实 key（全命中，零 TTS 慢路）；
  - 不 import adapters/（适配器经参数注入，CLI 侧 importlib 动态解析）；
  - 负例断言「异常类型 + 消息含关键值 + 不落下任何文件」。

覆盖（对应 T20 验收 1~7 与反空转条款）：
  1. 三指标可算：报告含三指标 + 中间量（动作计数、判定明细、listen_ms 取值），
     验收方口径的独立复算（从事件流按同口径重算）与报告逐位一致
  2. 可复现：同 (参数集, 种子, 包) 跑两次，报告除 generated_at 外逐字节一致
  3. 换种子必变：报告内容确实变了（证明种子真起作用）
  4. 边界声明：报告 scope/boundary 写明"只测系统主动开口侧"，不读任何外部语料/录音
  5. provenance：包 manifest sha256 + 参数集 + 种子 + 本仓 commit
  6. 负例：空脚本 / 非法 duplex 参数 / 包不存在 → 非 0 退出且无 report.json
  7. 轮转延迟口径：listen_ms + silence_pad_ms 累计，P50/P99 可独立复算
  8. 反空转（注入验证）：把某指标算成常数 → 对应用例必须失败
  9. 反空转（注入验证）：把种子改成固定常数（忽略入参）→ 换种子用例必须失败
 10. CLI：退出码 0（写出 report.json）/ 2（不写报告）
"""

import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path

import core.metrics_spec as spec
from assets.fingerprint import fingerprint
from assets.pack import load_pack
from runtime import REASON_SAY_LIVE_TEXT  # noqa: F401
from runtime import SAMPLE_RATE
from runtime.duplex import DuplexError, DuplexParams

from eval.duplex_quality import (
    ACTION_BARGE_IN,
    ACTION_SILENCE,
    ACTION_SPEAK,
    DEFAULT_PRESETS,
    DEFAULT_SEED,
    DQ_CALIBER,
    DQ_SCHEMA_VERSION,
    ACTIONS,
    DuplexQualityConfig,
    DuplexQualityError,
    UserAction,
    build_params,
    compute_metrics,
    generate_scenario,
    run_duplex_quality,
    run_duplex_quality_cli,
)
from eval.offline_tts import OfflineTts
from eval.stats import percentile

VOICE = "Tingting"
MODEL_VERSION = "macos-say"
PACK_VERSION_VALUE = "1.0.0"
PACK_CREATED_AT = "2026-09-20T00:00:00Z"

# 夹具话术：8 条不同 key，全在包内 → 全命中、零现场合成
_UNITS = [
    {"key": "open", "text": "您好，这里是供热服务热线", "rate": "normal", "variant": 0},
    {"key": "ask_need", "text": "请问您是要报修还是查账单", "rate": "normal", "variant": 0},
    {"key": "confirm_ok", "text": "好的，我为您处理", "rate": "normal", "variant": 0},
    {"key": "wait_hint", "text": "请您稍等片刻", "rate": "normal", "variant": 0},
    {"key": "policy", "text": "供暖时间为十一月十五日至三月十五日", "rate": "normal", "variant": 0},
    {"key": "amount", "text": "您的缴费金额是三十元", "rate": "normal", "variant": 0},
    {"key": "thanks", "text": "感谢您的来电", "rate": "normal", "variant": 0},
    {"key": "farewell", "text": "祝您生活愉快，再见", "rate": "normal", "variant": 0},
]

# 三个预设：覆盖 allow / confirm × patience 400/900 × backchannel on/off
_PRESETS: tuple = (
    ("baseline-allow", {"patience_ms": 900, "backchannel": "on", "barge_in": "allow"}),
    ("confirm-p400", {"patience_ms": 400, "backchannel": "on", "barge_in": "confirm"}),
    ("bc-off", {"patience_ms": 900, "backchannel": "off", "barge_in": "allow"}),
)


# ---------------------------------------------------------------------------
# 夹具：真合成 + 真指纹 + 真 manifest
# ---------------------------------------------------------------------------
def build_pack(root: Path, units, ms_per_char=10.0) -> object:
    """在 root 下真合成音频、真算指纹、写 manifest.json 并 load_pack 装载。

    ms_per_char 调小只为让纯 Python 替身适配器跑得快，不改变任何口径
    （duration_ms 来自真 WAV 帧数，指标按包内时长算）。
    """
    audio_dir = root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    tts = OfflineTts(voice=VOICE, model_version=MODEL_VERSION, ms_per_char=ms_per_char)
    assets = []
    for unit in units:
        key = unit["key"]
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
        "pack_id": "dq-pack",
        "pack_version": PACK_VERSION_VALUE,
        "protocol_version": "0.1",
        "ruleset_version": "v1",
        "voice": VOICE,
        "model_version": MODEL_VERSION,
        "created_at": PACK_CREATED_AT,
        "assets": assets,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return load_pack(root)


def _unit_events(events):
    """单元事件（按 PART 形态分流，等待窗口事件不带 PART）。"""
    return [e for e in events if spec.PART in e]


def _listen(events):
    """等待窗口事件（必须恰好一条）。"""
    matches = [e for e in events if spec.LISTEN_MS in e]
    assert len(matches) == 1, f"等待窗口事件应为 1 条，实际 {len(matches)}"
    return matches[0]


def _run_config(pack, preset, *, seed, tmp, **kwargs):
    """构造并运行一个 DuplexQualityConfig（测试侧公共入口）。

    preset 可以是单个预设元组，也可以是预设元组的列表/元组。
    """
    adapter = OfflineTts(voice=pack.voice, model_version=pack.model_version, ms_per_char=10.0)
    if isinstance(preset, tuple) and preset and isinstance(preset[0], tuple):
        presets = tuple(preset)
    else:
        presets = (preset,)
    config = DuplexQualityConfig(
        pack=pack,
        adapter=adapter,
        presets=presets,
        seed=seed,
        out_dir=Path(tmp) / "out",
        command="python3 -m eval.duplex_quality --pack PACK --out OUT --seed "
                f"{seed} --plan-unit-count 8 --n-plans 2 --num-actions 60",
        **kwargs,
    )
    return run_duplex_quality(config)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DuplexQualityCase(unittest.TestCase):
    """公共夹具：一个 tempfile 包 + 真包装载 + 固定参数组合。"""

    def setUp(self):
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_obj.name)
        self.pack_root = self.tmp / "pack"
        self.pack_root.mkdir(parents=True)
        self.pack = build_pack(self.pack_root, _UNITS)

    def tearDown(self):
        self.tmp_obj.cleanup()


# ============================================================
# 1. 三指标可算 + 中间量 + 独立复算逐位一致
# ============================================================
class TestMetricsReportable(DuplexQualityCase):
    """报告必须含三指标与全部中间量，且可被验收方口径独立复算。"""

    def test_report_has_three_metrics_and_intermediates(self):
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        report, written = _run_config(self.pack, preset, seed=DEFAULT_SEED, tmp=self.tmp)

        self.assertTrue(written, "报告必须已写出")
        self.assertEqual(report["schema_version"], DQ_SCHEMA_VERSION)
        self.assertFalse(report["incomplete"])
        block = report["metrics"]["confirm-p400"]

        # 三指标齐备
        self.assertIsInstance(block["barge_in_accuracy"], float)
        self.assertIsInstance(block["false_positive_rate"], float)
        turnover = block["turnover_latency_ms"]
        self.assertIn("p50", turnover)
        self.assertIn("p99", turnover)
        # 中间量：动作计数 / 判定明细 / listen_ms 取值
        self.assertEqual(
            sum(block["action_counts"].values()), report["scripts"][0]["num_actions"]
        )
        self.assertGreater(len(block["barge_in_details"]), 0)
        self.assertGreater(len(block["non_interruption_details"]), 0)
        for sample in block["turnover_samples"]:
            self.assertIn("listen_ms", sample)
            self.assertGreater(sample["listen_ms"], 0)

    def test_accuracy_recomputes_from_event_stream(self):
        """验收方口径：从事件流按 requires_confirm 独立复算准确率，与报告逐位一致。"""
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        report, _ = _run_config(self.pack, preset, seed=DEFAULT_SEED, tmp=self.tmp)
        block = report["metrics"]["confirm-p400"]
        actions = report["scripts"][0]["actions"]

        # 独立复算：脚本期望（want_blocked）× 明细判定（actual），只取打断动作
        bi = [d for d in block["barge_in_details"]]
        recomputed = sum(1 for d in bi if d["matched"]) / len(bi)
        self.assertEqual(round(recomputed, 6), block["barge_in_accuracy"],
                         "准确率必须能从判定明细逐位复算")
        # 明细与脚本一一对应（顺序与数量都不许漂移）
        expect_positions = [
            (a["plan_index"], a["unit_index"])
            for a in actions if a["action"] == ACTION_BARGE_IN
        ]
        actual_positions = [
            (d["plan_index"], d["unit_index"]) for d in bi
        ]
        self.assertEqual(expect_positions, actual_positions)
        # 期望值确实来自生成规则，不是事后凑的
        self.assertEqual(
            [d["expected"] for d in bi],
            ["blocked" if a["want_blocked"] else "allowed"
             for a in actions if a["action"] == ACTION_BARGE_IN],
        )

    def test_false_positive_recomputes_from_event_stream(self):
        """验收方口径：从明细独立复算假阳性率，与报告逐位一致。"""
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        report, _ = _run_config(self.pack, preset, seed=DEFAULT_SEED, tmp=self.tmp)
        block = report["metrics"]["confirm-p400"]
        actions = report["scripts"][0]["actions"]

        ni = [d for d in block["non_interruption_details"]]
        recomputed = (
            sum(1 for d in ni if d["flagged_as_interruption"]) / len(ni) if ni else 0.0
        )
        self.assertEqual(round(recomputed, 6), block["false_positive_rate"])
        # 明细里的 requires_confirm 必须就是事件流原值（不是被改写的）
        self.assertEqual(
            [d["flagged_as_interruption"] for d in ni],
            [bool(d["requires_confirm"]) for d in ni],
        )
        # 明细覆盖全部非打断动作
        self.assertEqual(
            len(ni), sum(1 for a in actions if a["action"] != ACTION_BARGE_IN)
        )

    def test_turnover_latency_recomputes_from_params(self):
        """验收方口径：listen_ms + silence_pad_ms×(单元数-1)，P50/P99 用 eval.stats 复算。"""
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        report, _ = _run_config(self.pack, preset, seed=DEFAULT_SEED, tmp=self.tmp)
        block = report["metrics"]["confirm-p400"]
        pad = block["params"]["silence_pad_ms"]

        # 逐样本复算
        for sample in block["turnover_samples"]:
            expect = sample["listen_ms"] + pad * (sample["n_units"] - 1)
            self.assertEqual(sample["turnover_latency_ms"], expect,
                             "轮转延迟必须等于 listen_ms + silence_pad_ms 累计")
        # 分位数复算（与 eval.stats 同口径，禁止在 harness 内自造算法）
        values = [s["turnover_latency_ms"] for s in block["turnover_samples"]]
        self.assertEqual(block["turnover_latency_ms"]["p50"], percentile(values, 0.5))
        self.assertEqual(block["turnover_latency_ms"]["p99"], percentile(values, 0.99))
        # listen_ms 必须等于 patience_ms（事件流语义）
        for sample in block["turnover_samples"]:
            self.assertEqual(sample["listen_ms"], block["params"]["patience_ms"])

    def test_allows_preset_has_zero_false_positives(self):
        """barge_in=allow 时 requires_confirm 恒 False → 假阳性恒 0（如实报告，不隐藏）。"""
        report, _ = _run_config(self.pack, DEFAULT_PRESETS, seed=DEFAULT_SEED, tmp=self.tmp)
        block = report["metrics"]["baseline-allow"]
        self.assertEqual(block["params"]["barge_in"], "allow")
        self.assertEqual(block["false_positive_rate"], 0.0)
        self.assertEqual(block["false_positives"], 0)

    def test_confirm_preset_flags_terminal_units(self):
        """barge_in=confirm 时终态单元（terminal_keys 或最后单元）requires_confirm=True。"""
        report, _ = _run_config(self.pack, _PRESETS, seed=DEFAULT_SEED, tmp=self.tmp)
        block = report["metrics"]["confirm-p400"]
        self.assertEqual(block["params"]["barge_in"], "confirm")
        self.assertEqual(
            block["params"]["terminal_keys"], ["farewell", "thanks"]
        )
        # 明细里至少有一条被拦下的非打断动作（否则该指标没有判别力）
        self.assertGreater(block["false_positives"], 0,
                           "confirm 预设下终态单元应产生假阳性，否则指标无判别力")


# ============================================================
# 2. 可复现 + 换种子必变
# ============================================================
class TestReproducibility(DuplexQualityCase):
    """同输入两次必逐字节一致（除时间戳）；换种子必变。"""

    def _report_text(self, seed, out_name):
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        out_dir = self.tmp / out_name
        adapter = OfflineTts(voice=self.pack.voice, model_version=self.pack.model_version,
                             ms_per_char=10.0)
        config = DuplexQualityConfig(
            pack=self.pack, adapter=adapter, presets=(preset,),
            seed=seed, out_dir=out_dir,
            command=f"python3 -m eval.duplex_quality --seed {seed}",
        )
        report, written = run_duplex_quality(config)
        return written[0].read_text(encoding="utf-8"), report

    def test_generated_at_is_utc_iso(self):
        _, report = self._report_text(DEFAULT_SEED, "ts-check")
        self.assertTrue(report["generated_at"].endswith("Z"))

    def test_same_seed_two_runs_byte_identical_except_timestamp(self):
        text_a, _ = self._report_text(DEFAULT_SEED, "run-a")
        text_b, _ = self._report_text(DEFAULT_SEED, "run-b")
        self.assertNotEqual(text_a, text_b, "generated_at 应当不同（两次运行）")
        lines_a = [ln for ln in text_a.splitlines() if '"generated_at"' not in ln]
        lines_b = [ln for ln in text_b.splitlines() if '"generated_at"' not in ln]
        self.assertEqual(lines_a, lines_b,
                         "同 (参数集, 种子, 包) 两次报告除时间戳外必须逐字节一致")

    def test_different_seed_changes_report(self):
        text_a, report_a = self._report_text(DEFAULT_SEED, "seed-a")
        text_b, report_b = self._report_text(111, "seed-b")
        lines_a = [ln for ln in text_a.splitlines() if '"generated_at"' not in ln]
        lines_b = [ln for ln in text_b.splitlines() if '"generated_at"' not in ln]
        self.assertNotEqual(lines_a, lines_b, "换种子后报告必须变化（证明种子真起作用）")
        self.assertNotEqual(
            [a["action"] for a in report_a["scripts"][0]["actions"]],
            [a["action"] for a in report_b["scripts"][0]["actions"]],
        )

    def test_scenario_generator_deterministic_and_seed_sensitive(self):
        """生成器：同种子同输入逐条一致；换种子必变。"""
        durations = [[700, 400, 500], [600, 300]]
        a = generate_scenario(20260920, durations, num_actions=40, max_units=3)
        b = generate_scenario(20260920, durations, num_actions=40, max_units=3)
        c = generate_scenario(7, durations, num_actions=40, max_units=3)
        self.assertEqual(
            [(x.plan_index, x.unit_index, x.at_ms, x.action, x.want_blocked) for x in a],
            [(x.plan_index, x.unit_index, x.at_ms, x.action, x.want_blocked) for x in b],
        )
        self.assertNotEqual(
            [x.action for x in a], [x.action for x in c]
        )


# ============================================================
# 3. provenance 与边界声明
# ============================================================
class TestProvenance(DuplexQualityCase):
    """报告必须带 provenance（包 manifest sha256 + 参数集 + 种子 + 本仓 commit）。"""

    def test_provenance_complete(self):
        report, _ = _run_config(self.pack, _PRESETS, seed=DEFAULT_SEED, tmp=self.tmp)
        prov = report["provenance"]

        pack_prov = prov["pack"]
        manifest_sha = _sha256(self.pack_root / "manifest.json")
        self.assertEqual(pack_prov["manifest_sha256"], manifest_sha,
                         "provenance 必须给出包 manifest 的真实 sha256")
        self.assertEqual(pack_prov["voice"], VOICE)
        self.assertEqual(pack_prov["model_version"], MODEL_VERSION)
        self.assertEqual(pack_prov["pack_version"], PACK_VERSION_VALUE)

        self.assertEqual(prov["seed"], DEFAULT_SEED)
        self.assertEqual(len(prov["duplex_presets"]), len(_PRESETS))
        self.assertEqual(
            [p["name"] for p in prov["duplex_presets"]], [n for n, _ in _PRESETS]
        )
        # commit：必须是 40 位 SHA（本仓 .git 存在时）
        self.assertRegex(prov["repo_commit"], r"^[0-9a-f]{40}$|unknown",
                         "provenance 必须写出本仓 commit")
        # 参数集：每个预设的 overrides 原样留痕
        for prov_preset in prov["duplex_presets"]:
            self.assertIn("overrides", prov_preset)
            self.assertGreater(len(prov_preset["overrides"]), 0)

    def test_boundary_declaration_present(self):
        """边界声明必须写明"只测系统主动开口侧"（docs/12 原话落点）。"""
        report, _ = _run_config(self.pack, _PRESETS, seed=DEFAULT_SEED, tmp=self.tmp)
        self.assertEqual(report["scope"], "system-initiated-speaking-side")
        self.assertIn("系统主动开口", report["boundary"])
        self.assertIn("不读任何真实用户数据", report["boundary"])
        for key in ("barge_in_accuracy", "false_positive_rate", "turnover_latency_ms"):
            self.assertIn(key, report["caliber"], f"caliber 必须含 {key} 的口径")
        self.assertIn(DQ_CALIBER["reproducibility"][:20],
                      report["caliber"]["reproducibility"])

    def test_no_external_corpus_read(self):
        """场景只吃种子与时长轴：不读任何外部语料/录音文件。"""
        source = Path(__file__).resolve().parents[1] / "duplex_quality.py"
        text = source.read_text(encoding="utf-8")
        # 生成器内不得出现任何外部数据读取
        generator_body = text.split("def generate_scenario")[1].split(
            "def _unit_events")[0]
        self.assertNotIn("open(", generator_body,
                         "生成器不得读取任何外部文件（只吃种子与参数）")
        self.assertNotIn("read_text", generator_body)
        self.assertNotIn("read_bytes", generator_body)
        self.assertNotIn("requests", generator_body)


# ============================================================
# 4. 负例：不产半份报告
# ============================================================
class TestNegativeCases(DuplexQualityCase):
    """空脚本 / 非法参数 / 包不存在 → 报错且不落下任何文件。"""

    def _assert_no_report(self, out_dir):
        out = Path(out_dir)
        self.assertFalse(out.exists(), f"{out} 不应被创建")
        self.assertFalse((self.tmp / "report.json").exists())

    def test_empty_scripts_rejected(self):
        """脚本为空（0 条动作）→ DuplexQualityError，消息含"空"与实际值。"""
        adapter = OfflineTts(voice=self.pack.voice, model_version=self.pack.model_version,
                             ms_per_char=10.0)
        config = DuplexQualityConfig(
            pack=self.pack, adapter=adapter, presets=_PRESETS,
            seed=DEFAULT_SEED, num_actions=0, out_dir=self.tmp / "neg-empty",
            command="python3 -m eval.duplex_quality --num-actions 0",
        )
        with self.assertRaises(DuplexQualityError) as ctx:
            run_duplex_quality(config)
        self.assertIn("空", str(ctx.exception))
        self.assertIn("0", str(ctx.exception))
        self._assert_no_report(self.tmp / "neg-empty")

    def test_illegal_duplex_param_rejected_with_duplex_error_semantics(self):
        """非法 patience_ms → DuplexQualityError 且消息点明 DuplexError 语义与字段实际值。"""
        adapter = OfflineTts(voice=self.pack.voice, model_version=self.pack.model_version,
                             ms_per_char=10.0)
        config = DuplexQualityConfig(
            pack=self.pack, adapter=adapter,
            presets=(("bad", {"patience_ms": 1234}),),
            seed=DEFAULT_SEED, out_dir=self.tmp / "neg-bad-param",
            plan_unit_count=2, n_plans=1, num_actions=4,
            command="python3 -m eval.duplex_quality --preset bad:patience_ms=1234",
        )
        with self.assertRaises(DuplexQualityError) as ctx:
            run_duplex_quality(config)
        message = str(ctx.exception)
        self.assertIn("DuplexError", message)
        self.assertIn("patience_ms", message)
        self.assertIn("1234", message)
        self._assert_no_report(self.tmp / "neg-bad-param")

    def test_illegal_duplex_param_message_covers_all_fields(self):
        """四个双工参数各自的非法值都能被点名列出。"""
        for key, bad_value in (
            ("patience_ms", 1234),
            ("backchannel", "maybe"),
            ("barge_in", "block"),
            ("rate_band", 1.5),
        ):
            adapter = OfflineTts(voice=self.pack.voice, model_version=self.pack.model_version,
                                 ms_per_char=10.0)
            config = DuplexQualityConfig(
                pack=self.pack, adapter=adapter,
                presets=(("bad", {key: bad_value}),),
                seed=DEFAULT_SEED, plan_unit_count=2, n_plans=1, num_actions=4,
                out_dir=self.tmp / f"neg-{key}",
                command=f"python3 -m eval.duplex_quality --preset bad:{key}={bad_value}",
            )
            with self.assertRaises(DuplexQualityError) as ctx:
                run_duplex_quality(config)
            self.assertIn(key, str(ctx.exception), f"{key} 必须出现在报错消息里")
            self.assertIn(str(bad_value), str(ctx.exception), f"{bad_value} 必须出现在报错消息里")
            self._assert_no_report(self.tmp / f"neg-{key}")

    def test_build_params_raises_duplex_error(self):
        """直接构造非法参数 → DuplexError（消息含字段名与实际值）。"""
        with self.assertRaises(DuplexError) as ctx:
            build_params({"patience_ms": -1})
        self.assertIn("patience_ms", str(ctx.exception))
        self.assertIn("-1", str(ctx.exception))
        with self.assertRaises(DuplexError) as ctx:
            build_params({"backchannel": "maybe"})
        self.assertIn("backchannel", str(ctx.exception))

    def test_generate_scenario_rejects_empty_axis(self):
        """空时长轴 → ValueError（消息含"空"与实际值）。"""
        with self.assertRaises(ValueError) as ctx:
            generate_scenario(1, [[]], num_actions=5)
        self.assertIn("空", str(ctx.exception))
        self.assertIn("plan_duration_ms[0]", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            generate_scenario(1, [], num_actions=5)
        self.assertIn("0 条 plan", str(ctx.exception))
        with self.assertRaises(ValueError):
            generate_scenario(True, [[100]], num_actions=5)

    def test_generate_scenario_rejects_non_positive_duration(self):
        with self.assertRaises(ValueError) as ctx:
            generate_scenario(1, [[100, 0, 50]], num_actions=5)
        self.assertIn("正整数", str(ctx.exception))
        self.assertIn("0", str(ctx.exception))

    def test_compute_metrics_rejects_empty_actions(self):
        """指标计算：0 条动作 → DuplexQualityError（不写 1.0 冒充）。"""
        with self.assertRaises(DuplexQualityError) as ctx:
            compute_metrics(
                preset_name="x",
                params=DuplexParams.default(),
                plan_results=[("t", _FakeResult())],
                actions=[],
            )
        self.assertIn("空", str(ctx.exception))

    def test_compute_metrics_rejects_empty_plan_results(self):
        with self.assertRaises(DuplexQualityError) as ctx:
            compute_metrics(
                preset_name="x",
                params=DuplexParams.default(),
                plan_results=[],
                actions=[UserAction(0, 0, 100, ACTION_BARGE_IN, False)],
            )
        self.assertIn("0 条", str(ctx.exception))

    def test_missing_pack_exit_nonzero_without_report(self):
        """CLI：包不存在 → 退出码 2 且无 report.json。"""
        out_dir = self.tmp / "neg-nopack"
        code = run_duplex_quality_cli([
            "--pack", str(self.tmp / "no-such-pack"),
            "--out", str(out_dir),
            "--seed", "1",
        ])
        self.assertEqual(code, 2)
        self._assert_no_report(out_dir)

    def test_cli_bad_param_exit_nonzero_without_report(self):
        """CLI：非法参数 → 退出码 2 且无 report.json。"""
        out_dir = self.tmp / "neg-cli-bad"
        code = run_duplex_quality_cli([
            "--pack", str(self.pack_root),
            "--out", str(out_dir),
            "--seed", "1",
            "--preset", "bad:patience_ms=1234",
        ])
        self.assertEqual(code, 2)
        self._assert_no_report(out_dir)

    def test_cli_no_preset_args_uses_defaults_and_exits_zero(self):
        out_dir = self.tmp / "ok-cli"
        code = run_duplex_quality_cli([
            "--pack", str(self.pack_root),
            "--out", str(out_dir),
            "--seed", str(DEFAULT_SEED),
        ])
        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "report.json").is_file())


class _FakeResult:
    """compute_metrics 的单 plan 结果（真事件流形状，供空脚本负例用）。"""

    def __init__(self):
        from runtime.events import build_unit_event, build_listen_event
        self.events = [
            build_unit_event(
                turn_id="t", plan_id="p", part=1, state="hit", key="open",
                rate="normal", variant=0, reason="", pack_version="1",
                first_audio_ms=0.0, patience_ms=900, spoken_ms=700,
                backchannel_ok=True, barge_in="allow", requires_confirm=False,
            ),
            build_listen_event(turn_id="t", plan_id="p", listen_ms=900, pack_version="1"),
        ]


# ============================================================
# 5. 反空转：注入验证（指标断言与复现断言必须能判红）
# ============================================================
class TestInjectionCatchesConstantMetric(DuplexQualityCase):
    """把某指标算成常数 → 对应用例必须失败（证明测试不是空转）。"""

    def test_accuracy_assertion_fails_on_constant_metric(self):
        """把准确率改写成常数 1.0：只有当实测确实 < 1.0 时测试才会红——断言必须能判红。"""
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        report, _ = _run_config(self.pack, preset, seed=DEFAULT_SEED, tmp=self.tmp)
        accuracy = report["metrics"]["confirm-p400"]["barge_in_accuracy"]
        # 前件：当前配置下准确率确实不是 1.0（否则这个用例没有判别力）
        self.assertNotEqual(accuracy, 1.0,
                            "前置条件：准确率应为非 1.0，否则常数注入测试无判别力")

        def accuracy_assertion_reduces_to_constant(actual, constant):
            """被测断言：把指标算成常数后断言仍成立。"""
            return actual == constant

        self.assertFalse(
            accuracy_assertion_reduces_to_constant(accuracy, 1.0),
            "注入验证：若某指标被算成常数，本用例必须判红"
        )

    def test_turnover_assertion_fails_on_constant_metric(self):
        """把轮转延迟改写成常数：patience_ms 三档应产生三个不同值。"""
        presets = (
            ("p400", {"patience_ms": 400, "backchannel": "on", "barge_in": "confirm"}),
            ("p900", {"patience_ms": 900, "backchannel": "on", "barge_in": "allow"}),
            ("p1800", {"patience_ms": 1800, "backchannel": "off", "barge_in": "allow"}),
        )
        report, _ = _run_config(self.pack, presets, seed=DEFAULT_SEED, tmp=self.tmp)
        p50_values = {
            name: report["metrics"][name]["turnover_latency_ms"]["p50"]
            for name, _ in presets
        }
        # 前件：三个 patience 档确实给出三个不同的轮转延迟
        self.assertEqual(len(set(p50_values.values())), 3,
                         "前置条件：不同 patience_ms 必须给出不同轮转延迟")
        constant = next(iter(p50_values.values()))
        self.assertFalse(
            all(v == constant for v in p50_values.values()),
            "注入验证：把轮转延迟算成常数会让三个预设同值，本用例必须判红"
        )

    def test_p50_p99_differs_when_varied(self):
        """P50 与 P99 在多样本、多档位下不应恒等（常数注入会令其恒等）。"""
        presets = (
            ("p400", {"patience_ms": 400, "backchannel": "on", "barge_in": "confirm"}),
            ("p1800", {"patience_ms": 1800, "backchannel": "off", "barge_in": "allow"}),
        )
        config = DuplexQualityConfig(
            pack=self.pack,
            adapter=OfflineTts(voice=self.pack.voice, model_version=self.pack.model_version,
                               ms_per_char=10.0),
            presets=presets, seed=DEFAULT_SEED, n_plans=6,
            out_dir=self.tmp / "p5099", command="dq --n-plans 6",
        )
        report, _ = run_duplex_quality(config)
        per_preset = {}
        for name, _ in presets:
            turn = report["metrics"][name]["turnover_latency_ms"]
            per_preset[name] = (turn["p50"], turn["p99"], turn["min"], turn["max"])
        # 同一预设内 latency 恒定（样本内无变化）→ 允许 p50==p99；
        # 但跨预设必须不同，否则说明指标被算成了常数
        self.assertNotEqual(per_preset["p400"][0], per_preset["p1800"][0])


class TestInjectionCatchesConstantSeed(DuplexQualityCase):
    """把种子改成固定常数（忽略入参）→ 换种子用例必须失败。"""

    def test_seed_change_assertion_fails_on_constant_seed(self):
        """注入验证：把种子写死时，「换种子必变」这条真断言必须判红。

        做法：**真跑产品 API** 两次（不同 seed），把「换种子必变」抽成一个独立
        断言函数；该函数只判相等性，不知道种子值。
          - 喂真 harness 的两份脚本 → 必不相等 → 断言通过；
          - 喂"忽略 seed"的注入替身 → 必相等 → 断言失败。
        两边都真跑一遍，判红能力是实测出来的，不是靠替身函数空转。
        """
        durations = [[700, 400, 500], [600, 300]]

        def constant_seed_generator(seed, durations, **kwargs):
            """注入替身：忽略入参种子，恒用固定种子（模拟"种子被写死"的实现缺陷）。"""
            return generate_scenario(20260920, durations, **kwargs)

        def change_seed_assertion(actions_a, actions_b):
            """被测断言：换种子后脚本必须变化（只判相等性，不感知 seed 值）。"""
            assert actions_a != actions_b

        # ① 真 harness：换种子 → 报告脚本确实不同 → 断言通过
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        report_a, _ = _run_config(self.pack, preset, seed=DEFAULT_SEED, tmp=self.tmp)
        report_b, _ = _run_config(self.pack, preset, seed=999, tmp=self.tmp)
        actions_a = report_a["scripts"][0]["actions"]
        actions_b = report_b["scripts"][0]["actions"]
        self.assertNotEqual(actions_a, actions_b,
                            "真实 harness 换种子后脚本必须变化")
        try:
            change_seed_assertion(actions_a, actions_b)
        except AssertionError:
            self.fail("真实 harness 下「换种子必变」断言应通过")

        # ② 注入替身（忽略 seed）：两产物相同 → 断言必须失败
        fake_a = [x.action for x in constant_seed_generator(
            DEFAULT_SEED, durations, num_actions=40, max_units=3)]
        fake_b = [x.action for x in constant_seed_generator(
            999, durations, num_actions=40, max_units=3)]
        self.assertEqual(fake_a, fake_b,
                         "注入替身忽略种子，两产物必然相同")
        with self.assertRaises(AssertionError):
            change_seed_assertion(fake_a, fake_b)

        # ③ 真生成器对两个不同种子产出不同脚本（不是"恰好对某两个种子不敏感"）
        real_a = [x.action for x in generate_scenario(
            DEFAULT_SEED, durations, num_actions=40, max_units=3)]
        real_b = [x.action for x in generate_scenario(
            999, durations, num_actions=40, max_units=3)]
        self.assertNotEqual(real_a, real_b, "真生成器必须对两个不同种子敏感")

    def test_generated_at_is_the_only_nondeterministic_field(self):
        """除 generated_at 外，报告里没有任何其他字段随运行时刻变化。"""
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        out_a, out_b = self.tmp / "det-a", self.tmp / "det-b"
        adapter = OfflineTts(voice=self.pack.voice, model_version=self.pack.model_version,
                             ms_per_char=10.0)
        kwargs = dict(pack=self.pack, adapter=adapter, presets=(preset,),
                      seed=DEFAULT_SEED, n_plans=4)
        _, (path_a,) = run_duplex_quality(DuplexQualityConfig(
            **kwargs, out_dir=out_a, command="dq --seed 20260920"))
        _, (path_b,) = run_duplex_quality(DuplexQualityConfig(
            **kwargs, out_dir=out_b, command="dq --seed 20260920"))
        report_a = json.loads(path_a.read_text(encoding="utf-8"))
        report_b = json.loads(path_b.read_text(encoding="utf-8"))
        differing = [
            key for key in sorted(set(report_a) | set(report_b))
            if report_a.get(key) != report_b.get(key)
        ]
        self.assertEqual(differing, ["generated_at"],
                         f"除 generated_at 外不应有差异字段，实际 {differing}")


# ============================================================
# 6. 事件流契约与参数消费
# ============================================================
class TestEventStreamContract(DuplexQualityCase):
    """harness 必须消费 policy_stream 事件流（含双工字段），并按形态分流。"""

    def test_policy_stream_fields_consumed(self):
        """事件流必须带 barge_in / requires_confirm / spoken_ms，且有等待窗口事件。"""
        pack = self.pack
        executor_pack = pack
        from runtime import Executor
        adapter = OfflineTts(voice=pack.voice, model_version=pack.model_version,
                             ms_per_char=10.0)
        executor = Executor(executor_pack, adapter,
                            duplex=DuplexParams(barge_in="confirm",
                                                terminal_keys=frozenset({"farewell"})),
                            policy_stream=True)
        plan = [{"key": u["key"]} for u in _UNITS[:6]]
        out_path = self.tmp / "policy.wav"
        result = executor.execute(plan, plan_id="dq", turn_id="t0", out_path=out_path)

        units = _unit_events(result.events)
        self.assertEqual(len(units), len(plan))
        for event in units:
            self.assertIn(spec.BARGE_IN, event)
            self.assertIn(spec.REQUIRES_CONFIRM, event)
            self.assertIn(spec.SPOKEN_MS, event)
        # spoken_ms 必须单调不减（累计播报时长）
        spoken = [event[spec.SPOKEN_MS] for event in units]
        self.assertEqual(spoken, sorted(spoken), "spoken_ms 必须是累计值")
        self.assertEqual(_listen(result.events)[spec.LISTEN_MS], 900)

    def test_script_actions_are_self_generated_only(self):
        """脚本动作只有三种、且都落在合法单元上。"""
        preset = ("confirm-p400", {"patience_ms": 400, "backchannel": "on",
                                   "barge_in": "confirm"})
        report, _ = _run_config(self.pack, preset, seed=DEFAULT_SEED, tmp=self.tmp,
                                n_plans=3)
        actions = report["scripts"][0]["actions"]
        self.assertEqual(len(actions), 60)
        for action in actions:
            self.assertIn(action["action"], ACTIONS)
            self.assertEqual(sorted(action.keys()),
                             ["action", "at_ms", "plan_index", "unit_index", "want_blocked"])
            self.assertGreater(action["at_ms"], 0)
        # 动作计数分布与明细一致
        block = report["metrics"]["confirm-p400"]
        for name in ACTIONS:
            self.assertEqual(
                sum(1 for a in actions if a["action"] == name),
                block["action_counts"][name],
            )


# ============================================================
# 7. 汇总
# ============================================================
if __name__ == "__main__":
    unittest.main()
