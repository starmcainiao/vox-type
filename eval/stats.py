"""
eval.stats — 分位数 / 百分位 bootstrap 置信区间 / 样本量下限校验（纯函数，零第三方依赖）

职责：给 eval/ 的全部数字提供唯一统计实现——线性插值分位数、固定种子的百分位
      bootstrap 置信区间、样本量下限拦截。
不负责：不做采样（bench/）、不做报告组装（report/）、不定义指标字段名（core.metrics_spec）。

口径理由（冻结，报告 caliber 照抄）：
  1. 首响延迟是厚尾分布（eval/AGENTS.md §①）→ 禁均值外推，一律报 P50/P99 + 置信区间 + 样本量；
  2. n < 100 时 P99 的置信区间必然很宽，报告必须如实呈现，不得只看点估计；
  3. 分位数一律线性插值（QUANTILE_METHOD="linear"），位置 = (n-1)*q；
  4. bootstrap 用 random.Random(seed) 独立实例：固定 seed → 同输入必得同区间（可复现），
     且绝不污染全局随机状态（全局状态跨测试/跨进程不稳定，评测会失效）。
"""

from random import Random
from typing import Any, List, Sequence, Tuple

# ---------------------------------------------------------------------------
# 冻结常量（报告 caliber 里要原样写出来）
# ---------------------------------------------------------------------------

# 样本量下限：低于它一律不出报告（厚尾分布下 20 条以下连 P99 的分位估计都没有意义）
MIN_REPEATS: int = 20

# 默认重复次数：厚尾分布下 30 条是"能看趋势"的最低实用档
DEFAULT_REPEATS: int = 30

# 百分位 bootstrap 的重采样次数：2000 是常规档，再多只是让区间边界的估计噪声变小
BOOTSTRAP_RESAMPLES: int = 2000

# 默认种子：固定 → 同一份原始数据在任何机器、任何时间重算都得到同一区间
DEFAULT_SEED: int = 20260917

# 分位数算法名（写进报告，改这里 = 改口径 = 破坏性变更）
QUANTILE_METHOD: str = "linear"


# ---------------------------------------------------------------------------
# 内部：序列规整
# ---------------------------------------------------------------------------
def _as_floats(samples: Any, *, what: str) -> List[float]:
    """把入参规整为 float 列表；字符串/不可迭代/含非数值 → TypeError（消息含实际类型与 what）。"""
    if isinstance(samples, (str, bytes)):
        raise TypeError(
            f"{what}: 不能是字符串，实际为 {type(samples).__name__}（请传数值序列）"
        )
    try:
        values: List[float] = [float(v) for v in samples]
    except TypeError as exc:
        raise TypeError(
            f"{what}: 必须是可迭代的数值序列，实际为 {type(samples).__name__}"
        ) from exc
    except ValueError as exc:
        raise TypeError(f"{what}: 序列含非数值元素: {exc}") from exc
    return values


def _check_q(q: Any, *, what: str) -> float:
    """校验 q 落在 (0,1) 开区间（bool 是 int 子类，必须显式排除）。"""
    if (
        not isinstance(q, (int, float))
        or isinstance(q, bool)
        or not (0.0 < q < 1.0)
    ):
        raise ValueError(
            f"{what}: q 必须落在 (0,1) 开区间内，实际值为 {q!r}"
        )
    return float(q)


# ---------------------------------------------------------------------------
# 1. 分位数
# ---------------------------------------------------------------------------
def percentile(samples: Sequence[float], q: float) -> float:
    """线性插值分位数（位置 = (n-1)*q）。

    参数：
        samples: 数值序列（可未排序，内部排序副本，不改入参）
        q:       分位点，开区间 (0,1)

    返回：
        分位数值

    异常：
        ValueError: samples 为空（消息含实际样本量 0）或 q 越界
        TypeError:  samples 是字符串 / 不可迭代 / 含非数值元素
    """
    values = _as_floats(samples, what="percentile.samples")
    n = len(values)
    if n == 0:
        # WHY：空样本的分位数无定义，必须显式报错并把实际样本量写进消息——
        #      静默返回 0.0 会污染整份报告（P99=0 = 声称"零延迟"）。
        raise ValueError(
            f"percentile.samples: 样本为空（实际样本量 {n}），无法计算分位数"
        )
    q = _check_q(q, what="percentile")

    ordered = sorted(values)
    pos = (n - 1) * q          # 线性插值位置（n-1 而不是 n：首尾样本就是 min/max）
    lo = int(pos)              # pos >= 0，截断即 floor
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


# ---------------------------------------------------------------------------
# 2. 样本量下限
# ---------------------------------------------------------------------------
def require_min_samples(samples: Any, *, name: str) -> None:
    """校验样本量不低于 MIN_REPEATS，否则抛 ValueError（消息含 name 与实际样本量）。

    参数：
        samples: 带 len() 的序列
        name:    调用方标识（写进报错，便于定位是哪个环节样本不足）

    异常：
        ValueError: len(samples) < MIN_REPEATS
        TypeError:  samples 没有 len()
    """
    if not name:
        raise ValueError("require_min_samples: name 不能为空（报错需要定位到调用方）")
    try:
        count = len(samples)
    except TypeError as exc:
        raise TypeError(
            f"{name}: 样本必须是带 len() 的序列，实际为 {type(samples).__name__}"
        ) from exc
    if count < MIN_REPEATS:
        raise ValueError(
            f"{name}: 有效样本量 {count} 小于 MIN_REPEATS={MIN_REPEATS}——"
            f"厚尾分布禁均值外推（eval/AGENTS.md §①），样本不足不得出报告"
        )
    return None


# ---------------------------------------------------------------------------
# 3. 百分位 bootstrap 置信区间
# ---------------------------------------------------------------------------
def bootstrap_ci(
    samples: Sequence[float],
    q: float,
    *,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
    level: float = 0.95,
) -> Tuple[float, float]:
    """百分位 bootstrap：返回 (下界, 上界)。

    做法：用 random.Random(seed) 有放回重采样 resamples 次，每次算同一个分位数 q，
          取重采样分布的 (1-level)/2 与 1-(1-level)/2 分位作为区间端点。

    可复现性：同一份 samples + 同一 seed + 同一 resamples/level → 必得同一区间
              （random.Random 是实例级状态，不读全局随机状态，也不受 PYTHONHASHSEED 影响）。

    参数：
        samples:   数值序列（长度必须 >= MIN_REPEATS）
        q:         分位点，开区间 (0,1)
        seed:      随机种子（必须是整数）
        resamples: 重采样次数，>= 1
        level:     置信水平，开区间 (0,1)，默认 0.95

    返回：
        (下界, 上界)，下界 <= 上界

    异常：
        ValueError: 样本量不足 / q、level、resamples、seed 非法
        TypeError:  samples 是字符串 / 不可迭代 / 含非数值元素
    """
    name = "bootstrap_ci.samples"
    values = _as_floats(samples, what=name)
    require_min_samples(values, name=name)

    q = _check_q(q, what="bootstrap_ci")
    if (
        not isinstance(level, (int, float))
        or isinstance(level, bool)
        or not (0.0 < level < 1.0)
    ):
        raise ValueError(
            f"bootstrap_ci.level: 置信水平必须落在 (0,1) 开区间内，实际值为 {level!r}"
        )
    if not isinstance(resamples, int) or isinstance(resamples, bool) or resamples < 1:
        raise ValueError(
            f"bootstrap_ci.resamples: 必须是 >=1 的整数，实际值为 {resamples!r}"
        )
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(
            f"bootstrap_ci.seed: 必须是整数（固定种子是报告可复现的前提），实际值为 {seed!r}"
        )

    # 双侧等尾
    tail = (1.0 - level) / 2.0

    rng = Random(seed)          # 独立实例：不污染全局随机状态
    n = len(values)
    quantiles = [
        percentile(rng.choices(values, k=n), q)   # 有放回重采样（choices 是 C 实现，2000 次很快）
        for _ in range(resamples)
    ]
    return (percentile(quantiles, tail), percentile(quantiles, 1.0 - tail))
