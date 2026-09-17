# cli/__init__.py
# CLI 公共接口导出（docs/08 §8.1：cli/ 是薄壳，只做解析 → 调用 → 打印 → 退出码）

from cli.main import main

__all__ = ["main"]
