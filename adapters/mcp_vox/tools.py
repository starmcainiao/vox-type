"""tools.py — MCP 最小子集的三个工具（vox_lookup / vox_plan / vox_pack_check）。

只走 `assets` 与 `runtime` 的公开 `__all__`（load_pack / lookup / validate_pack /
REASON_KEY_NOT_PREBAKED），不 import 任何下划线私有名；只出控制面信息，**不出音频字节**
（数据面留在进程内 adapter）。

工具语义（T46 §三，钉死）：
  - vox_lookup(pack_dir, text|key) → 命中 {hit:true,key,rate_key,variant,duration_ms,model_version}；
    未命中 {hit:false,reason}（正常结果，不是 error）；包不存在/非法 → isError:true。
  - vox_plan(pack_dir, keys[]) → {plan:[{key,...}], all_hit:bool, misses:[]}
  - vox_pack_check(pack_dir) → {passed, keys, violations[]}；校验不过 → isError:true + 明细
"""

import json
from pathlib import Path

from assets import load_pack, lookup, validate_pack
from runtime import REASON_KEY_NOT_PREBAKED

PACK_DIR_SCHEMA = {
    "type": "object",
    "properties": {
        "pack_dir": {
            "type": "string",
            "description": "资产包根目录（含 manifest.json）",
        },
    },
    "required": ["pack_dir"],
}

TOOLS = [
    {
        "name": "vox_lookup",
        "description": "查一条话术是否命中预铸包（只出元数据，不出音频字节）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack_dir": PACK_DIR_SCHEMA["properties"]["pack_dir"],
                "text": {"type": "string", "description": "话术原文（与 key 二选一）"},
                "key": {"type": "string", "description": "话术 key（与 text 二选一）"},
            },
            "required": ["pack_dir"],
        },
    },
    {
        "name": "vox_plan",
        "description": "按 keys[] 出播报计划，逐条标注命中与未命中",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack_dir": PACK_DIR_SCHEMA["properties"]["pack_dir"],
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "话术 key 列表",
                },
            },
            "required": ["pack_dir", "keys"],
        },
    },
    {
        "name": "vox_pack_check",
        "description": "校验资产包（走 assets 公开校验 API），返回违规明细",
        "inputSchema": PACK_DIR_SCHEMA,
    },
]


def _hit_fields(entry, model_version):
    """命中条目的出参——刻意不含 path / 音频字节。"""
    return {
        "key": entry.key,
        "rate_key": entry.rate_key,
        "variant": entry.variant,
        "duration_ms": entry.duration_ms,
        "model_version": model_version,
    }


def _find_entry(pack, key=None, text=None):
    """按 key 或 text 在包内定位候选条目（包内顺序，首条优先）。"""
    for entry in pack.assets:
        if key is not None and entry.key == key:
            return entry
        if text is not None and entry.text == text:
            return entry
    return None


def _confirmed_hit(pack, entry):
    """把候选交给 assets 公开 lookup 复验（指纹/文件/失效），None = 未命中。"""
    if entry is None:
        return None
    return lookup(
        pack,
        entry.key,
        entry.part_index,
        entry.rate_key,
        entry.variant,
        entry.text,
    )


def _manifest_keys(root):
    """尽力从 manifest 读出 key 列表；读不了就返回空列表（不抛）。"""
    try:
        with open(Path(root) / "manifest.json", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    assets = data.get("assets", []) if isinstance(data, dict) else []
    return sorted(
        {item["key"] for item in assets
         if isinstance(item, dict) and isinstance(item.get("key"), str)}
    )


def _lookup(arguments):
    pack = load_pack(Path(arguments["pack_dir"]))
    key = arguments.get("key")
    text = arguments.get("text")
    if not key and not text:
        raise ValueError("vox_lookup 需要 text 或 key 之一")
    hit = _confirmed_hit(pack, _find_entry(pack, key=key, text=text))
    if hit is None:
        return {"hit": False, "reason": REASON_KEY_NOT_PREBAKED}, False
    return {"hit": True, **_hit_fields(hit, pack.model_version)}, False


def _plan(arguments):
    pack = load_pack(Path(arguments["pack_dir"]))
    keys = arguments.get("keys")
    if not isinstance(keys, list):
        raise ValueError("vox_plan 的 keys 必须是数组")
    plan, misses = [], []
    for key in keys:
        hit = _confirmed_hit(pack, _find_entry(pack, key=key))
        if hit is None:
            misses.append(key)
        else:
            plan.append(_hit_fields(hit, pack.model_version))
    return {"plan": plan, "all_hit": not misses, "misses": misses}, False


def _pack_check(arguments):
    root = Path(arguments["pack_dir"])
    violations = validate_pack(root)
    return {
        "passed": not violations,
        "keys": _manifest_keys(root),
        "violations": violations,
    }, bool(violations)


_HANDLERS = {
    "vox_lookup": _lookup,
    "vox_plan": _plan,
    "vox_pack_check": _pack_check,
}


def call_tool(name, arguments):
    """执行一个工具并包成 MCP tools/call 结果；任何异常都转 isError，不崩。"""
    if not isinstance(arguments, dict):
        arguments = {}
    handler = _HANDLERS.get(name)
    if handler is None:
        payload, is_error = {"error": f"未知工具: {name}"}, True
    else:
        try:
            payload, is_error = handler(arguments)
        except Exception as exc:  # noqa: BLE001 — 错误语义就是 isError，不外泄栈
            payload, is_error = {"error": f"{type(exc).__name__}: {exc}"}, True
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
        ],
        "isError": is_error,
    }
