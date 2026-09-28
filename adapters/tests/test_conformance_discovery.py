"""
adapters.tests.test_conformance_discovery — tts-* 适配器的**自动发现**（T62）

本模块取代 test_conformance.py 里的手写白名单。三条硬约束：

  1. **零第三方依赖**：只用标准库（`pathlib` / `importlib` / `sys`）。
  2. **扫不到即报错**：`ADAPTERS_DIR` 下没有任何 tts 适配器目录时 `discover_adapters`
     直接抛 `RuntimeError`，绝不让发现结果为空列表——空套件比没有套件更坏，
     因为它会给「所有适配器都合规」一个毫无依据的绿灯（docs/13 的老教训）。
  3. **只收 `tts_*/` 且必须有 `adapter.py`**：`textmatch` / `mcp_vox` / `framework_kefu`
     以及 `asr_*/` 都不是 TTS 适配器，前缀匹配就把它们挡在外面；`adapter.py` 缺失的
     `tts_*/` 目录不算适配器（否则一个还没写完的空目录会让整套件炸掉）。

两条导入路径都支持（这是两个既有适配器写出来的**事实**，不是偏好）：
  - `adapters.tts_macsay.adapter` —— 类在 `adapter.py` 里；
  - `adapters.tts_omlx`          —— 类由包级 `__init__` 导出，`adapter.py` 里没有同名符号。
定位类**不按名字**（按名字找就得为这两种布局各写一条分叉），
而是按「成员齐备」这个两者都满足的形态约束找，见 `_find_adapter_class`。

asr 覆盖缺口显式留痕：发现 `asr_*/` 但没有对应套件时打**一行 stderr**，
不假装覆盖了（`adapters/` 目前确实有 `asr_omlx` 而没有任何 ASR 一致性测试）。
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

from typing import Any, List, Tuple

# adapters/ 目录（本文件在 adapters/tests/ 下，parents[1] 就是 adapters/）
ADAPTERS_DIR: Path = Path(__file__).resolve().parents[1]

# TTS 适配器的目录前缀。前缀匹配是刻意的：
# `textmatch` / `mcp_vox` / `framework_kefu` / `asr_omlx` 都不带 `tts_`，天然被排除。
TTS_PREFIX: str = "tts_"
ASR_PREFIX: str = "asr_"

# 适配器实现所在的模块文件。缺失即不算适配器（见模块 docstring 第 3 条）。
ADAPTER_MODULE: str = "adapter.py"

# 一个 TTS 适配器类**必须**具备的成员。少一个就不是适配器——
# 发现阶段就报「缺少成员」，而不是让它溜进套件后以 AttributeError 的方式失败
# （那种失败在 subTest 报告里只有一串 traceback，读不出「这个目录不是适配器」）。
REQUIRED_MEMBERS: Tuple[str, ...] = ("name", "rate_map", "rate_value")

# asr 覆盖缺口提示：进程内只打一次。测试会重复调用 discover_adapters，
# 每次调用都打一行会把测试输出淹没成噪声。
_ASR_HINT_PRINTED = False


def _warn_asr_gap(asr_dirs: List[str]) -> None:
    """发现 asr_* 但没有任何 ASR 一致性套件 → 打一行 stderr，不假装覆盖了。"""
    global _ASR_HINT_PRINTED
    if not asr_dirs or _ASR_HINT_PRINTED:
        return
    _ASR_HINT_PRINTED = True
    print(
        "[conformance] 发现 ASR 适配器目录 "
        + "、".join(asr_dirs)
        + "，但没有对应的 ASR 一致性套件——本套件**不覆盖** ASR 适配器，"
          "请勿按「适配器全部覆盖」来解读本套件结果。",
        file=sys.stderr,
    )


def _find_adapter_class(module: Any) -> Any:
    """在适配器模块里定位适配器类。

    判据只有一条：**模块命名空间里恰好有一个类同时具备 `REQUIRED_MEMBERS` 三者**。
    不依赖类名、不依赖模块名、不依赖 `__all__`——因为两个既有适配器把类放在不同位置
    （`tts_macsay` 放在 `adapter.py`，`tts_omlx` 由包级 `__init__` 导出），
    按名字找必然要写分叉；按「成员齐备」找是这两个适配器**都满足**的形态约束
    （`adapters/AGENTS.md §②` 要求声明 `name` / `rate_map`），再加 `rate_value`。

    两个失败方向都响亮抛错，不猜：
      零个  → 该目录不是适配器（或模块根本没导出类）；
      两个以上 → 有辅助类冒充适配器，挑一个都是静默降级。
    """
    found: List[Any] = [
        obj for obj in vars(module).values()
        if isinstance(obj, type) and _has_required_members(obj)
    ]
    if not found:
        defined = [k for k, v in vars(module).items() if isinstance(v, type)]
        raise RuntimeError(
            f"在 {module.__name__} 中找不到 TTS 适配器类"
            f"（该模块定义的类 {defined} 都没有 {REQUIRED_MEMBERS} 三者齐备）"
        )
    if len(found) > 1:
        names = ", ".join(c.__name__ for c in found)
        raise RuntimeError(
            f"{module.__name__} 中有 {len(found)} 个类同时具备 {REQUIRED_MEMBERS}"
            f"（{names}）——无法判定哪个是适配器，拒绝猜（猜错就是静默降级）"
        )
    return found[0]


def _has_required_members(obj: Any) -> bool:
    """obj 是否具备 `REQUIRED_MEMBERS` 三者。"""
    return all(hasattr(obj, member) for member in REQUIRED_MEMBERS)


def tts_adapter_dirs(base: Path | None = None) -> List[str]:
    """列出 `base`（缺省 `ADAPTERS_DIR`）下所有 tts 适配器目录名，排序后返回。

    「是 tts 适配器」= 目录名以 `tts_` 开头**且**含 `adapter.py`。
    前缀过滤天然排除 `textmatch` / `mcp_vox` / `framework_kefu` 与 `asr_*/`；
    `adapter.py` 的要求把「还在写、没写完的空目录」挡在外面——
    一个半成品目录不该让整套件炸掉，更不该被当成已交付的适配器。
    `__pycache__` 等目录名不以 `tts_` 开头，天然不在扫描结果里。
    """
    root = ADAPTERS_DIR if base is None else base
    if not root.is_dir():
        return []
    return sorted(
        path.name for path in root.iterdir()
        if path.is_dir() and path.name.startswith(TTS_PREFIX) and (path / ADAPTER_MODULE).is_file()
    )


def _dir_of(cls: Any) -> str:
    """从适配器类反查它所在的适配器目录名（`adapters.<dir>` 的那一段）。

    `cls.__module__` 对两个既有适配器分别是
    `adapters.tts_macsay.adapter` 与 `adapters.tts_omlx.adapter`
    （后者由包级 `__init__` 导出，但类**定义**仍在 adapter.py 里），
    取 `<pkg>.adapter` 的 `<pkg>` 再取最后一段即得目录名。
    """
    module = getattr(cls, "__module__", "") or ""
    package = module.rsplit(".", 1)[0]
    return package.rsplit(".", 1)[-1]


def adapter_error_type(target: Any) -> type:
    """返回该适配器**自己声明**的 `TtsError` 类（必须是 RuntimeError 子类）。

    `target` 可以传**类或实例**（套件里的 subTest 循环拿的是实例）——
    取 `__module__` 时两者等价，但**不要**在报错信息里用 `target.__name__`：
    实例没有 `__name__`，会抛 AttributeError 把真正的「找不到 TtsError」
    这条错误信息盖掉（实测：AC2 的假适配器就是这样把报错变成了 AttributeError）。

    为什么不能在整个套件里 import 一个共享的 TtsError：
    本仓两个适配器各自定义了自己的 TtsError（`tts_macsay/adapter.py` 与
    `tts_omlx/transport.py`，两者**不是**同一个类），而 `core/` 没有统一的
    TTS 异常。若套件只 import 其中一个，另一个适配器抛自己的 TtsError 时
    `assertRaises` 就永远匹配不上——套件要么假失败、要么被迫把适配器改成
    抛别人的异常类。后者会改适配器对外行为（T62 明令禁止），
    所以这里按「每个适配器用它自己的异常类」来判，判据不放宽：
    仍必须是 RuntimeError 的子类（fail-closed，不许静默返回）。

    找不到即抛错：适配器的模块里没有 TtsError 说明异常契约不存在，
    这不是「跳过这个断言」能糊过去的。
    """
    name = getattr(target, "__name__", None) or target.__class__.__name__
    module_name = getattr(target, "__module__", "") or ""
    for candidate in (module_name, module_name.rsplit(".", 1)[0] + ".adapter",
                      module_name.rsplit(".", 1)[0]):
        module = sys.modules.get(candidate)
        err = getattr(module, "TtsError", None) if module is not None else None
        if isinstance(err, type) and issubclass(err, RuntimeError):
            return err
    raise RuntimeError(
        f"{name} 所在模块 {module_name!r} 里找不到 RuntimeError 子类的 TtsError"
        "——异常契约不存在，无法判定「失败是否抛错」"
    )


def discover_adapters() -> List[Any]:
    """扫 `adapters/tts_*/adapter.py`，返回适配器类列表（按目录名排序，结果稳定）。

    抛 RuntimeError 的情形：没有任何 tts 适配器目录（空套件是假绿灯）；
    或某个 tts 适配器的模块导入失败 / 找不到适配器类。
    这些**都必须**失败而不是跳过——静默跳过等于让套件对新增适配器零覆盖。
    """
    if not ADAPTERS_DIR.is_dir():
        raise RuntimeError(f"adapters 目录不存在: {ADAPTERS_DIR}")

    tts_dirs = tts_adapter_dirs()
    asr_dirs = sorted(
        path.name for path in ADAPTERS_DIR.iterdir()
        if path.is_dir() and path.name.startswith(ASR_PREFIX)
    )
    _warn_asr_gap(asr_dirs)

    if not tts_dirs:
        raise RuntimeError(
            f"在 {ADAPTERS_DIR} 下没有发现任何 tts 适配器"
            f"（目录需匹配 {TTS_PREFIX}*/ 且含 {ADAPTER_MODULE}）——"
            "拒绝按 0 个适配器跑一致性测试（空套件会给出毫无依据的绿灯）"
        )

    classes: List[Any] = []
    for directory in tts_dirs:
        package = f"adapters.{directory}"
        module_path = f"{package}.adapter"
        try:
            # 先按 `<pkg>.adapter` 导入（类定义在 adapter.py 里时走这条）；
            # import_module 会顺带把父包 `adapters.<pkg>` 放进 sys.modules，
            # 所以 adapter.py 里的 `from . import postprocess` 才解析得通。
            try:
                module = importlib.import_module(module_path)
            except ModuleNotFoundError:
                # `adapter.py` 不存在（类只在包级 `__init__` 里导出）→ 包级导入。
                module = importlib.import_module(package)
            classes.append(_find_adapter_class(module))
        except RuntimeError:
            # 已经是本模块包好的、带目录名的报错，原样上抛保留目录名。
            raise
        except Exception as exc:  # noqa: BLE001 - 导入失败必须响亮，不允许跳过
            # ModuleNotFoundError 也会由第三方依赖缺失触发，同样在这里报错：
            # 跳过就等于让套件对这个适配器零覆盖，正是 T62 要消除的失效方式。
            raise RuntimeError(
                f"{directory}: {module_path} 导入失败"
                f"（{type(exc).__name__}: {exc}）"
            ) from exc
    return classes


def assert_all_adapters_collected(collected: List[Any], *, adapters_dir: Path | None = None) -> None:
    """CI 关卡：目录里 tts 适配器数量必须 == 套件实际收集到的类数量，不等即失败。

    判据是**数量相等 + 目录名一一对应**（比单看数量更严：两个都等于 2 但指向不同
    适配器也算不等）。缺失的目录名逐个点名——本项目门禁的老教训是自撰文本用
    grep 判定、逐字引文不用，故目录名排序后以「、」连接，便于人在输出里 grep。

    这道关卡防的是「新增 tts_* 目录却漏进套件」：自动发现正常情况下两者恒等，
    所以本关卡看起来多余——但一旦有人把 `ADAPTER_CLASSES` 退回手写白名单，
    数量一不一致它就转红，而不会让新适配器继续「在白名单外、对套件零覆盖」
    （T62 的头号缺口正是这个）。
    """
    on_disk = tts_adapter_dirs(adapters_dir)
    missing = [name for name in on_disk if name not in {_dir_of(cls) for cls in collected}]

    if len(collected) != len(on_disk) or missing:
        raise AssertionError(
            f"TTS 适配器覆盖不全：目录里有 {len(on_disk)} 个"
            f"（{'、'.join(on_disk)}），套件实际收集到 {len(collected)} 个"
            f"（{'、'.join(sorted(_dir_of(c) for c in collected))}）"
            + (f"——未进入套件：{'、'.join(missing)}" if missing else "")
            + "；新增 tts_* 适配器必须进一致性套件，否则判红"
        )


class TestAdapterDiscoveryGate(unittest.TestCase):
    """CI 关卡：新增 tts-* 适配器却漏进套件 → 判红（T62 §二.C）。

    这里刻意**不**在模块顶部 `from ...test_conformance import ADAPTER_CLASSES`——
    `test_conformance` 在模块顶部反向导入本模块，顶部互导会形成循环导入，
    其中一个模块会拿到对方半初始化的命名空间（`discover_adapters` 还没定义），
    表现为一个只在某些发现顺序下复现的假失败。
    因此 `ADAPTER_CLASSES` 在测试方法体内按需导入：测的是**套件真注册表**，
    而不是「再跑一遍发现」——后者即使有人把套件退回手写白名单也会照常通过，
    那道关卡就只剩装饰了。
    """

    def test_discovery_is_not_empty(self):
        """发现结果必须非空：0 个适配器说明扫描没生效，不是「适配器等会儿来」。"""
        classes = discover_adapters()
        self.assertGreater(
            len(classes), 0,
            "discover_adapters() 返回 0 个适配器——目录扫描没生效，"
            "一致性套件会给出毫无依据的绿灯",
        )

    def test_every_directory_is_in_the_suite(self):
        """目录里的每个 tts_* 适配器都必须在套件的真注册表里。"""
        from adapters.tests.test_conformance import ADAPTER_CLASSES

        assert_all_adapters_collected(ADAPTER_CLASSES)

    def test_gate_fails_when_collected_is_short(self):
        """负例：证明这道关卡**有牙齿**——收集到的比目录里少时它必须转红。

        门禁若永远绿就等于没有；本用例把「漏掉一个适配器」的情形喂给关卡，
        要求它点名缺失的目录名。
        """
        classes = discover_adapters()
        if len(classes) < 2:
            self.fail(
                f"本负例需要至少 2 个适配器才能模拟「漏掉一个」，实际 {len(classes)}"
                f"（{'、'.join(_dir_of(c) for c in classes)}）——负例构造条件不成立"
            )
        dropped = classes[0]
        short = classes[1:]
        with self.assertRaises(AssertionError) as ctx:
            assert_all_adapters_collected(short)
        self.assertIn(
            _dir_of(dropped), str(ctx.exception),
            "关卡报错时必须点名被漏掉的适配器目录名，否则人没法定位该补谁",
        )

    def test_gate_ignores_non_tts_directories(self):
        """`textmatch` / `mcp_vox` / `framework_kefu` / `asr_*` 不得被当成 tts 适配器。

        误收会让套件对非 TTS 模块跑 TTS 断言，炸的方式和「适配器缺成员」一样，
        排查时看不出是扫描越界——所以这条要单独断言，而不是靠观察。
        """
        names = tts_adapter_dirs()
        for name in names:
            self.assertTrue(
                name.startswith(TTS_PREFIX),
                f"{name} 被当成 tts 适配器收进来了——扫描前缀越界",
            )
            self.assertNotIn(
                name, ("textmatch", "mcp_vox", "framework_kefu", "asr_omlx"),
                f"{name} 不是 TTS 适配器，不该出现在 {names!r} 里",
            )


def reset_asr_hint_for_test() -> None:
    """测试用：复位 asr 提示的「只打一次」开关（生产代码不调用它）。"""
    global _ASR_HINT_PRINTED
    _ASR_HINT_PRINTED = False


if __name__ == "__main__":
    unittest.main()
