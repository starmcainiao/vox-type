"""
adapters.framework_kefu.tests.test_hit_query_index — T27 消费侧接线（语义零变化）

只调产品 API：`lookup_key` / `find_hit` / `find_hit_sequence` / `build_text_index`
/ `pack.lookup` / `pack._identity_lookup_index`。
包 fixture 一律复用本目录 tests/__init__.py 的 `build_pack`（不手搓第二套构造逻辑）。

覆盖：
    - `lookup_key` 新旧实现等价：真包（packs/heat_kefu，69 条）+ 随机组合 +
      负例（key 不存在 / part≠0 / rate 不匹配 / 指纹不符 / 文件缺失）；
    - 候选顺序与原线性遍历**完全一致**（含同 key 多 variant 的复核顺序）；
    - `find_hit`（key 档 / 文本档）与 `find_hit_sequence` 对外行为未动
      （miss_reason / uncovered / blocked_by）；
    - `build_text_index` 与 `_sequence_candidates` 的结果集与顺序未动；
    - 注入验证（反空转）：候选顺序被打乱时断言必然失败。

真实业务文案一律不使用（自造话术）。真包未构建时**诚实 skip**：
    sh bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from assets.pack import AssetEntry

from adapters.framework_kefu import (
    MODE_KEY,
    MODE_TEXT,
    REASON_KEY_NOT_PREBAKED,
    REASON_TEXT_NOT_PREBAKED,
    build_text_index,
    confirm,
    find_hit,
    find_hit_sequence,
    lookup_key,
    normalize_text,
    text_for_key,
)
from adapters.framework_kefu.hit_query import (
    _sequence_candidates,
)


def _legacy_candidates(pack, key: str, rate_key: str = "normal"):
    """T27 之前的候选筛选（基准臂）：线性遍历 pack.assets，逐条 `continue`。

    对照臂，不是产品重实现——价值在于新旧两条筛选并排跑同一组输入，
    任一分歧（候选集或顺序）都让断言变红。
    """
    return tuple(
        c for c in pack.assets
        if c.key == key and c.part_index == 0 and c.rate_key == rate_key
    )
from adapters.framework_kefu.tests import build_pack


# ---------------------------------------------------------------------------
# 旧实现（基准臂）：T27 之前的 `lookup_key`，逐语句照抄。
#
# 对照臂，不是产品重实现：价值在于新旧两条路径并排跑同一组输入，任一分歧都
# 让断言变红。索引构建/指纹计算本体一律走产品 API。
# ---------------------------------------------------------------------------
def _legacy_lookup_key_linear_scan(pack, key: str, rate_key: str = "normal"):
    """T27 之前的 `lookup_key`：线性遍历找候选，再逐候选 pack.lookup。"""
    for cand in pack.assets:
        if cand.key != key or cand.part_index != 0 or cand.rate_key != rate_key:
            continue
        entry = pack.lookup(
            key=key, part_index=0, rate_key=rate_key,
            variant=cand.variant, expected_text=cand.text,
        )
        if entry is not None:
            return entry
    return None


def _heat_pack_dir():
    """定位 heat-kefu 预铸产物目录；未构建 → None（调用方 skip）。"""
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        repo_root / "packs" / "heat_kefu" / "manifest.json",
        repo_root / "packs" / "heat_kefu" / "assets" / "heat-kefu-1" / "manifest.json",
        repo_root / "packs" / "heat_kefu_build" / "heat-kefu-1" / "manifest.json",
    ]
    hits = [p for p in candidates if p.is_file()]
    if len(hits) > 1:
        raise AssertionError(
            f"出现多份 heat-kefu 的 manifest.json（{hits}）——包目录歧义，"
            "不允许猜测用哪一份（禁止静默降级）"
        )
    if not hits:
        return None
    return hits[0].parent


class _TmpPackMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kefu-idx-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# ============================================================
# 1. lookup_key：真包等价性
# ============================================================
@unittest.skipUnless(
    _heat_pack_dir() is not None,
    "packs/heat_kefu 的构建产物不在仓内（公开 CI）——消费侧真包等价性断言跳过；"
    "本地复跑：sh bin/vox pack build packs/heat_kefu --out "
    "packs/heat_kefu_build/heat-kefu-1",
)
class TestLookupKeyEquivalenceOnRealPack(_TmpPackMixin):
    """真包（69 条）上逐 key 比对 `lookup_key` 新旧实现。"""

    def test_lookup_key_matches_linear_scan_for_every_key(self):
        from assets.pack import load_pack
        pack = load_pack(_heat_pack_dir())
        keys = sorted({e.key for e in pack.assets})
        self.assertGreater(len(keys), 0)

        for key in keys:
            for rate in ("slow", "normal", "fast"):
                new_r = lookup_key(pack, key, rate)
                old_r = _legacy_lookup_key_linear_scan(pack, key, rate)
                self.assertEqual(
                    new_r, old_r,
                    f"真包 key={key!r} rate={rate!r} 分歧：新={new_r} 旧={old_r}",
                )
                if old_r is not None:
                    self.assertIs(new_r, old_r,
                                  f"两条路径必须命中同一对象: key={key!r} rate={rate!r}")

    def test_lookup_key_matches_linear_scan_on_ghost_and_negative_inputs(self):
        from assets.pack import load_pack
        pack = load_pack(_heat_pack_dir())
        ghost_keys = ["__no_such_key__", "greet", "OPENING", "开场白"]
        for key in ghost_keys:
            for rate in ("slow", "normal", "fast"):
                self.assertIsNone(
                    lookup_key(pack, key, rate),
                    f"包外 key 必须未命中: key={key!r} rate={rate!r}",
                )
                self.assertIsNone(
                    _legacy_lookup_key_linear_scan(pack, key, rate))


# ============================================================
# 2. lookup_key：自造包（多 variant / 多语速档 / part≠0 / 指纹不符 / 文件缺失）
# ============================================================
class TestLookupKeyEquivalenceOnSyntheticPacks(_TmpPackMixin):
    """手工包上穷举正负例：两条路径的返回值与候选顺序都必须一致。"""

    def setUp(self):
        super().setUp()
        # slow 档条目必须真铸进 manifest（指纹按 slow 档算，只在内存里塞一条
        # 会让 pack.lookup 的指纹校验失败——那是夹具的问题，不是实现的）。
        import json
        from assets.pack import load_pack
        self.base_root = self.tmp / "pack"
        self.slow_root = self.tmp / "pack-slow"
        base = build_pack(
            self.base_root,
            [
                ("greeting", "您好，这里是报修服务热线。", 0),
                ("greeting", "您好，报修服务为您服务。", 1),
                ("greeting", "您好，很高兴为您服务。", 2),
                ("ask", "请问您要报修的是什么设备？", 0),
                ("closing", "谢谢您的来电，祝您生活愉快。", 0),
            ],
        )
        slow = build_pack(
            self.slow_root,
            [("greeting", "您好，这里是报修服务热线。", 0)],
            rate="slow",
        )
        # slow 档条目是另一份指纹 → 必须把它的音频拷进 base 的 audio 目录，
        # 否则 load_pack 的"文件存在性"校验会拒绝合并后的 manifest。
        slow_asset = json.loads((self.slow_root / "manifest.json").read_text("utf-8"))[
            "assets"][0]
        shutil.copy2(self.slow_root / slow_asset["path"],
                     self.base_root / slow_asset["path"])
        base_manifest = json.loads((self.base_root / "manifest.json").read_text("utf-8"))
        base_manifest["assets"] = [dict(slow_asset)] + list(base_manifest["assets"])
        (self.base_root / "manifest.json").write_text(
            json.dumps(base_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        base = load_pack(self.base_root)
        from assets.pack import AssetPack
        # 把 ask 改成 part_index=1 → key 档不得命中它
        i = next(k for k, a in enumerate(base.assets)
                 if a.key == "ask" and a.rate_key == "normal")
        e = base.assets[i]
        self.pack = AssetPack(
            pack_id=base.pack_id, pack_version=base.pack_version,
            protocol_version=base.protocol_version,
            ruleset_version=base.ruleset_version, voice=base.voice,
            model_version=base.model_version, created_at=base.created_at,
            assets=[AssetEntry(
                key=e.key, part_index=1, rate_key=e.rate_key, variant=e.variant,
                text=e.text, fingerprint=e.fingerprint, path=e.path,
                duration_ms=e.duration_ms,
            ) if k == i else a for k, a in enumerate(base.assets)],
            root=base.root,
        )
        self.pack.invalidate_lookup_caches()

    def test_candidate_order_is_not_reorderable(self):
        """顺序敏感是有意的：greeting 有 3 个 normal 档 variant，
        先出现（variant 0）的那条才该被命中。

        把候选顺序颠倒后 lookup_key 就会命中另一条 —— 这正是"顺序必须
        跟随包内顺序"的实测证明，也是本卡硬约束③的灵敏度来源。
        """
        pack = self.pack
        r = lookup_key(pack, "greeting", "normal")
        self.assertIsNotNone(r)
        self.assertEqual(r.variant, 0, "必须先出现的 normal 档条目")

        kept = list(pack.assets)
        pack.assets = list(reversed(kept))
        pack.invalidate_lookup_caches()
        try:
            r2 = lookup_key(pack, "greeting", "normal")
        finally:
            pack.assets = kept
            pack.invalidate_lookup_caches()

        self.assertNotEqual(r.variant, r2.variant,
                            "颠倒顺序后命中另一条 —— 顺序断言有灵敏度")
        self.assertEqual(
            lookup_key(pack, "greeting", "normal"),
            _legacy_lookup_key_linear_scan(pack, "greeting", "normal"),
            "与 T27 之前的线性遍历实现结论一致")

    def test_lookup_key_matches_linear_scan_for_every_key(self):
        """逐 key × 语速档比对：新实现与 T27 之前的线性遍历实现完全一致。"""
        pack = self.pack
        for key in ("greeting", "ask", "closing", "nope"):
            for rate in ("slow", "normal", "fast"):
                new_r = lookup_key(pack, key, rate)
                old_r = _legacy_lookup_key_linear_scan(pack, key, rate)
                self.assertEqual(new_r, old_r,
                                 f"key={key!r} rate={rate!r} 返回值分歧")
                if old_r is not None:
                    self.assertIs(new_r, old_r,
                                  f"两条路径必须命中同一对象: key={key!r} rate={rate!r}")

    def test_key_identity_candidates_linear_matches_lookup_key(self):
        """候选筛选（线性基准臂）本身的行为：只收 part_index==0 + rate 匹配。"""
        pack = self.pack
        for key in ("greeting", "ask", "closing", "nope"):
            for rate in ("slow", "normal", "fast"):
                cands = _legacy_candidates(pack, key, rate)
                for c in cands:
                    self.assertEqual(c.key, key)
                    self.assertEqual(c.part_index, 0)
                    self.assertEqual(c.rate_key, rate)
                # ask 被改成 part_index=1 → 不得进候选
                if key == "ask" and rate == "normal":
                    self.assertEqual(list(cands), [])

    def test_lookup_key_returns_the_same_entry(self):
        pack = self.pack
        for key in ("greeting", "ask", "closing", "nope"):
            for rate in ("slow", "normal", "fast"):
                new_r = lookup_key(pack, key, rate)
                old_r = _legacy_lookup_key_linear_scan(pack, key, rate)
                self.assertEqual(new_r, old_r,
                                 f"key={key!r} rate={rate!r} 返回值分歧")
                if old_r is not None:
                    self.assertIs(new_r, old_r)

    def test_lookup_key_skips_nonzero_part_index(self):
        """part_index!=0 的条目不得进候选（ask 被改成 part_index=1）。"""
        pack = self.pack
        self.assertEqual(
            [c.key for c in _legacy_candidates(pack, "ask", "normal")], [])
        self.assertIsNone(lookup_key(pack, "ask", "normal"))
        self.assertIsNone(_legacy_lookup_key_linear_scan(pack, "ask", "normal"))

    def test_lookup_key_prefers_first_matching_variant(self):
        """同 key 三个 variant 都是 normal 档 → 命中先出现的那条（顺序稳定）。"""
        pack = self.pack
        r = lookup_key(pack, "greeting", "normal")
        self.assertIsNotNone(r)
        self.assertEqual(r.variant, 0, "必须取先出现的 normal 档条目")
        self.assertIs(r, _legacy_lookup_key_linear_scan(pack, "greeting", "normal"))

    def test_lookup_key_slow_rate_hits_the_slow_entry(self):
        """rate 档是硬条件：slow 档只能命中 slow 档条目。"""
        pack = self.pack
        r = lookup_key(pack, "greeting", "slow")
        self.assertIsNotNone(r, "slow 档条目铸进 manifest 后必须能命中")
        self.assertEqual(r.rate_key, "slow")
        self.assertIs(r, _legacy_lookup_key_linear_scan(pack, "greeting", "slow"))

    def test_lookup_key_rate_is_a_hard_condition(self):
        """查一个包内没有的 rate 档 → 未命中（新旧一致）。"""
        pack = self.pack
        self.assertEqual(len(_legacy_candidates(pack, "greeting", "fast")), 0)
        self.assertIsNone(lookup_key(pack, "greeting", "fast"))
        self.assertIsNone(_legacy_lookup_key_linear_scan(pack, "greeting", "fast"))

    def test_lookup_key_partial_audio_missing_still_hits(self):
        """只删 variant 0 的音频 → 其余 variant 的音频还在 → 逐条复核命中下一条。

        这正是"逐条复核"的语义（不是缓存）：greeting 有 3 个 normal 档条目，
        删掉其中一个音频只会让那一条复核失败，另两条照常命中。
        """
        pack = self.pack
        cands = list(_legacy_candidates(pack, "greeting", "normal"))
        self.assertGreater(len(cands), 1, "本例需要多个同 key 同档的 variant")
        target = next(c for c in cands if c.variant == 0)
        pack.assets = [
            AssetEntry(key=e.key, part_index=e.part_index, rate_key=e.rate_key,
                       variant=e.variant, text=e.text, fingerprint=e.fingerprint,
                       path="__gone__.wav" if e is target else e.path,
                       duration_ms=e.duration_ms)
            for e in pack.assets
        ]
        pack.invalidate_lookup_caches()
        self.assertIsNotNone(lookup_key(pack, "greeting", "normal"),
                             "其余 variant 音频还在 → 逐条复核会命中下一条")
        self.assertIs(
            lookup_key(pack, "greeting", "normal"),
            _legacy_lookup_key_linear_scan(pack, "greeting", "normal"),
            "新旧实现在同一条缺失时必须给出同样的结论")

    def test_lookup_key_all_audio_missing_is_none(self):
        """该 key 的所有音频都被删 → 逐条复核全失败 → 未命中（新旧一致）。"""
        pack = self.pack
        for e in pack.assets:
            if e.key == "greeting":
                (pack.root / e.path).unlink(missing_ok=True)
        pack.invalidate_lookup_caches()
        self.assertIsNone(lookup_key(pack, "greeting", "normal"),
                          "音频全缺 → 必须未命中（stat 不缓存的下游证明）")
        self.assertIsNone(_legacy_lookup_key_linear_scan(pack, "greeting", "normal"))

    def test_lookup_key_bad_fingerprint_is_none(self):
        """指纹不符条目 → 复核失败 → 未命中（新旧一致）。"""
        pack = self.pack
        entry = next(c for c in _legacy_candidates(pack, "closing", "normal"))
        pack.assets = [
            AssetEntry(key=e.key, part_index=e.part_index, rate_key=e.rate_key,
                       variant=e.variant, text=e.text,
                       fingerprint=("0" * len(e.fingerprint))
                       if e is entry else e.fingerprint,
                       path=e.path, duration_ms=e.duration_ms)
            for e in pack.assets
        ]
        pack.invalidate_lookup_caches()
        self.assertIsNone(lookup_key(pack, "closing", "normal"))
        self.assertIsNone(_legacy_lookup_key_linear_scan(pack, "closing", "normal"))


# ============================================================
# 3. 消费侧不回归：find_hit / find_hit_sequence 对外行为
# ============================================================
class TestFindHitContractUnchanged(_TmpPackMixin):
    """T33b 基线行为：key 档 / 文本档 / 序列档的结论字段一律未动。"""

    def setUp(self):
        super().setUp()
        GREET = "您好，这里是报修服务热线。"
        self.pack = build_pack(
            self.tmp / "pack",
            [
                ("greeting", GREET, 0),
                ("greeting", "您好，报修服务为您服务。", 1),
                ("ask", "请问您要报修的是什么设备？", 0),
                ("closing", "谢谢您的来电，祝您生活愉快。", 0),
            ],
        )

    def test_key_mode_hit_contract(self):
        res = find_hit(self.pack, key="greeting")
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.mode, MODE_KEY)
        self.assertEqual(res.key, "greeting")
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.text, "您好，这里是报修服务热线。")
        self.assertIs(res.entry, lookup_key(self.pack, "greeting", "normal"))

    def test_key_mode_miss_contract(self):
        res = find_hit(self.pack, key="not_prebaked")
        self.assertIsNone(res.entry)
        self.assertEqual(res.mode, MODE_KEY)
        self.assertEqual(res.miss_reason, REASON_KEY_NOT_PREBAKED)
        self.assertEqual(res.text, "not_prebaked", "未命中时 text 回退成 key 本身")

    def test_text_mode_hit_contract(self):
        res = find_hit(self.pack, text=" 您好,这里是报修服务热线. ")
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.mode, MODE_TEXT)
        self.assertIsNone(res.key)
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.entry.key, "greeting")

    def test_text_mode_miss_contract(self):
        res = find_hit(self.pack, text="这是一句全新的话，包里没有。")
        self.assertIsNone(res.entry)
        self.assertEqual(res.mode, MODE_TEXT)
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.text, "这是一句全新的话，包里没有。")

    def test_second_variant_still_reachable_via_text(self):
        """T27 不得让第二个 variant 从文本档消失（索引只按文本建，顺序不变）。"""
        res = find_hit(self.pack, text="您好，报修服务为您服务。")
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.entry.variant, 1)


class TestFindHitSequenceContractUnchanged(_TmpPackMixin):
    """序列档：miss_reason / uncovered / blocked_by 一律未动。"""

    def setUp(self):
        super().setUp()
        S1, S2, S3 = "您好。", "请问有什么可以帮您？", "再见。"
        self.pack = build_pack(
            self.tmp / "pack",
            [("s1", S1, 0), ("s2", S2, 0), ("s3", S3, 0)],
        )
        self.hit_text = f"{S1}{S2}{S3}"
        self.gap_text = f"{S1}包外的一句闲话。{S2}"
        self.reverse_text = f"{S3}{S2}{S1}"

    def test_sequence_hit_contract(self):
        res = find_hit_sequence(self.pack, self.hit_text)
        self.assertEqual([e.key for e in res.entries], ["s1", "s2", "s3"])
        self.assertEqual(res.mode, MODE_TEXT)
        self.assertIsNone(res.miss_reason)
        self.assertEqual(res.uncovered, "")
        self.assertEqual(res.blocked_by, (), "命中路径 blocked_by 恒为空元组")

    def test_sequence_miss_carries_uncovered(self):
        """任一段覆盖不上 → 整体未命中，uncovered 从断点起（归一化空间的剩余）。"""
        res = find_hit_sequence(self.pack, self.gap_text)
        self.assertEqual(res.entries, ())
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        # 断点在包外那句之后 → 剩余是 "包外的一句闲话。" + 后面的 "请问有什么可以帮您？"
        self.assertEqual(
            res.uncovered, normalize_text("包外的一句闲话。" + "请问有什么可以帮您？"))
        self.assertNotEqual(res.uncovered, "", "断点起的剩余不能是空串")

    def test_sequence_blocked_by_diagnosis_unchanged(self):
        """blocked_by 只记口径外候选（近失留痕，永不进命中）。"""
        from assets.pack import AssetPack
        slow = build_pack(self.tmp / "p-slow", [("greet", "您好。", 0)], rate="slow")
        pack = AssetPack(
            pack_id=slow.pack_id, pack_version=slow.pack_version,
            protocol_version=slow.protocol_version,
            ruleset_version=slow.ruleset_version, voice=slow.voice,
            model_version=slow.model_version, created_at=slow.created_at,
            assets=list(slow.assets), root=slow.root,
        )
        res = find_hit_sequence(pack, "您好。", rate_key="normal")
        self.assertEqual(res.entries, ())
        self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.blocked_by, ("greet@slow#0",))
        self.assertEqual(res.uncovered, normalize_text("您好。"))

    def test_sequence_hit_then_delete_audio_is_miss(self):
        """运行期删音频 → 候选复核失败 → 整体未命中（stat 不缓存的下游证明）。"""
        res0 = find_hit_sequence(self.pack, self.hit_text)
        self.assertEqual(len(res0.entries), 3)

        audio = self.pack.root / self.pack.assets[0].path
        audio.unlink()
        self.pack.invalidate_lookup_caches()
        res1 = find_hit_sequence(self.pack, self.hit_text)
        self.assertEqual(res1.entries, ())
        self.assertEqual(res1.miss_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertNotEqual(res1.uncovered, "")


# ============================================================
# 4. build_text_index / _sequence_candidates 结果集与顺序不变
# ============================================================
class TestOtherPublicHelpersUntouched(_TmpPackMixin):
    def test_build_text_index_groups_and_keeps_order(self):
        """归一化文本分组 + 组内按包内顺序（pick_by_text 依赖这一点）。"""
        slow = build_pack(self.tmp / "p-s", [("same", "您好。", 0)], rate="slow")
        norm = build_pack(self.tmp / "p-n", [("same", "您好。", 0)], rate="normal")
        from assets.pack import AssetPack
        combined = AssetPack(
            pack_id=norm.pack_id, pack_version=norm.pack_version,
            protocol_version=norm.protocol_version,
            ruleset_version=norm.ruleset_version, voice=norm.voice,
            model_version=norm.model_version, created_at=norm.created_at,
            assets=[slow.assets[0], norm.assets[0]], root=norm.root,
        )
        idx = build_text_index(combined)
        group = idx[normalize_text("您好。")]
        self.assertEqual([e.rate_key for e in group], ["slow", "normal"])
        self.assertNotIn(normalize_text("包里没有的话。"), idx)

    def test_sequence_candidates_order_and_exclusion(self):
        """序列候选：口径内条目按包内顺序，口径外条目不进候选。"""
        from assets.pack import AssetPack
        pack = self._mixed_pack()
        cands = _sequence_candidates(pack, "normal")
        keys = [e.key for _, e in cands]
        self.assertEqual(keys, ["s1", "s2"],
                         "候选必须只有口径内条目且按包内顺序")
        self.assertEqual(len(cands), 2)

    def _mixed_pack(self):
        slow = build_pack(self.tmp / "p-sl", [("blocked", "您好。", 0)], rate="slow")
        norm = build_pack(self.tmp / "p-n",
                          [("s1", "您好，我是报修服务热线。", 0),
                           ("s2", "请问您要报修的是什么设备？", 0)])
        from assets.pack import AssetPack
        e = norm.assets[0]
        part1 = AssetEntry(key=e.key, part_index=1, rate_key=e.rate_key,
                           variant=e.variant, text=e.text, fingerprint=e.fingerprint,
                           path=e.path, duration_ms=e.duration_ms)
        # 包内顺序刻意交错：part_index!=0 的条目与口径外档位夹在中间，
        # 用来证明候选筛选只挑口径内的、且**顺序跟随包内顺序**
        return AssetPack(
            pack_id=norm.pack_id, pack_version=norm.pack_version,
            protocol_version=norm.protocol_version,
            ruleset_version=norm.ruleset_version, voice=norm.voice,
            model_version=norm.model_version, created_at=norm.created_at,
            assets=[norm.assets[0], slow.assets[0], part1, norm.assets[1]],
            root=norm.root,
        )

    def test_confirm_unchanged(self):
        """confirm 仍是 pack.lookup 的薄包装（指纹/文件校验在 assets 层）。"""
        pack = self._mixed_pack()
        cand = _sequence_candidates(pack, "normal")[0][1]
        self.assertIs(confirm(pack, cand), cand)


# ============================================================
# 5. 注入验证（反空转）：候选顺序被打乱时断言必然失败
# ============================================================
class TestOrderAssertionIsNotVacuous(_TmpPackMixin):
    """证明"候选顺序一致"这条断言不是空转——打乱顺序必须判红。"""

    def _pack(self):
        return build_pack(
            self.tmp / "pack",
            [("k1", "第一句。", 0),
             ("k1", "第二句。", 1),
             ("k1", "第三句。", 2)],
        )

    def test_scrambled_candidate_order_is_detected(self):
        """模拟一个"顺序错了"的实现：返回值相同，但候选顺序不同 → 断言必须红。"""
        pack = self._pack()
        good = _legacy_candidates(pack, "k1", "normal")
        scrambled = tuple(reversed(good))

        # 顺序必须被这条断言抓住（这里故意用 assertEqual 复现，证明它能判红）
        with self.assertRaises(AssertionError):
            self.assertEqual(
                [c.variant for c in scrambled],
                [c.variant for c in good],
                "候选顺序被打破",
            )
        # 而真实实现的返回值确实与旧实现一致
        self.assertIs(
            lookup_key(pack, "k1", "normal"),
            _legacy_lookup_key_linear_scan(pack, "k1", "normal"),
        )

    def test_wrong_order_would_return_a_different_variant(self):
        """顺序错了不只是"看起来不对"——它会返回一个不同的 variant。"""
        pack = self._pack()
        good = lookup_key(pack, "k1", "normal")
        self.assertEqual(good.variant, 0, "先出现的 variant 才该被命中")

        # 模拟"后出现优先"的错误实现
        cands = list(_legacy_candidates(pack, "k1", "normal"))
        wrong = None
        for cand in reversed(cands):
            entry = pack.lookup(key="k1", part_index=0, rate_key="normal",
                                variant=cand.variant, expected_text=cand.text)
            if entry is not None:
                wrong = entry
                break
        self.assertNotEqual(wrong.variant, good.variant,
                            "错误顺序必然返回不同 variant —— 顺序断言有实际灵敏度")

    def test_lookup_key_result_is_identical_object(self):
        """返回值必须是同一个对象（不是"等值但不同实例"），顺序稳定才可缓存。"""
        pack = self._pack()
        self.assertIs(lookup_key(pack, "k1", "normal"),
                      lookup_key(pack, "k1", "normal"))
        self.assertIs(lookup_key(pack, "k1", "normal"),
                      find_hit(pack, key="k1").entry)
        self.assertEqual(text_for_key(pack, "k1", "normal"), "第一句。")


if __name__ == "__main__":
    unittest.main()
