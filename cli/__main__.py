# cli/__main__.py
# `python3 -m cli` 的入口（docs/08 §8.1：bin/vox 也是 exec 到这里）
#
# WHY 单独一个 __main__.py：bin/vox 是 POSIX sh 薄壳，必须 exec 一个 Python 入口；
#      用 `python3 -m cli` 而不是 `python3 cli/main.py`——后者会把 cli/ 目录
#      （而非仓库根）放进 sys.path，导致 `import core` / `import compiler` 找不到。

import sys

from cli.main import main

if __name__ == "__main__":
    sys.exit(main())
