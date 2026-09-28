"""server.py — vox-type 的 MCP 最小子集 stdio 服务器（手写，零第三方依赖）。

协议子集（T46 §三/§四.3）：
  传输：stdio，**一行一条 JSON-RPC 2.0 消息**（多行 JSON 文本不当两条消息，处理为解析失败）；
  方法：initialize / tools/list / tools/call 三个；
  工具：vox_lookup / vox_plan / vox_pack_check 三个（只出控制面，不出音频字节）。
  **不在本卡范围**：HTTP-SSE 传输、resources（资源）、prompts（提示）、
  sampling、批处理、进度通知、取消等 MCP 其余能力。

错误语义：未知方法 → -32601（Method not found）；无 id 的 notification（含未知通知）静默忽略；
  工具内部错误 → tools/call 结果 isError:true（不崩、不吐栈）。
"""

import json
import sys

from .tools import TOOLS, call_tool

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "vox-type-mcp", "version": "1.0.0"}


def _response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def handle(message):
    """处理一条 JSON-RPC 消息；返回响应 dict，notification 返回 None。"""
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    request_id = message.get("id")
    is_notification = "id" not in message
    method = message.get("method")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": dict(SERVER_INFO),
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = call_tool(params.get("name"), params.get("arguments"))
    elif is_notification:
        return None
    else:
        return _error(request_id, -32601, f"Method not found: {method}")

    if is_notification:
        return None
    return _response(request_id, result)


def serve(stdin=None, stdout=None):
    """stdio 主循环：逐行读一条 JSON 消息，逐行回一条 JSON 响应。"""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"Parse error: {exc}")
        else:
            response = handle(message)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


def main():
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
