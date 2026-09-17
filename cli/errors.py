"""
cli.errors — 退出码常量 + CLI 层异常体系

职责：把 docs/08 §8.2 冻结的退出码语义落成常量，并提供「异常 → 退出码」的类型化载体。
不负责：不做任何判定（命中判定 / 四属性 / 质检阈值 / 统计口径一律在对应层实现，
       CLI 只调各层公开 API）。

设计红线（docs/08 §8.2，逐条照做）：
  1. 退出码语义冻结：0 成功 / 2 用法与参数 / 3 运行期失败 / 4 质检不通过 / 5 fail-closed 中止。
     脚本化调用依赖它，一字不能改。
  2. **2 与 3 必须分得开**：配置期（参数、路径、JSON 解析、源格式、未知适配器）→ 2；
     执行期（合成、写盘、内部缺陷 TypeError/AttributeError/ValueError…）→ 3，
     且 stderr **必带 `type(exc).__name__`**。
     WHY：把内部缺陷折叠成 2 会让人去查命令行参数，定位方向直接错掉——
          这是 T08 审计 #2 已登记、由本卡收掉的欠账。
  3. **不静默**：任何非零退出都要有 stderr 一行说明。4 / 5 属「预期内的业务结论」，
     措辞中性（「不通过」/「已按 fail-closed 中止」），不得当成「错误」糊弄过去，
     也不得因为它们是业务结论就吞掉。
  4. `--json` 时机器可读结果走 stdout（纯 JSON，无其他输出），人读摘要走 stderr；
     不带 `--json` 时摘要走 stdout——两个方向的 stdout 语义由 commands.emit_result 落地。
"""


# ---------------------------------------------------------------------------
# 1. 退出码常量（冻结）
# ---------------------------------------------------------------------------
EXIT_OK: int = 0              # 成功：命令完成且结论为「通过 / 已完成」
EXIT_USAGE: int = 2           # 用法或参数错误（配置期）
EXIT_RUNTIME: int = 3         # 运行期失败（执行期）——stderr 必带异常类型名
EXIT_QUALITY: int = 4         # 质检不通过（非错误，是业务结论）
EXIT_FAIL_CLOSED: int = 5     # fail-closed 中止（业务语义）


# ---------------------------------------------------------------------------
# 2. 异常体系（类型即退出码）
# ---------------------------------------------------------------------------
class CLIError(Exception):
    """CLI 层统一异常基类：类型自带退出码，main() 只做「捕获 → 打印 → 返回码」。

    属性：
        exit_code: 该异常对应的冻结退出码

    WHY 用异常而不是散落的 return：命令函数里判定分支很多，用异常可以把
        「这里错了」的语义钉在出错点，退出码归口到 main() 一处——
        不会出现某个分支忘了返回码、静默退成 0 的情况。
    """

    exit_code: int = EXIT_RUNTIME

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UsageError(CLIError):
    """用法或参数错误（退出码 2）——缺子命令、缺必填参数、路径不存在、
    JSON 解析失败、SourceError / ScriptError / AssetPackError / DuplexError、未知适配器。"""

    exit_code = EXIT_USAGE


class QualityFailure(CLIError):
    """质检不通过（退出码 4）——四属性有违规，或 prebake 报 clean=False。

    这是「预期内的业务结论」，不是错误：措辞必须是「不通过」，不得写成「失败/错误」。
    """

    exit_code = EXIT_QUALITY


class FailClosedError(CLIError):
    """fail-closed 中止（退出码 5）——run 出现未命中/降级而 allow_fallback 未开启。

    WHY 单列一类：它是 runtime.RuntimeMissError 的业务语义映射，
        与「运行期崩溃（3）」性质完全不同——plan 被业务规则拦下，不是程序坏了。
    """

    exit_code = EXIT_FAIL_CLOSED


# ---------------------------------------------------------------------------
# 3. 输出纪律（docs/08 §8.2）
# ---------------------------------------------------------------------------
def describe(exit_code: int) -> str:
    """返回退出码对应的中文结论短语（stderr 前缀用，措辞按 §8.2 保持中性）。"""
    if exit_code == EXIT_OK:
        return "通过"
    if exit_code == EXIT_USAGE:
        return "用法或参数错误"
    if exit_code == EXIT_RUNTIME:
        return "运行期失败"
    if exit_code == EXIT_QUALITY:
        return "不通过"
    if exit_code == EXIT_FAIL_CLOSED:
        return "已按 fail-closed 中止"
    return f"未知退出码 {exit_code}"


def format_runtime_line(exc: BaseException, *, detail: str = "") -> str:
    """运行期失败的 stderr 说明行——**必带 `type(exc).__name__`**（docs/08 §8.2）。

    参数：
        exc:    异常实例
        detail: 调用方补充的上下文（如 key / reason / 路径），可为空字符串

    返回：
        单行字符串，形如 `TypeError: adapter 缺少必需成员: ['synthesize']`

    WHY「3 必须打印异常类型名」：调用者只看到类型名就知道该去看哪一层
        （TypeError/AttributeError → 本层或适配器契约；AudioError → runtime/audio）。
        只给消息不给类型，会让人去猜——正是本卡要消灭的返工。
    """
    if detail:
        return f"{type(exc).__name__}: {detail}"
    return f"{type(exc).__name__}: {exc}"
