"""
eval.tests.test_stats — 分位数 / bootstrap 置信区间 / 样本量下限

覆盖（对应 T08 验收与反空转条款）：
  1. 线性插值分位数的精确值（含端点与中间插值）
  2. 空样本 → ValueError，消息含实际样本量 0
  3. q 越界 / 类型错误 → ValueError / TypeError，消息含实际值
  4. 同 seed 两次 bootstrap 区间完全相同（可复现的硬断言）
  5. 不同 seed 区间允许不同（证明 seed 真的参与了采样，不是死代码）
  6. 区间边界合法：下界 <= 点估计 <= 上界，且落在样本 min/max 之内
  7. 样本数不足 → ValueError，消息含 name 与实际样本量
  8. level / resamples / seed 非法 → ValueError
  9. 冻结常量取值被钉住（改口径 = 破坏性变更，测试必须失败）

纪律：全部走产品 API（percentile / bootstrap_ci / require_min_samples），
      不在测试里重实现算法；负例断言异常类型 + 消息含关键值。
"""

import math
import unittest

from eval.stats import (
    BOOTSTRAP_RESAMPLES,
    DEFAULT_REPEATS,
    DEFAULT_SEED,
    MIN_REPEATS,
    QUANTILE_METHOD,
    bootstrap_ci,
    percentile,
    require_min_samples,
)


class PercentileTest(unittest.TestCase):
    """线性插值分位数。"""

    def test_endpoint_quantiles(self):
        """q 极接近 0/1 时逼近最小/最大值（n-1 位置的线性插值，非 clamp）。"""
        samples = [10.0, 20.0, 30.0, 40.0]
        # 位置 = (n-1)*q：q 越小位置越接近 0，值越接近 min——这里验证「逼近」而非严格等于，
        # 因为线性插值法在开区间 (0,1) 内永远不会精确落到端点样本上。
        low = percentile(samples, 1e-9)
        high = percentile(samples, 1 - 1e-9)
        self.assertAlmostEqual(low, 10.0, places=6)
        self.assertAlmostEqual(high, 40.0, places=6)
        self.assertGreaterEqual(low, min(samples))
        self.assertLessEqual(high, max(samples))

    def test_linear_interpolation_midpoint(self):
        """位置 = (n-1)*q：4 个样本 q=0.5 → 位置 1.5 → 20+30 的中点 25。"""
        self.assertAlmostEqual(percentile([10.0, 20.0, 30.0, 40.0], 0.5), 25.0, places=9)

    def test_linear_interpolation_fractional(self):
        """位置落在两个样本之间：5 个样本 q=0.3 → 位置 (n-1)*q = 4*0.3 = 1.2
        → sorted[1]=20 + 0.2*(sorted[2]-sorted[1]) = 20 + 0.2*(30-20) = 22。"""
        self.assertAlmostEqual(percentile([10.0, 20.0, 30.0, 40.0, 50.0], 0.3), 22.0, places=9)

    def test_exact_position_no_interpolation(self):
        """位置正好落在整数索引上（3 个样本 q=0.5 → 位置 1 → 中位值本身）。"""
        self.assertAlmostEqual(percentile([5.0, 8.0, 11.0], 0.5), 8.0, places=9)

    def test_unsorted_input(self):
        """入参未排序也要正确（内部排序副本，不改入参）。"""
        samples = [40.0, 10.0, 30.0, 20.0]
        original = list(samples)
        self.assertAlmostEqual(percentile(samples, 0.5), 25.0, places=9)
        self.assertEqual(samples, original, "percentile 不得修改入参")

    def test_single_sample(self):
        """单样本时任意 q 都返回该值（位置恒为 0）。"""
        for q in (0.01, 0.5, 0.99):
            self.assertAlmostEqual(percentile([42.0], q), 42.0, places=9)

    def test_p99_of_small_sample(self):
        """P99 在 20 个样本上仍可计算（报告要报 P99 而不是均值）。"""
        samples = [float(i) for i in range(1, 21)]
        # 位置 = 19 * 0.99 = 18.81 → 第 19 位(值 19) + 0.81*(20-19) = 19.81
        self.assertAlmostEqual(percentile(samples, 0.99), 19.81, places=9)

    def test_empty_raises_with_count(self):
        """空样本必须报错，且消息含实际样本量 0（禁止静默返回 0.0 = 声称零延迟）。"""
        with self.assertRaises(ValueError) as ctx:
            percentile([], 0.5)
        self.assertIn("0", str(ctx.exception))
        self.assertIn("percentile", str(ctx.exception))

    def test_q_out_of_range_raises(self):
        """q 必须落在 (0,1) 开区间内，消息含实际值。"""
        for bad in (0.0, 1.0, -0.2, 1.5):
            with self.assertRaises(ValueError) as ctx:
                percentile([1.0, 2.0, 3.0], bad)
            self.assertIn(repr(bad), str(ctx.exception))

    def test_q_boolean_rejected(self):
        """bool 是 int 子类，必须显式拒绝（True == 1 会绕过 (0,1) 校验）。"""
        with self.assertRaises(ValueError) as ctx:
            percentile([1.0, 2.0, 3.0], True)
        self.assertIn("True", str(ctx.exception))

    def test_string_rejected(self):
        """字符串逐字符迭代会被误当数值序列，必须显式拒绝。"""
        with self.assertRaises(TypeError) as ctx:
            percentile("1234", 0.5)
        self.assertIn("字符串", str(ctx.exception))

    def test_non_numeric_rejected(self):
        """序列含非数值元素时报 TypeError 并带原始值。"""
        with self.assertRaises(TypeError) as ctx:
            percentile([1.0, "x", 3.0], 0.5)
        self.assertIn("非数值", str(ctx.exception))


class RequireMinSamplesTest(unittest.TestCase):
    """样本量下限校验。"""

    def test_below_min_raises_with_name_and_count(self):
        """低于 MIN_REPEATS 抛 ValueError，消息含 name 与实际样本量。"""
        with self.assertRaises(ValueError) as ctx:
            require_min_samples([1.0, 2.0, 3.0], name="fast_arm")
        msg = str(ctx.exception)
        self.assertIn("fast_arm", msg)
        self.assertIn("3", msg)
        self.assertIn(str(MIN_REPEATS), msg)

    def test_exactly_min_ok(self):
        """等于 MIN_REPEATS 不报错。"""
        self.assertIsNone(require_min_samples(list(range(MIN_REPEATS)), name="slow_arm"))

    def test_above_min_ok(self):
        """超过 MIN_REPEATS 不报错。"""
        self.assertIsNone(require_min_samples(list(range(DEFAULT_REPEATS)), name="slow_arm"))

    def test_empty_raises(self):
        """空序列走同一条报错路径，且把实际样本量 0 写进消息。"""
        with self.assertRaises(ValueError) as ctx:
            require_min_samples([], name="fast_arm")
        self.assertIn("0", str(ctx.exception))

    def test_empty_name_rejected(self):
        """name 为空会让报错失去定位能力，必须拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            require_min_samples([], name="")
        self.assertIn("name", str(ctx.exception))

    def test_non_sized_rejected(self):
        """没有 len() 的对象报 TypeError 并带实际类型名。"""
        with self.assertRaises(TypeError) as ctx:
            require_min_samples(iter([1.0, 2.0]), name="fast_arm")
        self.assertIn("iterator", str(ctx.exception))
        self.assertIn("fast_arm", str(ctx.exception))


class BootstrapCiTest(unittest.TestCase):
    """百分位 bootstrap 置信区间。"""

    def _samples(self):
        """20 个样本（正好过下限），跨度足够大以便 seed 差异可见。"""
        return [float(i) for i in range(1, MIN_REPEATS + 1)]

    def test_same_seed_reproducible(self):
        """同一份数据 + 同一 seed + 同一 resamples → 必得同一区间（可复现的硬约束）。"""
        samples = self._samples()
        first = bootstrap_ci(samples, 0.5, seed=DEFAULT_SEED, resamples=200)
        second = bootstrap_ci(samples, 0.5, seed=DEFAULT_SEED, resamples=200)
        self.assertEqual(first, second)

    def test_different_seeds_can_differ(self):
        """换 seed 区间必须允许变化——若 8 个不同 seed 全相同，说明 seed 没参与采样。"""
        samples = self._samples()
        intervals = {
            bootstrap_ci(samples, 0.5, seed=seed, resamples=200)
            for seed in range(DEFAULT_SEED, DEFAULT_SEED + 8)
        }
        self.assertGreater(
            len(intervals),
            1,
            "8 个不同 seed 得到完全相同的区间，说明 seed 未参与重采样（不可复现的假象）",
        )

    def test_interval_well_formed(self):
        """下界 <= 点估计 <= 上界，且两个端点都落在样本 min/max 之内。

        这是有放回重采样的必然性质：每次重采样的样本都取自原始样本，
        所以任何分位数的估计值都不可能小于 min(samples) 或大于 max(samples)。
        （注意：区间端点不必包住原始 min/max——那是过度断言，尾部分位在
        小样本下会落在样本区间内部。）
        """
        samples = self._samples()
        for q in (0.5, 0.99):
            lo, hi = bootstrap_ci(samples, q, seed=DEFAULT_SEED, resamples=400)
            point = percentile(samples, q)
            self.assertLessEqual(lo, hi, f"q={q} 区间下界应 <= 上界")
            self.assertLessEqual(min(samples), lo)
            self.assertLessEqual(hi, max(samples))
            self.assertLessEqual(lo, point)
            self.assertGreaterEqual(hi, point)

    def test_ci_monotonic_in_quantile(self):
        """同一个 seed 下，尾部越高分位 → 区间端点单调不减。

        定理：两次 bootstrap_ci 用同一个 seed 与同一份样本，第 k 次重采样的
        样本序列完全相同；分位数函数在每个重采样样本上对 q 单调不减，
        于是「各重采样分位的向量」对 q 逐点单调，其尾部分位点亦单调。
        这是实现层面的硬性质，与样本量无关，比"区间一定更宽"的统计经验可靠。
        """
        samples = self._samples()
        for resamples in (200, 500):
            p50_lo, p50_hi = bootstrap_ci(samples, 0.5, seed=DEFAULT_SEED, resamples=resamples)
            p99_lo, p99_hi = bootstrap_ci(samples, 0.99, seed=DEFAULT_SEED, resamples=resamples)
            self.assertLessEqual(p50_lo, p99_lo)
            self.assertLessEqual(p50_hi, p99_hi)
            self.assertLessEqual(percentile(samples, 0.5), percentile(samples, 0.99))

    def test_narrower_level_gives_wider_interval(self):
        """置信水平越低（要求越严）区间越宽。"""
        samples = self._samples()
        tight = bootstrap_ci(samples, 0.5, seed=DEFAULT_SEED, resamples=400, level=0.80)
        wide = bootstrap_ci(samples, 0.5, seed=DEFAULT_SEED, resamples=400, level=0.99)
        self.assertLess(tight[1] - tight[0], wide[1] - wide[0])

    def test_insufficient_samples_raises_with_name_and_count(self):
        """样本量不足必须报错，消息含调用方标识与实际样本量。"""
        with self.assertRaises(ValueError) as ctx:
            bootstrap_ci([1.0, 2.0, 3.0], 0.5, seed=DEFAULT_SEED)
        msg = str(ctx.exception)
        self.assertIn("bootstrap_ci", msg)
        self.assertIn("3", msg)
        self.assertIn(str(MIN_REPEATS), msg)

    def test_invalid_seed_raises(self):
        """seed 必须是整数（固定种子是可复现的前提）。"""
        with self.assertRaises(ValueError) as ctx:
            bootstrap_ci(self._samples(), 0.5, seed="20260917")
        self.assertIn("seed", str(ctx.exception))
        self.assertIn("'20260917'", str(ctx.exception))

    def test_invalid_level_raises(self):
        for bad in (0.0, 1.0, 1.5, True):
            with self.assertRaises(ValueError) as ctx:
                bootstrap_ci(self._samples(), 0.5, seed=DEFAULT_SEED, level=bad)
            self.assertIn("level", str(ctx.exception))

    def test_invalid_resamples_raises(self):
        for bad in (0, -5):
            with self.assertRaises(ValueError) as ctx:
                bootstrap_ci(self._samples(), 0.5, seed=DEFAULT_SEED, resamples=bad)
            self.assertIn("resamples", str(ctx.exception))
            self.assertIn(repr(bad), str(ctx.exception))

    def test_does_not_pollute_global_random(self):
        """bootstrap 用实例级 RNG，不得改动全局随机状态（跨进程/跨测试稳定性）。

        用 random.getstate() 直接比对整份内部状态，而不是抽两组随机数比较——
        抽样的两组来自同一条连续流，本来就该不同，那样比是无效断言。
        """
        import random

        random.seed(12345)
        state_before = random.getstate()
        bootstrap_ci(self._samples(), 0.5, seed=DEFAULT_SEED, resamples=50)
        self.assertEqual(
            random.getstate(),
            state_before,
            "bootstrap_ci 污染了全局随机状态（必须用 random.Random(seed) 独立实例）",
        )

    def test_does_not_mutate_input(self):
        samples = self._samples()
        original = list(samples)
        bootstrap_ci(samples, 0.5, seed=DEFAULT_SEED, resamples=50)
        self.assertEqual(samples, original)


class ConstantsTest(unittest.TestCase):
    """冻结常量取值——改这里等于改口径（破坏性变更）。"""

    def test_frozen_values(self):
        self.assertEqual(MIN_REPEATS, 20)
        self.assertEqual(DEFAULT_REPEATS, 30)
        self.assertEqual(BOOTSTRAP_RESAMPLES, 2000)
        self.assertEqual(DEFAULT_SEED, 20260917)
        self.assertEqual(QUANTILE_METHOD, "linear")

    def test_default_resamples_used_when_omitted(self):
        """不传 resamples 时用冻结默认值（报告 caliber 里要写出来）。"""
        samples = [float(i) for i in range(1, MIN_REPEATS + 1)]
        explicit = bootstrap_ci(samples, 0.5, seed=DEFAULT_SEED, resamples=BOOTSTRAP_RESAMPLES)
        implicit = bootstrap_ci(samples, 0.5, seed=DEFAULT_SEED)
        self.assertEqual(explicit, implicit)


if __name__ == "__main__":
    unittest.main()
