"""
runtime.duplex — 双工参数与校验

职责：定义执行器可调的双工参数（耐心窗 / 语速收敛带 / 段间静音垫 / 槽位微停顿 / 段级淡入淡出），
      并在构造时（__post_init__）立即校验。
不负责：不做播放与合成，不做命中判定，不解析 ASR（rate_band 只作为参数透传给上游）。

参数口径来源：docs/06 §6.2.4（双工参数）与 §6.2.7 ④（fade_ms）。
设计红线：非法取值一律抛 DuplexError，绝不静默回落成默认值——
    回落会让"本该走慢思考耐心窗却跑了默认值"这种事事后无法定位。
"""

from dataclasses import dataclass

# 端点判断耐心窗三档（400 快语速 / 900 默认 / 1800 慢思考）
VALID_PATIENCE_MS = frozenset({400, 900, 1800})

# 附和开关与打断策略（本轮只做开关，不自造"密度"数值）
VALID_BACKCHANNEL = frozenset({"on", "off"})
VALID_BARGE_IN = frozenset({"allow", "confirm"})

# 段间静音垫与槽位微停顿的合法区间（闭区间，单位 ms）
SILENCE_PAD_MS_RANGE = (120, 300)
SLOT_PAD_MS_RANGE = (50, 150)

# 语速档位间的相对速率差（用于"档位差是否落在收敛带内"的判定）
# WHY 用相邻档差而不是速率比值：rate_band 是收敛带（±比例），判的是"两档之间的
# 跨度"是否超带。相邻档差取 0.20（相邻档差 1/0.85 - 1 ≈ 17.6%，四舍五入取 0.20
# 作为文档口径），跨两档即 0.40。于是：
#   · 默认 ±15% 带宽 < 0.20 → 关键信息缺 slow 档时判"超带"，fail-closed；
#   · 业务把 rate_band 放宽到 ≥0.20 → 允许回落（带内）并留痕。
# 若用速率比值（normal/slow - 1 ≈ 0.176），默认 0.15 会把"相邻档差"判成超带，
# 而 ±15% 的语义是"容许档位抖动"，两种口径结论相反——这里取相邻档差口径。
# 该表是 RATE_VALUES 的替代表述（相邻档差已足以定序与定距），不额外引入速率值。
RATE_ADJACENT_GAP: float = 0.20
_RATE_ORDER = ("slow", "normal", "fast")


def rate_distance(rate_a, rate_b) -> float:
    """两档之间的相对速率差（每跨一个相邻档 +0.20）。

    参数：
        rate_a: 档位名（slow / normal / fast）
        rate_b: 档位名（slow / normal / fast）

    返回：
        非负的比例差；同档为 0，相邻档 0.20，跨两档 0.40。

    异常：
        DuplexError: 档位名不在合法档位中（消息含档位名）
    """
    for name in (rate_a, rate_b):
        if name not in _RATE_ORDER:
            raise DuplexError(
                f"rate_band 判据无法比对未知语速档位 {name!r}，"
                f"只支持 {list(_RATE_ORDER)}"
            )
    return abs(_RATE_ORDER.index(rate_a) - _RATE_ORDER.index(rate_b)) * RATE_ADJACENT_GAP


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class DuplexError(Exception):
    """双工参数非法。

    消息必须包含导致失败的字段名与实际值，不允许吞掉上下文——
    否则上层无法判断是配置写错还是被静默改写。
    """


# ---------------------------------------------------------------------------
# 参数对象
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DuplexParams:
    """双工参数。

    属性：
        patience_ms:    端点判断耐心窗，只能取 400 / 900 / 1800
        rate_band:      语速收敛带 ±比例（默认 0.15 = ±15%）
        backchannel:    附和开关 on / off
        barge_in:       打断策略 allow / confirm
        silence_pad_ms: 单元之间句间静音垫，120–300
        slot_pad_ms:    槽值前后微停顿，50–150
        fade_ms:        段级线性淡入淡出，>= 0（docs/06 §6.2.7 ④）
        terminal_keys:  终态话术键集合（T19 只增，默认空）——barge_in=confirm 时，
                        命中这些 key 的单元被视为终态（需确认才可打断）；
                        集合为空时退化为"plan 最后一个单元"
    """

    patience_ms: int = 900
    rate_band: float = 0.15
    backchannel: str = "on"
    barge_in: str = "allow"
    silence_pad_ms: int = 200
    slot_pad_ms: int = 80
    fade_ms: int = 5
    terminal_keys: frozenset = frozenset()

    def __post_init__(self) -> None:
        # WHY：冻结对象 + 构造即校验，非法参数在任何一次 execute() 之前就被拦下，
        #      而不是等到运行中悄悄按默认值跑。
        # terminal_keys 归一成 frozenset：调用方传集合/列表都能用，
        # 而 dataclass frozen 只冻引用不冻内容，必须在此收口。
        if self.terminal_keys is not None and not isinstance(self.terminal_keys, frozenset):
            try:
                object.__setattr__(self, "terminal_keys", frozenset(self.terminal_keys))
            except TypeError:
                object.__setattr__(self, "terminal_keys", frozenset())
        self.validate()

    def validate(self) -> "DuplexParams":
        """校验全部字段，通过则返回 self（便于链式使用与测试直接调用）。

        异常：
            DuplexError: 任一字段非法，消息含字段名与实际值
        """
        # 耐心窗：封闭三档，不接受插值
        if not self._is_int(self.patience_ms) or self.patience_ms not in VALID_PATIENCE_MS:
            raise DuplexError(
                f"patience_ms 必须是 {sorted(VALID_PATIENCE_MS)} 之一，"
                f"实际值为 {self.patience_ms!r}"
            )

        # 语速收敛带：比例值，(0, 1]
        if (
            not isinstance(self.rate_band, (int, float))
            or isinstance(self.rate_band, bool)
            or not (0 < self.rate_band <= 1)
        ):
            raise DuplexError(
                f"rate_band 必须落在 (0, 1] 区间，实际值为 {self.rate_band!r}"
            )

        # 两个枚举开关：只认白名单
        if self.backchannel not in VALID_BACKCHANNEL:
            raise DuplexError(
                f"backchannel 必须是 {'/'.join(sorted(VALID_BACKCHANNEL))} 之一，"
                f"实际值为 {self.backchannel!r}"
            )
        if self.barge_in not in VALID_BARGE_IN:
            raise DuplexError(
                f"barge_in 必须是 {'/'.join(sorted(VALID_BARGE_IN))} 之一，"
                f"实际值为 {self.barge_in!r}"
            )

        # 两个区间参数
        low, high = SILENCE_PAD_MS_RANGE
        if not self._is_int(self.silence_pad_ms) or not (low <= self.silence_pad_ms <= high):
            raise DuplexError(
                f"silence_pad_ms 必须落在 {low}–{high} ms，实际值为 {self.silence_pad_ms!r}"
            )
        low, high = SLOT_PAD_MS_RANGE
        if not self._is_int(self.slot_pad_ms) or not (low <= self.slot_pad_ms <= high):
            raise DuplexError(
                f"slot_pad_ms 必须落在 {low}–{high} ms，实际值为 {self.slot_pad_ms!r}"
            )

        # 淡入淡出：允许 0（关闭），不允许负数
        if not self._is_int(self.fade_ms) or self.fade_ms < 0:
            raise DuplexError(
                f"fade_ms 必须是非负整数，实际值为 {self.fade_ms!r}"
            )

        # 终态键集合：只允许非空字符串 key（空字符串会误标所有匿名单元）
        if self.terminal_keys is not None:
            if not isinstance(self.terminal_keys, frozenset):
                raise DuplexError(
                    f"terminal_keys 必须是 frozenset（或可转 frozenset 的集合），"
                    f"实际类型为 {type(self.terminal_keys).__name__}"
                )
            bad = [k for k in self.terminal_keys if not isinstance(k, str) or not k]
            if bad:
                raise DuplexError(
                    f"terminal_keys 必须只含非空字符串，实际含 {bad!r}"
                )

        return self

    @staticmethod
    def _is_int(value) -> bool:
        """判断是否为真整数（bool 是 int 的子类，必须排除）。"""
        return isinstance(value, int) and not isinstance(value, bool)

    @classmethod
    def fast(cls) -> "DuplexParams":
        """快语速预设：耐心窗 400ms，其余字段取默认值。"""
        return cls(patience_ms=400)

    @classmethod
    def default(cls) -> "DuplexParams":
        """默认预设：耐心窗 900ms，其余字段取默认值。"""
        return cls(patience_ms=900)

    @classmethod
    def slow_thinking(cls) -> "DuplexParams":
        """慢思考预设：耐心窗 1800ms，其余字段取默认值。"""
        return cls(patience_ms=1800)
