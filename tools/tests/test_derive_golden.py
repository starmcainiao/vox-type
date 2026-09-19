"""tools/tests/test_derive_golden.py — golden set 派生：两字段口径 / 过滤 / 策略12 / 映射可覆盖。

**卡内判据（验收 6）**：
  ① `user_utterance` 与 `assistant_reply_free` **都必须在**（docs/11 §11.9.3 的范畴错误澄清）；
  ② 占位符 / 过长 / 过短的用户话术被过滤；
  ③ 策略12「其它」→ `expect_kind = "unspecified"`；
  ④ `--strategy-map` 可覆盖默认映射。

全部**离线**、只读临时语料；不复制被验逻辑（断言的是产物形状与字段口径，
过滤与映射判定直接调被测函数）。
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.corpus_fetch import derive_golden as dg  # noqa: E402
from tools.corpus_fetch.derive_golden import (  # noqa: E402
    DEFAULT_STRATEGY_TO_KEY,
    DERIVED_BY,
    MAX_UTTER_LEN,
    MIN_UTTER_LEN,
    DeriveError,
    derive,
    is_usable_utterance,
    load_strategy_map,
    main,
)


def _dialogue(
    user_texts,
    *,
    strategy="策略1 礼貌问候",
    agent_text="您好，请问有什么可以帮您？",
) -> dict:
    """造一条最小组件：用户话术若干轮，每轮之后紧跟一条带策略标注的客服话轮。

    客服话轮**必须紧跟在用户话轮之后**——`derive` 找的是「该用户话术之后的第一条客服话轮」，
    这是 `expect_key` 的来源（docs/11 §11.9.3）。
    """
    turns = []
    for u in user_texts:
        turns.append({"speaker": "客户", "text": u})
        turns.append({"speaker": "客服", "text": agent_text, "strategy": strategy})
    return {"dialogue": turns}


def _write_corpus(tmp: Path, dialogues) -> Path:
    p = tmp / "CSConv.json"
    p.write_text(json.dumps(dialogues, ensure_ascii=False), encoding="utf-8")
    return p


class UtteranceFilterTests(unittest.TestCase):
    """验收 6 ②：过短 / 过长 / 含占位符一律不进 golden set。"""

    def test_short_utterance_is_excluded(self):
        # 少于 MIN_UTTER_LEN（4）= 无信息量
        self.assertFalse(is_usable_utterance("你好"))
        self.assertFalse(is_usable_utterance(""))
        # 边界：恰好 MIN_UTTER_LEN 应放行
        self.assertTrue(is_usable_utterance("四个月了"))

    def test_long_utterance_is_excluded(self):
        self.assertFalse(is_usable_utterance("字" * (MAX_UTTER_LEN + 1)))
        self.assertTrue(is_usable_utterance("字" * MAX_UTTER_LEN))

    def test_placeholder_utterance_is_excluded(self):
        # 占位符说明已被脱敏替换，不是真实说法（docs/11 §11.6 第 3 条）
        for t in ("请问[公司名称]怎么注销", "我打了[手机号码]没接", "我的[姓名]是张三"):
            self.assertFalse(is_usable_utterance(t), t)
        # 连续星号（打码）同样排除
        self.assertFalse(is_usable_utterance("身份证 1234**5678"))

    def test_clean_utterance_passes(self):
        self.assertTrue(is_usable_utterance("这个月工资延迟了，周转不开"))


class StrategyTwelveTests(unittest.TestCase):
    """验收 6 ③：策略12「其它」→ expect_kind = unspecified（不得命中任何预铸 key）。

    docs/11 §11.5：这一档**必须存在**，否则把所有轮次都算成「应该有 key」，命中率是虚高的。
    """

    def test_strategy12_maps_to_unspecified(self):
        tmp = Path("/tmp/vox-derive-unspec")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(
                tmp,
                [_dialogue(["我这边情况比较特殊", "具体怎么算分期"], strategy="策略12 其它")],
            )
            cases, _unk = derive(corpus, cap_per_strategy=50)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(len(cases), 2)
        for c in cases:
            self.assertEqual(c["expect_key"], None)
            self.assertEqual(c["expect_kind"], "unspecified")
            self.assertEqual(c["source_strategy"], "策略12 其它")

    def test_default_map_marks_strategy12_as_none(self):
        # 卡内要求：默认映射里 策略12 的值必须是 None（不是空字符串、不是缺失）
        self.assertIn("策略12 其它", DEFAULT_STRATEGY_TO_KEY)
        self.assertIs(DEFAULT_STRATEGY_TO_KEY["策略12 其它"], None)
        # 其余 11 类都有具体 key
        keyed = [k for k, v in DEFAULT_STRATEGY_TO_KEY.items() if v is not None]
        self.assertEqual(len(keyed), 11)


class TwoFieldTests(unittest.TestCase):
    """验收 6 ①：每条用例必须同时有 user_utterance 与 assistant_reply_free。"""

    def test_every_case_carries_both_fields(self):
        tmp = Path("/tmp/vox-derive-two-fields")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(
                tmp,
                [
                    _dialogue(
                        ["这个月工资延迟了，周转不开", "能分两期吗"],
                        strategy="策略5 情感管理",
                        agent_text="非常理解您的困难，我们来看怎么帮您。",
                    ),
                    _dialogue(["我想问一下还款日期"], strategy="策略7 信息传达", agent_text="本期还款日是 15 号。"),
                ],
            )
            cases, _unk = derive(corpus, cap_per_strategy=50)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(len(cases), 3)
        for c in cases:
            # 两字段都必须存在且非空——缺任一字段 = 另一个实验跑不起来
            self.assertIn("user_utterance", c)
            self.assertIn("assistant_reply_free", c)
            self.assertTrue(len(c["user_utterance"]) >= MIN_UTTER_LEN)
            self.assertTrue(c["assistant_reply_free"])
            # 两个字段必须是**不同的句子**（用户说的话 vs 助手说的话）
            self.assertNotEqual(c["user_utterance"], c["assistant_reply_free"])
            # 来源标注：半自动，且写明标注者与日期
            self.assertEqual(c["labeled_by"], DERIVED_BY)
            self.assertEqual(c["source_id"], "tongyi_dianjin/DianJin-CSC-Data")
            self.assertIn("/", c["turn_ref"])

    def test_user_utterance_comes_from_the_customer_turn(self):
        # 口径回归：user_utterance 必须是客户说的话，不是客服的
        tmp = Path("/tmp/vox-derive-role")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(
                tmp,
                [
                    {
                        "dialogue": [
                            {"speaker": "客户", "text": "我这个账户登不上去"},
                            {"speaker": "客服", "text": "好的我帮您查一下", "strategy": "策略3 重述或转述"},
                        ]
                    }
                ],
            )
            cases, _unk = derive(corpus, cap_per_strategy=10)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["user_utterance"], "我这个账户登不上去")
        self.assertEqual(cases[0]["assistant_reply_free"], "好的我帮您查一下")


class UnknownStrategyTests(unittest.TestCase):
    """T12b 修 4：未知策略标签跳过**必须留痕**（计数 + 打印 + 非零退出），不猜。

    原来的形态是 `if strategy not in mapping: continue`，上一行注释写「一律跳过并留痕」，
    实际**没有任何计数或日志**——轮次静默消失、golden set 静默残缺、命中率口径失真。
    「注释说做了实际没做」正是本仓 hunted 的假陈述，所以这里把注释的行为钉成真的。
    """

    def test_unknown_strategy_is_skipped_not_guessed(self):
        tmp = Path("/tmp/vox-derive-unknown")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(
                tmp,
                [_dialogue(["我想知道怎么注销"], strategy="策略99 不存在")],
            )
            cases, unk = derive(corpus, cap_per_strategy=50)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(cases, [])
        # 修 4：跳过的未知策略必须**被记下来**（Counter 里点名 + 条数），不再是「无痕迹的 continue」
        self.assertEqual(unk, {"策略99 不存在": 1})

    def test_unknown_strategy_labels_are_counted_per_label(self):
        tmp = Path("/tmp/vox-derive-unkcount")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(
                tmp,
                [
                    _dialogue(["我想注销账户怎么办"], strategy="策略99 不存在"),
                    _dialogue(["这个费率怎么算的"], strategy="策略99 不存在"),
                    _dialogue(["我想问下额度问题"], strategy="策略88 也不存在"),
                ],
            )
            cases, unk = derive(corpus, cap_per_strategy=50)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(cases, [])
        self.assertEqual(unk["策略99 不存在"], 2)   # 按标签分别计数，不是只记总数
        self.assertEqual(unk["策略88 也不存在"], 1)
        self.assertEqual(sum(unk.values()), 3)
        # 计数不污染 cases：跳过的轮次不产生任何用例
        self.assertEqual(len(cases), 0)

    def test_main_prints_the_skipped_labels_and_exits_nonzero(self):
        """验收 5 实测：映射表外的策略标签 → 结尾打印跳过清单 + 退出码非 0。"""
        tmp = Path("/tmp/vox-derive-unkcli")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(
                tmp,
                [
                    _dialogue(["请问客服电话多少"], strategy="策略7 信息传达"),
                    _dialogue(["我这边情况特殊一些"], strategy="策略77 未登记"),
                ],
            )
            out = tmp / "out.jsonl"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--corpus", str(corpus), "--out", str(out)])
            printed = buf.getvalue()
            lines = out.read_text(encoding="utf-8").strip().splitlines()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertNotEqual(rc, 0)                     # 跑批铁律：非零退出码
        self.assertEqual(rc, 4)
        self.assertIn("策略77 未登记", printed)        # 必须打印被跳过的**具体标签**
        self.assertIn("跳过", printed)
        # 用例照写盘（已知的策略不受影响），但退出码已经表明产物不完整
        self.assertEqual(len(lines), 1)

    def test_main_exits_zero_and_prints_nothing_when_all_known(self):
        """验收 5 正例：全部策略都在映射表内 → 打印里不含跳过项，退出码 0。"""
        tmp = Path("/tmp/vox-derive-allknown")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(
                tmp,
                [
                    _dialogue(["请问客服电话多少"], strategy="策略7 信息传达"),
                    _dialogue(["我这边情况特殊一些"], strategy="策略12 其它"),
                ],
            )
            out = tmp / "out.jsonl"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--corpus", str(corpus), "--out", str(out)])
            printed = buf.getvalue()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(rc, 0)
        self.assertNotIn("跳过", printed)              # 不含跳过项
        self.assertNotIn("未登记", printed)


class CapAndCapPerStrategyTests(unittest.TestCase):
    """cap_per_strategy 只限**有 key 的策略**；unspecified 档不设上限（否则 §11.5 要求的档会消失）。"""

    def test_cap_applies_to_keyed_strategies_only(self):
        tmp = Path("/tmp/vox-derive-cap")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(
                tmp,
                [
                    _dialogue(["一", "二", "三", "四"][:1] + [f"用户问题{i}" for i in range(4)],
                              strategy="策略1 礼貌问候"),
                    _dialogue([f"特殊情况{i}" for i in range(4)], strategy="策略12 其它"),
                ],
            )
            cases, _unk = derive(corpus, cap_per_strategy=2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        keyed = [c for c in cases if c["expect_kind"] == "key"]
        unspecified = [c for c in cases if c["expect_kind"] == "unspecified"]
        self.assertEqual(len(keyed), 2)          # 被 cap 截断
        self.assertEqual(len(unspecified), 4)    # 不受 cap 限制


class StrategyMapTests(unittest.TestCase):
    """验收 6 ④：--strategy-map 可覆盖默认映射（业务换了包时不必改代码）。"""

    def test_custom_map_overrides_the_default(self):
        tmp = Path("/tmp/vox-derive-map")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(tmp, [_dialogue(["请问客服电话是多少"], strategy="策略7 信息传达")])
            custom = {"策略7 信息传达": "custom_contact_key"}
            cases, _unk = derive(corpus, cap_per_strategy=10, strategy_to_key=custom)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["expect_key"], "custom_contact_key")

    def test_load_strategy_map_reads_a_json_object(self):
        tmp = Path("/tmp/vox-derive-mapfile")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            p = tmp / "map.json"
            p.write_text(json.dumps({"策略1 礼貌问候": "greeting", "策略12 其它": None}), encoding="utf-8")
            self.assertEqual(load_strategy_map(p)["策略1 礼貌问候"], "greeting")
            self.assertIs(load_strategy_map(p)["策略12 其它"], None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_strategy_map_fails_closed_on_missing_file(self):
        with self.assertRaises(DeriveError):
            load_strategy_map(Path("/tmp/vox-no-such-map-file.json"))

    def test_load_strategy_map_fails_closed_on_bad_json(self):
        tmp = Path("/tmp/vox-derive-badmap")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            p = tmp / "bad.json"
            p.write_text("{not json", encoding="utf-8")
            with self.assertRaises(DeriveError):
                load_strategy_map(p)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_strategy_map_fails_closed_on_non_object(self):
        tmp = Path("/tmp/vox-derive-nonobj")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            p = tmp / "arr.json"
            p.write_text(json.dumps(["策略1", "策略2"]), encoding="utf-8")
            with self.assertRaises(DeriveError):
                load_strategy_map(p)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_strategy_map_fails_closed_on_bad_value_type(self):
        tmp = Path("/tmp/vox-derive-badval")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            p = tmp / "val.json"
            p.write_text(json.dumps({"策略1 礼貌问候": 12345}), encoding="utf-8")
            with self.assertRaises(DeriveError):
                load_strategy_map(p)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EmptyOutputTests(unittest.TestCase):
    """空产物必须是失败，不能写一个空文件让人以为跑成功了（原脚本纪律）。"""

    def test_derive_returns_empty_list_when_nothing_qualifies(self):
        tmp = Path("/tmp/vox-derive-empty")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            corpus = _write_corpus(tmp, [_dialogue(["好"], strategy="策略1 礼貌问候")])
            self.assertEqual(derive(corpus, cap_per_strategy=50)[0], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_non_list_corpus_raises(self):
        tmp = Path("/tmp/vox-derive-nonlist")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            p = tmp / "bad.json"
            p.write_text(json.dumps({"dialogue": []}), encoding="utf-8")
            # 迁到 tools/ 后统一走 DeriveError（原脚本是 ValueError）——断言的是「抛了」，
            # 不锁定异常类型，避免把实现细节当成口径。
            with self.assertRaises((DeriveError, ValueError)):
                derive(p, cap_per_strategy=10)[0]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
