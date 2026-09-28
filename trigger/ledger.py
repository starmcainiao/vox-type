"""
trigger.ledger — 轮级留痕（state 结构化层 + plan 的配对，JSONL 追加写）

职责：把「这一轮的状态 + 打算说什么」按 docs/12 §12.5 定的两条通道之一（正样本）
      写进 JSONL 追加写文件，供之后做监督微调的数据快照（docs/12 §12.10）。
不负责：不做 state → plan 的判定（trigger.build_plan）、不写事件流（runtime/executor
      的事件契约在冻结区，本模块不碰）、不做命中/未命中判定、不做统计。

为什么必须只放结构化层（docs/12 §12.10 末的红线）：
  配对里「话术」是我们自己写的（不敏感），「状态快照」可能含用户信息（账号、金额、原始文本）。
  因此留痕的 state 字段**只取声明为结构化层的字段**，敏感层一律不落盘——
  结构化/敏感的分层声明在 trigger.json 里（T16 的 format 已校验），本模块**按声明取**，
  不靠字段名嗅探、不靠调用方自觉。

落盘纪律：
  - 位置由参数指定，**缺省落在仓外**（$VOX_LEDGER_DIR → ~/.vox-ledger/turns.jsonl）；
    仓库目录本身不进默认可写路径，防止私人数据入仓。
  - JSONL 追加写，一行一条记录，UTF-8，以 \n 结尾（训练脚本顺序扫，不需要随机查）。
  - 写入失败**不静默丢弃**：抛 LedgerError，消息含目标路径与失败原因
    （「留痕失败要不要阻断播报」是**调用方的决定**，本模块只负责把失败响亮地交出去）。
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import PLAN_ID, TURN_ID, TS, KEY
from trigger.format import LAYER_STRUCTURED, LAYER_SENSITIVE, Trigger
from trigger.plan import TriggerPlan


# ---------------------------------------------------------------------------
# 1. 常量（字段名一律引 core.metrics_spec，不自造字面量）
# ---------------------------------------------------------------------------
# 记录字段名：turn_id / plan_id / ts / key 全部是 core.metrics_spec 的冻结常量
TURN_ID_FIELD: str = TURN_ID
PLAN_ID_FIELD: str = PLAN_ID
TS_FIELD: str = TS
KEY_FIELD: str = KEY

# 本模块新增的留痕专有字段（事件流里没有、也不是指标口径字段，故不在 core 里）
TRIGGER_ID_FIELD: str = "trigger_id"
RULE_ID_FIELD: str = "rule_id"
STATE_FIELD: str = "state"
PLAN_FIELD: str = "plan"

# 留痕的格式版本（追加式契约；格式变更必须 +1，不许原地改字段语义）
LEDGER_VERSION: int = 1
LEDGER_VERSION_FIELD: str = "ledger_version"

# 缺省落盘位置（仓外）：$VOX_LEDGER_DIR/turns.jsonl → ~/.vox-ledger/turns.jsonl
LEDGER_ENV_VAR: str = "VOX_LEDGER_DIR"
LEDGER_DIR_NAME: str = ".vox-ledger"
LEDGER_FILE_NAME: str = "turns.jsonl"

# 留痕文件写入的超时保护（秒）：JSONL 追加写是本地文件操作，正常毫秒级；
# 这里不做超时中断（本地写不应中断），只做异常捕获。


# ---------------------------------------------------------------------------
# 2. 异常
# ---------------------------------------------------------------------------
class LedgerError(Exception):
    """留痕写入异常——所有失败统一抛出此异常。

    消息**必须含目标路径**与失败原因（docs/12 §12.10：写日志失败必须留痕，
    不得静默丢——仓库红线）。本模块只负责把失败响亮地交出去，
    是否阻断播报由调用方决定。
    """


# ---------------------------------------------------------------------------
# 3. 内部辅助
# ---------------------------------------------------------------------------
def _structured_fields(trigger: Trigger) -> Tuple[str, ...]:
    """取出声明为结构化层的字段名（顺序与 trigger.json 一致）。"""
    return tuple(name for name, decl in trigger.state_fields if decl.layer == LAYER_STRUCTURED)


def _redact_layer(trigger: Trigger) -> Tuple[str, ...]:
    """取出声明为敏感层的字段名（留痕里绝不出现其值）。"""
    return tuple(name for name, decl in trigger.state_fields if decl.layer == LAYER_SENSITIVE)


def _unit_dict(unit: Any) -> Dict[str, Any]:
    """把一个 core.protocol.PlanUnit 转成可 JSON 序列化的字典。

    只输出 key / rate / variant / slots（留痕要能还原监督配对），
    不输出 text（plan 单元不产生自由文本，text 恒为 None——不写进留痕）。
    """
    return {
        KEY_FIELD: getattr(unit, "key", None),
        "rate": getattr(unit, "rate", None),
        "variant": getattr(unit, "variant", None),
        "slots": dict(getattr(unit, "slots", None) or {}),
    }


def _build_record(trigger: Any, state: Any, turn: Any, *, turn_id: Any,
                  plan_id: Any, ts: Any) -> Dict[str, Any]:
    """构造一条留痕记录：只放结构化层 + plan，绝不放敏感层的值。"""
    structured = _structured_fields(trigger)
    redacted = _redacted_names(trigger)

    state_in = state if isinstance(state, dict) else {}
    structured_state = {name: state_in[name] for name in structured if name in state_in}

    if isinstance(turn, TriggerPlan):
        plan_units = turn.plan
        keys = list(turn.keys)
        rule_id = turn.rule_id
        trigger_id = getattr(trigger, "trigger_id", None)
    elif isinstance(turn, list):
        # 允许直接传 core 协议的 plan 列表（此时没有 rule_id，留空表示「未由触发器产出」）
        plan_units = turn
        keys = [getattr(u, "key", None) for u in turn]
        rule_id = None
        trigger_id = getattr(trigger, "trigger_id", None)
    else:
        raise LedgerError(
            f"plan 必须是 TriggerPlan 或 core 协议的 plan 列表，"
            f"实际类型为 {type(turn).__name__}"
        )

    record = {
        LEDGER_VERSION_FIELD: LEDGER_VERSION,
        TURN_ID_FIELD: turn_id,
        PLAN_ID_FIELD: plan_id,
        TS_FIELD: ts,
        TRIGGER_ID_FIELD: trigger_id,
        RULE_ID_FIELD: rule_id,
        STATE_FIELD: structured_state,
        PLAN_FIELD: [_unit_dict(u) for u in plan_units],
    }
    # 可还原监督配对：(state 结构化层, plan key 列表)
    record["keys"] = keys

    # 落盘前自检：敏感层字段名**以及其值**都不得出现在记录里
    # （值级别的检查覆盖「结构化字段恰好装了敏感值」这种错配——
    #  那类错配本该由 format.validate_state 拦下，这里再加一道）
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
    for name in redacted:
        value = state_in.get(name)
        if isinstance(value, str) and value and value in serialized:
            raise LedgerError(
                f"留痕记录里出现了敏感层字段 '{name}' 的值 "
                f"（docs/12 §12.10 的分层红线：敏感层绝不落盘；"
                f"请检查该字段的 layer 声明）"
            )
    return record


def _redacted_names(trigger: Any) -> Tuple[str, ...]:
    """敏感层字段名（见 _redact_layer，拆成独立函数只为语义清晰）。"""
    return _redact_layer(trigger)


# ---------------------------------------------------------------------------
# 4. 路径解析
# ---------------------------------------------------------------------------
def default_ledger_path() -> Path:
    """缺省的留痕路径（仓外）：$VOX_LEDGER_DIR/turns.jsonl → ~/.vox-ledger/turns.jsonl。

    仓库目录本身不进默认可写路径——留痕可能含用户的状态，默认落仓内 = 私人数据入仓。
    """
    base = os.environ.get(LEDGER_ENV_VAR)
    if not base:
        base = str(Path.home() / LEDGER_DIR_NAME)
    return Path(base) / LEDGER_FILE_NAME


def _repo_root() -> Path:
    """仓库根（本文件 → trigger → 仓库根）。"""
    return Path(__file__).resolve().parent.parent


def _resolve_path(path: Any) -> Path:
    """把调用方给的落盘位置解析成绝对路径；缺省走仓外缺省路径。"""
    if path is None:
        return default_ledger_path()
    if isinstance(path, (str, Path)):
        p = Path(path)
    else:
        raise LedgerError(f"留痕目标路径必须是字符串或 Path，实际类型为 {type(path).__name__}")
    if not p.is_absolute():
        # 相对路径按「当前工作目录」解析，不偷偷锚定到仓库根
        # （相对路径锚定到仓库根 = 私人数据悄悄入仓，与本模块的红线相反）
        p = Path.cwd() / p
    return p.resolve(strict=False)


def _inside_repo(path: Path) -> bool:
    """判断目标路径是否落在仓库目录内（用于给出明确的报错信息）。"""
    try:
        path.resolve().relative_to(_repo_root())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 5. 公开 API
# ---------------------------------------------------------------------------
def structured_record(trigger: Any, state: Any, turn: Any, *, turn_id: Any,
                      plan_id: Any, ts: Any) -> Dict[str, Any]:
    """构造一条留痕记录（不写盘）。

    与 record_turn 共享同一套过滤逻辑，但**不产生任何副作用**——
    供调用方在决定写不写之前先看一眼记录内容，也供测试直接断言。

    参数：
        trigger:  format.load_trigger 的产物（提供 layer 声明与 trigger_id）
        state:    state 快照（扁平字典；只取结构化层的字段）
        turn:     trigger.build_plan 的产物，或 core 协议的 plan 列表
        turn_id:  会话轮次 ID（字段名引 core.metrics_spec.TURN_ID）
        plan_id:  播报计划 ID（字段名引 core.metrics_spec.PLAN_ID）
        ts:       时间戳（**必须注入**，不读真实时钟——测试要能断言确定性）

    异常：
        LedgerError: plan 形状非法 / 记录里出现敏感层的值
    """
    if turn_id is None:
        raise LedgerError(f"{TURN_ID_FIELD} 不能为空（留痕必须能关联到事件流的轮次）")
    if plan_id is None:
        raise LedgerError(f"{PLAN_ID_FIELD} 不能为空（留痕必须能关联到事件流的计划）")
    if ts is None:
        raise LedgerError(
            f"{TS_FIELD} 不能为空：本模块不读真实时钟，"
            f"时间戳必须由调用方注入（否则确定性无法测试）"
        )
    return _build_record(trigger, state, turn, turn_id=turn_id, plan_id=plan_id, ts=ts)


def record_turn(trigger: Any, state: Any, turn: Any, *, turn_id: Any,
                plan_id: Any, ts: Any, path: Any = None) -> Path:
    """把一轮的 (state 结构化层, plan) 追加写进 JSONL 文件，返回文件路径。

    参数：
        trigger / state / turn / turn_id / plan_id / ts: 同 structured_record
        path:    留痕文件路径。**缺省落在仓外**（见 default_ledger_path）。
                 相对路径按当前工作目录解析；不锚定到仓库根。

    返回：
        写入的文件的绝对路径。

    异常：
        LedgerError: 路径非法 / 目录不存在或不可写 / 文件不可写 / 序列化失败 /
                     记录里出现敏感层的值 / 参数缺失。
                     消息**含目标路径与失败原因**。
                     注意：本模块**不静默丢弃**，也不自动回落到别的目录——
                     「留痕失败要不要阻断播报」由调用方决定。
    """
    record = structured_record(
        trigger, state, turn, turn_id=turn_id, plan_id=plan_id, ts=ts
    )

    target = _resolve_path(path)

    if target.is_dir():
        raise LedgerError(
            f"留痕写入失败: {target} 是一个目录（留痕必须是文件，JSONL 追加写）"
        )

    try:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        data = line.encode("utf-8")

        # 追加写：不截断已有内容（JSONL 的正当形态，见 docs/12 §12.10）
        parent = target.parent
        if not parent.exists():
            # 缺父目录**不自动创建**：自动建目录会让「路径写错」变成静默落盘，
            # 正是本模块要防的静默降级。调用方应显式给出一个存在的目录。
            raise LedgerError(
                f"留痕写入失败: {target} 的父目录不存在（{parent}）"
                f"——本模块不自动创建目录（避免路径写错被静默掩盖）"
            )
        with open(target, "ab", buffering=0) as fh:
            fh.write(data)
    except LedgerError:
        raise
    except (OSError, IOError, UnicodeError, ValueError) as e:
        raise LedgerError(
            f"留痕写入失败: {target} —— {type(e).__name__}: {e}"
        ) from e

    return target


def read_turns(path: Any = None) -> Tuple[Dict[str, Any], ...]:
    """读回一个留痕文件的全部记录（顺序保留），供离线导出训练数据用。

    这是「可还原监督配对」的读侧：从一条记录能取出
    (record['state'], record['keys']) 这一对，且 state 只含结构化层。
    本函数不校验格式（留痕是本地日志，不是契约产物），只负责如实读回。
    """
    target = _resolve_path(path)
    if not target.exists():
        return tuple()

    records: List[Dict[str, Any]] = []
    try:
        with open(target, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise LedgerError(
                        f"留痕文件 {target} 含无法解析的行（{e}）——"
                        f"按 fail-closed 抛错，不做静默跳行"
                    ) from e
    except (OSError, IOError, UnicodeError) as e:
        raise LedgerError(f"留痕文件读取失败: {target} —— {type(e).__name__}: {e}") from e
    return tuple(records)


def supervision_pairs(record: Any) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    """从一条留痕记录里取出监督配对 (state 结构化层, plan key 列表)。

    这是留痕作为训练数据的**全部意义**（docs/12 §12.5 的正样本通道）：
    状态 → 该说什么。key 列表按 plan 里出现的顺序，便于逐 unit 对齐。
    """
    if not isinstance(record, dict):
        raise LedgerError(
            f"留痕记录必须是字典，实际类型为 {type(record).__name__}"
        )
    state = record.get(STATE_FIELD)
    if not isinstance(state, dict):
        raise LedgerError(f"留痕记录缺少 {STATE_FIELD} 字段（必须是字典）")
    keys_raw = record.get("keys")
    if not isinstance(keys_raw, list):
        raise LedgerError(f"留痕记录缺少 'keys' 字段（必须是列表）")
    return state, tuple(keys_raw)
