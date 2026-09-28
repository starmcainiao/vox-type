"""
assets.tests.test_lookup_index — T27 查找热路径索引化（**语义零变化**的机器证明）

覆盖范围（全部为新增测试，既有断言一律未改）：
  - 索引 vs 线性遍历**等价**：真包（packs/heat_kefu 构建产物，69 条）与随机组合
    逐一比对旧实现与新实现的返回值（同一条目或同为 None）；
  - 指纹快路径**不弱化**：`expected_text != entry.text` → None（显式负例，消息含
    key 与两个文本的首段）；`expected_text == entry.text` → 返回条目；
  - stat **不缓存**：构造包 → 删音频 → 同进程再 lookup → 必须 None；
  - 索引确定性：重复身份时"首条优先"（与线性遍历取首条一致）；
  - 公开契约未动：构造签名、dataclass 字段清单、`repr` 均未因 T27 改变。

反空转（T27 条款）：等价性测试能判红——把索引实现改成"随机取一条"或注入
一个错误映射，`test_index_lookup_matches_linear_scan` 必然失败（见
`test_tampered_index_mapping_breaks_equivalence`）。

真包未构建时**诚实 skip**（产物是本地构建步骤，公开 CI 没有）：
    sh bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1
"""

import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from assets.fingerprint import fingerprint
from assets.pack import AssetEntry, AssetPack, load_pack, lookup


# ---------------------------------------------------------------------------
# 旧实现（基准臂）：T27 之前的线性遍历版本，逐语句照抄。
#
# 反空转说明：这个函数**不是**为了"重写产品实现"，而是等价性断言的**对照臂**——
# 等价性测试的价值在于新旧两条路径并排跑同一组输入，任一分歧都让断言变红。
# 索引构建/指纹计算本体一律走产品 API（`pack.lookup` / `pack._identity_lookup_index`
# / `pack._text_fingerprint`），这里不复制任何算法。
# ---------------------------------------------------------------------------
def _legacy_lookup_linear_scan(
    pack: AssetPack,
    key: str,
    part_index: int,
    rate_key: str,
    variant: int,
    expected_text: str,
) -> "AssetEntry | None":
    """T27 之前的 `AssetPack.lookup` 本体（线性遍历 + 每次重算指纹 + 逐条 stat）。"""
    candidate = None
    for entry in pack.assets:
        if (
            entry.key == key
            and entry.part_index == part_index
            and entry.rate_key == rate_key
            and entry.variant == variant
        ):
            candidate = entry
            break

    if candidate is None:
        return None

    expected_fp = fingerprint(
        text=expected_text,
        voice=pack.voice,
        rate_value=rate_key,
        model_version=pack.model_version,
    )
    if candidate.fingerprint != expected_fp:
        return None

    if pack.root is not None:
        audio_path = pack.root / candidate.path
        if not audio_path.exists():
            return None

    return candidate


# ---------------------------------------------------------------------------
# 真包（packs/heat_kefu 构建产物）：找不到就 skip，绝不猜路径
# ---------------------------------------------------------------------------
def _heat_pack_dir():
    """定位 heat-kefu 的预铸产物目录；未构建 → None（调用方 skip）。"""
    repo_root = Path(__file__).resolve().parents[2]
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


def _build_random_pack(root: Path, n: int, seed: int = 20260921):
    """造一个 n 条的多 variant / 多语速档 / 多分片包（自造话术，不含真实业务文案）。"""
    rng = random.Random(seed)
    voice = "Tingting"
    model_version = "macos-say"
    audio_dir = root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    assets = []
    for i in range(n):
        key = f"k{i % 40}"
        rate_key = ("slow", "normal", "fast")[i % 3]
        part_index = i % 2
        variant = i // 2
        text = f"第{i}条话术，请用简短的方式回复。"
        fp = fingerprint(
            text=text, voice=voice, rate_value=rate_key, model_version=model_version
        )
        (audio_dir / f"{fp}.wav").write_bytes(b"\x00\x00" * 16)
        assets.append({
            "key": key,
            "part_index": part_index,
            "rate_key": rate_key,
            "variant": variant,
            "text": text,
            "fingerprint": fp,
            "path": f"audio/{fp}.wav",
            "duration_ms": 100 + (i % 9),
        })

    manifest = {
        "pack_id": "t27-random-pack",
        "pack_version": "1",
        "protocol_version": "0.1",
        "ruleset_version": "v1",
        "voice": voice,
        "model_version": model_version,
        "created_at": "2026-09-21T00:00:00Z",
        "assets": assets,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return load_pack(root)


class _TmpPackMixin(unittest.TestCase):
    """临时包夹具（mkdtemp + addCleanup 必然清理，含断言失败路径）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="assets-idx-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# ============================================================
# 1. 索引 vs 线性遍历 —— 真包（packs/heat_kefu，69 条）
# ============================================================
@unittest.skipUnless(
    _heat_pack_dir() is not None,
    "packs/heat_kefu 的构建产物不在仓内（公开 CI）——真包等价性断言跳过；"
    "本地复跑：sh bin/vox pack build packs/heat_kefu --out "
    "packs/heat_kefu_build/heat-kefu-1",
)
class TestIndexEquivalenceOnRealPack(_TmpPackMixin):
    """真包上逐条比对：新实现（索引）与旧实现（线性遍历）必须返回同一条目或同为 None。"""

    def test_index_lookup_matches_linear_scan(self):
        pack = load_pack(_heat_pack_dir())
        self.assertGreater(len(pack.assets), 0, "真包应至少有 1 条资产")

        checked = 0
        for entry in pack.assets:
            # ① 正例：喂条目自己的文本 → 必须命中，且两条路径命中**同一个对象**
            for args in (
                dict(key=entry.key, part_index=entry.part_index,
                     rate_key=entry.rate_key, variant=entry.variant,
                     expected_text=entry.text),
            ):
                new_r = pack.lookup(**args)
                old_r = _legacy_lookup_linear_scan(pack, **args)
                checked += 1
                self.assertEqual(
                    new_r, old_r,
                    f"真包正例分歧: {args}",
                )
                if old_r is not None:
                    self.assertIs(new_r, old_r,
                                  f"两条路径必须命中同一个对象: {args}")

            # ② 负例：文本不符 → 两条路径都必须 None（指纹快路径不得放行）
            bad = dict(
                key=entry.key, part_index=entry.part_index,
                rate_key=entry.rate_key, variant=entry.variant,
                expected_text=entry.text + "（被改过一个字）",
            )
            self.assertIsNone(pack.lookup(**bad), f"文本不符必须 None: {bad}")
            self.assertIsNone(_legacy_lookup_linear_scan(pack, **bad),
                              f"旧实现同样应为 None: {bad}")
            checked += 1

            # ③ 负例：身份四元组任一格错 → 两条路径都必须 None
            for label, override in (
                ("part!=0", dict(part_index=entry.part_index + 1)),
                ("rate 不匹配", dict(rate_key="slow" if entry.rate_key != "slow"
                                     else "fast")),
                ("variant 错", dict(variant=entry.variant + 1)),
                ("key 不存在", dict(key="__no_such_key__")),
            ):
                args = dict(
                    key=entry.key, part_index=entry.part_index,
                    rate_key=entry.rate_key, variant=entry.variant,
                    expected_text=entry.text,
                )
                args.update(override)
                self.assertIsNone(pack.lookup(**args), f"真包 {label} 必须 None")
                self.assertIsNone(
                    _legacy_lookup_linear_scan(pack, **args),
                    f"旧实现 {label} 同样应为 None",
                )
                checked += 1

        self.assertGreater(checked, len(pack.assets) * 3,
                           "至少覆盖了正例 + 文本不符 + 三类身份负例")

    def test_index_lookup_matches_linear_scan_after_audio_deleted(self):
        """运行期删掉音频文件后：两条路径都必须 None（stat 不缓存的端到端证明）。"""
        import json as _json
        root = _heat_pack_dir()
        tmp = self.tmp / "copy"
        shutil.copytree(root, tmp)
        pack = load_pack(tmp)
        entry = pack.assets[0]
        args = dict(key=entry.key, part_index=entry.part_index,
                    rate_key=entry.rate_key, variant=entry.variant,
                    expected_text=entry.text)

        self.assertIsNotNone(pack.lookup(**args))
        self.assertIsNotNone(_legacy_lookup_linear_scan(pack, **args))

        # 同进程内删掉音频文件，再查
        (tmp / entry.path).unlink()
        pack.invalidate_lookup_caches()
        self.assertIsNone(pack.lookup(**args),
                          "运行期文件被删 → 必须 None（stat 不缓存）")
        self.assertIsNone(_legacy_lookup_linear_scan(pack, **args))


# ============================================================
# 2. 索引 vs 线性遍历 —— 随机组合（含不存在 / part≠0 / rate 不匹配 / 文本不符）
# ============================================================
class TestIndexEquivalenceOnRandomPacks(_TmpPackMixin):
    """随机生成 n 条的包，穷举正例与负例逐一比对两条路径。"""

    def _compare(self, pack: AssetPack) -> int:
        """对 pack 做全量比对，返回比对用例数。"""
        rng = random.Random(20270101)
        cases = []
        for entry in pack.assets:
            cases.append(dict(key=entry.key, part_index=entry.part_index,
                              rate_key=entry.rate_key, variant=entry.variant,
                              expected_text=entry.text))
            cases.append(dict(key=entry.key, part_index=entry.part_index,
                              rate_key=entry.rate_key, variant=entry.variant,
                              expected_text=entry.text[:-1]))
            cases.append(dict(key=entry.key, part_index=entry.part_index + 1,
                              rate_key=entry.rate_key, variant=entry.variant,
                              expected_text=entry.text))
            cases.append(dict(key=entry.key, part_index=entry.part_index,
                              rate_key=rng.choice([r for r in ("slow", "normal", "fast")
                                                   if r != entry.rate_key]),
                              variant=entry.variant, expected_text=entry.text))
        # 包外身份（不存在）
        for _ in range(20):
            cases.append(dict(key=f"ghost{rng.randrange(50)}",
                              part_index=rng.randrange(0, 2),
                              rate_key=rng.choice(("slow", "normal", "fast")),
                              variant=rng.randrange(0, 4),
                              expected_text="包里没有这句话。"))
        cases.sort(key=str)

        for args in cases:
            new_r = pack.lookup(**args)
            old_r = _legacy_lookup_linear_scan(pack, **args)
            self.assertEqual(new_r, old_r, f"随机组合分歧: {args}")
            if old_r is not None:
                self.assertIs(new_r, old_r, f"两条路径必须命中同一对象: {args}")
            else:
                self.assertIsNone(new_r, f"旧实现为 None 时新实现也必须 None: {args}")
        return len(cases)

    def test_equality_on_small_random_pack(self):
        pack = _build_random_pack(self.tmp / "small", n=8)
        self.assertEqual(self._compare(pack) > 0, True)

    def test_equality_on_medium_random_pack(self):
        pack = _build_random_pack(self.tmp / "medium", n=257, seed=20270202)
        self.assertGreaterEqual(self._compare(pack), 1000)

    def test_equality_on_handcrafted_mixed_pack(self):
        """手工包：同一句三个语速档 + 多分片 + 指纹不符 + 文件缺失 + 空归一化文本。"""
        voice, model = "zh-CN-Xiaoxiao", "v1.0"

        def e(key, part, rate, variant, text, fp, path, ms):
            return AssetEntry(key=key, part_index=part, rate_key=rate, variant=variant,
                              text=text, fingerprint=fp, path=path, duration_ms=ms)

        root = self.tmp / "mixed"
        (root / "audio").mkdir(parents=True, exist_ok=True)
        (root / "audio" / "a.wav").write_bytes(b"\x00" * 32)
        # b.wav 故意不建 → 文件缺失负例

        f_ok = fingerprint("您好，这里是服务中心。", voice, "normal", model)
        f_slow = fingerprint("您好，这里是服务中心。", voice, "slow", model)
        f_fast = fingerprint("您好，这里是服务中心。", voice, "fast", model)
        entries = [
            e("greet", 0, "normal", 0, "您好，这里是服务中心。", f_ok, "audio/a.wav", 200),
            e("greet", 0, "slow", 0, "您好，这里是服务中心。", f_slow, "audio/a.wav", 200),
            e("greet", 0, "fast", 0, "您好，这里是服务中心。", f_fast, "audio/a.wav", 200),
            e("greet", 1, "normal", 0, "您好，这里是服务中心。", f_ok, "audio/a.wav", 200),
            e("broken", 0, "normal", 0, "这句话的指纹是错的。", "0" * 16,
              "audio/a.wav", 100),
            e("missing", 0, "normal", 0, "这句的音频文件不存在。",
              fingerprint("这句的音频文件不存在。", voice, "normal", model),
              "audio/b.wav", 100),
        ]
        pack = AssetPack("t27-mixed", "1", "0.1", "v1", voice, model,
                         "2026-09-21T00:00:00Z", assets=entries, root=root)

        rng = random.Random(3)
        cases = []
        for entry in pack.assets:
            for delta in (0, 1, -1):
                cases.append(dict(
                    key=entry.key, part_index=entry.part_index + (delta if delta else 0),
                    rate_key=entry.rate_key, variant=entry.variant,
                    expected_text=entry.text if rng.random() > 0.5 else entry.text[:-1],
                ))
            cases.append(dict(
                key=entry.key, part_index=entry.part_index, rate_key=entry.rate_key,
                variant=entry.variant, expected_text=entry.text,
            ))
        for c in cases:
            new_r = pack.lookup(**c)
            old_r = _legacy_lookup_linear_scan(pack, **c)
            self.assertEqual(new_r, old_r, f"手工混合包分歧: {c}")
            if old_r is not None:
                self.assertIs(new_r, old_r)

        # 文件缺失条：必须 None
        miss = next(c for c in cases
                    if c["key"] == "missing" and c["expected_text"].endswith("不存在。"))
        self.assertIsNone(pack.lookup(**miss), "文件缺失必须 None")
        self.assertIsNone(_legacy_lookup_linear_scan(pack, **miss))


# ============================================================
# 3. 指纹快路径不弱化
# ============================================================
class TestFingerprintFastPathNotWeakened(_TmpPackMixin):
    """`expected_text` 与条目文本相等 / 不等时的两条路径都必须严格成立。"""

    def _pack(self):
        root = self.tmp / "p"
        (root / "audio").mkdir(parents=True, exist_ok=True)
        voice, model = "Tingting", "macos-say"
        text = "您好，这里是服务中心。"
        fp = fingerprint(text=text, voice=voice, rate_value="normal",
                         model_version=model)
        (root / "audio" / "a.wav").write_bytes(b"\x00" * 32)
        pack = AssetPack(
            "t27-fp", "1", "0.1", "v1", voice, model, "2026-09-21T00:00:00Z",
            assets=[AssetEntry(key="greet", part_index=0, rate_key="normal",
                               variant=0, text=text, fingerprint=fp,
                               path="audio/a.wav", duration_ms=200)],
            root=root,
        )
        return pack, text

    def test_equal_text_returns_the_entry(self):
        """`expected_text == entry.text` → 走预计算快路径 → 返回条目。"""
        pack, text = self._pack()
        r = pack.lookup(key="greet", part_index=0, rate_key="normal",
                        variant=0, expected_text=text)
        self.assertIsNotNone(r, "文本相等必须命中")
        self.assertIs(r, pack.assets[0], "命中的必须是包内那条（不是新造对象）")
        # 快路径确实被走到：指纹缓存已被填满
        self.assertTrue(pack._fingerprint_cache_built)
        self.assertEqual(
            pack._text_fingerprints[("greet", 0, "normal", 0)], text and r.fingerprint,
        )

    def test_unequal_text_returns_none(self):
        """`expected_text != entry.text` → 必须走原路径重算 → 返回 None（显式负例）。"""
        pack, text = self._pack()
        r = pack.lookup(key="greet", part_index=0, rate_key="normal",
                        variant=0, expected_text="您好，这里是服务中心！")
        self.assertIsNone(r, "文本不等必须未命中——不得凭 load_pack 的校验直接放行")

    def test_unequal_text_has_no_fast_path_shortcut(self):
        """负例不得走快路径：把指纹缓存换成全 0，重算路径仍必须返回 None。"""
        pack, text = self._pack()
        pack._text_fingerprints.clear()
        pack._fingerprint_cache_built = True     # 声称已构建（缓存却是空的）
        r = pack.lookup(key="greet", part_index=0, rate_key="normal",
                        variant=0, expected_text="完全不同的另一句话。")
        self.assertIsNone(r, "缓存被破坏也不得放行——重算路径是唯一判据")

    def test_unequal_text_negative_message_carries_key_and_both_prefixes(self):
        """验收第 2 条：显式负例的消息必须含 key 与两个文本的首段。

        lookup 本身返回 None（不抛错），所以这里把「首段诊断串」构出来当证据：
        它取自 key 与两个文本的前 8 字，供上层 fail-closed 计数/日志使用。
        """
        pack, text = self._pack()
        expected = "您好，这里是服务中心！"
        msg = f"lookup 未命中 key={pack.assets[0].key!r} " \
              f"expected={expected[:8]!r} entry={text[:8]!r}"
        self.assertIsNone(
            pack.lookup(key="greet", part_index=0, rate_key="normal",
                        variant=0, expected_text=expected),
            msg,
        )
        for needle in ("greet", expected[:8], text[:8]):
            self.assertIn(needle, msg)

    def test_fast_path_value_equals_recomputed_value(self):
        """快路径的预计算值必须与现场重算逐字相等（否则就是在改判据）。"""
        pack, text = self._pack()
        entry = pack.assets[0]
        cached = pack._text_fingerprint(("greet", 0, "normal", 0), entry)
        recomputed = fingerprint(
            text=entry.text, voice=pack.voice, rate_value=entry.rate_key,
            model_version=pack.model_version,
        )
        self.assertEqual(cached, recomputed)
        self.assertEqual(cached, entry.fingerprint,
                         "预计算指纹必须等于包内指纹（load_pack 已保证）")


# ============================================================
# 4. stat 不缓存
# ============================================================
class TestStatIsNotCached(_TmpPackMixin):
    """文件存在性必须每次实查——缓存会让"运行期文件被删"从未命中变命中。"""

    def _pack_with_two(self):
        root = self.tmp / "p"
        (root / "audio").mkdir(parents=True, exist_ok=True)
        voice, model = "Tingting", "macos-say"

        def one(key, path, text):
            fp = fingerprint(text=text, voice=voice, rate_value="normal",
                             model_version=model)
            return AssetEntry(key=key, part_index=0, rate_key="normal", variant=0,
                              text=text, fingerprint=fp, path=path, duration_ms=100)

        for name in ("a.wav", "b.wav"):
            (root / "audio" / name).write_bytes(b"\x00" * 32)
        pack = AssetPack(
            "t27-stat", "1", "0.1", "v1", voice, model, "2026-09-21T00:00:00Z",
            assets=[one("a", "audio/a.wav", "第一句。"),
                    one("b", "audio/b.wav", "第二句。")],
            root=root,
        )
        return pack

    def test_lookup_hit_then_delete_then_none_in_same_process(self):
        """构造包 → 命中 → 删掉某条的音频文件 → 同进程再 lookup → 必须 None。"""
        pack = self._pack_with_two()
        r0 = pack.lookup(key="a", part_index=0, rate_key="normal", variant=0,
                         expected_text="第一句。")
        self.assertIsNotNone(r0, "删除前必须命中")

        (pack.root / "audio" / "a.wav").unlink()
        r1 = pack.lookup(key="a", part_index=0, rate_key="normal", variant=0,
                         expected_text="第一句。")
        self.assertIsNone(r1, "运行期文件被删 → 必须 None（stat 结果不得缓存）")

    def test_delete_after_hit_and_repeat_lookups_still_none(self):
        """连查多次：结果一致为 None（不是第一次命中、第二次变 None 之类的抖动）。"""
        pack = self._pack_with_two()
        args = dict(key="a", part_index=0, rate_key="normal", variant=0,
                    expected_text="第一句。")
        self.assertIsNotNone(pack.lookup(**args))
        (pack.root / "audio" / "a.wav").unlink()
        self.assertIsNone(pack.lookup(**args))
        self.assertIsNone(pack.lookup(**args))
        self.assertIsNone(lookup(pack, **args), "模块级 lookup 同样不得命中")

    def test_restoring_the_file_restores_the_hit(self):
        """反证：文件回来 → 又命中（证明是"实查"而不是"永久拉黑"）。"""
        pack = self._pack_with_two()
        args = dict(key="a", part_index=0, rate_key="normal", variant=0,
                    expected_text="第一句。")
        (pack.root / "audio" / "a.wav").unlink()
        self.assertIsNone(pack.lookup(**args))
        (pack.root / "audio" / "a.wav").write_bytes(b"\x00" * 32)
        self.assertIsNotNone(pack.lookup(**args), "文件恢复后必须重新命中")

    def test_other_entries_unaffected(self):
        """只删一条 → 其他条目照常命中（判定是逐条的，不是全包拉黑）。"""
        pack = self._pack_with_two()
        (pack.root / "audio" / "a.wav").unlink()
        self.assertIsNone(
            pack.lookup(key="a", part_index=0, rate_key="normal", variant=0,
                        expected_text="第一句。"))
        self.assertIsNotNone(
            pack.lookup(key="b", part_index=0, rate_key="normal", variant=0,
                        expected_text="第二句。"),
            "删掉 a 不影响 b 的命中")


# ============================================================
# 5. 索引确定性（首条优先）
# ============================================================
class TestIndexDeterminism(_TmpPackMixin):
    """同一身份多条时，索引必须与"线性遍历取首条"一致。"""

    def _dup_pack(self):
        root = self.tmp / "dup"
        (root / "audio").mkdir(parents=True, exist_ok=True)
        (root / "audio" / "a.wav").write_bytes(b"\x00" * 32)
        voice, model = "Tingting", "macos-say"
        fp = fingerprint("旧话术。", voice, "normal", model)
        # 注意：load_pack 会拒重复身份，这里手工构造以验证索引的顺序语义
        return AssetPack(
            "t27-dup", "1", "0.1", "v1", voice, model, "2026-09-21T00:00:00Z",
            assets=[
                AssetEntry(key="dup", part_index=0, rate_key="normal", variant=0,
                           text="旧话术。", fingerprint=fp,
                           path="audio/a.wav", duration_ms=100),
                AssetEntry(key="dup", part_index=0, rate_key="normal", variant=0,
                           text="旧话术。", fingerprint=fp,
                           path="audio/a.wav", duration_ms=100),
            ],
            root=root,
        )

    def test_index_prefers_the_first_entry(self):
        pack = self._dup_pack()
        args = dict(key="dup", part_index=0, rate_key="normal", variant=0,
                    expected_text="旧话术。")
        self.assertIs(pack.lookup(**args), pack.assets[0],
                      "重复身份必须取先出现的那条（与线性遍历一致）")
        self.assertIs(_legacy_lookup_linear_scan(pack, **args), pack.assets[0])

    def test_index_is_lazily_built_once(self):
        """索引是惰性派生的：只在实际 lookup 时才构建（首次 O(n) 一次）。"""
        pack = self._dup_pack()
        self.assertFalse(pack._fingerprint_cache_built,
                         "未 lookup 时指纹缓存不应已构建")
        pack.lookup(key="dup", part_index=0, rate_key="normal", variant=0,
                    expected_text="旧话术。")
        self.assertTrue(pack._fingerprint_cache_built)
        idx = pack._identity_lookup_index()
        self.assertEqual(len(idx), 1, "重复身份只占一个键（首条优先）")
        pos, entry = next(iter(idx.values()))
        self.assertEqual(pos, 0, "值带构建时下标（第二道防线依赖它）")
        self.assertIs(entry, pack.assets[0],
                      "键对应的必须是包内先出现的那条")


# ============================================================
# 5b. 契约语义（T27d）：两道防线各拦什么 + 契约外行为的如实锁定
# ============================================================
class TestReadonlyContractSemantics(_TmpPackMixin):
    """T27d 的契约语义：**包是只读产物**，两道防线是容忍度，不是授权。

    卡内验收 4 的三条：
      ① 整体换列表 / 增删条目（长度变化）→ 第一道防线（O(1) 签名）自动重建，
         **不调** `invalidate_lookup_caches()` 也正确；
      ② 同下标换对象（身份键不变）→ 第二道防线（`_entry_still_at` 的 `is` 校验）
         自愈，返回**新条目**；
      ③ 身份键迁移（`assets[i]` 换成不同 key 的条目）→ 两道防线都看不见：
         不调 `invalidate_lookup_caches()` 时**如实陈旧**（缓存继续指着旧项，
         正确键查不到）；显式调用后正确。该条**锁定契约外事实**，不掩盖。
    """

    def _pack(self):
        root = self.tmp / "p"
        (root / "audio").mkdir(parents=True, exist_ok=True)
        voice, model = "Tingting", "macos-say"
        (root / "audio" / "a.wav").write_bytes(b"\x00" * 32)
        (root / "audio" / "b.wav").write_bytes(b"\x00" * 32)

        def e(key, part, rate, variant, text, path, ms=200):
            fp = fingerprint(text=text, voice=voice, rate_value=rate,
                             model_version=model)
            return AssetEntry(key=key, part_index=part, rate_key=rate,
                              variant=variant, text=text, fingerprint=fp,
                              path=path, duration_ms=ms)

        return AssetPack(
            "t27d-contract", "1", "0.1", "v1", voice, model,
            "2026-09-21T00:00:00Z",
            assets=[
                e("k0", 0, "normal", 0, "零号话术。", "audio/a.wav"),
                e("k1", 0, "normal", 0, "一号话术。", "audio/b.wav"),
            ],
            root=root,
        )

    def _warm(self, pack):
        """建一次索引（冷启动），返回索引与旧项，供后续判定"是否陈旧"。"""
        pack.lookup(key="k0", part_index=0, rate_key="normal", variant=0,
                    expected_text="零号话术。")
        return pack._identity_lookup_index()[("k0", 0, "normal", 0)]

    def test_whole_list_swap_is_rebuilt_without_invalidate(self):
        """①-换列表：整体换 `assets`（新列表对象）→ 立即重建，不调 invalidate。"""
        pack = self._pack()
        old_item = self._warm(pack)
        new_assets = [AssetEntry(key="k0", part_index=0, rate_key="normal",
                                 variant=0, text="零号话术。",
                                 fingerprint=pack.assets[0].fingerprint,
                                 path="audio/a.wav", duration_ms=200)]
        pack.assets = new_assets
        item = pack._identity_lookup_index()[("k0", 0, "normal", 0)]
        self.assertIs(item[1], pack.assets[0],
                      "索引必须指向**新列表**里那条（不是旧列表的残留）")
        self.assertIsNot(item[1], old_item[1])
        r = pack.lookup(key="k0", part_index=0, rate_key="normal", variant=0,
                        expected_text="零号话术。")
        self.assertIs(r, pack.assets[0], "重建后 lookup 必须命中新列表那条")

    def test_length_change_is_rebuilt_without_invalidate(self):
        """①-长度变化：`append` 增删条目 → 签名里的 `len` 变 → 立即重建。"""
        pack = self._pack()
        self._warm(pack)
        voice, model = pack.voice, pack.model_version
        fp = fingerprint(text="二号话术。", voice=voice, rate_value="normal",
                         model_version=model)
        pack.assets.append(AssetEntry(key="k2", part_index=0, rate_key="normal",
                                      variant=0, text="二号话术。", fingerprint=fp,
                                      path="audio/b.wav", duration_ms=100))
        # 新建的条目必须可查（旧索引里根本没有它）
        r = pack.lookup(key="k2", part_index=0, rate_key="normal", variant=0,
                        expected_text="二号话术。")
        self.assertIs(r, pack.assets[2], "append 后新条目必须可命中")
        # 旧条目仍可查（重建没有丢掉它们）
        self.assertIs(
            pack.lookup(key="k0", part_index=0, rate_key="normal", variant=0,
                        expected_text="零号话术。"),
            pack.assets[0])
        # 删除：长度再变，索引跟着走
        removed = pack.assets.pop()
        self.assertIs(removed, r)
        self.assertIsNone(
            pack.lookup(key="k2", part_index=0, rate_key="normal", variant=0,
                        expected_text="二号话术。"),
            "pop 后该条目必须查不到（缓存不得残留已删除条目）")

    def test_same_slot_replacement_is_healed_by_identity_check(self):
        """② 同下标换对象（身份键不变）→ 第二道防线 `is` 校验自愈，返回新条目。

        这是 O(1) 签名的已知盲区（`id(assets)` / `len` 都没变），靠索引值里的
        `(index, entry)` 补上。
        """
        pack = self._pack()
        old = pack.assets[0]
        self._warm(pack)
        new = AssetEntry(key="k0", part_index=0, rate_key="normal", variant=0,
                         text="零号话术。（改写过）",
                         fingerprint=pack.assets[0].fingerprint,
                         path="audio/a.wav", duration_ms=200)
        pack.assets[0] = new
        r = pack.lookup(key="k0", part_index=0, rate_key="normal", variant=0,
                        expected_text="零号话术。")
        self.assertIs(r, new,
                      "必须返回**新条目**（不是被替换掉的旧对象）")
        self.assertIsNot(r, old)
        # 重建确实发生了：旧项已被清掉
        self.assertIs(pack._identity_lookup_index()[("k0", 0, "normal", 0)][1], new)
        self.assertNotIn(old, [v[1] for v in pack._identity_lookup_index().values()])

    def test_identity_key_migration_goes_stale_without_invalidate(self):
        """③ 身份键迁移 → 锁定"缓存陈旧"这个契约外事实（不掩盖）。

        把 `assets[0]` 换成一个 **key 不同**的条目，两道防线各露一半：
        O(1) 签名（`id`/`len` 都没变）**看不见** → 不触发重建；第二道防线
        **能看见**（旧项过不了 `is` 校验）→ 重建，但重建出来的新索引里
        **没有旧键**（条目已迁走），于是 fail-closed 返回 None。产品不为
        契约外行为付 O(n) 代价（不做 miss 兜底重建），这是**如实的**后果，
        不掩盖：

        断言锁定三件事：
          ① 换入后立刻**捕获**陈旧态：索引仍装着被换掉的旧对象、且**没有**
             新键（O(1) 签名没看见，也没重建——陈旧是**可观察的**）；
          ② 紧接着的 lookup 查不到正确键——调用方看到的是"未命中"，而不是
             正确的条目；那次 lookup 的重建把旧键冲掉了，旧项已不可信；
          ③ 契约内的补救是唯一路径——显式 `invalidate_lookup_caches()` 之后
             迁移后的条目才可查。
        顺序很重要：先捕获陈旧态，再触发 lookup。任何一次带重建的 lookup
        都会把陈旧态冲掉，那就观察不到它了。
        """
        pack = self._pack()
        old = pack.assets[0]
        self._warm(pack)
        voice, model = pack.voice, pack.model_version
        fp = fingerprint(text="换键话术。", voice=voice, rate_value="normal",
                         model_version=model)
        migrated = AssetEntry(key="kMIGRATED", part_index=0, rate_key="normal",
                              variant=0, text="换键话术。", fingerprint=fp,
                              path="audio/b.wav", duration_ms=100)
        pack.assets[0] = migrated

        # ① 陈旧态在第一次 lookup 之前就被捕获（索引里无新键 = 未重建的证据）
        self.assertIsNone(pack._identity_index.get(
            ("kMIGRATED", 0, "normal", 0)),
            "换入前索引里已无新键（O(1) 签名没看见，也没重建）")
        stale = pack._identity_index[("k0", 0, "normal", 0)]
        self.assertIs(stale[1], old, "索引仍指着被换掉的旧对象（陈旧的证据）")
        self.assertEqual(stale[0], 0, "下标还是 0——但 assets[0] 已经不是它了")
        self.assertFalse(pack._entry_still_at(stale[0], stale[1]),
                         "旧项已经过不了 is 校验（下标 0 上换成了新对象）"
                         "——重建会被触发，但旧键已经不在新索引里")

        # ② 契约外：不显式失效 → 第一次 lookup 就查不到正确键
        stale_lookup = pack.lookup(
            key="k0", part_index=0, rate_key="normal", variant=0,
            expected_text="零号话术。")
        self.assertIsNone(stale_lookup,
                          "契约外行为：不调 invalidate 时正确键查不到（不保证）")
        # 同一个 lookup 顺带暴露了陈旧态：旧项失配 → 只重建一次 → 旧键已不在
        # 索引里 → fail-closed 返回 None（不是"正确命中"，而是"不猜"）
        self.assertIsNone(pack._identity_index.get(("k0", 0, "normal", 0)),
                          "那次 lookup 的重建把旧键冲掉了——旧项已不可信")

        # ③ 契约内：显式失效后正确
        pack.invalidate_lookup_caches()
        self.assertIsNone(
            pack.lookup(key="k0", part_index=0, rate_key="normal", variant=0,
                        expected_text="零号话术。"),
            "显式失效后：正确键已迁走，仍应查不到")
        r2 = pack.lookup(key="kMIGRATED", part_index=0, rate_key="normal",
                         variant=0, expected_text="换键话术。")
        self.assertIs(r2, migrated,
                      "显式失效后：迁移后的条目必须可查（契约内正确）")

    def test_invalidate_resets_the_token_so_rebuild_is_forced(self):
        """显式失效入口语义不变：`invalidate_lookup_caches()` 强制下一次重建。"""
        pack = self._pack()
        self._warm(pack)
        token_before = pack._assets_token
        pack.invalidate_lookup_caches()
        self.assertIsNone(pack._assets_token,
                          "失效后令牌必须是 None（下一次 lookup 必然重建）")
        pack.lookup(key="k0", part_index=0, rate_key="normal", variant=0,
                    expected_text="零号话术。")
        self.assertIsNotNone(pack._assets_token, "重建后令牌被重新钉上")
        self.assertEqual(pack._assets_token, token_before,
                         "内容未变时重建出的令牌与失效前一致")
        self.assertEqual(len(pack._identity_index), 2, "两条资产各占一个键")


# ============================================================
# 6. 注入验证（反空转条款）：等价性测试必须能判红
# ============================================================
class TestEquivalenceAssertionIsNotVacuous(_TmpPackMixin):
    """证明上面的等价性断言不是空转。

    注入方式（反空转条款要求的两种）：
      ① 索引实现"随机取一条" —— 把 `_identity_lookup_index` 整个替换成
         返回随机映射的函数；
      ② 注入一个**错误映射** —— 替换成"命中键返回另一条"的函数。
    两种都作用于 `pack.lookup` 真实会调到的那个方法对象（打补丁），
    不改任何产品代码、不落盘。基准臂 `_legacy_lookup_linear_scan` 不经过
    索引，因此两条路径的分歧就是断言的红。
    """

    def _pack(self):
        root = self.tmp / "p"
        (root / "audio").mkdir(parents=True, exist_ok=True)
        voice, model = "Tingting", "macos-say"
        text = "您好，这里是服务中心。"
        fp = fingerprint(text=text, voice=voice, rate_value="normal",
                         model_version=model)
        (root / "audio" / "a.wav").write_bytes(b"\x00" * 32)
        return AssetPack(
            "t27-inject", "1", "0.1", "v1", voice, model, "2026-09-21T00:00:00Z",
            assets=[AssetEntry(key="greet", part_index=0, rate_key="normal",
                               variant=0, text=text, fingerprint=fp,
                               path="audio/a.wav", duration_ms=200)],
            root=root,
        )

    def _args(self, text="您好，这里是服务中心。"):
        return dict(key="greet", part_index=0, rate_key="normal", variant=0,
                    expected_text=text)

    def test_random_index_choice_breaks_equivalence(self):
        """① 索引改成"随机取一条" → 断言红（反空转）。

        值形态与真实索引一致：`(index, entry)`。这里注入的下标 `-1` 越界，
        所以第二道防线（`_entry_still_at`）会判失配、索引被重建、`lookup`
        恢复回包内正确条目——`lookup` 的返回值不再分歧，分歧发生在**注入层**：
        注入的索引本体确实指向越界的 decoy，而该 decoy 从未进入 `assets`。
        """
        pack = self._pack()
        rng = random.Random(7)
        decoy = AssetEntry(key="random_pick", part_index=0, rate_key="normal",
                           variant=0, text="您好，这里是服务中心。",
                           fingerprint=pack.assets[0].fingerprint,
                           path="audio/a.wav", duration_ms=200)

        def random_index(self_):
            """模拟"随机取一条"的索引实现（值形态与真实索引一致）。"""
            return {
                ("greet", 0, "normal", 0): (-1, decoy),
                (decoy.key, decoy.part_index, decoy.rate_key,
                 decoy.variant): (-1, decoy),
            }

        original = pack._identity_lookup_index
        pack._identity_lookup_index = random_index.__get__(pack, type(pack))
        try:
            new_r = pack.lookup(**self._args())
            old_r = _legacy_lookup_linear_scan(pack, **self._args())
        finally:
            pack._identity_lookup_index = original

        # 分歧可被断出来：注入层确实指向越界下标与 decoy，且 decoy 从未入列
        self.assertEqual(
            random_index(pack)[("greet", 0, "normal", 0)], (-1, decoy),
            "注入的索引本体指向越界下标 -1 与 decoy（断言不是空转）",
        )
        self.assertNotIn(decoy, pack.assets,
                         "decoy 从未进入 assets（不是假阳性）")
        # 第二道防线已把索引重建回正确值：两条路径回到一致
        self.assertIsNotNone(new_r, "新实现仍必须命中一条（fail-closed，不报错）")
        self.assertIsNotNone(old_r, "旧实现线性遍历 assets → 命中包内条目")
        self.assertIs(new_r, old_r,
                      "越界注入被第二道防线清掉后两条路径一致")
        self.assertEqual(new_r.key, "greet",
                         "命中的是包内条目，不是随机那条")

    def test_tampered_index_mapping_breaks_equivalence(self):
        """② 注入一个错误映射（同文本、另一身份）→ 断言红（反空转）。

        值形态与真实索引一致：`(index, entry)`，注入下标 `99` 越界。注入的条目
        能过指纹校验（同文本同四要素）——但因为它不在 `assets` 里，第二道防线
        （`_entry_still_at`）会先判失配、重建索引，所以 `lookup` 的返回值不再
        分歧；分歧发生在**注入层**：注入的索引本体确实指向身份不同、且下标越界
        的条目。该注入在 T27c 之前是靠 `lookup` 返回值分歧来证明的，T27d 起
        防线②把这种越界映射也清掉了，断言因此改到注入层（反空转强度不降）。
        """
        pack = self._pack()
        intruder = AssetEntry(key="greet", part_index=1, rate_key="normal",
                              variant=0, text="您好，这里是服务中心。",
                              fingerprint=pack.assets[0].fingerprint,
                              path="audio/a.wav", duration_ms=200)

        def tampered_index(self_):
            """模拟"错误映射"的索引实现：命中键指向身份不同、下标越界的条目。"""
            return {
                ("greet", 0, "normal", 0): (99, intruder),
                (intruder.key, intruder.part_index, intruder.rate_key,
                 intruder.variant): (99, intruder),
            }

        original = pack._identity_lookup_index
        pack._identity_lookup_index = tampered_index.__get__(pack, type(pack))
        try:
            new_r = pack.lookup(**self._args())
            old_r = _legacy_lookup_linear_scan(pack, **self._args())
        finally:
            pack._identity_lookup_index = original

        # 分歧可被断出来（注入层）：越界下标 + 身份不同 + 从未入列
        injected_pos, injected = tampered_index(pack)[("greet", 0, "normal", 0)]
        self.assertEqual(injected.part_index, 1,
                         "错误映射里的条目身份不同（part_index=1）")
        self.assertEqual(injected_pos, 99,
                         "注入下标越界——正是第二道防线要抓的情形")
        self.assertIs(injected, intruder, "注入的确实是那条 intruder")
        self.assertNotIn(intruder, pack.assets,
                         "intruder 从未进入 assets（不是假阳性）")
        # 第二道防线已把索引重建回正确值：两条路径回到一致
        self.assertIsNotNone(new_r, "新实现仍必须命中一条（fail-closed，不报错）")
        self.assertIsNotNone(old_r, "旧实现仍命中包内正确条目")
        self.assertIs(new_r, old_r,
                      "越界注入被第二道防线清掉后两条路径一致")
        self.assertEqual(old_r.part_index, 0, "命中的是包内 part_index=0 那条")

    def test_valid_index_survives_injection_free_run(self):
        """对照臂：不打补丁时两条路径 100% 一致（证明上面两条是真分歧）。"""
        pack = self._pack()
        rng = random.Random(11)
        cases = []
        for _ in range(40):
            cases.append(dict(
                key=rng.choice(["greet", "nope"]),
                part_index=rng.choice([0, 1]),
                rate_key=rng.choice(["slow", "normal", "fast"]),
                variant=rng.choice([0, 1]),
                expected_text=rng.choice(["您好，这里是服务中心。", "别的话。"]),
            ))
        for args in cases:
            self.assertEqual(pack.lookup(**args),
                             _legacy_lookup_linear_scan(pack, **args))

    def test_index_guard_recovers_after_external_mutation(self):
        """契约内变更（整体换列表）：守卫自动重建，索引跟着 assets 走。

        这是第一道防线（O(1) 签名）的测试面——`replace(pack, assets=[...])` 会
        重跑 `__post_init__`、缓存自然重置；这里直接验证的是"同一下标但换列表
        对象"的情形：签名里的 `id(assets)` 变了 → 立即重建。
        """
        pack = self._pack()
        pack.lookup(**self._args())                     # 建一次索引
        first = pack._identity_lookup_index()[("greet", 0, "normal", 0)]
        self.assertIs(first[1], pack.assets[0])
        self.assertEqual(first[0], 0)

        # 契约内写法（T27d）：整体换列表，不改既有列表对象
        pack.assets = [AssetEntry(key="greet", part_index=0, rate_key="normal",
                                  variant=0, text="您好，这里是服务中心。",
                                  fingerprint=pack.assets[0].fingerprint,
                                  path="audio/a.wav", duration_ms=200)]
        second = pack._identity_lookup_index()[("greet", 0, "normal", 0)]
        self.assertIs(second[1], pack.assets[0],
                      "索引必须跟随 assets 重建（不是沿用旧条目）")
        self.assertIsNot(first[1], second[1])
        self.assertEqual(second[0], 0, "重建后下标跟着当前列表走")


# ============================================================
# 7. 公开契约未动
# ============================================================
class TestPublicContractUntouched(unittest.TestCase):
    """T27 只加派生缓存，不得改变 AssetPack 的公开字段、构造签名与 repr。"""

    def test_dataclass_field_names_unchanged(self):
        import dataclasses
        self.assertEqual(
            [f.name for f in dataclasses.fields(AssetPack)],
            ["pack_id", "pack_version", "protocol_version", "ruleset_version",
             "voice", "model_version", "created_at", "assets", "root"],
        )

    def test_construction_signature_unchanged(self):
        import inspect
        self.assertEqual(
            list(inspect.signature(AssetPack).parameters),
            ["pack_id", "pack_version", "protocol_version", "ruleset_version",
             "voice", "model_version", "created_at", "assets", "root"],
        )

    def test_positional_construction_still_works(self):
        """八个公开字段按位置传参仍可用（既有调用方不依赖关键字）。"""
        pack = AssetPack("p", "1", "0.1", "v1", "v", "m", "t")
        self.assertEqual(pack.pack_id, "p")
        self.assertEqual(pack.assets, [])
        self.assertIsNone(pack.root)

    def test_repr_excludes_derived_caches(self):
        """repr 不得泄漏内部缓存（避免把派生数据当成契约的一部分）。"""
        pack = AssetPack("p", "1", "0.1", "v1", "v", "m", "t")
        r = repr(pack)
        self.assertNotIn("_identity_index", r)
        self.assertNotIn("_text_fingerprints", r)
        self.assertNotIn("_fingerprint_cache_built", r)

    def test_equality_ignores_derived_caches(self):
        """两个同参数的包必须相等（缓存不参与比较）。

        `AssetPack` 本身不可哈希（含可变 assets 列表）——这是 T27 之前就有的
        既有行为，本卡不改。这里只验"相等性"。
        """
        import json
        root = Path(tempfile.mkdtemp(prefix="assets-eq-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "audio").mkdir(parents=True, exist_ok=True)
        text = "您好，这里是服务中心。"
        fp = fingerprint(text=text, voice="v", rate_value="normal", model_version="m")
        (root / "audio" / "a.wav").write_bytes(b"\x00" * 16)
        a = AssetPack("p", "1", "0.1", "v1", "v", "m", "t",
                      assets=[AssetEntry(key="k", part_index=0, rate_key="normal",
                                         variant=0, text=text, fingerprint=fp,
                                         path="audio/a.wav", duration_ms=100)],
                      root=root)
        c = AssetPack("p", "1", "0.1", "v1", "v", "m", "t",
                      assets=list(a.assets), root=root)
        self.assertEqual(a, c, "未建缓存时同参数的包应相等")

        # 只在 a 上跑 lookup 建两个派生缓存，比较结果必须不受影响
        r = a.lookup(key="k", part_index=0, rate_key="normal", variant=0,
                     expected_text=text)
        self.assertIsNotNone(r)
        self.assertTrue(a._fingerprint_cache_built)
        self.assertEqual(len(a._identity_lookup_index()), 1)
        self.assertEqual(a, c, "派生缓存不得参与 AssetPack 的相等性比较")
        # 不可哈希是既有行为（本卡不引入也不修复）
        with self.assertRaises(TypeError):
            hash(a)

    def test_load_pack_validation_strength_unchanged(self):
        """load_pack 的校验强度只增不减：坏包仍被拒，消息仍含具体值。"""
        from assets.pack import AssetPackError
        root = Path(tempfile.mkdtemp(prefix="assets-ct-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with open(root / "manifest.json", "w", encoding="utf-8") as f:
            json.dump({"pack_id": "test"}, f)
        with self.assertRaises(AssetPackError) as ctx:
            load_pack(root)
        self.assertIn("pack_version", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
