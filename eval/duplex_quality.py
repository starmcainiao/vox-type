"""
eval.duplex_quality — 双工质量评测 harness（打断准确率 / 假阳性 / 轮转延迟）

为什么这一层只测"系统主动开口"侧（docs/12 原话）：
  客服场景的双工是"用户打断我"——那是别人的行为，本仓测不了；
  前置包场景是"我什么时候该开口"——这是我们自己的行为，可以自造、可以重放。
  本仓 runtime 是离线产物生成器（plan → WAV + 事件流），
  所以三指标**全部从 policy_stream 事件流算**，不需要真实音频、不依赖实时播放、不碰麦克风。

三个指标怎么算（T20 卡内裁定，逐条写进报告 caliber 与 eval/AGENTS.md）：
  1. 打断准确率（barge_in_accuracy）
     对脚本里每个 barge_in 动作，判据来自**事件流**（与该单元是否终态、barge_in 模式无关）：
        requires_confirm == True  → 系统"应拦下等确认"（blocked）
        requires_confirm == False → 系统"应允许打断"（allowed）
     脚本期望由**生成规则**在生成时刻记录（want_blocked 布尔），与事件流判定独立比对。
     准确率 = 判定与期望一致的动作数 / 打断动作总数。
     卡面口径"该单元若 barge_in=allow → 应允许；requires_confirm=true → 应拦"在本仓的
     runtime 实现里两者等价（barge_in=allow 时 _requires_confirm 恒 False，allow 下"应允许"
     就是 requires_confirm=False），所以一律以 requires_confirm 这个事件流布尔值为准——
     事件流是唯一事实来源，参数值不进判定（否则同一事实算两遍、且无法被事件流独立复算）。
  2. 假阳性（false_positive_rate）
     脚本里 speak / silence 动作（**非打断**）落在 requires_confirm=True 的单元上，
     即被系统判成"打断需拦"。比例 = 误拦数 / 非打断动作数（0 为理想）。
     只有 barge_in=confirm 且命中终态键（或落在 plan 最后一个单元）时该值才可能 > 0，
     所以本指标**只在 confirm 预设下才有判别力**——allow 预设下恒 0，报告如实写出。
  3. 轮转延迟（turnover_latency_ms）
     从"最后一个单元播完"到"等待窗口结束"的时长 = 等待窗口事件里的 listen_ms（= patience_ms）
     + 单元间切换耗时（silence_pad_ms 累计，(单元数-1) 段）。单位 ms，报 P50 / P99。
     与 first_audio_ms 不同：它是**设计量**（由参数与包内预铸时长推得），不吃挂钟时间，
     所以同输入跑两次必逐值相等——这才是本层"可复现"的硬承诺。

可复现性（eval/AGENTS.md ③.1）：
  场景脚本只用 random.Random(seed) 独立实例生成，不读全局随机状态、不受 PYTHONHASHSEED 影响；
  指标只取事件流里的稳定字段（spoken_ms 来自包内 manifest duration_ms，不吃 wall-clock）。
  同一 (参数集, 种子, 包) 跑两次，报告除 generated_at 外逐字节一致；换种子必变。

层边界（eval/AGENTS.md ⑤）：静态依赖只有 core/（事件字段常量）、assets/（load_pack）、
  runtime/（Executor / DuplexParams / DuplexError）+ 标准库。
  适配器一律注入（默认 OfflineTts），绝不在这里静态 import adapters/。
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import core.metrics_spec as spec
from assets.pack import AssetPackError, load_pack
from core.protocol import VALID_RATES
from runtime import Executor, RuntimeMissError, SAMPLE_RATE  # noqa: F401  (SAMPLE_RATE 供测试侧核对口径)
from runtime.duplex import DuplexError, DuplexParams

from .offline_tts import OfflineTts
from .stats import percentile
# 文件级 I/O 与指纹摘要的统一实现（仅标准库）：manifest 指纹与 bench/raw 指纹
# 共用同一个 sha256_of_file，分块策略不可能各自漂移
from . import _io

# ---------------------------------------------------------------------------
# 报告 schema 与口径（字段名冻结；新增只能追加）
# ---------------------------------------------------------------------------
DQ_SCHEMA_VERSION: str = "vox-eval-duplex-quality/1"

# 脚本动作白名单（生成器只产这三种，CLI 也按这三种构造）
ACTION_BARGE_IN: str = "barge_in"
ACTION_SPEAK: str = "speak"
ACTION_SILENCE: str = "silence"
ACTIONS: Tuple[str, ...] = (ACTION_BARGE_IN, ACTION_SPEAK, ACTION_SILENCE)

# 默认场景形状：单元数、总动作数、种子
DEFAULT_UNIT_COUNT: int = 8
DEFAULT_NUM_ACTIONS: int = 60
DEFAULT_SEED: int = 20260920
DEFAULT_PLAN_ID: str = "dq"

# 默认双臂预设（卡内要求"patience_ms / backchannel / barge_in / rate_band 的若干组合"）
DEFAULT_PRESETS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("baseline-allow", {"patience_ms": 900, "backchannel": "on", "barge_in": "allow"}),
    ("confirm-patience400", {"patience_ms": 400, "backchannel": "on", "barge_in": "confirm"}),
    ("backchannel-off", {"patience_ms": 900, "backchannel": "off", "barge_in": "allow"}),
)

# 报告口径说明（逐条写进报告，改这里 = 改口径 = 破坏性变更）
DQ_CALIBER: Dict[str, str] = {
    "barge_in_accuracy": (
        "barge_in_accuracy = 判定与脚本期望一致的打断动作数 / 打断动作总数（取值 0.0~1.0）。"
        "判定来自事件流：该动作落在的单元 requires_confirm=True → blocked（应拦下等确认）；"
        "False → allowed（应允许打断）。脚本期望（want_blocked）由生成规则在生成时刻记录，"
        "与事件流判定独立比对。barge_in=allow 的预设下 requires_confirm 恒 False（可打断），"
        "该指标退化为'期望允许的比例'，报告如实写出、不隐藏。"
    ),
    "false_positive_rate": (
        "false_positive_rate = 非打断动作（speak / silence）落在 requires_confirm=True 单元上的数量 "
        "/ 非打断动作总数（取值 0.0~1.0，越低越好，0 为理想）。"
        "非打断动作落在终态单元（barge_in=confirm 且命中 terminal_keys 或 plan 最后一个单元）"
        "上时被系统判成'打断需拦'——即误把普通说话/沉默当成打断。"
        "该指标只在 barge_in=confirm 的预设下才有判别力，allow 预设下恒 0。"
    ),
    "turnover_latency_ms": (
        "turnover_latency_ms = 轮转延迟（ms）= listen_ms（最后一个单元播完 → 等待窗口结束，"
        "取自事件流末尾的等待窗口事件，语义 = patience_ms）+ silence_pad_ms 累计"
        "（单元数-1 段，即单元间切换耗时）。这是**设计量**：只由参数与包内预铸时长推得，"
        "不吃挂钟时间，同 (参数集, 种子, 包) 跑两次必逐值相等（bench 的 first_audio_ms 不承诺这点）。"
    ),
    "turn_count": "turn_count = 本预设实际执行的 plan 条数（脚本按 max_units 切成若干条 plan）。",
    "action_counts": "action_counts = 脚本动作按类型计数；sum == num_actions。",
    "turnover_samples": "turnover_samples = 轮转延迟的逐条样本（每 plan 一条），供独立复算与分布核对。",
    "barge_in_details": (
        "barge_in_details = 每个 barge_in 动作的判定明细（unit_index / unit_key / at_ms / "
        "expected / actual / matched），供验收方独立复算准确率。"
    ),
    "non_interruption_details": (
        "non_interruption_details = 每个非打断动作的判定明细，供验收方独立复算假阳性。"
    ),
    "quantile_method": (
        "分位数口径完全沿用 eval.stats.percentile（linear，位置=(n-1)*q），"
        "与 bench 同口径——本模块不自造分位数算法，空样本由 stats 层报错后转成 "
        "DuplexQualityError。P50/P99 可直接用 eval.stats.percentile 逐位复算。"
    ),
    "reproducibility": (
        "同 (参数集, 种子, 包) 跑两次：报告除 generated_at 外逐字节一致；换种子必变。"
        "场景脚本只用 random.Random(seed) 独立实例，不读全局随机状态、不受 PYTHONHASHSEED 影响；"
        "指标只取事件流里的稳定字段（spoken_ms 取包内 manifest duration_ms）。"
    ),
    "boundary": (
        "只测'系统主动开口'侧（docs/12 原话：发起方是我们、场景可自造）。"
        "脚本全部由固定种子生成，不读任何真实用户数据、录音或会话；"
        "不依赖真实音频播放与实时交互（本仓 runtime 是离线产物生成器）。"
    ),
}


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class DuplexQualityError(Exception):
    """harness 配置 / 场景脚本 / 执行期错误。

    抛此异常时不得产生任何报告文件（先建后写，全部校验通过才落盘）。
    消息必须包含导致失败的具体值，不允许吞掉上下文——否则上层无法判断
    是配置写错还是执行器真的出了状况。
    """


# ---------------------------------------------------------------------------
# 参数预设
# ---------------------------------------------------------------------------
def build_params(overrides: Optional[Dict[str, Any]],
                 terminal_keys: Optional[Sequence[str]] = None) -> DuplexParams:
    """按覆盖字典构造 DuplexParams；非法取值由 DuplexParams.__post_init__ 抛 DuplexError。

    纪律（不静默降级）：这里**不捕获、不回落、不补默认**——
    非法参数必须把 DuplexError 原样抛给上层，否则"本该被拦的配置悄悄跑了默认值"
    在事后再也无法定位。调用方若需要中文消息，由 run_duplex_quality_cli 补。
    """
    return DuplexParams(
        **(overrides or {}),
        terminal_keys=frozenset(terminal_keys or ()),
    )


def _preset_terminal_keys(overrides: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    """确认语义预设需要显式终态键，否则 requires_confirm 只会在"最后一个单元"上出现。

    WHY 按参数值（而非预设名）决定：终态键是**场景配置**，不是双工参数
    （barge_in=confirm 只回答"终态要不要确认"，不回答"哪些话术是终态"）。
    报告里如实写出该键集合，可被验收方逐位复算。
    """
    if (overrides or {}).get("barge_in") == "confirm":
        return ("farewell", "thanks")
    return ()


# ---------------------------------------------------------------------------
# 场景生成（自造，只吃种子与参数）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UserAction:
    """一条用户行为脚本动作（自造场景）。

    属性：
        plan_index:   属于第几条 plan（0 起）——plan 内时长轴才有效
        unit_index:   落在哪个单元（0 起，与事件流 spoken_ms 累计的单元同序）
        at_ms:        该单元的播出时长（= 单元事件里的 spoken_ms，来自包内 manifest duration_ms）
        action:       barge_in / speak / silence
        want_blocked: 仅 barge_in 动作为 True——生成规则记录的"该动作是否应被拦"（脚本期望）
    """

    plan_index: int
    unit_index: int
    at_ms: int
    action: str
    want_blocked: bool = False


def _weighted(rng: Random, choices: Sequence[Tuple[str, float]], total: float) -> str:
    """按权重取一个动作名；权重之和必须与 total 一致（防配置漂移）。"""
    roll = rng.random() * total
    upto = 0.0
    for name, weight in choices:
        upto += weight
        if roll < upto:
            return name
    return choices[-1][0]          # 浮点尾差兜底（权重和已校验过，这里只是数值容差）


def generate_scenario(
    seed: int,
    plan_duration_ms: Sequence[int],
    *,
    num_actions: int = DEFAULT_NUM_ACTIONS,
    max_units: int = DEFAULT_UNIT_COUNT,
    weights: Optional[Tuple[Tuple[str, float], ...]] = None,
) -> List[UserAction]:
    """按固定种子生成用户行为脚本（不读任何外部语料/录音，只吃种子与时长轴）。

    生成规则（可判定，写进报告便于独立复算）：
      1. 时长轴按每 plan 一条、单元逐条累计，切成若干条 plan（每条至多 max_units 个单元）；
      2. 动作按固定权重分布采样：barge_in 22% / speak 38% / silence 40%；
      3. 每个动作落在某条 plan 的某个单元上（plan 内按单元序号均匀采样，at_ms = 该单元播出时长）；
      4. barge_in 动作的脚本期望 want_blocked：该单元是本条 plan 的最后一个单元 → True
         （终态要确认），否则 → False（中段可打断）。

    参数：
        seed:           随机种子（整数；bool 是 int 子类，必须排除）
        plan_duration_ms: 每条 plan 的单元播出时长序列（嵌套序列，非空）
        num_actions:    总动作数，>= 1
        max_units:      每条 plan 的单元数上限，>= 1
        weights:        动作权重（默认固定分布；和必须 > 0）

    异常：
        ValueError: 动作数/单元数为 0、种子非整数、时长轴为空或含非正数值、权重非法
    """
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(
            f"generate_scenario.seed: 必须是整数（固定种子是复现前提），实际为 {seed!r}"
        )
    if not isinstance(num_actions, int) or isinstance(num_actions, bool) or num_actions < 1:
        raise ValueError(
            f"generate_scenario.num_actions: 必须是 >=1 的整数，实际为 {num_actions!r}"
        )
    if not isinstance(max_units, int) or isinstance(max_units, bool) or max_units < 1:
        raise ValueError(
            f"generate_scenario.max_units: 必须是 >=1 的整数，实际为 {max_units!r}"
        )

    # 时长轴规整：每条 plan 一条时长序列
    plans: List[List[int]] = []
    try:
        for plan_row in plan_duration_ms:
            plans.append([int(v) for v in plan_row])
    except TypeError as exc:
        raise ValueError(
            f"generate_scenario.plan_duration_ms: 必须是嵌套序列，实际为 "
            f"{type(plan_duration_ms).__name__}"
        ) from exc
    if not plans:
        raise ValueError(
            "generate_scenario.plan_duration_ms: 时长轴为空（0 条 plan）——"
            "没有可落点的单元，无法生成脚本"
        )
    for i, row in enumerate(plans):
        if not row:
            raise ValueError(
                f"generate_scenario.plan_duration_ms[{i}]: 单元时长序列为空——"
                "该 plan 没有可落点的单元"
            )
        bad = [v for v in row if v <= 0]
        if bad:
            raise ValueError(
                f"generate_scenario.plan_duration_ms[{i}]: 单元时长必须为正整数毫秒，"
                f"实际含 {bad!r}"
            )

    if weights is None:
        weights = ((ACTION_BARGE_IN, 0.22), (ACTION_SPEAK, 0.38), (ACTION_SILENCE, 0.40))
    else:
        names = [name for name, _ in weights]
        if sorted(names) != sorted(set(ACTIONS)):
            raise ValueError(
                f"generate_scenario.weights: 动作名必须是 {ACTIONS}，实际为 {names!r}"
            )
        total = sum(w for _, w in weights)
        if total <= 0:
            raise ValueError(
                f"generate_scenario.weights: 权重之和必须 > 0，实际为 {total!r}"
            )
    total_weight = sum(w for _, w in weights)

    rng = Random(seed)
    actions: List[UserAction] = []
    for _ in range(num_actions):
        plan_idx = rng.randrange(len(plans))
        row = plans[plan_idx]
        unit_idx = rng.randrange(len(row))
        action = _weighted(rng, weights, total_weight)
        want_blocked = action == ACTION_BARGE_IN and unit_idx == len(row) - 1
        actions.append(UserAction(
            plan_index=plan_idx,
            unit_index=unit_idx,
            at_ms=row[unit_idx],
            action=action,
            want_blocked=bool(want_blocked),
        ))
    return actions


# ---------------------------------------------------------------------------
# 事件流解析（按形态分流，不按位置假设）
# ---------------------------------------------------------------------------
def _unit_events(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """取单元事件（必带 PART）；等待窗口事件不带 PART，天然被排除。"""
    return [e for e in events if spec.PART in e]


def _listen_event(events: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """取唯一一条等待窗口事件；缺失或多条都是事件流契约被破坏，fail-closed。"""
    matches = [e for e in events if spec.LISTEN_MS in e]
    if not matches:
        raise DuplexQualityError(
            "事件流缺少等待窗口事件（应带 listen_ms）——policy_stream 未生效，"
            "无法计算轮转延迟"
        )
    if len(matches) > 1:
        raise DuplexQualityError(
            f"事件流出现 {len(matches)} 条等待窗口事件（应恰好 1 条）——事件流契约被破坏"
        )
    return matches[0]


def _pack_plan_keys(
    pack: Any, n_plans: int, plan_unit_count: int
) -> Tuple[List[List[str]], str]:
    """为 n_plans 条 plan 各取 plan_unit_count 个 key，返回 (plan_keys, terminal_key)。

    键从包内真实 key 集合里取（保证全命中、零 TTS、全 HIT），按 (plan, 单元序) 循环排列：
    同一条 plan 内不重复（时长轴按单元逐个铺开），跨 plan 复用同一组键
    （plan 单元数可能大于包内 key 数——那是评测配置的常态，复现纪律仍成立）。
    terminal_key = 最后一条 plan 的最后一个键。
    """
    keys = sorted({entry.key for entry in pack.assets})
    if not keys:
        raise DuplexQualityError("资产包内没有任何话术 key，无法构造 plan")
    if n_plans < 1 or plan_unit_count < 1:
        raise DuplexQualityError(
            f"plan 形状非法：n_plans={n_plans} plan_unit_count={plan_unit_count}（都必须 >= 1）"
        )
    if plan_unit_count > len(keys):
        raise DuplexQualityError(
            f"单条 plan 的单元数 {plan_unit_count} 大于包内 key 数 {len(keys)}——"
            "同一条 plan 内不允许复用 key（会让时长轴重复铺开）"
        )
    plan_keys = [[keys[u % len(keys)] for u in range(plan_unit_count)]
                 for _ in range(n_plans)]
    terminal_key = plan_keys[-1][-1]
    return plan_keys, terminal_key


# ---------------------------------------------------------------------------
# 单预设执行 + 指标计算
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _RunBundle:
    """一次 execute() 的留痕：结果、错误消息、事件流问题。"""

    result: Any = None
    error: Optional[str] = None
    event_issues: List[str] = None


def _execute_one(
    executor: Any, plan_units: Sequence[Dict[str, Any]], plan_id: str,
    turn_id: str, out_dir: Path, arm: str,
) -> _RunBundle:
    """执行一条 plan（policy_stream=True）；异常不吞，留痕。"""
    issues: List[str] = []
    try:
        result = executor.execute(
            list(plan_units), plan_id=plan_id, turn_id=turn_id,
            out_path=out_dir / f"{arm}_{turn_id}.wav",
        )
    except RuntimeMissError as exc:
        return _RunBundle(error=f"{turn_id}: {exc}", event_issues=issues)
    except Exception as exc:            # 任何异常都不能被吞掉（静默降级红线）
        return _RunBundle(
            error=f"{turn_id}: {type(exc).__name__}: {exc}", event_issues=issues
        )

    # 事件流完整性：每单元一条 + 末尾恰好一条等待窗口事件
    units = len(plan_units)
    unit_events = _unit_events(result.events)
    if len(unit_events) != units:
        issues.append(f"{turn_id}: 单元事件数 {len(unit_events)} ≠ 单元数 {units}")
    listen = [e for e in result.events if spec.LISTEN_MS in e]
    if len(listen) != 1:
        issues.append(f"{turn_id}: 等待窗口事件数 {len(listen)}（应恰好 1 条）")
    parts = [e.get(spec.PART) for e in unit_events]
    missing = sorted(set(range(1, units + 1)) - {int(p) for p in parts if isinstance(p, int)})
    if missing:
        issues.append(f"{turn_id}: 事件缺 part={missing}")
    return _RunBundle(result=result, event_issues=issues)


def compute_metrics(
    *,
    preset_name: str,
    params: DuplexParams,
    plan_results: Sequence[Tuple[str, Any]],
    actions: Sequence[UserAction],
) -> Dict[str, Any]:
    """把脚本 + 事件流折成三指标（口径见 DQ_CALIBER，可被验收方独立复算）。

    参数：
        preset_name:  预设名（写进报告）
        params:       本预设的双工参数
        plan_results: (turn_id, ExecutionResult) 列表，与 plan_index 同序
        actions:      脚本动作

    异常：
        DuplexQualityError: 脚本为空 / 时长轴为空 / 事件流缺等待窗口事件
        ValueError:       脚本里有动作落在不存在的 plan 上（配置漂移）
    """
    n_plans = len(plan_results)
    if not n_plans:
        raise DuplexQualityError("无可执行的 plan（0 条）——无法计算指标")
    if not actions:
        raise DuplexQualityError("用户行为脚本为空（0 条动作）——没有可判定的动作")

    # 各 plan 的单元事件与等待窗口事件
    plan_units_events: List[List[Dict[str, Any]]] = []
    listen_ms_values: List[int] = []
    for turn_id, result in plan_results:
        units = _unit_events(result.events)
        if not units:
            raise DuplexQualityError(
                f"{turn_id}: 事件流里没有任何单元事件（policy_stream 未生效）"
            )
        listen_ms_values.append(int(_listen_event(result.events)[spec.LISTEN_MS]))
        plan_units_events.append(units)

    # 判定映射：(plan_index, unit_index) → 该单元事件
    detail_by_unit: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for p_idx, units in enumerate(plan_units_events):
        for e in units:
            part = int(e[spec.PART])          # PART 从 1 起
            if part not in range(1, len(units) + 1):
                raise DuplexQualityError(
                    f"plan #{p_idx} 出现越界 part={part}（单元数 {len(units)}）——事件流契约被破坏"
                )
            detail_by_unit[(p_idx, part - 1)] = e

    def _judge(event: Dict[str, Any]) -> str:
        """事件流判定：requires_confirm → blocked / allowed（该单元是否终态由 runtime 决定）。"""
        return "blocked" if event[spec.REQUIRES_CONFIRM] else "allowed"

    # ① 打断准确率
    bi_details: List[Dict[str, Any]] = []
    for act in actions:
        if act.action != ACTION_BARGE_IN:
            continue
        event = detail_by_unit.get((act.plan_index, act.unit_index))
        if event is None:
            raise ValueError(
                f"脚本动作 (plan={act.plan_index}, unit={act.unit_index}) 落在不存在的单元上"
                f"（该 plan 单元数 {len(plan_units_events[act.plan_index])}）——脚本配置漂移"
            )
        actual = _judge(event)
        expected = "blocked" if act.want_blocked else "allowed"
        bi_details.append({
            "plan_index": act.plan_index,
            "unit_index": act.unit_index,
            "unit_key": event.get(spec.KEY),
            "part": event[spec.PART],
            "at_ms": act.at_ms,
            "expected": expected,
            "actual": actual,
            "matched": expected == actual,
        })
    if not bi_details:
        raise DuplexQualityError(
            "脚本里没有 barge_in 动作（0 条）——打断准确率无定义，不得写 1.0 冒充"
        )
    matched = sum(1 for d in bi_details if d["matched"])
    accuracy = round(matched / len(bi_details), 6)

    # ② 假阳性：非打断动作被系统判成"打断需拦"
    ni_details: List[Dict[str, Any]] = []
    false_positives = 0
    for act in actions:
        if act.action == ACTION_BARGE_IN:
            continue
        event = detail_by_unit.get((act.plan_index, act.unit_index))
        if event is None:
            raise ValueError(
                f"脚本动作 (plan={act.plan_index}, unit={act.unit_index}) 落在不存在的单元上"
                f"（该 plan 单元数 {len(plan_units_events[act.plan_index])}）——脚本配置漂移"
            )
        actual = _judge(event)
        flagged = actual == "blocked"
        if flagged:
            false_positives += 1
        ni_details.append({
            "plan_index": act.plan_index,
            "unit_index": act.unit_index,
            "unit_key": event.get(spec.KEY),
            "action": act.action,
            "at_ms": act.at_ms,
            "requires_confirm": bool(event[spec.REQUIRES_CONFIRM]),
            "flagged_as_interruption": flagged,
        })
    non_interruption = len(ni_details)
    false_positive_rate = round(false_positives / non_interruption, 6) if non_interruption else 0.0

    # ③ 轮转延迟 = listen_ms + silence_pad_ms × (单元数-1)
    samples: List[Dict[str, Any]] = []
    latency_values: List[int] = []
    for p_idx, (turn_id, _result) in enumerate(plan_results):
        n_units = len(plan_units_events[p_idx])
        silence_pad_total = params.silence_pad_ms * max(n_units - 1, 0)
        listen = listen_ms_values[p_idx]
        total = listen + silence_pad_total
        latency_values.append(total)
        samples.append({
            "plan_index": p_idx,
            "turn_id": turn_id,
            "n_units": n_units,
            "listen_ms": listen,
            "silence_pad_total_ms": silence_pad_total,
            "turnover_latency_ms": total,
        })
    return {
        "preset": preset_name,
        "params": {
            "patience_ms": params.patience_ms,
            "rate_band": params.rate_band,
            "backchannel": params.backchannel,
            "barge_in": params.barge_in,
            "silence_pad_ms": params.silence_pad_ms,
            "slot_pad_ms": params.slot_pad_ms,
            "fade_ms": params.fade_ms,
            "terminal_keys": sorted(params.terminal_keys),
        },
        "turn_count": n_plans,
        "action_counts": {
            name: sum(1 for a in actions if a.action == name) for name in ACTIONS
        },
        "barge_in_accuracy": accuracy,
        "barge_in_matched": matched,
        "barge_in_total": len(bi_details),
        "false_positive_rate": false_positive_rate,
        "false_positives": false_positives,
        "non_interruption_total": non_interruption,
        "turnover_latency_ms": {
            "n": len(latency_values),
            "p50": _p50(latency_values),
            "p99": _p99(latency_values),
            "min": min(latency_values),
            "max": max(latency_values),
        },
        "turnover_samples": samples,
        "barge_in_details": bi_details,
        "non_interruption_details": ni_details,
    }


def _p50(values: Sequence[float]) -> float:
    """P50，口径完全沿用 eval.stats.percentile（linear，位置=(n-1)*q）。

    WHY 不在本模块自造分位数算法：口径一旦在两处各写一份，漂移就没人发现
    （bench 与这里报出的 P50/P99 会悄悄不一致）。空样本的报错也交给 stats 层，
    本模块只负责把它转成 DuplexQualityError（错误分类归本层，算法归 stats）。
    """
    return _quantile(values, 0.5)


def _p99(values: Sequence[float]) -> float:
    """P99，同 _p50 口径。"""
    return _quantile(values, 0.99)


def _quantile(values: Sequence[float], q: float) -> float:
    """分位数（委托 eval.stats.percentile）；空样本转成 DuplexQualityError。

    转译纪律：stats 层抛 ValueError（通用契约），harness 层要抛 DuplexQualityError
    （本层契约）。消息保留"实际样本量 0"这个值——不写 0.0 冒充零延迟（静默降级）。
    """
    if not values:
        raise DuplexQualityError(
            "分位数样本为空（0 条）——不得写 0.0 冒充零延迟（静默降级）"
        )
    try:
        return percentile(values, q)
    except ValueError as exc:
        raise DuplexQualityError(f"分位数计算失败（{len(values)} 条样本）: {exc}") from exc


# ---------------------------------------------------------------------------
# 报告组装与落盘
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DuplexQualityConfig:
    """一次双工质量评测的配置。

    属性：
        pack:           已装载的资产包（load_pack 产物）
        adapter:        注入的 TTS 适配器（默认 OfflineTts；voice/model_version 取包里的值）
        presets:        (预设名, 双工参数覆盖字典) 列表，非空
        seed:           场景种子（整数，固定 → 可复现）
        plan_unit_count: 每条 plan 的单元数
        n_plans:        plan 条数
        num_actions:    脚本动作总数
        unit_weights:   动作权重分布（默认固定分布）
        out_dir:        输出目录（报告 + 音频）
        command:        可原样复现的完整命令行
    """

    pack: Any
    adapter: Any
    presets: Tuple[Tuple[str, Dict[str, Any]], ...]
    seed: int = DEFAULT_SEED
    plan_unit_count: int = DEFAULT_UNIT_COUNT
    n_plans: int = 2
    num_actions: int = DEFAULT_NUM_ACTIONS
    unit_weights: Optional[Tuple[Tuple[str, float], ...]] = None
    out_dir: Optional[Path] = None
    command: str = ""


def _utc_now_iso() -> str:
    """当前 UTC 时间戳（ISO 8601，带 Z）；报告里唯一的非确定性字段。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _commit_sha() -> str:
    """HEAD 指向的 40 位 SHA；无法解析时返回原始 ref 字符串（不编造值）。"""
    try:
        git_dir = Path(__file__).resolve().parents[1] / ".git"
        text = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("ref: "):
            ref = text[len("ref: "):].strip()
            ref_file = git_dir / ref
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8", errors="replace").strip()
            return text
        return text
    except OSError:
        return "unknown"


# T21 起 sha256 实现收敛到 eval._io.sha256_of_file（本文件曾持有第四份逐字拷贝）。
# 本模块是 manifest provenance 指纹的**唯一**读取点，与 bench 写 raw 指纹、
# report 验 raw 指纹共用同一个函数——分块策略不可能各自漂移。


def _manifest_fingerprint(pack: Any) -> Dict[str, Any]:
    """包 manifest 的 sha256 + 资产条数（provenance 要求）。

    取 manifest.json 的字节摘要：内容变了（重铸、换音色）摘要就变，
    这是"数字对应的到底是哪个包"的唯一凭证。
    """
    root = Path(pack.root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise DuplexQualityError(
            f"资产包根目录 {root} 下没有 manifest.json——无法给出 provenance，拒绝出报告"
        )
    return {
        "pack_id": pack.pack_id if hasattr(pack, "pack_id") else None,
        "pack_version": pack.pack_version,
        "voice": pack.voice,
        "model_version": pack.model_version,
        "asset_count": len(pack.assets),
        "manifest_path": manifest_path.name,
        "manifest_sha256": _io.sha256_of_file(manifest_path),
        "root": str(root),
    }


def build_quality_report(
    *,
    config: DuplexQualityConfig,
    blocks: Sequence[Dict[str, Any]],
    scripts: Sequence[Dict[str, Any]],
    errors: Sequence[str],
    warnings: Sequence[str],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """组装报告字典（不写盘）。字段名冻结，可追加不得改名。

    异常：
        DuplexQualityError: 没有预设块 / 有执行期错误却声称无错误（不得美化）
    """
    if not blocks:
        raise DuplexQualityError(
            "没有可写的预设块（0 条）——执行期全部失败，不得产半份报告"
        )
    incomplete = bool(errors)
    return {
        "schema_version": DQ_SCHEMA_VERSION,
        "generated_at": generated_at if generated_at is not None else _utc_now_iso(),
        "command": config.command,
        "seed": config.seed,
        "incomplete": incomplete,
        "incomplete_reasons": list(errors),
        "warnings": list(warnings),
        "scope": "system-initiated-speaking-side",      # 只测"系统主动开口"侧
        "boundary": DQ_CALIBER["boundary"],
        "provenance": {
            "pack": _manifest_fingerprint(config.pack),
            "duplex_presets": [
                {"name": name, "overrides": dict(overrides or {})}
                for name, overrides in config.presets
            ],
            "seed": config.seed,
            "plan_unit_count": config.plan_unit_count,
            "n_plans": config.n_plans,
            "num_actions": config.num_actions,
            "unit_weights": [[n, w] for n, w in config.unit_weights]
                if config.unit_weights else None,
            "repo_commit": _commit_sha(),
        },
        "caliber": dict(DQ_CALIBER),
        "scripts": list(scripts),
        "metrics": {block["preset"]: block for block in blocks},
    }


def write_quality_report(report: Dict[str, Any], out_dir: Any) -> Path:
    """写 report.json 到 out_dir（唯一一次写盘；失败前不留下任何文件）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def run_duplex_quality(
    config: DuplexQualityConfig,
    *,
    generated_at: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Path]]:
    """跑全部预设，返回 (报告字典, 已写出的文件路径列表)。

    负例纪律（不产半份报告）：
      - 空脚本 / 非法参数 / 包不存在 → 抛 DuplexQualityError，**不建目录、不写任何文件**；
      - 执行期失败（RuntimeMissError 等）→ 报告如实标 incomplete，仍写出（可复查）。
    """
    if not config.presets:
        raise DuplexQualityError(
            "presets 为空（0 条）——没有可评测的双工参数组合"
        )
    if not isinstance(config.seed, int) or isinstance(config.seed, bool):
        raise DuplexQualityError(
            f"seed 必须是整数，实际为 {config.seed!r}"
        )
    if config.num_actions < 1:
        raise DuplexQualityError(
            f"num_actions 必须是 >=1，实际为 {config.num_actions!r}（0 条动作=空脚本）"
        )

    # ① 构造 plan 形状（全命中 key，取自包内真实 key 集合）
    plan_keys, _terminal = _pack_plan_keys(config.pack, config.n_plans, config.plan_unit_count)

    # ② 各预设的终端键：确认语义预设按预设名给出显式终态键（写进报告，可复算）
    blocks: List[Dict[str, Any]] = []
    scripts: List[Dict[str, Any]] = []
    errors: List[str] = []
    warnings: List[str] = []
    written: List[Path] = []

    tmp = tempfile.TemporaryDirectory(prefix="vox-dq-")
    try:
        audio_dir = Path(tmp.name)
        for preset_name, overrides in config.presets:
            terminal_keys = _preset_terminal_keys(overrides)
            try:
                params = build_params(overrides, terminal_keys)
            except DuplexError as exc:
                # 非法参数：必须 fail-closed，不落盘、不写任何中间文件
                raise DuplexQualityError(
                    f"预设 {preset_name!r} 的双工参数非法（DuplexError 语义）: {exc}"
                ) from exc

            executor = Executor(
                config.pack, config.adapter,
                duplex=params, policy_stream=True,
            )

            # 先跑全部 plan，成功后才生成脚本并算指标——中途失败不落下半份产物
            plan_results: List[Tuple[str, Any]] = []
            preset_ok = True
            for p_idx, keys in enumerate(plan_keys):
                turn_id = f"dq-{preset_name}-{p_idx:02d}"
                bundle = _execute_one(
                    executor, [{"key": k, "rate": "normal"} for k in keys],
                    f"dq-{preset_name}", turn_id, audio_dir, preset_name,
                )
                if bundle.error:
                    errors.append(f"[{preset_name}] 执行失败: {bundle.error}")
                    preset_ok = False
                    continue
                for issue in bundle.event_issues:
                    errors.append(f"[{preset_name}] {issue}")
                    preset_ok = False
                plan_results.append((turn_id, bundle.result))

            if not preset_ok:
                continue

            # 时长轴：每 plan 一条单元时长序列（来自事件流 spoken_ms 的差值，取包内预铸时长）
            durations: List[List[int]] = []
            for _turn_id, result in plan_results:
                units = _unit_events(result.events)
                # spoken_ms 是累计值；单元时长 = 差值（首单元 = 自身）
                row: List[int] = []
                prev = 0
                for e in units:
                    cum = int(e[spec.SPOKEN_MS])
                    row.append(cum - prev)
                    prev = cum
                durations.append(row)

            actions = generate_scenario(
                config.seed, durations,
                num_actions=config.num_actions,
                max_units=len(plan_keys[0]),
                weights=config.unit_weights,
            )
            scripts.append({
                "seed": config.seed,
                "num_actions": len(actions),
                "plan_unit_count": config.plan_unit_count,
                "n_plans": len(plan_keys),
                "actions": [
                    {"plan_index": a.plan_index, "unit_index": a.unit_index,
                     "at_ms": a.at_ms, "action": a.action,
                     "want_blocked": a.want_blocked}
                    for a in actions
                ],
            })
            try:
                blocks.append(compute_metrics(
                    preset_name=preset_name, params=params,
                    plan_results=plan_results, actions=actions,
                ))
            except DuplexQualityError as exc:
                errors.append(f"[{preset_name}] {exc}")
                preset_ok = False

        # 落盘：先算好报告，最后一次写盘（前面失败 → 不建目录、不写文件）
        if not blocks:
            raise DuplexQualityError(
                f"全部预设执行失败（{len(errors)} 条错误），不产半份报告: "
                + "; ".join(errors[:3])
            )
        report = build_quality_report(
            config=config, blocks=blocks, scripts=scripts,
            errors=errors, warnings=warnings, generated_at=generated_at,
        )
        out_dir = Path(config.out_dir) if config.out_dir else Path(
            tempfile.mkdtemp(prefix="vox-dq-out-")
        )
        written.append(write_quality_report(report, out_dir))
        return report, written
    finally:
        shutil.rmtree(tmp.name, ignore_errors=True)
        tmp.cleanup()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析 CLI 参数（argparse 自带未知参数/类型错误的非零退出）。"""
    parser = argparse.ArgumentParser(
        prog="python3 -m eval.duplex_quality",
        description=(
            "双工质量评测 harness：只测'系统主动开口'侧，场景由固定种子自造，"
            "三指标（打断准确率 / 假阳性 / 轮转延迟）全部从 policy_stream 事件流算"
        ),
    )
    parser.add_argument("--pack", required=True, help="资产包根目录（含 manifest.json）")
    parser.add_argument("--out", required=True, help="输出目录（报告 + 音频）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--plan-unit-count", type=int, default=DEFAULT_UNIT_COUNT)
    parser.add_argument("--n-plans", type=int, default=2)
    parser.add_argument("--num-actions", type=int, default=DEFAULT_NUM_ACTIONS)
    parser.add_argument(
        "--preset", action="append", default=None,
        help="预设 '<name>:patience_ms=900,backchannel=on,barge_in=allow,rate_band=0.15'；"
             "缺省用内置三预设",
    )
    parser.add_argument(
        "--adapter", default=None,
        help="适配器 '模块:类名'；缺省用离线替身 OfflineTts",
    )
    parser.add_argument("--plan-id", default=DEFAULT_PLAN_ID)
    return parser.parse_args(argv)


def _command_string(args: argparse.Namespace) -> str:
    """重建可原样复现的完整命令行（所有参数显式写出）。"""
    parts = [
        "python3", "-m", "eval.duplex_quality",
        "--pack", str(args.pack), "--out", str(args.out),
        "--seed", str(args.seed),
        "--plan-unit-count", str(args.plan_unit_count),
        "--n-plans", str(args.n_plans),
        "--num-actions", str(args.num_actions),
    ]
    if args.adapter:
        parts += ["--adapter", args.adapter]
    for preset in (args.preset or []):
        parts += ["--preset", preset]
    return " ".join(shlex_quote(p) for p in parts)


def shlex_quote(text: str) -> str:
    """命令行引用（与 bench 同风格，避免空格破坏可复现命令）。"""
    import shlex
    return shlex.quote(text)


def _parse_presets(preset_args: Optional[Sequence[str]]) -> Tuple[Tuple[Tuple[str, Dict[str, Any]], ...]]:
    """解析 --preset 参数；未给出时回落内置三预设。"""
    if not preset_args:
        return DEFAULT_PRESETS
    presets: List[Tuple[str, Dict[str, Any]]] = []
    for raw in preset_args:
        if ":" not in raw:
            raise DuplexQualityError(
                f"--preset 必须是 '<name>:<k=v,k=v>' 形式，实际为 {raw!r}"
            )
        name, _, body = raw.partition(":")
        overrides: Dict[str, Any] = {}
        for item in body.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                raise DuplexQualityError(
                    f"--preset {raw!r} 中的 {item!r} 不是 key=value 形式"
                )
            key, _, value = item.partition("=")
            overrides[key.strip()] = _coerce(value.strip())
        if not overrides:
            raise DuplexQualityError(f"--preset {raw!r} 没有给出任何参数")
        presets.append((name.strip(), overrides))
    if not presets:
        raise DuplexQualityError("--preset 为空列表（0 条预设）")
    return tuple(presets)


def _coerce(value: str) -> Any:
    """把 'k=v' 里的值按字面量转成 bool / int / float / str。"""
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _resolve_adapter(spec: Optional[str], pack: Any) -> Any:
    """动态解析适配器；缺省用 OfflineTts（voice/model_version 取包里的值）。

    WHY：eval/ 的静态 import 里不得出现 adapters/（层边界），所以 CLI 侧用 importlib 动态加载。
    """
    if not spec:
        return OfflineTts(voice=pack.voice, model_version=pack.model_version)
    if ":" not in spec:
        raise DuplexQualityError(
            f"--adapter 必须是 '模块:类名' 形式，实际为 {spec!r}"
        )
    module_name, _, class_name = spec.partition(":")
    try:
        import importlib
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise DuplexQualityError(f"无法导入适配器模块 {module_name!r}: {exc}") from exc
    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise DuplexQualityError(
            f"模块 {module_name!r} 中没有类 {class_name!r}"
        ) from exc
    try:
        adapter = cls()
    except Exception as exc:
        raise DuplexQualityError(
            f"适配器 {spec!r} 无法默认实例化: {type(exc).__name__}: {exc}"
        ) from exc
    for attr in ("synthesize", "voice", "model_version"):
        if not hasattr(adapter, attr):
            raise DuplexQualityError(
                f"适配器 {spec!r} 缺成员 {attr!r}（不符 TTS 接口契约）"
            )
    return adapter


# 需要在 CLI 顶层拦住的异常（其余属实现缺陷，直接 traceback）
_CLI_ERRORS = (
    DuplexQualityError,
    DuplexError,
    AssetPackError,
    OSError,
    ValueError,
    TypeError,
    ImportError,
    AttributeError,
    RuntimeError,
)


def run_duplex_quality_cli(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口。

    退出码：
        0 = 报告已写出
        2 = 配置/参数错误，或全部预设执行失败（**不产生报告文件**）
    """
    args = _parse_args(argv)
    command = _command_string(args)

    try:
        pack = load_pack(args.pack)
    except AssetPackError as exc:
        print(f"错误: 资产包不存在或非法: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"错误: 资产包路径无法访问 {args.pack!r}: {exc}", file=sys.stderr)
        return 2

    try:
        presets = _parse_presets(args.preset)
        adapter = _resolve_adapter(args.adapter, pack)
        config = DuplexQualityConfig(
            pack=pack, adapter=adapter, presets=presets,
            seed=args.seed, plan_unit_count=args.plan_unit_count,
            n_plans=args.n_plans, num_actions=args.num_actions,
            out_dir=Path(args.out), command=command,
        )
        report, written = run_duplex_quality(config)
    except _CLI_ERRORS as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    if report["incomplete"]:
        for reason in report["incomplete_reasons"]:
            print(f"错误: {reason}", file=sys.stderr)
        print(f"报告已写入（不完整）: {written[0]}", file=sys.stderr)
        return 2

    for path in written:
        print(f"报告已写入: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(run_duplex_quality_cli())
