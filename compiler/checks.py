"""
compiler.checks — 五项机器判据（docs/07 §7.3：四属性 + 降级留痕）

职责：把剧本源格式的可判定部分变成机器判据——C1 有出口 / C2 追问有上限 /
      C3 全 key 可达且都被预铸 / C4 不夹带未审核文本 / C5 降级留痕。
      判定顺序固定 C1 → C2 → C3 → C4 → C5，先结构后引用，避免同一问题报两次。
不负责：不做语义分析（句意/意图/「这句是不是追问」）、不读文件系统扫目录、
      不做运行时命中判定（runtime/）。

设计红线（docs/07 §7.3 / §7.4，照抄不自创）：
  1. **`skipped` 不等于通过**。WHY：`pack=None` 时 C3c（已预铸）没有判定输入，
     既不能判过也不能判不过——只能显式记进 `CheckResult.skipped`，让调用方（未来的
     报告层）据此标 `incomplete`。若默认判过，「未预铸的 key」会被当成合规剧本放行，
     正是仓库红线里「静默降级」的原型。
  2. **不做语义启发式**。WHY：「这句是不是追问」「换着 key 追问的累计次数」「跨轮
     死锁」「是否真转人工」四条属 docs/07 §7.4 登记的人工审查项。为它们写按问号/
     标点/关键词猜语义的代码 = 把人肉评审伪装成机器证明，既不可证伪也不可复核。
     所以本模块只用「单元结构 + key 集合 + 声明值」判定，没有正则、没有标点判断。
  3. C3a（可达）的违规**归 C1 报**：终态之后的单元既是吸收态也是孤立枝，由 C1b
     报一次 `orphan_branch`，C3 不重复报同一批单元。
  4. 判据码一字不改：code 名照 docs/07 §7.3 的表，message 必须含判据名 + 实际值
     + 上限/期望值。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from compiler.script import Script, resolve_action


# ---------------------------------------------------------------------------
# 1. 常量
# ---------------------------------------------------------------------------
# R-3 硬规则：追问最多 3 次（剧本 max_retry 声明值超过它 → C2b）
MAX_RETRY_RULE: int = 3

# 判据码 → 判据名（用于 message 里的人类可读前缀）
CRITERION_NAMES: Dict[str, str] = {
    "no_exit": "C1a 有出口",
    "orphan_branch": "C1b/C3a 无孤立枝",
    "retry_unbounded": "C2a 追问有上限",
    "max_retry_exceeds_rule": "C2b 追问有上限",
    "key_not_in_library": "C3b 全 key 已审核",
    "key_not_prebaked": "C3c 全 key 已预铸",
    "unreviewed_text": "C4a 不夹带未审核文本",
    "key_text_conflict": "C4b 不夹带未审核文本",
    "reason_on_keyed_unit": "C4c 不夹带未审核文本",
    "live_without_reason": "C5a 降级留痕",
    "reason_not_whitelisted": "C5b 降级留痕",
}

# 单元允许的字段全集（与 script.py 一致，供直接喂字典的调用方识别冲突）
# WHY 这里只用于「单元里有没有某个字段」的判断，不复制白名单校验逻辑。


# ---------------------------------------------------------------------------
# 2. 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Violation:
    """一条判据违规。

    属性：
        code:       判据码（照 docs/07 §7.3 的表）
        unit_index: 单元序号，**1-based**（与运行时事件的 part 对齐）；
                    剧本级违规（如 C2b）没有对应单元时取 0
        key:        相关的 key（无则 None）
        message:    必须含：判据名 + 实际值 + 上限/期望值
    """

    code: str
    unit_index: int
    key: Optional[str]
    message: str


@dataclass(frozen=True)
class CheckResult:
    """检查器返回值：违规列表 + 被跳过的判据码。

    属性：
        violations: 违规元组（空 = 全过）
        skipped:    被跳过的判据码（如 pack=None → ("key_not_prebaked",)）——
                    **非空表示本次判定不完整**，调用方必须往外传，不得当作通过。
    """

    violations: tuple[Violation, ...]
    skipped: tuple[str, ...]


# ---------------------------------------------------------------------------
# 3. 内部辅助
# ---------------------------------------------------------------------------
def _make_violation(
    code: str, unit_index: int, key: Optional[str], detail: str
) -> Violation:
    """构造一条违规，message 统一格式：`[{code}] {判据名}：{detail}`。

    统一格式让「判据名 + 实际值 + 上限/期望值」三要素在各条判据里都齐整可断言。
    """
    name = CRITERION_NAMES.get(code, code)
    return Violation(
        code=code,
        unit_index=unit_index,
        key=key,
        message=f"[{code}] {name}：{detail}",
    )


def _has_key(unit: Dict[str, Any]) -> bool:
    """单元是否带了非 None 的 key（与 core._validate_unit 的口径一致）。"""
    return "key" in unit and unit["key"] is not None


def _has_text(unit: Dict[str, Any]) -> bool:
    """单元是否带了非 None 的 text。"""
    return "text" in unit and unit["text"] is not None


def _unit_summary(unit: Dict[str, Any]) -> str:
    """单元的简短摘要（用于 message 里的「实际值」）。"""
    action = resolve_action(unit)
    key = unit.get("key")
    if key is not None:
        return f"action='{action}', key='{key}'"
    return f"action='{action}', text='{unit.get('text')}'"


def _is_terminal(unit: Dict[str, Any], terminal_keys: Tuple[str, ...]) -> bool:
    """单元是否终态：action == "END"，或它的 key ∈ terminal_keys。

    WHY 不是「必须有 END 单元」：rules/examples/ok_intake.json 用收尾话术
    closing_thank_you 表达结束、没有 END 单元，且已被验收为合规正例。
    所以出口由 terminal_keys 声明表达，END 只是可选的显式写法。
    """
    if resolve_action(unit) == "END":
        return True
    key = unit.get("key")
    return key is not None and key in terminal_keys


def _first_terminal_pos(units: Tuple[Dict[str, Any]], terminal_keys: Tuple[str, ...]) -> Optional[int]:
    """首个终态单元的 0-based 下标；没有终态则 None。"""
    for i, unit in enumerate(units):
        if _is_terminal(unit, terminal_keys):
            return i
    return None


def _key_runs(units: Tuple[Dict[str, Any]]) -> List[Tuple[str, int, int]]:
    """扫描连续同 key 的「跑」，返回 (key, 跑长, 跑末尾的 0-based 下标) 列表。

    WHY 只数连续同 key：线性 plan 无法表达「提问后用户没答 → 再问」的分支，
    所以「追问」在结构上就是同一 key 连续重复；text 单元或其他 key 都会打断跑。
    抓不到「换着 key 追问」——那是语义，见 docs/07 §7.4，不做启发式。
    """
    runs: List[Tuple[str, int, int]] = []
    run_key: Optional[str] = None
    run_len = 0
    run_end = -1
    for i, unit in enumerate(units):
        key = unit.get("key")
        if key is None:
            # 非 key 单元打断连续跑
            if run_key is not None:
                runs.append((run_key, run_len, run_end))
                run_key, run_len = None, 0
            continue
        if key == run_key:
            run_len += 1
            run_end = i
        else:
            if run_key is not None:
                runs.append((run_key, run_len, run_end))
            run_key, run_len, run_end = key, 1, i
    if run_key is not None:
        runs.append((run_key, run_len, run_end))
    return runs


# ---------------------------------------------------------------------------
# 4. 各条判据
# ---------------------------------------------------------------------------
def _check_c1(
    script: Script, violations: List[Violation]
) -> Optional[int]:
    """C1 有出口（对 §2.4「无非法吸收态」的当前形态映射）。

    C1a：末单元必须是终态，否则 no_exit。
    C1b：首个终态单元之后不得再有单元——终态之后永远执行不到，既是吸收态也是
         孤立枝。此处以 `orphan_branch` 报一次，C3 不重复报（见模块红线 3）。

    返回：
        首个终态单元的 0-based 下标（没有终态则 None），供调用方复用。
    """
    units = script.units

    # C1a
    if not _is_terminal(units[-1], script.terminal_keys):
        last = len(units)  # 1-based 末单元序号
        violations.append(
            _make_violation(
                "no_exit",
                last,
                units[-1].get("key"),
                f"末单元 #{last} 不是终态（{_unit_summary(units[-1])}），"
                f"期望 action=='END' 或 key ∈ terminal_keys={list(script.terminal_keys)}",
            )
        )

    # C1b
    terminal_pos = _first_terminal_pos(units, script.terminal_keys)
    if terminal_pos is not None:
        for i in range(terminal_pos + 1, len(units)):
            violations.append(
                _make_violation(
                    "orphan_branch",
                    i + 1,
                    units[i].get("key"),
                    f"单元 #{i + 1} 位于首个终态单元 #{terminal_pos + 1} 之后"
                    f"（{_unit_summary(units[i])}），线性 plan 永远执行不到",
                )
            )
    return terminal_pos


def _check_c2(script: Script, violations: List[Violation]) -> None:
    """C2 追问有上限（§2.4「有界」的可判定部分）。

    C2a：连续同 key 的跑长必须 ≤ max_retry，超过 → retry_unbounded。
    C2b：max_retry 声明值 > 3 → max_retry_exceeds_rule（R-3 硬规则，不按业务放宽）。
    """
    max_retry = script.max_retry

    # C2b
    if max_retry > MAX_RETRY_RULE:
        violations.append(
            _make_violation(
                "max_retry_exceeds_rule",
                0,
                None,
                f"剧本声明 max_retry={max_retry}，超过 R-3 的硬上限 {MAX_RETRY_RULE}",
            )
        )

    # C2a：一个违规跑报一条（取跑末尾的单元序号）
    for key, run_len, run_end in _key_runs(script.units):
        if run_len > max_retry:
            violations.append(
                _make_violation(
                    "retry_unbounded",
                    run_end + 1,
                    key,
                    f"key='{key}' 连续重复 {run_len} 次，超过上限 max_retry={max_retry}",
                )
            )


def _check_c3(
    script: Script,
    source: Any,
    pack: Any,
    violations: List[Violation],
    skipped: List[str],
) -> None:
    """C3 全 key 可达且都被预铸（§2.4 第 2 条）。

    C3a：可达性——终态之后的单元归 C1b 报 orphan_branch，这里不重复报。
    C3b：已审核——key 必须 ∈ source.phrases[].key，否则 key_not_in_library。
    C3c：已预铸——key 必须 ∈ pack.assets[].key，否则 key_not_prebaked。
         **pack is None 时跳过本条并记进 skipped，不得默认判过**（见模块红线 1）。
    """
    # WHY 只认这两个来源：话术库是「已审核」的事实来源，资产包是「已预铸」的
    #      事实来源。用文件系统扫目录代替它们会让判据依赖磁盘布局而不是产物内容，
    #      判据从此不可复核。
    library_keys: Set[str] = {p.key for p in source.phrases}

    if pack is None:
        # 跳过 ≠ 通过：显式登记，让调用方看得见本次判定不完整
        skipped.append("key_not_prebaked")
        pack_keys: Optional[Set[str]] = None
    else:
        pack_keys = {a.key for a in pack.assets}

    for i, unit in enumerate(script.units):
        if not _has_key(unit):
            continue
        key = unit["key"]
        index = i + 1  # 1-based

        # C3b
        if key not in library_keys:
            violations.append(
                _make_violation(
                    "key_not_in_library",
                    index,
                    key,
                    f"单元 #{index} 引用 key='{key}' 不在已审核话术库中"
                    f"（库内 {len(library_keys)} 个 key）——R-1 禁止自造文案",
                )
            )

        # C3c（pack=None 时已跳过，pack_keys 为 None）
        if pack_keys is not None and key not in pack_keys:
            violations.append(
                _make_violation(
                    "key_not_prebaked",
                    index,
                    key,
                    f"单元 #{index} 引用 key='{key}' 不在资产包中"
                    f"（包内 {len(pack_keys)} 个 key）——未预铸不得进剧本",
                )
            )


def _check_c4(script: Script, violations: List[Violation]) -> None:
    """C4 不夹带未审核文本（§2.4 第 3 条）。

    C4a：带 text 的单元 action 必须是 SAY_LIVE；action == "SAY" 且带 text →
         unreviewed_text（R-5 说的「用 SAY 伪装自由文本为资产命中」）。
    C4b：带 key 的单元不得同时带 text → key_text_conflict。
    C4c：reason 不得出现在非 SAY_LIVE 单元上 → reason_on_keyed_unit。
         C4b/C4c 与 script.py §7.2 的源格式校验同源，这里作为检查项复述，
         供不经 load_script 直接喂字典的调用方。

    注意：PAD/LISTEN/PRELOAD/END 这些原语当前不带文本；一旦将来要带，
    必须先改 docs/07，不得由本实现自行放宽。
    """
    for i, unit in enumerate(script.units):
        index = i + 1
        action = resolve_action(unit)
        has_key = _has_key(unit)
        has_text = _has_text(unit)

        # C4a
        if has_text and action == "SAY":
            violations.append(
                _make_violation(
                    "unreviewed_text",
                    index,
                    None,
                    f"单元 #{index} action='SAY' 却带 text='{unit.get('text')}'，"
                    f"伪装自由文本为资产命中——自由文本必须是 action='SAY_LIVE'",
                )
            )

        # C4b
        if has_key and has_text:
            violations.append(
                _make_violation(
                    "key_text_conflict",
                    index,
                    unit.get("key"),
                    f"单元 #{index} 同时带 key='{unit.get('key')}' 与 text，"
                    f"必须恰好给一个",
                )
            )

        # C4c
        if "reason" in unit and action != "SAY_LIVE":
            violations.append(
                _make_violation(
                    "reason_on_keyed_unit",
                    index,
                    unit.get("key"),
                    f"单元 #{index} 是 {action} 单元却带 reason='{unit.get('reason')}'"
                    f"（reason 只允许出现在 SAY_LIVE 单元上）",
                )
            )


def _check_c5(script: Script, violations: List[Violation]) -> None:
    """C5 降级留痕（R-5）。

    C5a：每个 SAY_LIVE 单元必须有非空字符串 reason，否则 live_without_reason。
    C5b：reason 必须 ∈ live_whitelist，否则 reason_not_whitelisted。
         live_whitelist 为空列表 = 本业务不允许任何 SAY_LIVE——任何 SAY_LIVE 的
         reason 都会被 C5b 拦下。这是有意的：白名单必须显式声明，不许「没写就当允许」。

    注意：C5 只对 SAY_LIVE 单元生效。action="SAY" 且带 text 的单元**不走** C5a，
         它走 C4a（unreviewed_text）——这是「伪装命中」和「合法降级」的分界线。
    """
    for i, unit in enumerate(script.units):
        if resolve_action(unit) != "SAY_LIVE":
            continue
        index = i + 1
        reason = unit.get("reason")

        # C5a
        if not isinstance(reason, str) or not reason:
            violations.append(
                _make_violation(
                    "live_without_reason",
                    index,
                    None,
                    f"单元 #{index} 是 SAY_LIVE 自由文本 text='{unit.get('text')}' "
                    f"但没有非空 reason，降级无留痕",
                )
            )
            continue

        # C5b
        if reason not in script.live_whitelist:
            violations.append(
                _make_violation(
                    "reason_not_whitelisted",
                    index,
                    None,
                    f"单元 #{index} 的 reason='{reason}' 不在 live_whitelist="
                    f"{list(script.live_whitelist)} 中",
                )
            )


# ---------------------------------------------------------------------------
# 5. 检查 API
# ---------------------------------------------------------------------------
def check_properties(script: Script, source: Any, pack: Any = None) -> CheckResult:
    """对一份剧本源跑五项机器判据，返回违规与跳过记录。

    判定顺序固定 C1 → C2 → C3 → C4 → C5（先结构后引用），同一问题只报一次。

    参数：
        script: 剧本源（load_script 的产物；也接受直接构造的 Script）
        source: 已审核话术库（compiler.load_source 的产物），用 phrases[].key 取已审核集合
        pack:   已预铸资产包（assets.load_pack 的产物），用 assets[].key 取已预铸集合；
                **可以为 None** —— 此时 C3c 跳过并记入 CheckResult.skipped

    返回：
        CheckResult(violations=违规元组, skipped=被跳过的判据码元组)
        violations 为空 = 全过；skipped 非空 = 本次判定不完整，不得当作通过。

    异常：
        TypeError: source 为 None（C3b 必须有已审核话术库作为输入）
    """
    if source is None:
        # WHY：C3b 的判据输入就是「已审核集合」，没有它判据无意义；
        #      允许传 None 会让「没检查」看起来像「检查过了」。
        raise TypeError(
            "source 不能为 None：C3b 需要已审核话术库（compiler.load_source 的产物）"
        )

    violations: List[Violation] = []
    skipped: List[str] = []

    # C1 有出口
    _check_c1(script, violations)

    # C2 追问有上限
    _check_c2(script, violations)

    # C3 全 key 可达且都被预铸
    _check_c3(script, source, pack, violations, skipped)

    # C4 不夹带未审核文本
    _check_c4(script, violations)

    # C5 降级留痕
    _check_c5(script, violations)

    return CheckResult(violations=tuple(violations), skipped=tuple(skipped))
