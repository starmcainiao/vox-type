"""
conftest.py — 仓根 pytest 引导（唯一作用：让「仓根一条命令跑全量」可收集）

背景（可复现事实）：
  本仓有十一个测试根（core rules assets adapters compiler runtime eval cli tools trigger packs），
  每根的 `tests/` 都带 `__init__.py`，于是它们的**包名都叫 `tests`**。
  pytest 默认 `importmode=prepend` 按「包名」导入模块名：
  先收的根把 `tests.test_xxx` 写进 `sys.modules`，后收的根拿到**别人的模块**，
  于是 `python3 -m pytest --collect-only -q` 报
  `ModuleNotFoundError: No module named 'tests.test_structure_budget'`
  并以 `7 errors during collection` 中断。

修法（不是改测试文件）：
  仓内十一个 `tests/` 的**包名必然撞**（每根一个 `tests`，这是十一个测试根的目录约定），
  靠改名或改测试文件的 import 行都会污染「每层自带 tests/」这条结构纪律。
  故用 pytest 的 `importlib` 导入模式：模块名改由**相对 rootdir 的路径**推导
  （`tools.tests.test_x` / `rules.tests.test_x`），天然唯一，撞名不复存在。

  importlib 模式不再把测试根的父目录塞进 `sys.path`，所以这里显式补仓根：
  测试文件按产品包绝对路径 import（`from core.protocol import parse_plan`），
  仓根必须在 `sys.path` 上——`unittest` 侧靠 `python3 -m` 的 cwd，pytest 侧靠这一行。

零依赖纪律：本文件**不依赖 pytest 包**（它只被 pytest 读取，不反过来引用 pytest）；
`python3 tools/run_all_tests.py` 走 unittest 路径，与本文件无关，仓内不装 pytest 也能跑全量。
"""

import sys
from pathlib import Path

# 仓根（conftest.py 所在目录）
_REPO_ROOT = Path(__file__).resolve().parent

# 仓根入 sys.path：让测试里的 `from core.protocol import ...` / `from tools.xxx import ...`
# 在 importlib 模式下同样成立（幂等，不覆盖已有条目）。
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def pytest_configure(config):
    """把 import 模式切到 importlib，隔离十一个同名的 `tests` 包。

    只在**没人显式指定** `--import-mode` 时才改（默认值是 `prepend`），
    尊重命令行上显式给的口径。
    """
    if getattr(config.option, "importmode", "prepend") == "prepend":
        config.option.importmode = "importlib"
