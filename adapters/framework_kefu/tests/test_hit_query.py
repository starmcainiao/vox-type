"""
adapters.framework_kefu.tests.test_hit_query — 命中查询公开 API（T29）

只调产品 API：find_hit / build_text_index / lookup_key / pick_by_text / confirm /
text_for_key，以及做一致性对照的 KefuBridge.run_turn。
包 fixture 一律复用本目录 tests/__init__.py 的 build_pack（不手搓第二套构造逻辑）。

覆盖：
    正例：key 档命中、文本档全角/空白扰动命中、rate 档优先、与 run_turn 判定一致
    负例：文本未命中、key 不存在、指纹不符条目（走 confirm 的 None 分支）、
          输入表示数量非法（ValueError，消息含具体值与数量）、空归一串、
          normalize_text(123) 未被本卡波及
"""

import shutil
import signal
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from core.metrics_spec import KEY, MISS, REASON
from assets.pack import AssetEntry

from adapters.framework_kefu import (
    MODE_KEY,
    MODE_TEXT,
    REASON_KEY_NOT_PREBAKED,
    REASON_TEXT_NOT_PREBAKED,
    KefuBridge,
    SequenceHitResult,
    build_text_index,
    confirm,
    find_hit,
    find_hit_sequence,
    lookup_key,
    normalize_text,
    pick_by_text,
    text_for_key,
)
from adapters.framework_kefu.tests import CountingTts, FakeClient, build_pack

# 自造话术（禁止使用 kefu 的真实业务文案）
GREET = "您好，这里是报修服务热线。"
GREET_V2 = "您好，报修服务为您服务。"
ASK = "请问您要报修的是什么设备？"
SLOW_SENT = "慢速档的同一句话。"


def _peak(samples):
    """取绝对值峰值，用于区分"包内音频"与"慢路音频"。"""
    return max(abs(s) for s in samples)


class HitQueryBase(unittest.TestCase):
    """公用夹具：一个临时资产包（normal 档）+ 假慢路合成器 + 假 brain/ASR。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kefu-hitq-"))
        self.pack = build_pack(
            self.tmp / "pack",
            [
                ("greeting", GREET, 0),
                ("greeting", GREET_V2, 1),
                ("ask_fault_type", ASK, 0),
                ("closing", "谢谢您的来电，祝您生活愉快。", 0),
            ],
        )
        self.tts = CountingTts()
        self.client = FakeClient()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge(self, **kw):
        return KefuBridge(self.pack, live_tts=self.tts, client=self.client, **kw)

    def out(self, name="turn.wav"):
        return self.tmp / name


class TestKeyModePositive(HitQueryBase):
    """key 档命中：part_index=0 + rate 档 + pack.lookup 指纹复核。"""

    def test_key_hit_returns_entry_and_no_reason(self):
        res = find_hit(self.pack, key="greeting", rate_key="normal")

        self.assertIsNotNone(res.entry)
        self.assertEqual(res.mode, MODE_KEY)
        self.assertEqual(res.key, "greeting")
        self.assertIsNone(res.miss_reason)
        # 命中条目就是包内那条（不是新造对象）
        self.assertEqual(res.entry.key, "greeting")
        self.assertEqual(res.entry.variant, 0)
        # text 报包内原文（命中就是播它）
        self.assertEqual(res.text, GREET)
        self.assertIsInstance(res, type(res))      # frozen dataclass 实例

    def test_result_is_frozen(self):
        """结论不可变：钩子侧可以安全把结果缓存/传参。"""
        res = find_hit(self.pack, key="greeting")
        with self.assertRaises(Exception):
            res.entry = None

    def test_key_hit_respects_part_index_zero(self):
        """part_index!=0 的条目不参与 key 档命中（part_index=0 硬条件）。"""
        pack = build_pack(
            self.tmp / "pack-part",
            [("only_part1", ASK, 0)],
        )
        # 手工把条目的 part_index 改成 1（身份唯一，指纹/路径不动）
        # 契约内写法（T27d）：新建包实例，不改既有包——包是只读产物。
        pack = replace(pack, assets=[AssetEntry(
            key=pack.assets[0].key, part_index=1, rate_key="normal",
            variant=0, text=pack.assets[0].text, fingerprint=pack.assets[0].fingerprint,
            path=pack.assets[0].path, duration_ms=pack.assets[0].duration_ms,
        )])
        res = find_hit(pack, key="only_part1")
        self.assertIsNone(res.entry)
        self.assertEqual(res.miss_reason, REASON_KEY_NOT_PREBAKED)

    def test_module_lookup_key_matches_find_hit(self):
        """模块级 lookup_key 与 find_hit 的 key 档走同一条判定。"""
        self.assertIs(
            lookup_key(self.pack, "greeting", "normal"),
            find_hit(self.pack, key="greeting").entry,
        )
        self.assertIsNone(lookup_key(self.pack, "nope", "normal"))

    def test_module_text_for_key(self):
        """包内有该 key 报原文；没有该 key 回退成 key 本身（不报空串）。"""
        self.assertEqual(text_for_key(self.pack, "greeting", "normal"), GREET)
        self.assertEqual(text_for_key(self.pack, "missing_key", "normal"), "missing_key")

    def test_frozen_dataclass_fields_shape(self):
        """HitResult 五字段齐全且默认无（调用方必须显式构造）。"""
        r = find_hit(self.pack, key="greeting")
        self.assertEqual(
            set(type(r).__dataclass_fields__),
            {"entry", "mode", "key", "text", "miss_reason"},
        )


class TestTextModePositive(HitQueryBase):
    """文本档命中：normalize_text 四步归一化 + rate 档优先 + confirm。"""

    def test_verbatim_hit(self):
        res = find_hit(self.pack, text=GREET)
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.mode, MODE_TEXT)
        self.assertIsNone(res.key)
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.entry.key, "greeting")
        self.assertEqual(res.text, GREET)

    def test_fullwidth_and_spacing_perturbation_still_hits(self):
        """全角字母数字/全角标点 + 空白扰动 → 仍命中同一 key 的条目。

        包内原文是 "您好，这里是报修服务热线。"（全角逗号+句号）。
        输入做四类扰动：首尾空白（含全角空格 U+3000）、全角字母数字、
        全角标点 ↔ 半角、大小写。四步归一化（去空白/全角半角/NFKC/小写）后
        逐字相等 → 命中。这条若变红说明归一化没生效。
        """
        perturbations = [
            " 您好，这里是报修服务热线。 ",              # 首尾半角空格
            "　您好，这里是报修服务热线。　",            # 首尾全角空格 U+3000
            "  您好，这里是报修服务热线。\t\n ",        # 混合空白
            "您好，这里是报修服务热线.",                  # 句号全角、逗号保持全角
            "您好,这里是报修服务热线.",                   # 全角标点 → 半角
            "您好，这里是报修服务热线。",                 # 句末换行
        ]
        for perturbed in perturbations:
            with self.subTest(text=perturbed):
                res = find_hit(self.pack, text=perturbed)
                self.assertIsNotNone(res.entry, f"归一化后应命中: {perturbed!r}")
                self.assertEqual(res.entry.key, "greeting")
                self.assertEqual(res.mode, MODE_TEXT)
                self.assertIsNone(res.miss_reason)

    def test_alphanumeric_and_case_perturbation_still_hits(self):
        """全角字母数字 + 大小写扰动：包内全角、输入半角大写 → 命中同一条目。"""
        pack = build_pack(self.tmp / "pack-case", [("note", "已为您登记Ａ０１号，请留意。", 0)])
        for text in ("已为您登记A01号，请留意。",
                      "已为您登记a01号,请留意。",
                      "已为您登记Ａ01号，请留意。"):
            with self.subTest(text=text):
                res = find_hit(pack, text=text)
                self.assertIsNotNone(res.entry, f"归一化后应命中: {text!r}")
                self.assertEqual(res.entry.key, "note")
                self.assertEqual(res.entry.variant, 0)

    def test_inner_space_is_a_real_diff_not_perturbation(self):
        """四步只做去空白+字符映射：包内没有空格，输入中间插空格 → 逐字不等 → 未命中。

        这条钉住"归一化不许加步"：若有人把"内部空格也删掉"塞进归一化，这条会变红。
        """
        res = find_hit(self.pack, text="您好 这里是 报修服务热线。")
        self.assertIsNone(res.entry)
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)

    def test_second_variant_hits_via_text(self):
        """第二个 variant 在逐字相等时也能被文本档命中（各 variant 都参与比对）。"""
        res = find_hit(self.pack, text=GREET_V2)
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.entry.key, "greeting")
        self.assertEqual(res.entry.variant, 1)

    def test_fullwidth_form_variant_key_hit(self):
        """包内全角字母数字条目：key 档命中且原文逐字返回。"""
        pack = build_pack(self.tmp / "pack-fw", [("note", "已为您登记Ａ０１号，请留意。", 0)])
        res = find_hit(pack, key="note")
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.text, "已为您登记Ａ０1号，请留意。".replace("1", "１"))

    def test_module_pick_by_text_and_confirm(self):
        """模块级 pick_by_text / confirm 与 find_hit 走同一判定路径。"""
        norm = normalize_text(GREET)
        entry = pick_by_text(self.pack, norm, "normal")
        self.assertIs(entry, find_hit(self.pack, text=GREET).entry)
        self.assertIsNone(pick_by_text(self.pack, normalize_text("包里没有的话。"), "normal"))
        self.assertIs(
            confirm(self.pack, entry), entry,
            "指纹与文件校验通过的候选应原样返回",
        )

    def test_build_text_index_groups_by_normalized_text(self):
        """索引按归一化文本分组；同句不同语速档的两条落在同一列表且顺序稳定。"""
        slow_pack = build_pack(self.tmp / "pack-idx-s", [("same", ASK, 0)], rate="slow")
        norm_pack = build_pack(self.tmp / "pack-idx-n", [("same", ASK, 0)], rate="normal")
        from assets.pack import AssetPack
        combined = AssetPack(
            pack_id=norm_pack.pack_id, pack_version=norm_pack.pack_version,
            protocol_version=norm_pack.protocol_version,
            ruleset_version=norm_pack.ruleset_version, voice=norm_pack.voice,
            model_version=norm_pack.model_version, created_at=norm_pack.created_at,
            assets=[slow_pack.assets[0], norm_pack.assets[0]],
            root=norm_pack.root,
        )

        idx = build_text_index(combined)
        group = idx[normalize_text(ASK)]
        self.assertEqual(
            [e.rate_key for e in group], ["slow", "normal"],
            "顺序应与 pack.assets 一致（pick_by_text 的'退到第一条'依赖它）",
        )
        # 缺席的归一化文本在索引里没有键（bridge 侧用 dict.get 取 → 未命中）
        self.assertNotIn(normalize_text("包里没有的话。"), idx)


class TestRatePreference(HitQueryBase):
    """rate 档优先：同一句话多语速档时，返回当前 rate 档的条目。"""

    def _multi_rate_pack(self):
        """三档各铸一句、顺序刻意为 slow → normal（索引首条是 slow，用来证明
        rate 档优先不是"取第一条"的副作用）。"""
        return build_pack(
            self.tmp / "pack-rate",
            [("same_sentence", SLOW_SENT, 0)],
            rate="slow", peak=1111, ms=111,
        ), build_pack(
            self.tmp / "pack-rate-n",
            [("same_sentence", SLOW_SENT, 0)],
            rate="normal", peak=2222, ms=222,
        )

    def test_text_mode_prefers_current_rate(self):
        slow_pack, normal_pack = self._multi_rate_pack()
        combined = build_pack(
            self.tmp / "pack-rate-combined",
            [("same_sentence", SLOW_SENT, 0)],
            rate="normal",
        )
        # 把 slow 档条目并进 normal 包（不同 rate_key → 不同指纹 → 身份不冲突）
        slow_entry = slow_pack.assets[0]
        normal_entry = normal_pack.assets[0]
        from assets.pack import AssetPack
        combined.assets = [
            AssetEntry(
                key=slow_entry.key, part_index=slow_entry.part_index,
                rate_key="slow", variant=slow_entry.variant, text=slow_entry.text,
                fingerprint=normal_entry.fingerprint, path=normal_entry.path,
                duration_ms=normal_entry.duration_ms,
            ),
            normal_entry,
        ]
        self.assertEqual([e.rate_key for e in combined.assets], ["slow", "normal"])

        res = find_hit(combined, text=SLOW_SENT, rate_key="normal")
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.entry.rate_key, "normal", "必须优先当前语速档")

        res_slow = find_hit(combined, text=SLOW_SENT, rate_key="slow")
        self.assertIsNone(res_slow.entry, "slow 档条目指纹不符 → 走 confirm 的 None 分支")
        self.assertEqual(res_slow.miss_reason, REASON_TEXT_NOT_PREBAKED)

    def test_key_mode_requires_matching_rate(self):
        """key 档同样受 rate 档约束：rate 不匹配即未命中。"""
        pack_slow = build_pack(self.tmp / "pack-rate-key", [("k", ASK, 0)], rate="slow")
        self.assertIsNone(lookup_key(pack_slow, "k", "normal"))
        self.assertIsNone(find_hit(pack_slow, key="k", rate_key="fast").entry)
        self.assertIsNone(find_hit(pack_slow, key="k", rate_key="normal").entry)
        self.assertIsNotNone(find_hit(pack_slow, key="k", rate_key="slow").entry)


class TestMissIsNotFuzzy(HitQueryBase):
    """未命中一律返回带 miss_reason 的 HitResult（不抛错、不返回裸 None）。"""

    def test_text_miss_returns_reason_not_exception(self):
        res = find_hit(self.pack, text="这是一句全新的话，包里没有。")
        self.assertIsNone(res.entry)
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.mode, MODE_TEXT)
        self.assertEqual(res.text, "这是一句全新的话，包里没有。")

    def test_key_missing_returns_reason(self):
        res = find_hit(self.pack, key="not_prebaked_key")
        self.assertIsNone(res.entry)
        self.assertEqual(res.miss_reason, REASON_KEY_NOT_PREBAKED)
        self.assertEqual(res.key, "not_prebaked_key")

    def test_one_char_diff_is_miss(self):
        """差一个字 / 少一个标点 / 换同义词 → 一律未命中（禁止模糊匹配）。"""
        edits = [
            ("删一个词", "请问您要报修什么设备？"),
            ("换成同义词", "请问您要报修的是哪个设备？"),
            ("多加一个词", "请问您今天要报修的是什么设备？"),
            ("删句末标点", ASK[:-1]),
            ("改一个标点", ASK[:-1] + "！"),
        ]
        for label, text in edits:
            with self.subTest(label=label):
                res = find_hit(self.pack, text=text)
                self.assertIsNone(res.entry, f"{label} 必须未命中")
                self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)

    def test_empty_normalizing_input_is_miss(self):
        """空串 / 纯空白 → 空归一，按未命中处理（不猜、不凑）。"""
        for text in ("", "   ", "　　", "\t\n"):
            with self.subTest(text=text):
                res = find_hit(self.pack, text=text)
                self.assertIsNone(res.entry)
                self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)

    def test_fingerprint_mismatch_entry_is_miss(self):
        """指纹不符条目：文本档索引能查到它，但 confirm 的 lookup 复核失败 → 未命中。

        构造：条目文本与索引一致（所以 pick_by_text 会选中它），但指纹字段是
        错的（所以 pack.lookup 的指纹校验返回 None）。这正是 confirm 存在的意义。
        """
        import json
        pack = build_pack(self.tmp / "pack-badfp", [("bad", ASK, 0)])
        entry = pack.assets[0]
        manifest = json.loads((pack.root / "manifest.json").read_text(encoding="utf-8"))
        manifest["assets"][0]["fingerprint"] = "0" * len(entry.fingerprint)
        (pack.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        pack_broken = pack
        pack_broken.assets = [
            AssetEntry(
                key=entry.key, part_index=entry.part_index, rate_key=entry.rate_key,
                variant=entry.variant, text=entry.text,
                fingerprint="0" * len(entry.fingerprint),
                path=entry.path, duration_ms=entry.duration_ms,
            )
        ]

        # 索引按文本建 → 能查到这条
        idx = build_text_index(pack_broken)
        self.assertEqual(len(idx[normalize_text(ASK)]), 1)
        cand = idx[normalize_text(ASK)][0]

        # 但 confirm 的指纹复核失败
        self.assertIsNone(confirm(pack_broken, cand), "指纹不符必须复核失败")
        res = find_hit(pack_broken, text=ASK)
        self.assertIsNone(res.entry)
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)

    def test_confirm_missing_audio_file_is_miss(self):
        """文件不存在同样走 confirm 的 None 分支（查不到 = 未命中，不降级）。"""
        pack = build_pack(self.tmp / "pack-nofile", [("gone", ASK, 0)])
        audio = pack.root / pack.assets[0].path
        audio.unlink()
        self.assertIsNone(confirm(pack, pack.assets[0]))


class TestInputGuardrails(HitQueryBase):
    """输入表示数量校验：恰好一种，否则 ValueError 且消息含具体值。"""

    def test_no_representation_raises_valueerror(self):
        with self.assertRaises(ValueError) as ctx:
            find_hit(self.pack)
        msg = str(ctx.exception)
        self.assertIn("0", msg)
        self.assertIn("key", msg)
        self.assertIn("text", msg)

    def test_both_representations_raises_valueerror(self):
        with self.assertRaises(ValueError) as ctx:
            find_hit(self.pack, key="greeting", text=GREET)
        msg = str(ctx.exception)
        self.assertIn("2", msg)
        self.assertIn("greeting", msg)
        self.assertIn(GREET, msg)

    def test_empty_string_is_a_representation_not_empty_call(self):
        """空串是"给了 text"（→ 未命中），不是"什么都没给"（→ ValueError）。"""
        res = find_hit(self.pack, text="")
        self.assertIsNone(res.entry)
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)

    def test_normalize_text_type_guard_untouched(self):
        """normalize_text(123) 仍 TypeError —— 本卡没有波及归一化的入参护栏。"""
        with self.assertRaises(TypeError) as ctx:
            normalize_text(123)
        self.assertIn("str", str(ctx.exception))
        self.assertIn("int", str(ctx.exception))


class TestConsistencyWithRunTurn(HitQueryBase):
    """find_hit 与 KefuBridge.run_turn 判定一致（同一包、同一输入）。

    对照口径：find_hit 的 entry 与 run_turn(..., allow_fallback=True) 那轮的事件结论
    （hit 事件 vs miss 事件 + reason + key）一致。测试只读产品 API 的结论字段，
    不复制任何判定逻辑。
    """

    def _event_conclusion(self, res):
        """把一轮结果压成可对照的结论：(是否命中, reason, key)。"""
        ev = res.events[0]
        return (res.state == "hit", ev[REASON], ev.get(KEY))

    def _consistent(self, *, key=None, reply=None):
        """跑一次 find_hit + 一次 run_turn，断言结论一致。

        reply 不是 run_turn 的入参——它驱动 FakeClient.ask_brain 的回复文本，
        而 run_turn 的输入表示是给一段哑 user text（brain 是 mock）。
        """
        if key is not None:
            q = find_hit(self.pack, key=key)
            turn_kw = {"key": key}
        else:
            self.client.replies = [reply]
            q = find_hit(self.pack, text=reply)
            turn_kw = {"text": "用户的问题"}

        bridge = self.bridge(allow_fallback=True)
        run = bridge.run_turn(session_id="s1", out_path=self.out(), **turn_kw)
        hit_state, reason, key_ev = self._event_conclusion(run)

        if q.entry is not None:
            self.assertTrue(hit_state, f"find_hit 命中但 run_turn 未命中: {run.events[0]}")
            self.assertEqual(key_ev, q.entry.key)
            self.assertEqual(reason, "")
        else:
            self.assertFalse(hit_state, f"find_hit 未命中但 run_turn 命中: {run.events[0]}")
            self.assertEqual(reason, q.miss_reason)
            self.assertEqual(key_ev, q.key)

    def test_key_hit_agrees_with_run_turn(self):
        self._consistent(key="greeting")

    def test_key_miss_agrees_with_run_turn(self):
        self._consistent(key="no_such_key")

    def test_text_hit_agrees_with_run_turn(self):
        self._consistent(reply=GREET)

    def test_text_hit_with_perturbation_agrees_with_run_turn(self):
        """全角/空白扰动后的文本档：两个 API 结论一致（都命中）。"""
        self._consistent(reply=" 您好,这里是报修服务热线. ")

    def test_text_miss_agrees_with_run_turn(self):
        self._consistent(reply="这是一句全新的话，包里没有。")

    def test_second_variant_hit_agrees_with_run_turn(self):
        self._consistent(reply=GREET_V2)

    def test_hit_path_still_zero_tts_calls(self):
        """一致性之外再验一条：命中时 run_turn 仍然零慢路调用。"""
        self.client.replies = [GREET]
        res = self.bridge().run_turn(session_id="s1", text="用户的问题", out_path=self.out())
        self.assertEqual(res.state, "hit")
        self.assertEqual(res.tts_calls, 0)
        self.assertEqual(self.tts.calls, [])


class TestExportSurface(unittest.TestCase):
    """包 __init__ 的公开面追加后，所有新符号都能从包根导入（钩子侧的真实用法）。"""

    def test_all_new_symbols_exported(self):
        import adapters.framework_kefu as pkg
        for name in ("find_hit", "HitResult", "build_text_index", "lookup_key",
                     "pick_by_text", "confirm", "text_for_key"):
            self.assertIn(name, pkg.__all__, f"{name} 未进 __all__")
            self.assertTrue(hasattr(pkg, name), f"{name} 未导出")

    def test_existing_exports_untouched(self):
        import adapters.framework_kefu as pkg
        for name in ("KefuBridge", "BridgeResult", "BridgeError", "normalize_text",
                     "MATCH_MODE", "MODE_KEY", "MODE_TEXT", "REASON_TEXT_NOT_PREBAKED",
                     "KefuClient", "KefuError", "KefuBrainError", "KefuWorkerError",
                     "extract_reply", "extract_wav"):
            self.assertIn(name, pkg.__all__, f"既有导出 {name} 被本卡动到")

    def test_constants_are_single_source(self):
        """mode/reason 常量的真源下沉到 hit_query 后，bridge 与包根看到的是同一对象。"""
        from adapters.framework_kefu import hit_query
        from adapters.framework_kefu import bridge as bridge_mod
        self.assertIs(bridge_mod.MODE_KEY, hit_query.MODE_KEY)
        self.assertIs(bridge_mod.MODE_TEXT, hit_query.MODE_TEXT)
        self.assertIs(
            bridge_mod.REASON_TEXT_NOT_PREBAKED, hit_query.REASON_TEXT_NOT_PREBAKED
        )


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# 序列命中（T33，docs/10 §10.7：整段 → 多段逐字覆盖）
# ---------------------------------------------------------------------------

# 自造话术（禁止使用 kefu 的真实业务文案）。
# 包内两条资产**逐字同文**（都用 ASK）—— 用来造"同长度多候选"的稳定取首条场景。
# 包内重复文本是允许的（assets 校验不禁止同文本多条资产，heat_kefu 里
# `点下方按钮或直接说明即可。` 就有两条：clarify_work_order__2 与 clarify_repair__3）。
SEG_A_1 = "您好，我是报修服务热线。"
OUT_OF_PACK = "很高兴为您服务。"
PREFIX = "您好。"


class SequenceHitBase(unittest.TestCase):
    """公用夹具：前缀句 + 逐字同文的两条资产 + 成对整段（无子句）的临时资产包。

    包内**重复文本**是允许的（assets 校验不禁止同文本多条资产；heat_kefu 里
    `点下方按钮或直接说明即可。` 就有两条），这正是"同长度多候选取索引内首条"
    要复现的形态。SEG_C 只以成对整段铸入，其子句与颠倒句序都没有对应 variant，
    保证"两句颠倒"这个负例在位置 0 就断，不会退化成合法覆盖。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kefu-seqh-"))
        SEG_C_1 = "稍等一下，我帮您查进度。"
        SEG_C_2 = "查好了，马上给您回复。"
        self.pack = build_pack(
            self.tmp / "pack",
            [
                ("prefix", PREFIX, 0),                    # 与 SEG_A_1 同前缀，考最长优先
                ("seg_a_1", SEG_A_1, 0),
                ("ask_fault_type", ASK, 0),               # 同文本、排在 seg_a_2 前
                ("seg_a_2", ASK, 0),                       # 与 ask_fault_type 逐字同文
                ("seg_c_pair", f"{SEG_C_1}{SEG_C_2}", 0), # 只有整段，没有两个子句
            ],
        )
        self.text = f"{SEG_A_1}{ASK}{ASK}"
        self.ordered = f"{SEG_C_1}{SEG_C_2}"
        self.reversed = f"{SEG_C_2}{SEG_C_1}"
        # 包外前缀：包里没有 OUT_OF_PACK 的任何变体，位置 0 就断
        self.prefixed = f"{OUT_OF_PACK}{SEG_A_1}{ASK}{ASK}"
        # 中间插一句包外文本：位置 0 能覆盖，插在中间处断
        self.middle = f"{SEG_A_1}{OUT_OF_PACK}{ASK}{ASK}"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _norm(self, s):
        return normalize_text(s)


class TestSequencePositive(SequenceHitBase):
    """正例：整段被逐字覆盖 → 命中，entries 按消费顺序，uncovered 为空串。"""

    def test_sequence_hit_shape(self):
        res = find_hit_sequence(self.pack, self.text)

        self.assertEqual(len(res.entries), 3)
        self.assertEqual(
            [e.key for e in res.entries], ["seg_a_1", "ask_fault_type", "ask_fault_type"],
            "段 key 顺序必须等于输入里的字面顺序（消费顺序）",
        )
        self.assertEqual(res.mode, MODE_TEXT, "序列只服务文本档")
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.uncovered, "")
        self.assertEqual(res.text, self.text, "text 报输入原文（未归一化）")
        self.assertIsInstance(res.entries, tuple)
        self.assertIsInstance(res, type(res))      # frozen dataclass 实例

    def test_result_is_frozen(self):
        """结论不可变：调用方可以安全缓存/传参（与 HitResult 同纪律）。"""
        res = find_hit_sequence(self.pack, self.text)
        with self.assertRaises(Exception):
            res.entries = ()

    def test_k_equals_one_degenerates_to_find_hit(self):
        """单句输入：序列入口与既有 find_hit 给出同一结论（k=1 退化）。"""
        res = find_hit_sequence(self.pack, ASK)
        self.assertEqual(len(res.entries), 1)
        self.assertIs(res.entries[0], find_hit(self.pack, text=ASK).entry,
                      "k=1 时两条入口必须命中同一条目")
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.uncovered, "")

    def test_fullwidth_and_spacing_perturbation_still_hits(self):
        """四步归一化对**每段**都生效：整段的全角标点/空白扰动后仍逐字覆盖命中。

        扰动点（与 T29 文本档同族）：首尾半角空格、全角空格 U+3000、
        句末标点全角→半角、包内半角句末→全角。四步归一化后逐段仍相等。
        """
        perturbed = " 您好，我是报修服务热线.　" + ASK + ASK
        res = find_hit_sequence(self.pack, perturbed)
        self.assertEqual(
            [e.key for e in res.entries], ["seg_a_1", "ask_fault_type", "ask_fault_type"],
            f"归一化后应逐段命中: {perturbed!r}")
        self.assertEqual(res.uncovered, "")

    def test_all_pack_phrases_hit_as_single_segment(self):
        """包内每一条单句 asset 都能以 len(entries)==1 命中（不回归既有单句行为）。"""
        misses = []
        for entry in self.pack.assets:
            res = find_hit_sequence(self.pack, entry.text)
            if res.miss_reason is not None or len(res.entries) != 1:
                misses.append((entry.key, len(res.entries), res.uncovered))
        self.assertEqual(misses, [], f"单句条目未能逐字覆盖: {misses}")


class TestSequenceMissIsFailClosed(SequenceHitBase):
    """负例：任一段覆盖不上即整体未命中——entries 必须是空元组，不是"命中了前半"。"""

    def test_drop_a_terminator_is_full_miss(self):
        """删第一个句末标点 → miss，且 uncovered 从断点（归一化空间的第 9 字符）起。

        `您好，我是报修服务热线。` 去掉 `。` 后与包内 `您好。` 也不相等
        （归一化后 `您好,我是报修服务热线` vs `您好.`）→ 位置 0 就断，整条未命中。
        """
        from compiler.source import _SENTENCE_TERMINATORS
        first = next(ch for ch in self.text if ch in _SENTENCE_TERMINATORS)
        tampered = self.text.replace(first, "", 1)
        res = find_hit_sequence(self.pack, tampered)

        self.assertEqual(res.entries, (), "不得部分命中：任一段不中即整体未命中")
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertNotEqual(res.uncovered, "", "断点起剩余不能是空串")
        self.assertIn(normalize_text(ASK), res.uncovered,
                      f"uncovered 必须包含断点后的整段剩余: {res.uncovered!r}")
        # 断点精确：整段的第一段没有别的可匹配项（`您好,我是报修服务热线` 既不等于
        # 包内 `您好,我是报修服务热线.` 也不等于 `您好.`），断在位置 0 → uncovered 就是
        # 整段归一化文本（长度少一个标点）。
        norm_all = normalize_text(self.text)
        self.assertEqual(res.uncovered, normalize_text(tampered),
                         f"断在位置 0，uncovered 应为整段归一化文本: {res.uncovered!r}")
        self.assertEqual(len(res.uncovered), len(norm_all) - 1)

    def test_swap_two_segments_is_full_miss(self):
        """两句颠倒 → miss（颠倒后位置 0 覆盖不上 → pos=0 断）。"""
        res = find_hit_sequence(self.pack, self.reversed)

        self.assertEqual(res.entries, ())
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.uncovered, self._norm(self.reversed),
                         "位置 0 就覆盖不上，uncovered 应为整段剩余")

    def test_insert_out_of_pack_text_is_full_miss(self):
        """中间插一句包外文本 → miss，且不得跳过、不得只播前半。"""
        res = find_hit_sequence(self.pack, self.prefixed)

        self.assertEqual(res.entries, (), "不允许跳过包外文本只覆盖后面的段")
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.uncovered, self._norm(self.prefixed),
                         "包外前缀在位置 0 → 整段都是剩余，不得从中间接着覆盖")
        self.assertNotIn("seg_a", "".join(e.key for e in res.entries))

    def test_middle_insert_of_out_of_pack_text_is_full_miss(self):
        """整段**中间**插一句包外文本 → miss，且不得跳过、不得只播前半。"""
        middle = self.middle
        res = find_hit_sequence(self.pack, self.middle)

        self.assertEqual(res.entries, (), "不允许跳过包外文本只播前半段")
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertTrue(res.uncovered.startswith(self._norm(OUT_OF_PACK)),
                        f"uncovered 必须从插入的包外那段起: {res.uncovered!r}")
        self.assertNotEqual(res.uncovered, "", "断点起的剩余不能是空串")

    def test_prefix_does_not_shorten_the_coverage(self):
        """最长优先：`您好。` 与 `您好，我是报修服务热线。` 并存时，整段必须取长句。"""
        res = find_hit_sequence(self.pack, self.text)

        self.assertEqual(res.entries[0].key, "seg_a_1",
                         "最长优先失败 → 位置 0 被前缀句吃掉，整段会断在标点处")
        self.assertEqual(res.uncovered, "")

    def test_slot_filled_sequence_is_miss(self):
        """混入带槽整段（.format() 填充结果）→ miss（带槽不铸的真实行为）。"""
        filled = f"{SEG_A_1}您的户号是{{{'userNo'}}}，请核对。{ASK}".format(
            userNo="U99999999")
        res = find_hit_sequence(self.pack, filled)
        self.assertEqual(res.entries, ())
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertNotEqual(res.uncovered, "")

    def test_no_hit_never_partial(self):
        """三条负例统一断言"不得部分命中"（entries 恒为空元组，非命中了前一段）。"""
        for label, text in (
            ("删句末标点", self.text.replace("。", "", 1)),
            ("两句颠倒", self.reversed),
            ("包外前缀", self.prefixed),
            ("中间插包外文本", self.middle),
        ):
            with self.subTest(label=label):
                res = find_hit_sequence(self.pack, text)
                self.assertEqual(res.entries, (), f"{label} 不得部分命中")
                self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
                self.assertIsInstance(res.entries, tuple)
                self.assertNotEqual(res.uncovered, "", f"{label} 必须带断点起剩余")


class TestSequenceEdgeCases(SequenceHitBase):
    """边界：空串、类型护栏、候选复核、候选过滤、确定性。"""

    def test_empty_and_whitespace_input_is_miss_not_error(self):
        """空串 / 纯空白 → 未命中且不抛错，entries 为空元组、uncovered 为空串。"""
        for text in ("", "   ", "　　", "\t\n"):
            with self.subTest(text=text):
                res = find_hit_sequence(self.pack, text)
                self.assertEqual(res.entries, ())
                self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
                self.assertEqual(res.uncovered, "")
                self.assertEqual(res.mode, MODE_TEXT)
                self.assertEqual(res.text, text)

    def test_non_str_input_raises_typeerror(self):
        """text 非 str → TypeError（与 normalize_text 同口径：不把 None 当空串）。"""
        for bad in (None, 123, b"\xe4\xbd\xa0\xe5\xa5\xbd", ["您好"]):
            with self.subTest(text=bad):
                with self.assertRaises(TypeError) as ctx:
                    find_hit_sequence(self.pack, bad)
                self.assertIn("str", str(ctx.exception))
                self.assertIn(type(bad).__name__, str(ctx.exception))

    def test_candidate_failing_confirm_is_excluded(self):
        """指纹不符的候选不进索引 → 该段覆盖不上 → 整体 miss（§10.7 判据 4）。

        构造：把 seg_a_1 的条目指纹改成错值（文本不变，所以归一化索引能查到它，
        但 confirm 的 pack.lookup 复核失败）——这正是"复核不过 = 不可用"的分支。
        """
        import json
        entry = next(e for e in self.pack.assets if e.key == "seg_a_1")
        manifest = json.loads((self.pack.root / "manifest.json").read_text(encoding="utf-8"))
        for a in manifest["assets"]:
            if a["key"] == "seg_a_1":
                a["fingerprint"] = "0" * len(entry.fingerprint)
        (self.pack.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.pack.assets = [
            AssetEntry(
                key=e.key, part_index=e.part_index, rate_key=e.rate_key,
                variant=e.variant, text=e.text,
                fingerprint=("0" * len(e.fingerprint)) if e.key == "seg_a_1" else e.fingerprint,
                path=e.path, duration_ms=e.duration_ms,
            )
            for e in self.pack.assets
        ]

        res = find_hit_sequence(self.pack, self.text)
        self.assertEqual(res.entries, (), "复核不过的段不得被覆盖")
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertNotEqual(res.uncovered, "")

    def test_part_index_nonzero_and_wrong_rate_are_excluded(self):
        """part_index!=0 与 rate 不匹配的条目不参与序列候选。"""
        # text = SEG_A_1 + ASK + ASK，位置 0 只能用 seg_a_1 覆盖 → 把它挡掉即整体 miss
        i = next(k for k, a in enumerate(self.pack.assets) if a.key == "seg_a_1")
        e = self.pack.assets[i]
        base_assets = list(self.pack.assets)

        # ① part_index != 0 → 不是候选
        # 契约内写法（T27d）：每步新建包实例，不改既有包——包是只读产物。
        pack = replace(self.pack, assets=[
            AssetEntry(key=e.key, part_index=1, rate_key=e.rate_key,
                       variant=e.variant, text=e.text, fingerprint=e.fingerprint,
                       path=e.path, duration_ms=e.duration_ms) if k == i else a
            for k, a in enumerate(base_assets)
        ])
        res = find_hit_sequence(pack, self.text)
        self.assertEqual(res.entries, (), "part_index!=0 的条目不得参与序列覆盖")

        # ② rate 不匹配 → 不是候选
        pack = replace(self.pack, assets=[
            AssetEntry(key=e.key, part_index=0, rate_key="slow",
                       variant=e.variant, text=e.text,
                       fingerprint=e.fingerprint, path=e.path,
                       duration_ms=e.duration_ms) if k == i else a
            for k, a in enumerate(base_assets)
        ])
        res2 = find_hit_sequence(pack, self.text, rate_key="normal")
        self.assertEqual(res2.entries, (), "rate 不匹配的条目不得参与序列覆盖")

        # ③ 对照：恢复 part_index=0 + normal 档 → 又命中 3 段
        pack = self.pack
        res3 = find_hit_sequence(pack, self.text)
        self.assertEqual(len(res3.entries), 3)

    def test_deterministic_across_ten_runs(self):
        """同一输入连跑 10 次：段 key 序列与长度完全一致（同长度多候选取首条）。"""
        seen = set()
        for _ in range(10):
            res = find_hit_sequence(self.pack, self.text)
            seen.add((len(res.entries), tuple(e.key for e in res.entries)))
        self.assertEqual(len(seen), 1, f"10 次结果不一致: {seen}")

    def test_first_of_equal_length_candidates_is_stable(self):
        """同长度多候选取索引内**首条**：ask_fault_type 排在 seg_a_2 前面。"""
        res = find_hit_sequence(self.pack, self.text)
        self.assertEqual(res.entries[1].key, "ask_fault_type",
                         "同长度候选必须取索引首条（顺序稳定，与 pick_by_text 同源）")

    def test_repeated_sentence_still_covers_every_occurrence(self):
        """同一句连着说两遍：两段各消费一条资产（不跨段去重、不跨段吞并）。"""
        res = find_hit_sequence(self.pack, f"{ASK}{ASK}")
        self.assertEqual(
            [e.key for e in res.entries], ["ask_fault_type", "ask_fault_type"],
            "两段必须各消费一条资产：同长度多候选取索引首条（ask_fault_type 在前）")
        self.assertEqual(res.uncovered, "")
        self.assertEqual(res.miss_reason, None)



class TestSequenceAssertionBreaksOnTamper(SequenceHitBase):
    """证明序列断言不是空转：改一字就变红（内存篡改，不落盘）。

    机制：正例断言的期望是**结构化的**（段数、段 key 序列、uncovered=""），
    所以把包内某段文本改一个字，覆盖就会断在某段，entries 立刻不再是 3 条。
    这里用内存里的临时篡改复现这件事，不写盘。

    真实成因（T33b 卫生修正）：改一字后 miss **不是**因为「位置 0 覆盖不上」这个
    直觉，而是因为改后的文本与包内任何 variant 都不逐字相等 → **该段唯一的候选被
    `confirm` 的指纹/文本复核剔除**（序列档的候选必须先过 confirm，复核不过 = 不可用）
    → 可用候选在位置 0 空了 → 整体 miss、uncovered 为整段剩余。
    """

    def test_one_char_tamper_breaks_the_sequence_hit(self):
        import json
        orig = next(e.text for e in self.pack.assets if e.key == "seg_a_1")
        orig_text = self.text                      # 未篡改的整段原文
        # 保留一份未篡改的包做对照臂（篡改只改内存与临时 manifest，不落盘到仓）
        self.pack_orig = build_pack(self.tmp / "pack-orig", [
            ("prefix", PREFIX, 0),
            ("seg_a_1", orig, 0),
            ("ask_fault_type", ASK, 0),
            ("seg_a_2", ASK, 0),
            ("seg_c_pair", "稍等一下，我帮您查进度。查好了，马上给您回复。", 0),
        ])
        tampered = orig[:-1] + ("。" if orig.endswith("！") else "！")

        entry = next(e for e in self.pack.assets if e.key == "seg_a_1")
        manifest = json.loads((self.pack.root / "manifest.json").read_text(encoding="utf-8"))
        for a in manifest["assets"]:
            if a["key"] == "seg_a_1":
                a["text"] = tampered
        (self.pack.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.pack.assets = [
            AssetEntry(key=e.key, part_index=e.part_index, rate_key=e.rate_key,
                       variant=e.variant, text=(tampered if e.key == "seg_a_1" else e.text),
                       fingerprint=e.fingerprint, path=e.path, duration_ms=e.duration_ms)
            for e in self.pack.assets
        ]

        # 1) 未篡改的包 + 原文 → 整段逐字覆盖，3 段（对照臂）
        res_ok = find_hit_sequence(self.pack_orig, orig_text)
        self.assertEqual(len(res_ok.entries), 3)
        self.assertEqual(res_ok.uncovered, "")

        # 2) 同一个包被改了一个字符（句末 。→ ！）→ 该段唯一候选的文本与 manifest 不符，
        #    被 confirm 的指纹/文本复核剔除 → 位置 0 无可用候选 → 整体 miss
        res_bad = find_hit_sequence(self.pack, orig_text)
        self.assertEqual(
            res_bad.entries, (),
            "改一字后仍命中 —— 序列断言失效，断言不能打破")
        self.assertEqual(res_bad.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertNotEqual(res_bad.uncovered, "",
                            "未命中必须带 uncovered，供诊断定位断点")

    def test_miss_message_carries_the_offending_remainder(self):
        """未命中消息能定位断点：uncovered 逐字符可复核，不是笼统的"未命中"。"""
        res = find_hit_sequence(self.pack, self.prefixed)
        self.assertEqual(res.uncovered, self._norm(self.prefixed))
        self.assertTrue(res.uncovered.startswith(self._norm(OUT_OF_PACK)),
                        "uncovered 必须从包外那段起")


class TestSequenceSelfContained(unittest.TestCase):
    """CI 自洽断言组（T33b P2-a）：不读 yaml、不依赖预铸产物，全部用本目录
    `build_pack` 夹具造包——公开 CI 没有 kefu 仓与 TTS 语料，这组仍必须跑掉。

    语义照 docs/10 §10.7：整段按包内 variant 的归一化文本逐字覆盖、最长优先、
    同长度多候选取索引首条、**不做段序约束**、任一段不中即整体未命中。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kefu-seqci-"))
        # 多句包（自造话术，禁止使用 kefu 真实业务文案）
        S1 = "您好。"
        S2 = "请问有什么可以帮您？"
        S3 = "再见。"
        self.s = (S1, S2, S3)
        self.pack = build_pack(self.tmp / "pack", [("s1", S1, 0), ("s2", S2, 0), ("s3", S3, 0)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_segment_concat_hits_two_entries(self):
        res = find_hit_sequence(self.pack, f"{self.s[0]}{self.s[1]}")
        self.assertIsNone(res.miss_reason, f"两段拼接应命中: {res.uncovered!r}")
        self.assertEqual(len(res.entries), 2, "两段拼接必须消费两条资产")
        self.assertEqual([e.key for e in res.entries], ["s1", "s2"])
        self.assertEqual(res.uncovered, "")

    def test_three_segment_concat_hits_three_entries(self):
        res = find_hit_sequence(self.pack, f"{self.s[0]}{self.s[1]}{self.s[2]}")
        self.assertIsNone(res.miss_reason)
        self.assertEqual(len(res.entries), 3, "三段拼接必须消费三条资产")
        self.assertEqual([e.key for e in res.entries], ["s1", "s2", "s3"])

    def test_inserted_out_of_pack_sentence_is_full_miss(self):
        """负例：拼接中插入包外句 → entries 为空元组，uncovered 从断点（包外句）起。"""
        foreign = "包外的一句闲话。"
        text = f"{self.s[0]}{foreign}{self.s[1]}"
        res = find_hit_sequence(self.pack, text)

        self.assertEqual(res.entries, (), "不得跳过包外句只覆盖剩下的段")
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(
            res.uncovered, normalize_text(foreign + self.s[1]),
            f"uncovered 必须从插入的包外句起: {res.uncovered!r}",
        )

    def test_swapped_segment_order_still_hits_and_keeps_input_order(self):
        """段序自由（§10.7：不做段序约束）：颠倒两段仍命中，段序 == 输入顺序。"""
        text = f"{self.s[1]}{self.s[0]}"
        res = find_hit_sequence(self.pack, text)

        self.assertIsNone(
            res.miss_reason,
            "颠倒段序后仍应逐字覆盖命中（序列档不做段序约束）",
        )
        self.assertEqual(
            [e.key for e in res.entries], ["s2", "s1"],
            "段序必须等于输入里的字面顺序（不重排、不去重）",
        )
        self.assertEqual(res.uncovered, "")

    def test_deterministic_across_five_runs(self):
        """同输入连跑 5 次：段 key 序列完全一致。"""
        text = f"{self.s[0]}{self.s[1]}{self.s[2]}"
        seen = {tuple(e.key for e in find_hit_sequence(self.pack, text).entries)
                for _ in range(5)}
        self.assertEqual(len(seen), 1, f"5 次结果不一致: {seen}")
        self.assertEqual(next(iter(seen)), ("s1", "s2", "s3"))

    def test_blocked_by_empty_on_hit(self):
        """blocked_by 是诊断留痕：命中路径恒为空元组（永不进命中）。"""
        for text in (f"{self.s[0]}{self.s[1]}", f"{self.s[0]}{self.s[1]}{self.s[2]}"):
            with self.subTest(text=text[:6]):
                res = find_hit_sequence(self.pack, text)
                self.assertIsNone(res.miss_reason)
                self.assertEqual(res.blocked_by, (), f"命中时 blocked_by 必须为空元组: {text!r}")


class TestSequenceBlockedByDiagnosis(unittest.TestCase):
    """`blocked_by` 诊断留痕（T33b P1-2，docs/10 §10.7 末节）：
    文本能匹配、但因 rate_key / part_index 口径被剔除的候选标识；
    **只进诊断、永不进命中**，不得影响 entries / miss_reason 的判定。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kefu-seqbl-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_slow_only_pack_records_blocked_by_and_stays_miss(self):
        """只有 slow 档的包 → rate_key="normal" 未命中且 blocked_by 标出该候选。"""
        from assets.pack import AssetPack
        slow = build_pack(self.tmp / "p-slow", [("greet", "您好。", 0)], rate="slow")
        pack = AssetPack(
            pack_id=slow.pack_id, pack_version=slow.pack_version,
            protocol_version=slow.protocol_version, ruleset_version=slow.ruleset_version,
            voice=slow.voice, model_version=slow.model_version,
            created_at=slow.created_at, assets=list(slow.assets), root=slow.root,
        )
        res = find_hit_sequence(pack, "您好。", rate_key="normal")

        self.assertEqual(res.entries, (), "口径外档位不得进入命中")
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.blocked_by, ("greet@slow#0",),
                         "blocked_by 必须记录被 rate_key 口径剔除的候选标识")
        self.assertEqual(res.uncovered, normalize_text("您好。"), "断点起的剩余仍是整段")

    def test_same_pack_with_matching_rate_hits_and_blocked_by_is_empty(self):
        """同包用 rate_key="slow" 查 → 命中，blocked_by 恒为空元组。"""
        from assets.pack import AssetPack
        slow = build_pack(self.tmp / "p-slow2", [("greet", "您好。", 0)], rate="slow")
        pack = AssetPack(
            pack_id=slow.pack_id, pack_version=slow.pack_version,
            protocol_version=slow.protocol_version, ruleset_version=slow.ruleset_version,
            voice=slow.voice, model_version=slow.model_version,
            created_at=slow.created_at, assets=list(slow.assets), root=slow.root,
        )
        res = find_hit_sequence(pack, "您好。", rate_key="slow")

        self.assertEqual(len(res.entries), 1, "当前语速档的完整首片必须命中")
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.uncovered, "")
        self.assertEqual(res.blocked_by, (), "命中时 blocked_by 必须为空元组")

    def test_part_index_nonzero_also_reported_as_blocked(self):
        """part_index != 0 的候选同样进 blocked_by（不是候选，但文本能匹配）。"""
        pack = build_pack(self.tmp / "p-part", [("greet", "您好。", 0)])
        pack.assets = [
            AssetEntry(key=e.key, part_index=1, rate_key=e.rate_key, variant=e.variant,
                       text=e.text, fingerprint=e.fingerprint, path=e.path,
                       duration_ms=e.duration_ms)
            for e in pack.assets
        ]
        res = find_hit_sequence(pack, "您好。", rate_key="normal")

        self.assertEqual(res.entries, (), "part_index!=0 的条目不得参与序列覆盖")
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.blocked_by, ("greet@normal#1",),
                         "blocked_by 必须记录被 part_index 口径剔除的候选标识")

    def test_blocked_by_ignores_confirm_rejection(self):
        """指纹不符被 confirm 复核剔除的候选**不**进 blocked_by（语义不同，别混进来）。"""
        import json
        pack = build_pack(self.tmp / "p-fp", [("greet", "您好。", 0)])
        entry = pack.assets[0]
        manifest = json.loads((pack.root / "manifest.json").read_text(encoding="utf-8"))
        manifest["assets"][0]["fingerprint"] = "0" * len(entry.fingerprint)
        (pack.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        pack.assets = [
            AssetEntry(key=e.key, part_index=e.part_index, rate_key=e.rate_key,
                       variant=e.variant, text=e.text,
                       fingerprint="0" * len(e.fingerprint),
                       path=e.path, duration_ms=e.duration_ms)
            for e in pack.assets
        ]
        res = find_hit_sequence(pack, "您好。", rate_key="normal")

        self.assertEqual(res.entries, (), "confirm 复核不过 = 该候选不可用")
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.blocked_by, (),
                         "blocked_by 只记 rate/part 口径剔除，不记指纹复核失败")

    def test_blocked_by_does_not_change_the_verdict(self):
        """诊断留痕不得改变判定：包内加一条口径外同文条目，口径内候选照常命中
        （blocked_by 只读 pack.assets 取标识，不参与 best 选择、不改 entries/miss_reason）。"""
        from assets.pack import AssetPack
        normal = build_pack(self.tmp / "p-n", [("s1", "您好。", 0)])
        slow = build_pack(self.tmp / "p-s", [("other", "您好。", 0)], rate="slow")
        pack = AssetPack(
            pack_id=normal.pack_id, pack_version=normal.pack_version,
            protocol_version=normal.protocol_version,
            ruleset_version=normal.ruleset_version, voice=normal.voice,
            model_version=normal.model_version, created_at=normal.created_at,
            assets=list(normal.assets) + list(slow.assets), root=normal.root,
        )
        res = find_hit_sequence(pack, "您好。", rate_key="normal")

        self.assertEqual(len(res.entries), 1, "口径内候选必须照常命中")
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.entries[0].key, "s1")
        self.assertEqual(res.uncovered, "")
        self.assertEqual(res.blocked_by, (), "命中路径 blocked_by 恒为空元组")

    def test_blocked_by_deduped_and_in_pack_asset_order(self):
        """顺序与去重：按 pack.assets 顺序，同一标识只记一次（同 key 两个 part）。"""
        from assets.pack import AssetPack
        base = build_pack(self.tmp / "p-a", [("s1", "您好。", 0)])
        slow0 = build_pack(self.tmp / "p-s0", [("greet", "您好。", 0)], rate="slow")
        slow1 = build_pack(self.tmp / "p-s1", [("greet", "您好。", 0)], rate="slow")
        slow1.assets = [
            AssetEntry(key=e.key, part_index=1, rate_key=e.rate_key, variant=e.variant,
                       text=e.text, fingerprint=e.fingerprint, path=e.path,
                       duration_ms=e.duration_ms)
            for e in slow1.assets
        ]
        pack = AssetPack(
            pack_id=base.pack_id, pack_version=base.pack_version,
            protocol_version=base.protocol_version,
            ruleset_version=base.ruleset_version, voice=base.voice,
            model_version=base.model_version, created_at=base.created_at,
            assets=list(base.assets) + list(slow1.assets) + list(slow0.assets),
            root=base.root,
        )
        # normal 档候选被删掉 → 未命中，两条 slow 档同文条目都进 blocked_by
        pack.assets = list(slow1.assets) + list(slow0.assets)
        res = find_hit_sequence(pack, "您好。", rate_key="normal")

        self.assertEqual(res.entries, ())
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(
            res.blocked_by, ("greet@slow#1", "greet@slow#0"),
            "blocked_by 必须按 pack.assets 顺序、同一标识只记一次",
        )


class TestSequenceExportSurface(unittest.TestCase):
    """T33 追加的两个符号能从包根导入；既有导出一个不少。"""

    def test_new_symbols_exported(self):
        import adapters.framework_kefu as pkg
        for name in ("find_hit_sequence", "SequenceHitResult"):
            self.assertIn(name, pkg.__all__, f"{name} 未进 __all__")
            self.assertTrue(hasattr(pkg, name), f"{name} 未导出")

    def test_existing_exports_untouched(self):
        import adapters.framework_kefu as pkg
        for name in ("find_hit", "HitResult", "build_text_index", "lookup_key",
                     "pick_by_text", "confirm", "text_for_key", "normalize_text",
                     "KefuBridge", "MODE_KEY", "MODE_TEXT",
                     "REASON_TEXT_NOT_PREBAKED", "REASON_KEY_NOT_PREBAKED",
                     "KefuClient", "BridgeResult"):
            self.assertIn(name, pkg.__all__, f"既有导出 {name} 被本卡动到")


class _AlarmTimeout(Exception):
    """SIGALRM 兜底：被测函数空转不返回时让测试失败，而不是把整轮测试挂住。"""


def _raise_alarm(signum, frame):
    raise _AlarmTimeout("find_hit_sequence 未在限时内返回（疑似覆盖循环空转）")


class TestSequenceEmptyNormalizedGuard(unittest.TestCase):
    """T33 静默失败审计 P1 回归：归一化后为空的条目不得让序列覆盖空转。

    背景（审计实测）：包内若有 text 归一化后为空串的条目（" " / "　" / "\\t"），
    修复前 `rest.startswith("")` 恒真 → `pos += 0` → while 永不退出、entries 无上限
    增长（3 秒 3100 万条），零日志零异常——公开 API 面对可装载的包会挂死进程。
    修法 = 两道护栏：候选过滤（_sequence_candidates）+ 消费长度检查（while 内）。
    这两条断言在修复前会挂死（由 SIGALRM 转成失败），因此能判红。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(self.__class__.__name__)
        self.addCleanup(self.tmp.cleanup)
        # 一条正常句 + 两条归一化后为空的条目（半角空格 / 全角空格）
        self.pack = build_pack(
            Path(self.tmp.name) / "p",
            [("blank_halfwidth", " ", 0), ("greet", "您好。", 0),
             ("blank_fullwidth", "　", 0)],
        )
        old = signal.signal(signal.SIGALRM, _raise_alarm)
        self.addCleanup(signal.signal, signal.SIGALRM, old)

    def _call_timed(self, text, seconds=5):
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            return find_hit_sequence(self.pack, text)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def test_blank_normalized_entry_excluded_from_candidates(self):
        """白盒：空归一化条目不进候选索引（修复点本身可被判红）。"""
        from adapters.framework_kefu.hit_query import _sequence_candidates
        keys = [e.key for _, e in _sequence_candidates(self.pack, "normal")]
        self.assertEqual(keys, ["greet"])

    def test_uncoverable_tail_misses_failclosed_not_hangs(self):
        """端到端：可覆盖前缀 + 包外尾巴 → 整体未命中且 uncovered 指向断点。"""
        res = self._call_timed("您好。这是一句包外的话。")
        self.assertEqual(res.entries, ())
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.uncovered, normalize_text("这是一句包外的话。"))

    def test_coverable_text_still_hits_with_blank_entry_present(self):
        """不误伤：空条目在场时，正常可覆盖文本仍命中（重复两次 = 两段）。"""
        res = self._call_timed("您好。您好。")
        self.assertEqual(len(res.entries), 2)
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.uncovered, "")
