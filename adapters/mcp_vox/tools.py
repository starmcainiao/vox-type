"""tools.py — MCP 最小子集的四个工具（vox_lookup / vox_plan / vox_pack_check / vox_plan_from_state）。

只走 `assets` / `runtime` / `adapters.textmatch` / `trigger` 的公开 `__all__`（load_pack /
validate_pack / REASON_KEY_NOT_PREBAKED / find_hit / load_trigger / build_plan），
不 import 任何下划线私有名；只出控制面信息，**不出音频字节**（数据面留在进程内 adapter）。

命中口径（T47）：`vox_lookup` / `vox_plan` 的命中判定**不再在本文件自实现**，
    一律经 `adapters.textmatch.find_hit` —— 与 `framework_kefu` 桥同一份归一化与
    逐字判据（docs/10 §10.3 / §10.7）。此前这里是裸的文本相等比较（不走归一化），
    导致「包内 `价格①`、传入 `价格1`」在 kefu 入口命中、在 MCP 入口不命中且无留痕
    ——正是 AGENTS.md 纪律 4 禁止的静默降级。

工具语义（T46 §三，钉死）：
  - vox_lookup(pack_dir, text|key) → 命中 {hit:true,key,rate_key,variant,duration_ms,model_version}；
    未命中 {hit:false,reason}（正常结果，不是 error）；包不存在/非法 → isError:true。
  - vox_plan(pack_dir, keys[]) → {plan:[{key,...}], all_hit:bool, misses:[]}
  - vox_pack_check(pack_dir) → {passed, keys, violations[]}；校验不过 → isError:true + 明细
  - vox_plan_from_state(pack_dir, state, turn_id?) → {trigger_id, rule_id, plan:[{action,key,rate,variant,slots}],
    keys[], chars, budget_chars, plan_id}；state 非法 / 无兜底且无匹配 / 超预算 → isError:true + 明细。
    **只认包源目录**（含 `trigger.json`）——铸出来的资产包里没有 `trigger.json`（T86）。

`pack_dir` 一律只认**铸出来的资产包**（有 manifest.json）。用户手里最自然的路径却是
**包源目录** `packs/<业务>/`（有 phrases.json、没有 manifest.json），此时校验不过、但
`violations` 只说「缺少 manifest.json」——准确却不指路。故 `_pack_check` 在这一种形态上
**末尾追加一条**怎么铸的提示（T88）。
WHY 只加提示、不扩语义：让 MCP 也吃包源目录要 import `compiler` 的源格式校验，
违反 `adapters/AGENTS.md` §⑤「禁止 import compiler/」，故本文件只做纯文件系统的形态识别。
"""

import json
from pathlib import Path

from assets import load_pack, validate_pack
from runtime import REASON_KEY_NOT_PREBAKED
from adapters.textmatch import find_hit
from trigger import build_plan, load_trigger

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
    {
        "name": "vox_plan_from_state",
        "description": "按 state 快照出播报计划（走 trigger 公开 API；只出 plan 元数据，不出音频字节）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack_dir": {
                    "type": "string",
                    "description": "包源目录（含 trigger.json）；铸出来的资产包不含 trigger.json",
                },
                "state": {
                    "type": "object",
                    "description": "扁平 state 快照；字段须在 trigger.json 的 state_fields 里声明",
                },
                "turn_id": {
                    "type": "string",
                    "description": "可选：会话轮次 ID（参与变体稳定散列，便于重放）",
                },
            },
            "required": ["pack_dir", "state"],
        },
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
    """按 key 或 text 定位命中条目；判定口径由 adapters.textmatch 独家持有。

    WHY 不在本文件遍历比对（docs/10 §10.2 裁定 1）：归一化（§10.3 四步）与逐字
        命中（§10.7）是**跨入口唯一**的口径。本文件曾自实现一份裸的文本相等比较
        （不走归一化），实测分歧：包内 `价格①` / 传入 `价格1`
        在 kefu 入口命中、在本入口不命中，且无任何留痕（纪律 4 的静默降级）。

    WHY 不再叠一层 assets.lookup 复验：`find_hit` 内部已对每个候选走
        `confirm()`（= `pack.lookup`，含指纹 / 音频存在性校验），未命中的候选
        本来就不会出现在 `.entry` 里。本函数因此是**已复核**的结论，不是候选。

    异常：
        ValueError: key / text 不是恰好一种（透传自 find_hit，由 call_tool 转 isError）
    """
    return find_hit(pack, key=key, text=text).entry


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
    # 空串按「没给」处理（与上面那道门同一口径），保证只往 find_hit 递一种表示
    hit = _find_entry(pack, key=key or None, text=text or None)
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
        hit = _find_entry(pack, key=key)
        if hit is None:
            misses.append(key)
        else:
            plan.append(_hit_fields(hit, pack.model_version))
    return {"plan": plan, "all_hit": not misses, "misses": misses}, False


PACK_SOURCE_HINT = (
    "这是包源目录（有 phrases.json、没有 manifest.json）；vox_pack_check 只认铸出来的资产包。\n"
    "请先跑 `sh bin/vox pack build <包源目录> --out <输出目录>`，再把 <输出目录> 传进来。"
)


def _looks_like_pack_source(root):
    """只认「有 phrases.json、没有 manifest.json」这一种形态。

    判据纯文件系统，不 import `compiler`（`adapters/AGENTS.md` §⑤）。
    路径不存在时两项皆为 False，故不存在的目录不会误判成包源目录。
    """
    return (root / "phrases.json").exists() and not (root / "manifest.json").exists()


def _pack_check(arguments):
    root = Path(arguments["pack_dir"])
    # 先转 list 再追加：validate_pack 返回 list，但就地 append 到 tuple 会炸（纪律 4：不静默）
    violations = list(validate_pack(root) or [])
    passed = not violations
    if violations and _looks_like_pack_source(root):
        # 只在**校验不过**时追加，且只追加：passed 与 isError 语义不动，原违规项也不动
        violations.append(PACK_SOURCE_HINT)
    return {
        "passed": passed,
        "keys": _manifest_keys(root),
        "violations": violations,
    }, bool(violations)


TRIGGER_HINT = "（vox_plan_from_state 只认包源目录；铸出来的资产包不含 trigger.json）"


def _plan_from_state(arguments):
    """state 快照 → plan（T86）：把 `trigger/` 这条线接出一条产品出口。

    判定全部复用 `trigger` 的公开 API，本文件不自造任何规则匹配或预算逻辑：
        load_trigger  -> 装载并校验 trigger.json（字段声明 / 兜底至多一条）
        build_plan    -> validate_state + 按 rules 顺序取**第一条**命中 + 预算强制
    失败语义由 trigger 的公开异常承担（StateError / TriggerError / BudgetError），
    经 call_tool 统一转 isError；本文件不吞、不降级。
    """
    root = Path(arguments["pack_dir"])
    # 先给「传错目录」一个点名路径的错误：资产包里没有 trigger.json
    if not (root / "trigger.json").exists():
        raise ValueError(f"该目录没有 trigger.json：{root}{TRIGGER_HINT}")
    state = arguments.get("state")
    if not isinstance(state, dict):
        raise ValueError(
            f"vox_plan_from_state 的 state 必须是对象（扁平快照），"
            f"实际类型为 {type(state).__name__}"
        )
    trig = load_trigger(root)
    built = build_plan(trig, state, turn_id=arguments.get("turn_id"))
    plan = [
        {
            "action": str(unit.action),
            "key": unit.key,
            "rate": unit.rate,
            "variant": unit.variant,
            "slots": dict(unit.slots or {}),
        }
        for unit in built.plan
    ]
    return {
        "trigger_id": trig.trigger_id,
        "rule_id": built.rule_id,
        "plan": plan,
        "keys": list(built.keys),
        "chars": built.chars,
        "budget_chars": built.budget,
        "plan_id": built.plan_id,
    }, False


_HANDLERS = {
    "vox_lookup": _lookup,
    "vox_plan": _plan,
    "vox_pack_check": _pack_check,
    "vox_plan_from_state": _plan_from_state,
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
