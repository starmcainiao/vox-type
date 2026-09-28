"""__main__.py — 让 `python3 -m adapters.mcp_vox` 可直接起 stdio 服务（T71）。

为什么需要它（两条实跑事实）：
  1. `python3 -m adapters.mcp_vox` 原本报
     `No module named adapters.mcp_vox.__main__; 'adapters.mcp_vox' is a package and
     cannot be directly executed`——包没有入口模块。
  2. `python3 -m adapters.mcp_vox.server` 能在 **stderr** 吐一行
     `RuntimeWarning: 'adapters.mcp_vox.server' found in sys.modules after import of
     package 'adapters.mcp_vox', ...`（因本包 `__init__.py` 先 import 了 `.server`，
     runpy 因此认为该模块"已被包提前装进 sys.modules"）。协议不破、stdout 干净，
     但把 stderr 当错误的宿主会误判。

本文件的做法：在这里 import `.server`，而不是由 runpy 直接执行
`adapters.mcp_vox.server` 这个顶层模块——被导入时 `sys.modules['__main__']` 已经存在，
runpy 那条"提前装包"的告警不会触发。

只转派，不复制：本文件没有协议、工具、判定逻辑，一行都是。工具面与传输口径见
`server.py` 的 docstring（stdio 一行一条 JSON-RPC 2.0；只做
initialize / tools/list / tools/call）。
"""

from .server import serve

__all__ = ["main"]


def main():
    """stdio 主循环入口；返回 serve() 的退出码（stdin EOF 时为 0）。"""
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
