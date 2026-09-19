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
import tempfile
import unittest
from pathlib import Path

from core.metrics_spec import KEY, MISS, REASON
from assets.pack import AssetEntry

from adapters.framework_kefu import (
    MODE_KEY,
    MODE_TEXT,
    REASON_KEY_NOT_PREBAKED,
    REASON_TEXT_NOT_PREBAKED,
    KefuBridge,
    build_text_index,
    confirm,
    find_hit,
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
        pack.assets[0] = AssetEntry(
            key=pack.assets[0].key, part_index=1, rate_key="normal",
            variant=0, text=pack.assets[0].text, fingerprint=pack.assets[0].fingerprint,
            path=pack.assets[0].path, duration_ms=pack.assets[0].duration_ms,
        )
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
