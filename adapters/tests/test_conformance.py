"""
adapters.tests.test_conformance — TTS 适配器接口一致性测试（跨适配器复用）

覆盖范围（同一套断言跑所有 tts-* 适配器）：
  - 接口属性完整性：name / model_version / requires_core / voice / rate_map
  - rate_map 包含 slow/normal/fast 三档
  - rate_value 返回有限正数，且 slow < normal < fast（量纲无关，见 AC4 注释）
  - synthesize 空文本抛 TtsError
  - 有 say 时真实合成 WAV 格式校验（T77：逐适配器再问一次「现在能不能真合成」，
    适配器实现可选类方法 `probe_availability()` 且报不可用 → 该用例 skip；
    不实现 = 永远可用，仍真跑）

扩展方式（T62，2026-09-28 起）：**新增 tts-* 适配器不需要改这个文件**。
`ADAPTER_CLASSES` 由 `test_conformance_discovery.discover_adapters()` 扫
`adapters/tts_*/` 自动收集（零第三方依赖，pathlib + importlib）；
扫不到任何适配器时导入本模块即失败（空套件是毫无依据的绿灯，必须报错）。
「新增适配器却漏进套件」有第二道关卡兜底，见
`test_conformance_discovery.TestAdapterDiscoveryGate`。
"""

import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from adapters.tests.test_conformance_discovery import adapter_error_type, discover_adapters


# ============================================================
# 待测适配器类（T62：由目录扫描自动发现，不是手写字面量）
#
# 刻意保留 `ADAPTER_CLASSES` 这个名字：全套件的参数化都靠它，
# 换成 `discover_adapters()` 调用只是把「内容」自动化，不改调用方。
# 模块导入时即执行发现——发现失败 = 本模块导入失败 = 套件响亮报错，
# 不会退化成一个只测 macsay 的静默子集。
# ============================================================
ADAPTER_CLASSES = discover_adapters()


# ============================================================
# 1. 接口属性完整性（所有适配器共用）
# ============================================================
class TestAttributeCompleteness(unittest.TestCase):
    """每个 TTS 适配器必须声明完整的接口属性。"""

    def setUp(self):
        self.adapters = [cls() for cls in ADAPTER_CLASSES]

    def test_name_nonempty(self):
        """name 必须非空。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertTrue(tts.name, f"{tts.__class__.__name__}.name 不得为空")

    def test_model_version_nonempty(self):
        """model_version 必须非空。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertTrue(
                    tts.model_version,
                    f"{tts.__class__.__name__}.model_version 不得为空",
                )

    def test_requires_core_nonempty(self):
        """requires_core 必须非空。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertTrue(
                    tts.requires_core,
                    f"{tts.__class__.__name__}.requires_core 不得为空",
                )

    def test_voice_nonempty(self):
        """voice 必须非空（资产指纹的组成部分）。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertTrue(
                    tts.voice,
                    f"{tts.__class__.__name__}.voice 不得为空",
                )

    def test_rate_map_has_slow(self):
        """rate_map 必须包含 slow 档。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertIn("slow", tts.rate_map, f"{tts.name}.rate_map 缺少 slow")

    def test_rate_map_has_normal(self):
        """rate_map 必须包含 normal 档。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertIn("normal", tts.rate_map, f"{tts.name}.rate_map 缺少 normal")

    def test_rate_map_has_fast(self):
        """rate_map 必须包含 fast 档。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertIn("fast", tts.rate_map, f"{tts.name}.rate_map 缺少 fast")


# ============================================================
# 2. rate_value 行为（所有适配器共用）
# ============================================================
class TestRateValueBehavior(unittest.TestCase):
    """rate_value 返回有限正数、档位单调递增，未知档位抛 TtsError。"""

    def setUp(self):
        self.adapters = [cls() for cls in ADAPTER_CLASSES]

    def test_known_rates_finite_positive(self):
        """已知三档必须返回有限正数。

        WHY 这里不再断言 `isinstance(val, int)`（T62 / AC4）：
          `int` 不是 TTS 速率接口的契约，只是**某个量纲下的实现细节**。
          本仓两个适配器用的是**不同量纲**，且都对：
            - macsay（`tts_macsay`）：绝对语速，单位 wpm → 150 / 200 / 300，整数；
            - omlx  （`tts_omlx`）  ：倍率（乘法），     → 0.85 / 1.0 / 1.25，浮点。
          原断言 `assertIsInstance(val, int)` 把 macsay 的量纲当成了接口契约，
          逼每个新适配器去改返回值迁就一个实现细节——而实际上一次也没人去改，
          处置方式是「把不返回 int 的适配器留在白名单外」，
          于是套件持续绿灯、对 omlx 零覆盖（T62 的头号缺口）。
          断言因此收窄到**量纲无关**的部分：值是数、有限、为正。
          （bool 显式排除：`isinstance(True, int)` 为真，布尔当速率没有语义。）
        """
        for tts in self.adapters:
            for key in ("slow", "normal", "fast"):
                with self.subTest(adapter=tts.name, rate=key):
                    val = tts.rate_value(key)
                    self.assertIsInstance(
                        val, (int, float),
                        f"{tts.name}.rate_value({key!r}) 应返回数值，实际 {type(val).__name__}",
                    )
                    self.assertNotIsInstance(
                        val, bool,
                        f"{tts.name}.rate_value({key!r}) 返回布尔 {val!r}，速率必须是数",
                    )
                    self.assertTrue(
                        _is_finite(val),
                        f"{tts.name}.rate_value({key!r}) 必须是有限数，实际 {val!r}",
                    )
                    self.assertGreater(
                        val, 0,
                        f"{tts.name}.rate_value({key!r}) 必须为正（速率不能为 0 或负），实际 {val!r}",
                    )

    def test_rate_ordering_slow_normal_fast(self):
        """语义档位 slow < normal < fast 必须反映在数值上。

        量纲无关：无论绝对语速（wpm）还是倍率（乘法），
        「慢 < 中 < 快」的方向都不能反——反了就是资产预铸出来的音频
        快慢标错，且这种错在本层之外没人能发现。
        用严格 `<` 而非 `<=`：两档同值等于把档位语义抹平，同样是假通过。
        """
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                slow = tts.rate_value("slow")
                normal = tts.rate_value("normal")
                fast = tts.rate_value("fast")
                self.assertLess(
                    slow, normal,
                    f"{tts.name}: slow({slow}) 必须 < normal({normal})",
                )
                self.assertLess(
                    normal, fast,
                    f"{tts.name}: normal({normal}) 必须 < fast({fast})",
                )

    def test_normal_is_middle_rate(self):
        """`normal` 必须是三档的中位值。

        本断言是「`normal` 最接近 1，或在约定区间」这条要求的落地形式，
        两个分支都归到同一条判据上：
          - 倍率约定（omlx，normal=1.0）：1.0 是乘法恒等元，
            「最接近 1」的档必然是中位值——偏离恒等元就是变速；
          - 绝对语速约定（macsay，wpm，最小量级在百位）：
            「最接近 1」在这里没有语义（wpm 不会接近 1），
            退化为「约定区间」的中点语义——`normal` 是参考语速，
            必须夹在 `slow` 与 `fast` 之间。
        所以不写死 1.0 作为目标值：写死会让倍率约定的适配器永远过不了，
        重演「判据逼所有载体改成同一个量纲」的那个错。
        """
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                slow = tts.rate_value("slow")
                normal = tts.rate_value("normal")
                fast = tts.rate_value("fast")
                self.assertEqual(
                    normal,
                    sorted([slow, normal, fast])[1],
                    f"{tts.name}: normal({normal}) 必须是三档中位值"
                    f"（slow={slow}, fast={fast}）",
                )

    def test_unknown_rate_raises_tts_error(self):
        """未知档位必须抛该适配器自己声明的 TtsError。

        异常类按适配器逐个解析（见 `adapter_error_type` 的注释）：
        本仓两个适配器的 TtsError 不是同一个类，`core/` 也没有统一的 TTS 异常，
        所以套件不 import 某一个适配器的那份来套所有适配器。
        """
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                with self.assertRaises(adapter_error_type(tts)):
                    tts.rate_value("nonexistent_rate_xyz")

    def test_unknown_rate_message_contains_key(self):
        """未知档位的错误消息必须包含该 key 名（fail-closed，不许静默回落到默认速率）。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                with self.assertRaises(adapter_error_type(tts)) as ctx:
                    tts.rate_value("nonexistent_rate_xyz")
                self.assertIn("nonexistent_rate_xyz", str(ctx.exception))

    def test_error_type_is_runtime_error_subclass(self):
        """适配器的异常契约必须是 RuntimeError 子类（fail-closed 的机器保证）。

        速率/合成失败若以普通 `Exception` 或返回值形式出现，上游就拿不到
        「这是一次 TTS 失败」的信号——这正是本仓要消除的静默降级。
        找不到合规的异常类时 `adapter_error_type` 直接抛错，本用例随之变红。
        """
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                err_cls = adapter_error_type(tts)
                self.assertTrue(
                    issubclass(err_cls, RuntimeError),
                    f"{tts.name} 的 TtsError 必须是 RuntimeError 子类，实际 {err_cls}",
                )


# ============================================================
# 3. synthesize 空文本行为（所有适配器共用，不依赖外部命令）
# ============================================================
class TestSynthesizeEmptyText(unittest.TestCase):
    """空文本必须抛 TtsError（无条件测，不依赖 say）。"""

    def setUp(self):
        self.adapters = [cls() for cls in ADAPTER_CLASSES]

    def test_empty_string_raises_tts_error(self):
        """空字符串 → 该适配器自己的 TtsError（空文本必须报错，不许返回静音/空文件）。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                with self.assertRaises(adapter_error_type(tts)) as ctx:
                    tts.synthesize("", Path("/dev/null"))
                self.assertIn("空", str(ctx.exception), "错误消息应提及空文本")


# ============================================================
# 4. 真实合成 WAV 格式校验（需 say 可用，否则 skip）
# ============================================================
# 4.0 逐适配器可用性（T77）：守卫粒度必须对齐循环粒度
#
# 上面的类级门 `skipUnless(shutil.which("say"))` 判的是「本机有没有 say」，
# 但下面的循环是 `for tts in self.adapters:` **逐适配器**跑——
# 「有 say」不等于「所有适配器都能合成」：tts_omlx 的可用性取决于
# 远端服务在不在跑，与 say 无关（模拟 runner 条件：有 say、无 oMLX 服务 → errors=2）。
# 于是这里在循环内、每个适配器合成**之前**问它一句。
#
# 纪律（本卡最易做错的地方）：判定只许放在 `synthesize` 调用**之前**。
# 调用之后冒出来的任何异常都必须照常上抛——把真实合成失败也吞成 skip
# 等于空套件假绿灯，与 discover_adapters() 的「空套件必须报错」直接相反。
def skip_unless_available(self, tts) -> None:
    """逐适配器判定可用性：不可用则 `skipTest`，消息点名适配器与缺失原因。

    适配器可不实现可选类方法 `probe_availability()`（返回 `(可用, 原因)`）——
    **不实现 = 永远可用**，所以 tts_macsay 等无远端依赖的适配器行为完全不变、仍然真跑。
    hook 按**类**查（`type(tts)`）：实例上塞个同名属性不算实现。
    本函数不捕获任何异常，也不包 `try/except`；见 `test_conformance_probe`。
    """
    probe = getattr(type(tts), "probe_availability", None)
    if probe is None:
        return
    ok, reason = probe()
    if not ok:
        self.skipTest(f"{tts.name}: {reason}")


@unittest.skipUnless(shutil.which("say"), "macOS say 命令不可用，跳过真实合成用例")
class TestSynthesizeWavFormat(unittest.TestCase):
    """有 say 时：产出 WAV 必须为 16kHz/单声道/16-bit。"""

    def setUp(self):
        self.adapters = [cls() for cls in ADAPTER_CLASSES]
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_wav_is_mono_16bit_16000hz(self):
        """每个适配器合成的 WAV 必须满足 channels=1, sampwidth=2, framerate=16000。"""
        for tts in self.adapters:
            skip_unless_available(self, tts)  # 判在合成之前；合成失败仍照常冒泡，不许吞成 skip
            out = Path(self.tmp_dir) / f"{tts.name}_test.wav"
            tts.synthesize("接口一致性测试", out)
            with wave.open(str(out), "rb") as wf:
                self.assertEqual(
                    wf.getnchannels(), 1,
                    f"{tts.name}: 声道数应为 1（单声道）",
                )
                self.assertEqual(
                    wf.getsampwidth(), 2,
                    f"{tts.name}: 采样位宽应为 2 字节（16-bit）",
                )
                self.assertEqual(
                    wf.getframerate(), 16000,
                    f"{tts.name}: 采样率应为 16000 Hz",
                )
                self.assertGreater(
                    wf.getnframes(), 0,
                    f"{tts.name}: 帧数应 > 0",
                )

    def test_synthesize_with_each_rate(self):
        """每个适配器分别以 slow/normal/fast 合成，格式均合法。"""
        for tts in self.adapters:
            skip_unless_available(self, tts)  # 判在合成之前；合成失败仍照常冒泡，不许吞成 skip
            for rate in ("slow", "normal", "fast"):
                out = Path(self.tmp_dir) / f"{tts.name}_{rate}.wav"
                tts.synthesize("速率测试", out, rate_key=rate)
                with wave.open(str(out), "rb") as wf:
                    self.assertEqual(wf.getnchannels(), 1)
                    self.assertEqual(wf.getsampwidth(), 2)
                    self.assertEqual(wf.getframerate(), 16000)


def _is_finite(value: float) -> bool:
    """有限数判定（不引入 math 依赖到断言路径上，口径单点维护）。"""
    return value == value and value not in (float("inf"), float("-inf"))


if __name__ == "__main__":
    unittest.main()
