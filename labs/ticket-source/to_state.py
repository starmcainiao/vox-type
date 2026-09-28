"""
labs/ticket-source/to_state.py — 工单系统导出 → state 快照

职责：读「工单系统导出」形态的源 JSON，按 source_map.json 里声明的映射与
      派生规则，产出一份 state 快照；产出的 state 必须通过 T16 的
      trigger.validate_state（复用，不复制校验逻辑）。
不负责：不做 state → plan（trigger.build_plan）、不写留痕
      （trigger.record_turn）、不写任何文件（落盘归 run_e2e.py）、
      不联网、不启停服务、不改内核。

T21 的硬要求怎么落在这里：
  「映射是数据不是代码」——源字段 → state 字段、派生字段的计算规则全部在
      source_map.json 里声明；本文件只做通用执行。代码里**不出现**任何具体
      源字段名或 state 字段名的字面量映射（把 source_map.json 换一个不同的包
      映射表，本文件不改一行也能跑）。
  「未映射的源字段 → 报错」——源条目里出现 source_document.item_fields 之外的
      字段名 → SourceMapError（对齐 compiler/source.py 对 typo 的态度，不静默丢弃）。
  「敏感字段绝不进 state 结构化层」——sensitive_fields 里 handling=excluded 的字段
      不写入 state；handling=state_sensitive_layer 的字段写入 state 后必须由 T16 的
      validate_state 复核其 layer 声明（声明成 structured 会被 T16 拦下，本文件不
      自行给 layer 下结论）。
  「不依赖真实时钟」——as_of 未注入（缺 date / time / tz 任一）→ SourceMapError。
      本文件不 import datetime 的 now/today/utcnow。
  「fail-closed」——任何一步失败即抛 SourceMapError，绝不返回半个 state。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from trigger import StateError, Trigger, validate_state

HERE: Path = Path(__file__).resolve().parent
DEFAULT_SOURCE_MAP: str = "source_map.json"


class SourceMapError(Exception):
    """源数据 / 映射表不合法。

    消息必须含导致失败的具体值（字段名、条目序号、非法值），便于定位到
    tickets.source.json 或 source_map.json 的哪一行；不允许吞掉上下文。
    """


# ---------------------------------------------------------------------------
# 1. 读文件
# ---------------------------------------------------------------------------
def _read_json(path: Path, label: str) -> Dict[str, Any]:
    if not path.exists():
        raise SourceMapError(f"缺少{label}: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except UnicodeDecodeError as exc:
        raise SourceMapError(f"{label} 编码错误（非 UTF-8）: {path} ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise SourceMapError(f"{label} 不是合法 JSON: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise SourceMapError(
            f"{label} 顶层必须是 JSON 对象，实际类型为 {type(data).__name__}: {path}"
        )
    return data


def _is_int(value: Any) -> bool:
    """严格整数：bool 是 int 的子类，但 true/false 不是合法整数。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _coerce(value: Any, field: str, mapping: Dict[str, Any], ctx: str) -> Any:
    """按映射表声明的 type 做一次类型收敛；收敛不了就报错（不静默转型）。

    只校验类型，不校验 enum / min / max——那些声明的唯一来源是 packs/<包>/trigger.json，
    由 T16 的 validate_state 统一复核；这里再校验一遍就是两份口径，是静默降级的温床。
    """
    typ = mapping.get("type")
    if not isinstance(typ, str) or typ not in ("str", "int", "bool"):
        raise SourceMapError(
            f"{ctx} 的 state 字段 '{field}' 的映射 type='{typ!r}' 非法"
            f"（仅允许 str / int / bool，结构化层的封闭集合）"
        )
    if typ == "str":
        if value is None:
            raise SourceMapError(f"{ctx} 的 state 字段 '{field}' 不能为空值（映射未声明 nullable=true）")
        if not isinstance(value, str):
            raise SourceMapError(
                f"{ctx} 的 state 字段 '{field}' 声明 type='str'，实际类型为 {type(value).__name__}（值 {value!r}）"
            )
        return value
    if typ == "int":
        if not _is_int(value):
            raise SourceMapError(
                f"{ctx} 的 state 字段 '{field}' 声明 type='int'，实际值为 {value!r}"
                f"（bool 不是合法整数）"
            )
        return value
    if not isinstance(value, bool):
        raise SourceMapError(
            f"{ctx} 的 state 字段 '{field}' 声明 type='bool'，实际值为 {value!r}"
            f"（bool 必须是 true/false）"
        )
    return value


# ---------------------------------------------------------------------------
# 2. 「今天」的注入（不读系统时钟）
# ---------------------------------------------------------------------------
def parse_as_of(as_of: Any, ctx: str = "source_map.json") -> Tuple[date, str, str, str]:
    """解析注入的「今天」，返回 (date 对象, 日期串, 时间串, tz 串)。

    缺任一项即报错——未注入就静默用系统时钟，会让结果不确定、也无法测试。
    """
    if not isinstance(as_of, dict):
        raise SourceMapError(
            f"{ctx} 的 as_of 必须是字典（{{date, time, tz}}），"
            f"实际类型为 {type(as_of).__name__}"
        )
    date_s = as_of.get("date")
    time_s = as_of.get("time")
    tz_s = as_of.get("tz")
    missing = [k for k, v in (("date", date_s), ("time", time_s), ("tz", tz_s)) if not isinstance(v, str) or not v]
    if missing:
        raise SourceMapError(
            f"{ctx} 的 as_of 未注入（缺 {missing}）："
            f"派生字段依赖「今天」，必须由调用方显式注入，不读系统时钟"
        )
    try:
        as_date = date.fromisoformat(date_s)
    except ValueError:
        raise SourceMapError(f"{ctx} 的 as_of.date 不是 ISO 日期（YYYY-MM-DD）: {date_s!r}")
    if not isinstance(time_s, str) or ":" not in time_s:
        raise SourceMapError(f"{ctx} 的 as_of.time 不是 HH:MM:SS: {time_s!r}")
    return as_date, date_s, time_s, tz_s


def _parse_due_at(value: Any, field: str, tz: str, ctx: str) -> date:
    """解析源里的到期时间为日期；只取日期部分参与自然日计算。"""
    if not isinstance(value, str) or not value:
        raise SourceMapError(f"{ctx} 的字段 '{field}' 必须是 ISO 时间字符串，实际值为 {value!r}")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise SourceMapError(
            f"{ctx} 的字段 '{field}' 不是合法 ISO 时间: {value!r}"
        ) from None
    if dt.tzinfo is None:
        raise SourceMapError(
            f"{ctx} 的字段 '{field}' 缺时区（{value!r}）："
            f"无法与注入的 as_of.tz={tz!r} 对齐，不猜时区"
        )
    if dt.utcoffset() is None:
        raise SourceMapError(f"{ctx} 的字段 '{field}' 时区无法解析: {value!r}")
    return dt.date()


def _parse_day_unit(unit: Any, ctx: str) -> timedelta:
    if not isinstance(unit, str) or not unit:
        raise SourceMapError(f"{ctx} 的 day_unit 缺失（自然日单位必须显式声明），实际值为 {unit!r}")
    if unit in ("P1D", "P01D", "day", "days"):
        return timedelta(days=1)
    raise SourceMapError(
        f"{ctx} 的 day_unit='{unit}' 本实验只支持 P1D（自然日）"
    )


# ---------------------------------------------------------------------------
# 3. 派生运算（op 全在 source_map.json 里声明，这里是通用执行器）
# ---------------------------------------------------------------------------
def _evaluate_compute(
    op: str,
    args: Any,
    item: Dict[str, Any],
    derived: Dict[str, Any],
    as_of: date,
    timezone: str,
    day_unit: timedelta,
    field: str,
    ctx: str,
) -> Any:
    """按 source_map.json 里声明的 op 执行一个派生/投影计算。"""
    if op == "truthy_nonempty_string":
        src = item.get(args)
        if not isinstance(src, str):
            raise SourceMapError(
                f"{ctx} 的 state 字段 '{field}'：op=truthy_nonempty_string 的输入字段 "
                f"'{args}' 实际类型为 {type(src).__name__}（值 {src!r}）"
            )
        return src.strip() != ""

    if op == "calendar_days_until":
        if as_of is None:
            raise SourceMapError(f"{ctx} 的 state 字段 '{field}'：op=calendar_days_until 依赖「今天」但未注入 as_of")
        if not _is_int(day_unit.days) or day_unit.days <= 0:
            raise SourceMapError(f"{ctx} 的 state 字段 '{field}'：day_unit 必须是正的自然日数")
        src_field = args.get("input") if isinstance(args, dict) else None
        if not isinstance(src_field, str):
            raise SourceMapError(
                f"{ctx} 的 state 字段 '{field}'：op=calendar_days_until 的 input 必须是源字段名，"
                f"实际为 {args!r}"
            )
        due_date = _parse_due_at(item.get(src_field), src_field, timezone, ctx)
        delta = due_date - as_of
        return delta.days // day_unit.days

    if op == "max":
        if not isinstance(args, list) or len(args) == 0:
            raise SourceMapError(f"{ctx} 的 state 字段 '{field}'：op=max 的 args 必须是非空列表，实际为 {args!r}")
        values: List[Any] = []
        for i, arg in enumerate(args):
            if _is_int(arg):
                values.append(arg)
                continue
            if isinstance(arg, dict):
                ref = arg.get("ref")
                if not isinstance(ref, str) or ref not in derived:
                    raise SourceMapError(
                        f"{ctx} 的 state 字段 '{field}'：op=max 的 args[{i}] 引用了不存在的派生值 "
                        f"'{ref}'（已算出 {sorted(derived)}）"
                    )
                val = derived[ref]
                if arg.get("negate"):
                    if _is_int(val):
                        val = -val
                    else:
                        raise SourceMapError(
                            f"{ctx} 的 state 字段 '{field}'：args[{i}].negate 只能作用于整数，实际为 {val!r}"
                        )
                if not _is_int(val):
                    raise SourceMapError(
                        f"{ctx} 的 state 字段 '{field}'：op=max 的 args[{i}] 不是整数，实际为 {val!r}"
                    )
                values.append(val)
                continue
            raise SourceMapError(
                f"{ctx} 的 state 字段 '{field}'：op=max 的 args[{i}] 既不是整数也不是 {{ref}} 表达式，实际为 {arg!r}"
            )
        return max(values)

    if op == "derived_from":
        if not isinstance(args, dict):
            raise SourceMapError(f"{ctx} 的 state 字段 '{field}'：op=derived_from 的 args 必须是字典，实际为 {args!r}")
        ref = args.get("field")
        if not isinstance(ref, str) or ref not in derived:
            raise SourceMapError(
                f"{ctx} 的 state 字段 '{field}'：op=derived_from 引用了不存在的派生值 '{ref}'"
                f"（已算出 {sorted(derived)}）"
            )
        lhs = derived[ref]
        if not _is_int(lhs):
            raise SourceMapError(
                f"{ctx} 的 state 字段 '{field}'：op=derived_from 的 '{ref}' 必须是整数，实际为 {lhs!r}"
            )
        compare = args.get("compare")
        rhs = args.get("value")
        if compare not in ("lt", "le", "eq", "ne", "ge", "gt"):
            raise SourceMapError(f"{ctx} 的 state 字段 '{field}'：op=derived_from 的 compare='{compare!r}' 非法")
        if not _is_int(rhs):
            raise SourceMapError(f"{ctx} 的 state 字段 '{field}'：op=derived_from 的 value={rhs!r} 必须是整数")
        result = {
            "lt": lhs < rhs, "le": lhs <= rhs, "eq": lhs == rhs,
            "ne": lhs != rhs, "ge": lhs >= rhs, "gt": lhs > rhs,
        }[compare]
        return bool(result)

    if op == "format_as_of":
        if not isinstance(args, dict):
            raise SourceMapError(f"{ctx} 的 derived 字段 '{field}'：op=format_as_of 的 args 必须是字典，实际为 {args!r}")
        tz_ref = args.get("tz")
        if not isinstance(tz_ref, str) or not tz_ref:
            raise SourceMapError(f"{ctx} 的 derived 字段 '{field}'：op=format_as_of 缺 tz 引用")
        if "timezone" not in derived or not isinstance(derived["timezone"], str):
            raise SourceMapError(f"{ctx} 的 derived 字段 '{field}'：缺已算出的 timezone 派生值")
        if not derived.get("as_of_date") or not derived.get("as_of_time"):
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：op=format_as_of 依赖注入的 as_of，但 as_of 未注入"
            )
        return f"{derived['as_of_date']}T{derived['as_of_time']}{derived['timezone']}"

    if op == "sla_breached":
        if not isinstance(args, dict):
            raise SourceMapError(f"{ctx} 的 derived 字段 '{field}'：op=sla_breached 的 args 必须是字典，实际为 {args!r}")
        if as_of is None:
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：op=sla_breached 依赖「今天」但未注入 as_of"
            )
        sla_field = args.get("sla_field")
        due_field = args.get("due_field")
        if not isinstance(sla_field, str) or not isinstance(due_field, str):
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：op=sla_breached 必须声明 sla_field 与 due_field，"
                f"实际为 {args!r}"
            )
        sla = item.get(sla_field)
        if not _is_int(sla):
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：源字段 '{sla_field}'={sla!r} 不是整数小时"
            )
        opened = item.get("opened_at")
        if not isinstance(opened, str):
            raise SourceMapError(f"{ctx} 的 derived 字段 '{field}'：源缺 opened_at（算 SLA 需要开工时间），实际为 {opened!r}")
        try:
            opened_dt = datetime.fromisoformat(opened)
            due_dt = datetime.fromisoformat(item.get(due_field))
        except (TypeError, ValueError):
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：opened_at={opened!r} / {due_field}={item.get(due_field)!r} "
                f"不是合法 ISO 时间，算不出 SLA 是否超期"
            ) from None
        if opened_dt.tzinfo is None or due_dt.tzinfo is None:
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：opened_at 或 {due_field} 缺时区，"
                f"无法与 as_of.tz 对齐（不猜时区）"
            )
        if (due_dt - opened_dt).total_seconds() <= 0:
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：{due_field} 早于或等于 opened_at，SLA 无意义"
            )
        elapsed_hours = (due_dt - opened_dt).total_seconds() / 3600.0
        return bool(elapsed_hours > sla)

    if op == "days_between":
        if not isinstance(args, dict):
            raise SourceMapError(f"{ctx} 的 derived 字段 '{field}'：op=days_between 的 args 必须是字典，实际为 {args!r}")
        if as_of is None:
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：op=days_between 依赖「今天」但未注入 as_of"
            )
        if not _is_int(day_unit.days) or day_unit.days <= 0:
            raise SourceMapError(f"{ctx} 的 derived 字段 '{field}'：day_unit 必须是正的自然日数")
        src_field = args.get("from_field")
        if not isinstance(src_field, str):
            raise SourceMapError(
                f"{ctx} 的 derived 字段 '{field}'：op=days_between 的 from_field 必须是源字段名，实际为 {src_field!r}"
            )
        if src_field not in item:
            raise SourceMapError(f"{ctx} 的 derived 字段 '{field}'：源缺字段 '{src_field}'")
        src_date = _parse_due_at(item.get(src_field), src_field, timezone, ctx)
        return (as_of - src_date).days // day_unit.days

    raise SourceMapError(
        f"{ctx} 的 state 字段 '{field}'：未知的计算 op='{op}'（不是通用执行器支持的运算）"
    )


# ---------------------------------------------------------------------------
# 4. 主入口
# ---------------------------------------------------------------------------
def to_state(
    source_doc: Dict[str, Any],
    mapping_doc: Dict[str, Any],
    *,
    trigger: Trigger,
    tz: Optional[str] = None,
    day_unit: Optional[str] = None,
    as_of_override: Any = None,
) -> Dict[str, Any]:
    """把「工单系统导出」的一份条目转成 state 快照，并用 T16 的 validate_state 复核。

    参数：
        source_doc:      单条工单（字典）
        mapping_doc:     source_map.json 的内容
        trigger:         trigger.load_trigger 的产物（提供 layer 声明）
        tz:              时区（缺省取映射表的 timezone；**不从系统推断**）
        day_unit:        自然日单位（缺省取映射表的 day_unit）
        as_of_override:  「今天」的注入覆盖：None = 用映射表的 as_of；
                         dict = 用它覆盖映射表的 as_of（演示注入生效）；
                         字符串 'unset' = 强制当作未注入（演示 fail-closed）

    返回：
        一份通过 validate_state 的扁平 state 字典。

    异常：
        SourceMapError: 映射表 / 源条目不合法（缺字段、多余未映射字段、类型不符、
                        派生算不出、as_of 未注入）
        StateError:     产出的 state 不符合 T16 声明的字段表（layer 声明错配在这里被拦下）
    """
    ctx = f"源条目（ticket 条目 #{source_doc.get('_item_index', '?')}）"

    doc_cfg = mapping_doc.get("source_document")
    if not isinstance(doc_cfg, dict):
        raise SourceMapError("source_map.json 缺 source_document 配置")
    item_fields = doc_cfg.get("item_fields")
    if not isinstance(item_fields, list) or not item_fields:
        raise SourceMapError("source_map.json 的 source_document.item_fields 必须是非空列表")

    # 未映射的多余字段 → 报错（不静默丢弃；对齐 compiler/source.py 对 typo 的态度）
    unknown = sorted(set(source_doc.keys()) - set(item_fields) - {"_item_index"})
    if unknown:
        raise SourceMapError(
            f"{ctx} 含未映射的多余字段: {unknown}"
            f"（source_map.json 的 item_fields 已声明 {item_fields}；"
            f"多出来的字段必须显式声明为映射目标或 sensitive_fields 排除项，不得静默丢弃）"
        )

    # 「今天」的注入（缺省即报错，不读系统时钟）
    as_of_cfg = mapping_doc.get("as_of")
    if as_of_override == "unset":
        raise SourceMapError(
            f"{ctx} 依赖「今天」但 as_of 未注入（as_of_override='unset'）："
            f"派生字段必须由调用方显式注入，不读系统时钟（否则结果不确定、无法测试）"
        )
    if isinstance(as_of_override, dict):
        as_of_cfg = as_of_override
    if not isinstance(as_of_cfg, dict):
        raise SourceMapError(
            "source_map.json 缺 as_of：派生字段依赖「今天」，必须由映射表注入；"
            "不读系统时钟（否则结果不确定、无法测试）"
        )
    as_date, as_date_s, as_time_s, as_tz_s = parse_as_of(as_of_cfg, "source_map.json")

    resolved_tz = tz if tz is not None else mapping_doc.get("timezone")
    if not isinstance(resolved_tz, str) or not resolved_tz:
        raise SourceMapError("source_map.json 缺 timezone（源时间必须带时区才能与 as_of 对齐）")

    unit_cfg = day_unit if day_unit is not None else mapping_doc.get("day_unit")
    unit = _parse_day_unit(unit_cfg, "source_map.json")

    # 排除项：声明为不进 state 的敏感字段（不写进 state；validate_state 也不会看到它）
    sensitive_cfg = mapping_doc.get("sensitive_fields")
    if not isinstance(sensitive_cfg, dict):
        raise SourceMapError("source_map.json 缺 sensitive_fields 配置")
    excluded = {
        name for name, decl in sensitive_cfg.items()
        if isinstance(decl, dict) and decl.get("handling") == "excluded"
    }

    field_cfgs = mapping_doc.get("state_fields")
    if not isinstance(field_cfgs, dict) or not field_cfgs:
        raise SourceMapError("source_map.json 的 state_fields 必须是非空字典")

    state: Dict[str, Any] = {}
    derived: Dict[str, Any] = {"as_of_date": as_date_s, "as_of_time": as_time_s}

    for target, cfg in field_cfgs.items():
        if not isinstance(cfg, dict):
            raise SourceMapError(f"source_map.json 的 state_fields['{target}'] 必须是字典")
        nullable = bool(cfg.get("nullable", False))
        compute = cfg.get("compute")

        if compute is not None:
            if not isinstance(compute, dict) or not isinstance(compute.get("op"), str):
                raise SourceMapError(
                    f"source_map.json 的 state_fields['{target}'].compute 必须是含 op 的字典，实际为 {compute!r}"
                )
            value = _compute_field_value(target, cfg, source_doc, derived, as_date, resolved_tz, unit, ctx)
        else:
            src = cfg.get("from")
            if not isinstance(src, str) or not src:
                raise SourceMapError(
                    f"source_map.json 的 state_fields['{target}'] 必须声明 from（源字段名）或 compute（派生规则）"
                )
            if src in excluded:
                raise SourceMapError(
                    f"source_map.json 的 state_fields['{target}'] 映射自被排除的敏感字段 '{src}'"
                    f"（该字段已在 sensitive_fields 声明为 excluded，不得进 state）"
                )
            value = source_doc.get(src)
            if value is None:
                if nullable:
                    continue
                raise SourceMapError(
                    f"{ctx} 缺必需字段 '{src}'（映射到 state 字段 '{target}'，"
                    f"source_map.json 未声明 nullable=true）"
                )
            if src not in item_fields:
                raise SourceMapError(
                    f"source_map.json 的 state_fields['{target}'] 映射自未声明的源字段 '{src}'"
                    f"（不在 item_fields 内）"
                )

        state[target] = _coerce(value, target, cfg, ctx)

        # 可复用的派生值（如 days_left）登记进 derived，供后续 state 字段引用
        # （source_map.json 里 overdue_days 引用 days_left 就是走这条，而不是重算一次）
        derived[target] = state[target]

    # 复用 T16 的校验器：state 必须完全符合 packs/demo-brief/trigger.json 的声明
    # （layer / type / enum / min / max 都在这里复核；本文件不复制任何校验逻辑）
    validate_state(state, trigger)
    return state


def _compute_field_value(
    target: str,
    cfg: Dict[str, Any],
    item: Dict[str, Any],
    derived: Dict[str, Any],
    as_of: date,
    timezone: str,
    day_unit: timedelta,
    ctx: str,
) -> Any:
    """执行一个派生 state 字段的 compute 规则（op 与参数全在 source_map.json 里）。"""
    compute = cfg["compute"]
    op = compute["op"]
    args = compute.get("args")
    if args is None and op != "max":
        args = compute
    if op == "truthy_nonempty_string":
        args = cfg.get("from")
    return _evaluate_compute(op, args, item, derived, as_of, timezone, day_unit, target, ctx)


def to_derived_fields(
    item: Dict[str, Any],
    mapping_doc: Dict[str, Any],
    state: Dict[str, Any],
    *,
    as_of_override: Any = None,
) -> Dict[str, Any]:
    """按映射表算出**不进 state** 的派生字段（source_ref / as_of_iso / days_since_opened 等）。

    as_of_override 与 to_state 同一语义：None = 用映射表的 as_of；dict = 覆盖；
    'unset' = 强制当作未注入（fail-closed）。
    """
    ctx = "source_map.json 的 derived_fields"
    cfg = mapping_doc.get("derived_fields")
    if not isinstance(cfg, dict):
        raise SourceMapError("source_map.json 缺 derived_fields 配置")

    as_of_cfg = mapping_doc.get("as_of")
    if as_of_override == "unset":
        raise SourceMapError(
            f"{ctx} 依赖「今天」但 as_of 未注入（as_of_override='unset'）："
            f"必须由调用方显式注入，不读系统时钟"
        )
    if isinstance(as_of_override, dict):
        as_of_cfg = as_of_override
    as_date, as_date_s, as_time_s, as_tz_s = parse_as_of(as_of_cfg, "source_map.json")
    resolved_tz = mapping_doc.get("timezone")
    if not isinstance(resolved_tz, str) or not resolved_tz:
        raise SourceMapError("source_map.json 缺 timezone")
    unit = _parse_day_unit(mapping_doc.get("day_unit"), "source_map.json")

    # 先把 state 里的派生值也放进去，供 ref 引用（days_left / overdue_days / is_overdue）
    derived: Dict[str, Any] = {"as_of_date": as_date_s, "as_of_time": as_time_s, "timezone": resolved_tz}
    for key in ("days_left", "is_overdue", "overdue_days"):
        if key in state:
            derived[key] = state[key]

    out: Dict[str, Any] = {}
    for name, spec in cfg.items():
        if not isinstance(spec, dict):
            raise SourceMapError(f"{ctx}['{name}'] 必须是字典")
        compute = spec.get("compute")
        if compute is not None:
            out[name] = _evaluate_compute(
                compute["op"], compute.get("args", compute), item, derived,
                as_date, resolved_tz, unit, name, ctx,
            )
        else:
            src = spec.get("from")
            if not isinstance(src, str):
                raise SourceMapError(f"{ctx}['{name}'] 必须声明 compute 或 from")
            if src not in item:
                raise SourceMapError(f"{ctx}['{name}'] 引用了不存在的源字段 '{src}'")
            out[name] = item[src]
    return out


def load_source_tickets(path: Union[str, Path]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """读工单导出文件，返回 (条目列表, fixture 元信息)。"""
    doc = _read_json(Path(path), "源数据文件")
    list_path = doc.get("tickets")
    if not isinstance(list_path, list):
        raise SourceMapError("源数据文件缺 'tickets' 列表")
    if len(list_path) == 0:
        raise SourceMapError("源数据文件的 tickets 为空列表（无条目可处理）")
    tickets: List[Dict[str, Any]] = []
    for i, ticket in enumerate(list_path, start=1):
        if not isinstance(ticket, dict):
            raise SourceMapError(f"源数据文件的 tickets[{i}] 必须是字典，实际类型为 {type(ticket).__name__}")
        enriched = dict(ticket)
        enriched["_item_index"] = i
        tickets.append(enriched)
    return tickets, doc.get("_fixture_meta", {})


def run_mapping(
    *,
    tickets_path: Union[str, Path],
    mapping_path: Union[str, Path],
    trigger: Trigger,
    tz: Optional[str] = None,
    day_unit: Optional[str] = None,
) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]], Dict[str, Any]]:
    """端到端的映射：读源 → 逐条 to_state（含 T16 校验）→ 派生字段。返回 (结果列表, fixture 元信息)。"""
    tickets, meta = load_source_tickets(tickets_path)
    mapping_doc = _read_json(Path(mapping_path), "映射表文件")
    out: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    for item in tickets:
        state = to_state(item, mapping_doc, trigger=trigger, tz=tz, day_unit=day_unit)
        derived = to_derived_fields(item, mapping_doc, state)
        out.append((item, state, derived))
    return out, meta
