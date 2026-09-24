"""
trigger.reachability — 前置包 key / state 字段可达性检查（报告类 API）

职责：对一份**已装载**的 Trigger 做静态盘点，找出两类「声明了但触发器用不到」的
      死重量：
        1. phrases.json 里的话术 key 没有被任何规则的 units 引用；
        2. 已声明的 state 字段既不在任何 `when` 里、也不在任何单元的 `slots` 里。
      本模块只负责盘点并返回清单。
不负责：不做装载期格式校验（format.load_trigger 已负责）、不做运行时规则匹配
      （trigger.build_plan 已负责）、不写盘、不发网络请求、不改内核、不做音频。

语义裁定（T16d 卡定，执行方不得自行升级）：
  本检查**只报告、不报错**——`check_reachability` 永远返回一份
  ReachabilityReport，**不抛业务异常**，由调用方决定怎么处置。
  理由：双形态包（trigger.json + script.json 并存）里「某个 key 只被 script.json
  用到」是**合法形态**（demo 包的 `offer_help` / `ticket_status` 就是这种），
  把它当报错会误伤合法包。AGENTS.md 红线 4 的 fail-closed 管的是**执行路径的
  静默降级**（本该命中却换了路径 → 必须报错或留痕），不是资产清单的盘点报告。
  因此本模块刻意不抛错：**收紧成报错属越权**，需另走大版本与迁移期。

判定口径（T16d 卡定，不得自行放宽或收紧）：
  unreferenced_keys        = trigger.phrase_keys
                             − 所有规则所有单元的 key
  unreferenced_state_fields = 已声明字段名
                             − (所有 `when`（非 null）里的字段名 ∪ 所有单元 slots 里的名字)
  两个字段均为**排序后的元组**（确定性，可比较）。
  「被引用」的判定刻意宽于 `when`：只出现在 slots 也算被引用（demo 包的
  `overdue_days` 无任何 `when` 依赖，但被 `overdue` 规则的 slots 引用），
  只出现在 `when` 也算被引用——两种都参与触发或拼接，不是死重量。

与同层其他模块的关系：本模块是**独立的报告 API**，不被 format.py / plan.py
      调用，也不调用它们的私有符号；两者保持零改动（本卡 T16d 的白名单要求）。

边界声明：本模块**只盘点不判定业务合法性**——它不知道 script.json 存不存在，
  不知道某个字段是不是「本来就不该被判定」。清单是事实陈述，处置权在调用方。
"""

from dataclasses import dataclass
from typing import FrozenSet, Tuple

from trigger.format import Trigger


# ---------------------------------------------------------------------------
# 1. 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReachabilityReport:
    """一份可达性盘点结果（不可变，可比较，便于断言与留痕）。

    属性：
        unreferenced_keys:          phrases.json 声明了、但没有任何规则的 units
                                    引用到的话术 key（排序后的元组；空 = 全引用）
        unreferenced_state_fields:  已声明、但既不在任何 `when` 里也不在任何单元
                                    `slots` 里的 state 字段名（排序后的元组；空 = 全引用）

    两个字段都是元组而非列表：frozen dataclass 的字段若是列表则不可哈希、
    也无法逐字段比较，元组保证「同一 Trigger → 同一报告」可直接 == 断言。
    """

    unreferenced_keys: Tuple[str, ...]
    unreferenced_state_fields: Tuple[str, ...]


# ---------------------------------------------------------------------------
# 2. 内部辅助（集合提取，纯静态读取，不做任何校验）
# ---------------------------------------------------------------------------
def _referenced_keys(trigger: Trigger) -> FrozenSet[str]:
    """收集所有规则的所有单元引用到的话术 key。

    只读 trigger.rules 的单元 key；不校验 key 是否 ∈ phrase_keys
    （反方向的 key_not_in_library 校验是 format.load_trigger 的装载期职责，
    这里只盘点，不重复也不替代那道闸门）。
    """
    keys: set = set()
    for rule in trigger.rules:
        for unit in rule.units:
            keys.add(unit.key)
    return frozenset(keys)


def _referenced_fields(trigger: Trigger) -> FrozenSet[str]:
    """收集所有规则「被引用」的 state 字段名 = when 字段名 ∪ 单元 slots 名。

    `when` 为 None 的兜底规则没有字段可取（它无字段条件），跳过即可——
    它不产生任何字段引用。只读公开结构（TriggerRule.when / PlanUnit.slots），
    不解析 when 的比较符字典内部形状。
    """
    fields: set = set()
    for rule in trigger.rules:
        when = rule.when
        if when is not None:
            fields.update(when.keys())
        for unit in rule.units:
            fields.update(unit.slots)
    return frozenset(fields)


# ---------------------------------------------------------------------------
# 3. 公开 API
# ---------------------------------------------------------------------------
def check_reachability(trigger: Trigger) -> ReachabilityReport:
    """对一份已装载的 Trigger 做 key / state 字段可达性盘点，返回报告。

    职责：找出 phrases.json 里没有任何规则引用的 key，以及既不在任何 `when`
          也不在任何单元 `slots` 的已声明 state 字段。只做静态盘点。
    参数：
        trigger: format.load_trigger 的产物（不可变 Trigger）。输入即已装载的
                 Trigger，不需要包目录——phrase_keys / state_fields / rules
                 三个属性已足够，因此本函数不读盘、不 import compiler。
    返回值：
        ReachabilityReport —— 两个**排序后**的字符串元组（确定性）。
        空元组表示该类全引用；非空元组是死重量清单，不含任何严重度或处置建议。
    边界：
        报告语义——含未引用 key / 未引用字段的 Trigger 一样返回报告，
        **不抛业务异常、不写盘、不做 I/O**。调用方不得把本返回值当校验闸门
        用（那是 format/trigger 的活）；调用方也不得期望本函数自行升级成报错。
    调用方：包作者、审核工具、评测脚本——拿到清单后自行决定删 key、补规则、
        还是留着给同包的 script.json 用（双形态包合法形态）。
    """
    # 1. 话术 key：声明集合减引用集合（差集无序，排序输出保证确定性）
    unreferenced_keys = tuple(sorted(set(trigger.phrase_keys) - _referenced_keys(trigger)))

    # 2. state 字段：已声明字段名减引用集合。
    #    state_fields 是 (字段名, 声明) 的元组序列，只取字段名一侧。
    declared = tuple(name for name, _decl in trigger.state_fields)
    unreferenced_state_fields = tuple(sorted(set(declared) - _referenced_fields(trigger)))

    return ReachabilityReport(
        unreferenced_keys=unreferenced_keys,
        unreferenced_state_fields=unreferenced_state_fields,
    )
