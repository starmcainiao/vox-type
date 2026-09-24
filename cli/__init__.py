# cli/__init__.py
# CLI 公共接口导出（docs/08 §8.1：cli/ 是薄壳，只做解析 → 调用 → 打印 → 退出码）

from cli.main import main
from cli.commands.bench import load_corpus as _load_corpus

__all__ = ["main", "load_corpus"]


# load_corpus 的公开导出（T21 #18）。
# WHY 在这里转发而不是在 cli/commands/__init__.py 里：命令子模块（bench.py 等）在
#   **本包装载期间**就被 cli.main 动态加载，而它们反过来从 cli.commands 取共享底座，
#   因此 cli/commands/__init__.py 的 body 不能被提前执行完（会形成
#   cli.main ↔ cli.commands.bench 的循环）。cli/commands/__init__.py 的 __all__ 里
#   已列 load_corpus，使 `from cli.commands import load_corpus` 与
#   `from cli.commands.bench import load_corpus` 都成立——两处指向同一个 eval 实现，
#   CLI 不复制它的任何规则（语料校验判定全在 eval.load_corpus）。
load_corpus = _load_corpus
del _load_corpus
