"""
cli.commands — 三个命令共享的底座：适配器动态解析 + --out 越界拦截 + 结果打印

职责：给 pack_check / pack_build / run 提供「解析 → 调用 → 打印」的公共工具，
     让三个命令文件保持薄壳（每行都在解析参数或调各层公开 API）。
不负责：不做任何判定——本模块内不出现命中判定、四属性、质检阈值、统计口径。

两条关键纪律（本卡红线）：
  1. **适配器只能动态解析**（importlib，格式 `<模块>:<类名>`，缺省
     `adapters.tts_macsay:MacSayTts`），本包内**不得静态 import adapters**。
     WHY：`adapters/` 是扩展区（新增引擎不该改内核与 CLI）；静态 import 会把 CLI
          与某个具体引擎焊死。解析失败一律 → 退出码 2 且**绝不回落**——
          回落成默认引擎就是「本该用 A 引擎却换了 B」的静默降级，事后无法定位。
  2. **--out 越界拦截**：产物不许写进源目录 / 包目录（那是业务源与冻结区），
     判据是**解析后的绝对路径**是否为对方的子路径（含 `..` 逃逸）。
"""

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from cli.errors import UsageError


# ---------------------------------------------------------------------------
# 1. 适配器动态解析（缺省值冻结在 docs/08 §8.3）
# ---------------------------------------------------------------------------
# 缺省适配器：只有它一个实现，但**写在这里而不是 import 出来**——
# 保持「CLI 不知道任何具体引擎」的边界。
DEFAULT_ADAPTER_SPEC: str = "adapters.tts_macsay:MacSayTts"


def resolve_adapter(spec: Optional[str] = None) -> Any:
    """动态解析并实例化一个 TTS 适配器。

    参数：
        spec: `<模块>:<类名>`；None 或缺省时取 DEFAULT_ADAPTER_SPEC

    返回：
        适配器实例（供 compiler.prebake / runtime.Executor 使用）

    异常：
        UsageError: 格式非法 / 模块导入失败 / 类不存在 / 不是类 / 实例化失败——
                    一律退出码 2，**不回落**到默认适配器。
    """
    if not spec:
        spec = DEFAULT_ADAPTER_SPEC

    if not isinstance(spec, str) or ":" not in spec:
        raise UsageError(
            f"--adapter 格式必须是 <模块>:<类名>（如 {DEFAULT_ADAPTER_SPEC}），"
            f"实际值为 {spec!r}"
        )

    module_name, _, cls_name = spec.partition(":")
    module_name = module_name.strip()
    cls_name = cls_name.strip()
    if not module_name or not cls_name:
        raise UsageError(
            f"--adapter 的模块名或类名不能为空（格式 <模块>:<类名>），实际值为 {spec!r}"
        )

    # WHY 用 importlib 而不是静态 import：adapters 是扩展区，CLI 不得与具体引擎耦合。
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # ModuleNotFoundError / ImportError / 适配器自身装载错
        raise UsageError(
            f"无法导入适配器模块 {module_name!r}: {type(exc).__name__}: {exc}"
        ) from exc

    if not hasattr(module, cls_name):
        raise UsageError(
            f"适配器模块 {module_name!r} 中不存在类 {cls_name!r}（未知适配器）"
        )

    cls = getattr(module, cls_name)
    if not isinstance(cls, type):
        raise UsageError(
            f"--adapter 指向的 {module_name}:{cls_name} 不是类，"
            f"实际类型为 {type(cls).__name__}"
        )

    try:
        return cls()
    except Exception as exc:
        # 不回落：解析成功但实例化失败同样是参数问题（写错类名/构造需要参数）
        raise UsageError(
            f"无法实例化适配器 {spec!r}: {type(exc).__name__}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# 2. 路径与 --out 越界拦截
# ---------------------------------------------------------------------------
def assert_dir(path: Union[str, Path], *, label: str) -> Path:
    """断言路径存在且是目录，否则抛 UsageError（退出码 2）。"""
    p = Path(path)
    if not p.exists():
        raise UsageError(f"{label}不存在: {p}")
    if not p.is_dir():
        raise UsageError(f"{label}不是目录: {p}（实际是文件）")
    return p


def assert_not_within(
    target: Union[str, Path],
    parent: Union[str, Path],
    *,
    what: str,
    parent_label: str,
) -> Path:
    """断言 `target` 不落在 `parent` 目录内（含 `..` 逃逸），否则抛 UsageError（2）。

    参数：
        target:      产物路径（--out 的值）
        parent:      被保护目录（源包目录 / --pack 包目录）
        what:        产物的人类可读描述（用于错误信息）
        parent_label: 被保护目录的描述（用于错误信息）

    返回：
        target 的 Path 形式（供调用方继续使用）

    WHY 用「解析后的绝对路径」而不是字符串前缀比较：字符串比较会被
        `../` 逃逸绕过（`packs/repair/sub` 与 `packs/repair-evil` 都会误判），
        resolve() 之后做子路径判定才是可靠的。这是本卡登记的第 2 笔欠账。
    """
    parent_abs = Path(parent).resolve()
    target_abs = Path(target).resolve()
    if target_abs == parent_abs or parent_abs in target_abs.parents:
        raise UsageError(
            f"{what}不能落在{parent_label}内: {target}"
            f"（解析后为 {target_abs}）——产物不得写进源/包目录"
        )
    return Path(target)


def assert_not_exists_as_dir(path: Union[str, Path], *, label: str) -> None:
    """断言路径不存在、或存在但不是目录（供 --out <wav> 使用）。"""
    p = Path(path)
    if p.is_dir():
        raise UsageError(f"{label}不能是已存在的目录: {p}")


# ---------------------------------------------------------------------------
# 3. JSON 读取（失败 → 2，绝不静默）
# ---------------------------------------------------------------------------
def read_json_file(path: Union[str, Path], *, label: str) -> Any:
    """读取并解析一个 JSON 文件，失败抛 UsageError（退出码 2）。"""
    p = Path(path)
    if not p.exists():
        raise UsageError(f"{label}不存在: {p}")
    if p.is_dir():
        raise UsageError(f"{label}不是文件: {p}（实际是目录）")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except UnicodeDecodeError as exc:
        raise UsageError(f"{label}编码错误（非 UTF-8）: {p} ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise UsageError(f"{label}不是合法 JSON: {p} ({exc})") from exc
    except OSError as exc:
        raise UsageError(f"{label}读取失败: {p} ({type(exc).__name__}: {exc})") from exc


def parse_json_arg(text: str, *, label: str) -> Any:
    """解析命令行里直接给出的 JSON 字符串（如 --duplex）。"""
    if not isinstance(text, str) or not text.strip():
        raise UsageError(f"{label}不能为空字符串，实际值为 {text!r}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"{label}不是合法 JSON: {text!r} ({exc})") from exc


# ---------------------------------------------------------------------------
# 4. 结果打印（docs/08 §8.2：--json 的 stdout/stderr 分工）
# ---------------------------------------------------------------------------
def emit_result(payload: Dict[str, Any], *, as_json: bool, summary: str) -> None:
    """打印命令结果，严格按 --json 切换 stdout / stderr 语义。

    参数：
        payload: 机器可读结果（字段名照 docs/08 §8.3，不得改名）
        as_json: True 时 stdout 只写纯 JSON（验收方直接 json.loads(stdout)）
        summary: 人读摘要（一行）

    WHY 严格分工：`--json` 时 stdout 混进一句人读摘要会让 `json.loads(stdout)`
        直接炸，脚本化调用全部失效——所以 JSON 与人读输出**互斥**，不共存于同一流。
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if as_json:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        sys.stderr.write(summary + "\n")
        sys.stderr.flush()
    else:
        sys.stdout.write(summary + "\n")
        sys.stdout.flush()
        sys.stderr.write(text + "\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# 5. 违规序列化（四属性判据结果 → JSON）
# ---------------------------------------------------------------------------
def violations_to_dicts(violations: Tuple[Any, ...]) -> list:
    """把 compiler.Violation 元组转成 docs/08 §8.3 规定的字典列表。

    字段名冻结：code / unit_index / key / message（一字不改）。
    """
    from dataclasses import asdict

    return [asdict(v) for v in violations]
