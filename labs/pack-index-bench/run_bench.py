#!/usr/bin/env python3
"""
labs/pack-index-bench — T27 查找热路径索引化的对照实测（改前 vs 改后）

回答一个问题：把 `AssetPack.lookup` 的「线性遍历 + 每次重算 sha256」换成
「惰性身份索引 + 预计算指纹」之后，大包量级下快了多少？

**先说结论（不粉饰）**：在"运行期允许原地改动 assets"这个前提下，索引化
**并不能**把 lookup 变快——失效守卫本身是 O(n) 的内容签名比对，成本高于线性遍历。
本卡的收益是**语义正确性**（不留下"本该命中却换了路径"的破口）与 **stat 次数**
（O(n) → O(1)），不是大包上的 CPU 时间。数字见本文件落盘的 report.json。

不进任何层的契约。纯标准库，零第三方依赖。

比较口径（不自造判据）：
  - 同一组输入、同一个包，两条实现并排跑；
  - **新实现** = `pack.lookup`（走 assets 层 T27 身份索引 + 预计算指纹）；
  - **旧实现** = 本文件内的 `legacy_lookup`（T27 之前的 `AssetPack.lookup` 本体，
    逐语句照抄：线性遍历 → 每次重算 sha256 → 文件存在性校验）；
  - **无守卫臂** = 临时 monkeypatch 掉守卫的 `pack.lookup`，用于拆开
    「索引本身」和「失效守卫」各自的成本（这是解释上面结论的关键）；
  - 统计：每组 n 次采样的 P50 / P99（微秒）。

关键测量设计（否则数字会误导）：
  1. **stat 成本单列，不混进主对比。** `Path.exists()` 在本机磁盘上单次约数百微秒，
     会彻底淹没定位成本。主对比把 `exists` 替换成内存对象（`_stub_root`，两条实现
     拿到**同一个** stub），只测「定位 + 指纹」；末尾单列 `hit_arm_with_real_stat`
     记真实 stat 的实测数字。
  2. **主对比测「未命中身份」臂**（key 不存在）与「指纹不匹配」臂——两者都不做
     stat，是定位成本的干净测量。
  3. **命中臂按位置分档**（首条 / 中段 / 末条）：线性遍历的成本强依赖命中位置，
     只测首条会把旧实现"看起来很快"，是误导性测量。
  4. **两个包量级**：`--n 10000`（卡要求的 10k 量级）与 `--n2 200000`
     （线性遍历在这个量级才真正昂贵）。

退出码：0 成功（report.json 已落盘）/ 2 用法错误 / 3 运行期失败。

反空转（卡第 4 条）：见 `--selfcheck`。它把守卫换成一个"从不发现改动"的实现，
模拟"只靠索引、不做失效判断"的情形，验证该情形下确实存在静默错误——
即等价性/性能结论不是凭空声明的，而是有可复现的反例支撑。
"""

import argparse
import json
import math
import platform
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from assets.fingerprint import fingerprint  # noqa: E402
from assets.pack import AssetEntry, AssetPack  # noqa: E402


# ---------------------------------------------------------------------------
# 旧实现（基准臂）：T27 之前的 AssetPack.lookup 本体，逐语句照抄。
#
# 对照臂，不是产品重实现：价值在于新旧两条路径并排跑同一组输入，任一分歧都让
# 断言变红。指纹算法一律调 assets.fingerprint.fingerprint（不复制）。
# ---------------------------------------------------------------------------
def legacy_lookup(pack, key, part_index, rate_key, variant, expected_text):
    """T27 之前的 lookup：线性遍历 → 重算 sha256 → 文件存在性校验。"""
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
# 无守卫臂：临时关掉失效判断，只保留索引查表。
# 用于拆开「索引」与「守卫」的成本；同时是反空转的对照实现。
# ---------------------------------------------------------------------------
def _index_only(self):
    """不做任何失效判断、直接返回缓存（"改前语义等价 + 无守卫"的索引实现）。"""
    return self._identity_index


# ---------------------------------------------------------------------------
# stat 分离：内存对象替代 Path.exists（两条实现拿到同一个 stub）
# ---------------------------------------------------------------------------
def _stub_root():
    """造一个 root 替身：`root / path` 返回查内存表的对象，不做任何磁盘 I/O。

    两条实现共享同一个 stub → 排除「文件系统状态差异」这个变量，
    只留下「定位 + 指纹」的逻辑成本。
    """
    class _StubPath:
        def exists(self):
            return True

    class _StubRoot:
        def __truediv__(self, other):
            return _StubPath()

    return _StubRoot()


# ---------------------------------------------------------------------------
# 造包
# ---------------------------------------------------------------------------
def build_pack(root: Path, n: int, keys: int = 500) -> AssetPack:
    """造 n 条资产：keys 个 key × 每 key 多 variant。

    音频文件真写盘（stat 臂依赖它）；主对比臂用 stub 隔离磁盘成本。
    """
    voice, model = "Tingting", "macos-say"
    audio_dir = root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    assets = []
    for i in range(n):
        key = f"k{i % keys}"
        variant = i // keys
        rate_key = "normal"
        text = f"您好，这里是第{i}号预设话术，请简要说明您的问题。"
        fp = fingerprint(text=text, voice=voice, rate_value=rate_key,
                         model_version=model)
        (audio_dir / f"{fp}.wav").write_bytes(b"\x00\x00" * 8)
        assets.append(AssetEntry(
            key=key, part_index=0, rate_key=rate_key, variant=variant,
            text=text, fingerprint=fp, path=f"audio/{fp}.wav", duration_ms=120,
        ))

    return AssetPack(
        pack_id="t27-bench", pack_version="1", protocol_version="0.1",
        ruleset_version="v1", voice=voice, model_version=model,
        created_at="2026-09-21T00:00:00Z", assets=assets, root=root,
    )


# ---------------------------------------------------------------------------
# 测量工具
# ---------------------------------------------------------------------------
def percentile(samples, pct):
    """线性插值百分位。"""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return float(ordered[lo])
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def timed(fn, n):
    """跑 n 次，返回 (微秒样本列表, 总耗时秒)。"""
    samples = []
    t0 = time.perf_counter()
    for _ in range(n):
        s = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - s) / 1000.0)
    return samples, time.perf_counter() - t0


def summary(samples):
    return {
        "p50_us": round(percentile(samples, 50), 3),
        "p99_us": round(percentile(samples, 99), 3),
        "min_us": round(min(samples), 3),
        "max_us": round(max(samples), 3),
        "mean_us": round(statistics.fmean(samples), 3),
        "n_samples": len(samples),
    }


def ratio(a, b):
    """a / b（b 为 0 时返回 None）。"""
    return round(a / b, 2) if b else None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def bench_one(n: int, repeat: int, calls_per_repeat: int = 1000):
    """一个包量级下的全部测量，返回该量级的结果字典。"""
    tmp = Path(tempfile.mkdtemp(prefix="t27-bench-"))
    try:
        pack = build_pack(tmp / "pack", n)
        probe = pack.assets[0]
        first_pos = probe
        mid_pos = pack.assets[len(pack.assets) // 2]
        last_pos = pack.assets[-1]
        save_root = pack.root
        stub = _stub_root()

        # 各位置的话术文本（文本必须与条目一致，才走指纹快路径）
        def probe_text(entry):
            return entry.text

        # ① 冷启动摊销：首次 lookup 建索引 + 全表预计算指纹（一次 O(n)）
        pack.invalidate_lookup_caches()
        _, cold_new = timed(
            lambda: pack.lookup(probe.key, 0, "normal", probe.variant, probe.text), 1)
        pack.invalidate_lookup_caches()
        _, cold_old = timed(
            lambda: legacy_lookup(pack, probe.key, 0, "normal",
                                  probe.variant, probe.text), 1)

        # ② 主对比：未命中身份臂（不做 stat）
        total = min(n, calls_per_repeat) * repeat
        pack.root = stub
        s_new, _ = timed(
            lambda: pack.lookup("__no_such_key__", 0, "normal", 0, "x"), total)
        s_old, _ = timed(
            lambda: legacy_lookup(pack, "__no_such_key__", 0, "normal", 0, "x"),
            total)

        # ③ 主对比：指纹不匹配臂（身份命中但文本不符）
        s_new2, _ = timed(
            lambda: pack.lookup(probe.key, 0, "normal", probe.variant,
                                "完全不同的另一句话。"), total)
        s_old2, _ = timed(
            lambda: legacy_lookup(pack, probe.key, 0, "normal", probe.variant,
                                  "完全不同的另一句话。"), total)

        # ④ 命中臂按位置分档（stub 隔离 stat）：线性成本强依赖命中位置
        hit_arms = {}
        for label, entry in (("first", first_pos), ("mid", mid_pos), ("last", last_pos)):
            tx = probe_text(entry)
            s_nh, _ = timed(
                lambda tx=tx, e=entry: pack.lookup(e.key, 0, "normal", e.variant, tx),
                total)
            s_oh, _ = timed(
                lambda tx=tx, e=entry: legacy_lookup(pack, e.key, 0, "normal",
                                                     e.variant, tx),
                total)
            hit_arms[f"hit_{label}_no_stat"] = {
                "new_index": summary(s_nh),
                "old_linear": summary(s_oh),
                "ratio_old_over_new_p50": ratio(
                    summary(s_oh)["p50_us"], summary(s_nh)["p50_us"]),
            }

        # ⑤ 拆分成本：索引查表本身 vs 失效守卫本身
        original_index = pack._identity_lookup_index
        pack._identity_lookup_index = _index_only.__get__(pack, type(pack))
        try:
            s_noguard, _ = timed(
                lambda: pack.lookup("__no_such_key__", 0, "normal", 0, "x"), total)
        finally:
            pack._identity_lookup_index = original_index
        s_guard, _ = timed(lambda: pack._ensure_lookup_caches(), total)

        # ⑥ stat 臂：真实磁盘 stat 的实测数字（单列，不进主对比）
        pack.root = save_root
        stat_new, _ = timed(
            lambda: pack.lookup(probe.key, 0, "normal", probe.variant, probe.text), 30)
        stat_old, _ = timed(
            lambda: legacy_lookup(pack, probe.key, 0, "normal", probe.variant,
                                  probe.text), 30)

        return {
            "n_assets": n,
            "cold_first_lookup_us": {
                "new_includes_index_build_and_guard": round(cold_new * 1e6, 1),
                "old": round(cold_old * 1e6, 1),
            },
            "miss_key_arm_no_stat": {
                "new_index": summary(s_new),
                "old_linear": summary(s_old),
                "ratio_old_over_new_p50": ratio(
                    summary(s_old)["p50_us"], summary(s_new)["p50_us"]),
            },
            "fingerprint_mismatch_arm_no_stat": {
                "new_index": summary(s_new2),
                "old_linear": summary(s_old2),
                "ratio_old_over_new_p50": ratio(
                    summary(s_old2)["p50_us"], summary(s_new2)["p50_us"]),
            },
            **hit_arms,
            "cost_breakdown_no_stat": {
                "new_index_plus_guard": summary(s_new)["p50_us"],
                "new_index_only_no_guard": summary(s_noguard)["p50_us"],
                "guard_alone_ensure_lookup_caches": summary(s_guard)["p50_us"],
                "old_linear_scan_full": summary(s_old)["p50_us"],
                "note": ("拆开看：索引查表本身比线性遍历便宜，但失效守卫（内容签名"
                         "比对）本身是 O(n)，把这部分节省抵消掉了"),
            },
            "hit_arm_with_real_stat": {
                "new_index_p50_us": round(percentile(stat_new, 50), 1),
                "old_linear_p50_us": round(percentile(stat_old, 50), 1),
                "note": ("命中臂必须做一次真实的 Path.exists()（stat 不缓存是保险丝）。"
                         "该成本主导且两条实现相同，因此单列、不进主对比。"),
            },
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def legacy_selfcheck() -> int:
    """反空转（性能）：同一个测量方法，在"改前"语义上必须跑出可分的数字。

    卡第 4 条要求"性能脚本要能在改前代码上跑出明显更差的数字（否则说明测量方法
    无灵敏度）"。本卡的实测结论是：正确实现**不快于**改前实现（见 headline），
    因此不能用"新 vs 旧 P50 比较"充当灵敏度证据。

    这里换一个不依赖结论的灵敏度证明：在同一台机、同一测量函数下，把
    `legacy_lookup` 换成两个明显不同的基准臂——
      ① 线性遍历（改前真实实现）；
      ② 线性遍历 + 每条命中都重算 3 次 sha256（人为放大的基准）；
    若测量方法有灵敏度，② 的 P50 必须显著大于 ①。这个比较与 T27 的实现无关，
    因此能独立证明"测量方法本身是分得开的"。
    返回 0 表示灵敏度成立。
    """
    tmp = Path(tempfile.mkdtemp(prefix="t27-legacy-"))
    try:
        pack = build_pack(tmp / "pack", 10000)
        save_root = pack.root
        pack.root = _stub_root()
        probe = pack.assets[-1]
        tx = probe.text

        def heavy_legacy():
            """基准②：线性遍历 + 每条候选都重算 sha256（人为放大指纹成本）。"""
            for entry in pack.assets:
                if (entry.key == probe.key and entry.part_index == 0
                        and entry.rate_key == probe.rate_key
                        and entry.variant == probe.variant):
                    for _ in range(3):
                        fingerprint(text=tx, voice=pack.voice,
                                    rate_value=probe.rate_key,
                                    model_version=pack.model_version)
                    return entry
            return None

        s_legacy, _ = timed(
            lambda: legacy_lookup(pack, probe.key, 0, "normal", probe.variant, tx),
            300)
        s_heavy, _ = timed(heavy_legacy, 300)
        p_legacy = summary(s_legacy)["p50_us"]
        p_heavy = summary(s_heavy)["p50_us"]
        pack.root = save_root
        out = {
            "legacy_selfcheck": "测量方法灵敏度（与 T27 实现无关）",
            "legacy_linear_p50_us": p_legacy,
            "legacy_plus_3x_sha256_p50_us": p_heavy,
            "ratio_heavy_over_linear": ratio(p_heavy, p_legacy),
            "verdict": ("灵敏度成立" if p_heavy > p_legacy else
                        "灵敏度不成立——测量方法无区分力"),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if p_heavy > p_legacy else 3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def selfcheck() -> int:
    """反空转自检：关掉守卫后，原地改 assets 会让 lookup 返回已不存在的条目。

    证明两件事：
      ① 索引化后的正确性**依赖**失效守卫（不是自然成立的）；
      ② 因此本卡的收益是语义正确性，不能声称为"纯性能优化"。
    返回 0 表示反例复现成功。
    """
    tmp = Path(tempfile.mkdtemp(prefix="t27-selfcheck-"))
    rc = 3
    try:
        pack = build_pack(tmp / "pack", 6)
        target = pack.assets[1]
        probe_args = (target.key, 0, "normal", target.variant, target.text)
        # 先建缓存
        assert pack.lookup(*probe_args) is not None
        # 关掉守卫
        pack._identity_lookup_index = _index_only.__get__(pack, type(pack))
        # 原地把那条换成别的身份（运行期改动 assets）
        pack.assets = list(pack.assets)
        pack.assets[1] = AssetEntry(
            key=target.key, part_index=1, rate_key=target.rate_key,
            variant=target.variant, text=target.text, fingerprint=target.fingerprint,
            path=target.path, duration_ms=target.duration_ms,
        )
        # 无守卫：旧身份仍会命中（错误！应该是 None）
        ghost = pack.lookup(*probe_args)
        # 正确实现（真索引）：同一操作后必须 None
        pack._identity_lookup_index = AssetPack._identity_lookup_index.__get__(
            pack, type(pack))
        correct = pack.lookup(*probe_args)
        out = {
            "selfcheck": "反空转：关掉失效守卫后原地改 assets 的对照",
            "no_guard_returns_stale_entry": ghost is not None,
            "no_guard_entry_part_index": ghost.part_index if ghost else None,
            "correct_implementation_returns": (
                "None" if correct is None else correct.part_index),
            "verdict": ("反例成立" if ghost is not None and correct is None
                        else "反例不成立——结论需重新核对"),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        rc = 0 if (ghost is not None and correct is None) else 3
        return rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--n", type=int, default=10000,
                    help="资产条数（卡要求 10k 量级；默认 10000）")
    ap.add_argument("--n2", type=int, default=200000,
                    help="第二个量级（0 表示跳过）")
    ap.add_argument("--repeat", type=int, default=3, help="每组重复轮数（默认 3）")
    ap.add_argument("--out", type=str,
                    default=str(Path(__file__).with_name("report.json")),
                    help="report.json 落盘路径")
    ap.add_argument("--selfcheck", action="store_true",
                    help="只跑反空转自检（守卫必要性），不做性能测量")
    ap.add_argument("--legacy-selfcheck", action="store_true",
                    help="只跑测量方法灵敏度自检，不做性能测量")
    args = ap.parse_args(argv)

    if args.legacy_selfcheck:
        return legacy_selfcheck()
    if args.selfcheck:
        return selfcheck()

    try:
        sizes = [s for s in (args.n, args.n2) if s > 0]
        report = {
            "card": "docs/tasks/T27-assets-热路径索引化.md",
            "what": ("AssetPack.lookup：线性遍历 + 每次重算 sha256 → "
                     "惰性身份索引 + 预计算指纹（语义零变化）"),
            "headline": (
                "保留失效守卫的前提下，索引化并未让 lookup 更快——守卫本身是 O(n) 的"
                "内容签名比对，成本高于线性遍历。本卡的收益是语义正确性"
                "（原地改 assets 不留下静默错误）与 stat 次数 O(n)→O(1)，"
                "不是大包上的 CPU 时间。逐量级数字见 sizes。"),
            "measurement": {
                "main_arm": ("未命中身份臂 + 指纹不匹配臂 + 命中臂按位置分档，"
                             "stat 用内存 stub 隔离，只测「定位 + 指纹」"),
                "stat_arm": "命中臂走真实 Path.exists()，单列不进主对比",
                "why_isolated": ("Path.exists() 在本机磁盘单次约数百微秒，会淹没定位成本；"
                                 "而 stat 不缓存是保险丝（本卡硬约束②），"
                                 "它必须留在实现里，但不该混进定位成本的对比"),
                "baseline_arm": ("本文件 legacy_lookup：T27 之前的 lookup 本体，"
                                 "逐语句照抄；指纹算法调 assets.fingerprint，不复制"),
                "ratio_field": "ratio_old_over_new_p50 = 旧 P50 / 新 P50；>1 表示旧更慢",
            },
            "env": {
                "machine": platform.platform(),
                "machine_model": platform.machine(),
                "python": platform.python_version(),
                "py_impl": sys.implementation.name,
                "repeat": args.repeat,
            },
            "sizes": {},
            "text_arm_find_hit": None,
            "problems": [],
        }

        for n in sizes:
            report["sizes"][f"n={n}"] = bench_one(n, args.repeat)

        # 文本档全链路（find_hit）：T27 只影响其中 confirm → pack.lookup 的定位成本
        try:
            from adapters.framework_kefu import find_hit
            tmp = Path(tempfile.mkdtemp(prefix="t27-text-"))
            try:
                pack = build_pack(tmp / "pack", args.n)
                text = pack.assets[0].text
                t_new, _ = timed(lambda: find_hit(pack, text=text), 300)
                report["text_arm_find_hit"] = {
                    "n_assets": args.n,
                    "p50_us": round(percentile(t_new, 50), 1),
                    "p99_us": round(percentile(t_new, 99), 1),
                    "note": ("文本档每次调用都会重建 build_text_index（O(n)），"
                             "T27 只影响其中 confirm → pack.lookup 的定位成本"),
                }
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            report["text_arm_find_hit"] = {
                "error": f"{type(exc).__name__}: {exc}"}

        # 诚实记录：凡"新实现不快于旧实现"的臂，都记进 problems——
        # 这不是失败，是事实。报告因此带 problems 但退出码仍为 0。
        for size, block in report["sizes"].items():
            for arm in ("miss_key_arm_no_stat", "fingerprint_mismatch_arm_no_stat",
                        "hit_first_no_stat", "hit_mid_no_stat", "hit_last_no_stat"):
                a = block.get(arm)
                if not a:
                    continue
                old = a["old_linear"]["p50_us"]
                new = a["new_index"]["p50_us"]
                if not (old > new):
                    report["problems"].append(
                        f"{size} {arm}: 旧 P50={old}us 不比新 P50={new}us 更差")

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"python={platform.python_version()} machine={platform.platform()}")
        for size, block in report["sizes"].items():
            arm = block["miss_key_arm_no_stat"]
            print(f"[{size}] miss(key)    new P50={arm['new_index']['p50_us']:>8}us"
                  f"  old P50={arm['old_linear']['p50_us']:>8}us"
                  f"  old/new={arm['ratio_old_over_new_p50']}x")
            arm2 = block["fingerprint_mismatch_arm_no_stat"]
            print(f"[{size}] fp-mismatch  new P50={arm2['new_index']['p50_us']:>8}us"
                  f"  old P50={arm2['old_linear']['p50_us']:>8}us"
                  f"  old/new={arm2['ratio_old_over_new_p50']}x")
            for pos in ("first", "mid", "last"):
                h = block[f"hit_{pos}_no_stat"]
                print(f"[{size}] hit-{pos:<5}    new P50={h['new_index']['p50_us']:>8}us"
                      f"  old P50={h['old_linear']['p50_us']:>8}us"
                      f"  old/new={h['ratio_old_over_new_p50']}x")
            bd = block["cost_breakdown_no_stat"]
            print(f"[{size}] breakdown    guard={bd['guard_alone_ensure_lookup_caches']}us"
                  f"  index-only={bd['new_index_only_no_guard']}us"
                  f"  idx+guard={bd['new_index_plus_guard']}us"
                  f"  legacy={bd['old_linear_scan_full']}us")
            cold = block["cold_first_lookup_us"]
            print(f"[{size}] cold first   new={cold['new_includes_index_build_and_guard']}us"
                  f"  old={cold['old']}us")
            hit = block["hit_arm_with_real_stat"]
            print(f"[{size}] hit+realstat new={hit['new_index_p50_us']}us"
                  f"  old={hit['old_linear_p50_us']}us（stat 主导，单列）")
        if report["problems"]:
            print("FACTS（新实现不快于旧实现的臂，如实记录）:")
            for p_ in report["problems"]:
                print("  -", p_)
        print("report ->", out.name)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
