# eval/__init__.py
# 对拍与盲测层公共接口导出（eval/AGENTS.md ①②）
#
# 层边界（eval/AGENTS.md ⑤）：本包静态依赖只有 core/（指标字段常量）、
# assets/（只读包）、runtime/（只从 runtime/__init__ 取公共名）+ 标准库。
# adapters/ 一律经参数注入（CLI 侧 importlib 动态解析），绝不在这里静态 import。

# 双工质量 harness 不从包 __init__ 导出：-m eval.duplex_quality 走 runpy 时，
# 包 __init__ 已把 eval.duplex_quality 装进 sys.modules，再按路径二次 import 同一
# 模块会触发 CPython 的 RuntimeWarning（'found in sys.modules after import of
# package'）——那条警告会混进 stderr，掩盖真正的报错输出。harness 由 CLI 直接以
# 模块路径调用，测试侧也 import eval.duplex_quality，都不需要包级转发。

from .bench import BenchConfig, BenchReportError, run_bench, run_bench_cli
from .offline_tts import OfflineTts
from .report import (
    CALIBER,
    SCHEMA_VERSION,
    assess_completeness,
    build_report,
    check_raw_on_disk,
    render_summary,
    write_report,
)
from .stats import (
    BOOTSTRAP_RESAMPLES,
    DEFAULT_REPEATS,
    DEFAULT_SEED,
    MIN_REPEATS,
    QUANTILE_METHOD,
    bootstrap_ci,
    percentile,
    require_min_samples,
)

__all__ = [
    # 离线对拍 harness
    "run_bench",
    "run_bench_cli",
    "BenchConfig",
    "BenchReportError",
    # 双工质量 harness 见 eval.duplex_quality（不从包 __init__ 导出，见上）
    # 离线替身适配器（非产品件）
    "OfflineTts",
    # 报告
    "build_report",
    "write_report",
    "render_summary",
    "assess_completeness",
    "check_raw_on_disk",
    "SCHEMA_VERSION",
    "CALIBER",
    # 统计
    "percentile",
    "bootstrap_ci",
    "require_min_samples",
    "MIN_REPEATS",
    "DEFAULT_REPEATS",
    "BOOTSTRAP_RESAMPLES",
    "DEFAULT_SEED",
    "QUANTILE_METHOD",
]
