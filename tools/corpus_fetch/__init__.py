"""tools.corpus_fetch — 公开语料的自给管道（fetch / derive / baseline）。

由 `labs/corpus-harvest/` 的一次性脚本提升而来（T12）。**只写仓外**：
第三方原始语料一律落 `--out-root`（缺省 `~/corpus/`），本目录不得写入语料。

规格依据：`docs/11-语料自给与语义提案-口径.md`（§11.3 许可台账与 fail-closed 规则、
§11.5 golden 格式、§11.6 格式硬约束、§11.9 实测记录）。

- `fetch`            先验许可、再下载、逐文件 sha256 交叉核对 → 台账（仓外）
- `derive_golden`    语料 → golden set 对照集（策略标签 → key，半自动）
- `baseline_literal` 逐字档基线测量（复用产品归一化，不另写一份）
"""

from tools.corpus_fetch.fetch import (
    ALLOWED_LICENSES,
    COMMERCIAL_WARNING_PATTERNS,
    FetchError,
    LicenseRejected,
    NONCOMMERCIAL_PATTERNS,
    audit_license,
    load_sources,
    merge_lock,
    process,
    select_files,
    write_lock,
)
from tools.corpus_fetch.derive_golden import DEFAULT_STRATEGY_TO_KEY, derive, load_strategy_map

__all__ = [
    "ALLOWED_LICENSES",
    "COMMERCIAL_WARNING_PATTERNS",
    "NONCOMMERCIAL_PATTERNS",
    "DEFAULT_STRATEGY_TO_KEY",
    "FetchError",
    "LicenseRejected",
    "audit_license",
    "derive",
    "load_sources",
    "load_strategy_map",
    "merge_lock",
    "process",
    "select_files",
    "write_lock",
]
