"""adapters.mcp_vox — vox-type 的 MCP 最小子集适配器（stdio JSON-RPC，零依赖）。

导出面：MCP 客户端接入所需的公开入口。
依赖授权（adapters/AGENTS.md §⑨）：只走 `runtime` / `assets` 的公开 `__all__`。
"""

from .server import main, serve
from .tools import TOOLS, call_tool

__all__ = ["TOOLS", "call_tool", "serve", "main"]
