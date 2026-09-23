"""
adapters.tts_omlx.tests.test_adapter — oMLX TTS 适配器测试

覆盖（对应 T35 验收 1/2/3）：
  1. 默认音色路径产出 16kHz/单声道/16-bit WAV，且 runtime.audio.read_wav 能读
     —— 不依赖任何私人录音；需要本机 oMLX 真在跑，没在跑就显式 skip。
  2. 克隆路径：voice = clone-<sha8>（不含路径）、model_version 切换；
     VOX_TTS_REF 有而 VOX_TTS_REF_TEXT 缺 → fail-closed；ref_audio 确实是 base64。
  3. 空文本抛 TtsError；未知 rate 抛 TtsError 且消息含该档名；
     缺 ffmpeg 抛 TtsError（fail-closed，绝不返回静音/空文件）。
  4. 配置期负例：base_url 非 http(s) / model 空白 / timeout 非正 → ValueError。
  5. 降级链路形状：服务端返回 24kHz → 产物必须被降到 16kHz；产物不合契约 → 抛错。
  6. env 兜底（T40）：VOX_TTS_ENDPOINT / VOX_TTS_VOICE 生效并真的到达请求体；
     缺 env 时回落常量；非法 env（非 http(s)）仍抛 ValueError，不得静默回落。
     克隆路径优先于 VOX_TTS_VOICE（指纹与请求体都不受它影响）。

纪律：除默认音色冒烟外全部离线——用「假响应 / 假 runner」替身注入，
  不联网、不起服务、不用私人录音；替身不是产品件。
"""

import base64
import hashlib
import os
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Optional

from adapters.tts_omlx import OmlxTts, TtsError


# ---------------------------------------------------------------------------
# 测试替身（非产品件）
# ---------------------------------------------------------------------------
def make_wav(path: Path, framerate: int = 24000, channels: int = 1,
             sampwidth: int = 2, frames: int = 2400) -> None:
    """写一个最小合法 WAV（线性样本，帧数 > 0 且非全零）。"""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(struct.pack("<" + "h" * frames, *range(frames)) if sampwidth == 2
                       else bytes(frames * sampwidth))


def read_fmt(path: Path) -> tuple:
    """读 WAV 的 (声道, 字节位宽, 采样率, 帧数)。"""
    with wave.open(str(path), "rb") as wf:
        return (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes())


class FakeResponse:
    """urllib 响应形状的替身：只实现适配器用到的 read()。"""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body


def opener_returning(body: bytes):
    """返回一个「urlopen 替身」：任何请求都回成给定字节。"""
    return lambda req, timeout=None: FakeResponse(body)


class FakeRunner:
    """subprocess.run 替身：按给定格式写出 ffmpeg 的目标文件。

    WHY 这样造假：ffmpeg 的活就是把 24kHz WAV 转成 16kHz WAV；替身直接产出目标格式，
    而适配器随后仍要跑 wave 校验（_assert_contract）才会落盘——所以「产物不合格」
    这条负例仍然真实穿过产品代码，没有绕过判定。
    """

    def __init__(self, output_fmt=(1, 2, 16000), frames: int = 1600, rc: int = 0,
                 stderr: str = "") -> None:
        self.output_fmt = output_fmt
        self.frames = frames
        self.rc = rc
        self.stderr = stderr
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.rc == 0:
            make_wav(Path(cmd[-1]), framerate=self.output_fmt[2], channels=self.output_fmt[0],
                     sampwidth=self.output_fmt[1], frames=self.frames)
        result = type("Result", (), {})()
        result.returncode = self.rc
        result.stderr = self.stderr
        result.stdout = ""
        return result


def _stub_ffmpeg() -> Optional[str]:
    """给「替身 runner」用例用的 ffmpeg **占位可执行文件**（存在即可，真执行被 runner 接管）。

    WHY 不能是 `shutil.which("ffmpeg") or "ffmpeg"`（2026-09-23 CI 实测踩到）：
    适配器在合成前会 `shutil.which(self._ffmpeg)` 做**可用性检查**，裸名在没装 ffmpeg 的机器
    （CI runner）解析不到 → 直接抛 TtsError。后果分两种，**两种都是坏的**：

    · 正向用例（断言命令行带 16k / 产物是契约格式）→ **ERROR**，三档 Python 全红；
    · 负向用例（断言「格式不合规」「非 0 退出」要抛 TtsError）→ **假绿**：
      它们因**错误的原因**抛了同一个异常而「通过」，实际只验到「ffmpeg 不存在」，
      待验的复核逻辑一行没跑到。

    所以占位必须**解析得到**：取任一存在的可执行文件即可（真 ffmpeg 只在需要跑真合成的
    冒烟用例里才必需，那条另有 skipUnless）。取不到（极端环境）→ None，调用方 skip。
    """
    for cand in ("true", "sh"):
        p = shutil.which(cand)
        if p:
            return p
    return "/bin/sh" if Path("/bin/sh").exists() else None


# 不用写死路径的理由不变：仓内文本不得出现本机绝对路径（此处按名字查找，不写字面）。
FFMPEG_BIN: Optional[str] = _stub_ffmpeg()


class EnvGuard:
    """显式 clear / restore 的 env 守卫。

    不用上下文管理器：`with` 块一结束就清 env，会让「在 with 内设好 env、
    在 with 外断言」这种写法静默变成测了个空 env。
    """

    # VOX_TTS_ENDPOINT / VOX_TTS_VOICE 必须一起清：文档教 Linux 用户 export 它们，
    # 若不清，开发机上一旦 export 过，下面断言常量/默认音色的用例就会假红。
    KEYS = ("VOX_TTS_REF", "VOX_TTS_REF_TEXT", "VOX_TTS_MODEL", "VOX_FFMPEG",
            "VOX_TTS_ENDPOINT", "VOX_TTS_VOICE")

    def __init__(self, cleared: bool = True) -> None:
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        if cleared:
            self.clear()

    def clear(self) -> None:
        for k in self.KEYS:
            os.environ.pop(k, None)

    def restore(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TmpDir(unittest.TestCase):
    """给需要落盘的用例提供临时目录 + env 守卫 + 收尾清理。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = EnvGuard(cleared=True)

    def tearDown(self):
        self.env.restore()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def out(self, name="x.wav") -> Path:
        return Path(self.tmp) / name

    def write_ref(self, name="ref.wav", frames: int = 1600, empty: bool = False) -> Path:
        """写一个仓内合成的参考音频（测试自造，不是私人录音）。"""
        ref = Path(self.tmp) / name
        if empty:
            ref.write_bytes(b"")
        else:
            make_wav(ref, framerate=24000, frames=frames)
        return ref

    def default_tts(self, **kwargs) -> OmlxTts:
        """构造一个不带任何 clone 配置的适配器。"""
        return OmlxTts(**kwargs)


# ============================================================
# 1. 属性与配置期校验（无条件测，不依赖任何外部服务）
# ============================================================
class TestAttributes(TmpDir):
    """接口属性完整性 + 构造函数参数校验。"""

    def test_name_and_model_version_nonempty(self):
        """name / model_version 非空，二者都参与资产指纹。"""
        self.assertEqual(self.default_tts().name, "omlx-tts")
        self.assertEqual(self.default_tts().model_version, "Qwen3-TTS-12Hz-0.6B-Base-bf16")

    def test_requires_core_declared(self):
        """requires_core 必须声明（与 tts_macsay 同口径）。"""
        self.assertTrue(self.default_tts().requires_core)

    def test_rate_map_has_three_gears(self):
        """rate_map 必须包含 slow / normal / fast 三档。"""
        for key in ("slow", "normal", "fast"):
            self.assertIn(key, self.default_tts().rate_map, f"rate_map 缺少 {key}")

    def test_capabilities_declared(self):
        """capabilities 必须声明，且 streaming 如实标 False（流式产物是无界 WAV）。"""
        caps = self.default_tts().capabilities
        self.assertTrue(caps["zero_shot_clone"])
        self.assertFalse(caps["streaming"])

    def test_endpoint_is_default_omlx_speech(self):
        """默认端点必须是 oMLX 的 /v1/audio/speech。"""
        self.assertEqual(self.default_tts().endpoint(), "http://127.0.0.1:10099/v1/audio/speech")

    def test_base_url_must_be_http(self):
        """base_url 非 http(s) → ValueError（不静默回落到默认值）。"""
        with self.assertRaises(ValueError):
            OmlxTts(base_url="not-a-url")

    def test_endpoint_and_voice_env_fallbacks_are_honored(self):
        """env 兜底真的生效：VOX_TTS_ENDPOINT 覆盖基址、VOX_TTS_VOICE 覆盖音色。

        断言到**出口**而不只是属性：`endpoint()` 拼出的 URL 与 `_payload()` 里的 voice
        才是服务端真正收到的东西（只改属性、请求体照旧 = 发了等于没发）。
        CLI 的 `--adapter` 只传「模块:类名」、传不进构造参数，env 是服务器上唯一的入口。
        """
        os.environ["VOX_TTS_ENDPOINT"] = "http://127.0.0.1:8123/"
        os.environ["VOX_TTS_VOICE"] = "Some-Server-Voice"
        tts = OmlxTts()
        self.assertEqual(tts.base_url, "http://127.0.0.1:8123", "env 基址应生效（尾斜杠被剥掉）")
        self.assertEqual(tts.endpoint(), "http://127.0.0.1:8123/v1/audio/speech")
        self.assertEqual(tts.voice, "Some-Server-Voice")
        self.assertEqual(tts._payload("任意文本", 1.0)["voice"], "Some-Server-Voice",
                         "音色必须真的进请求体，否则 VOX_TTS_VOICE 是空转")
        self.assertEqual(OmlxTts(base_url="http://10.0.0.5:9999").base_url, "http://10.0.0.5:9999",
                         "显式参数优先于 env（顺序：参数 → env → 常量）")

    def test_endpoint_and_voice_fall_back_to_constants_without_env(self):
        """不设 env 时回落常量：基址 http://127.0.0.1:10099、音色 default（有序、可预期）。"""
        tts = self.default_tts()
        self.assertEqual(tts.base_url, "http://127.0.0.1:10099")
        self.assertEqual(tts.endpoint(), "http://127.0.0.1:10099/v1/audio/speech")
        self.assertEqual(tts.voice, "default")
        self.assertEqual(tts._payload("任意文本", 1.0)["voice"], "default")

    def test_invalid_env_endpoint_still_raises_value_error(self):
        """非法 VOX_TTS_ENDPOINT（非 http/https，含纯空白）→ ValueError，不得静默回落常量。

        fail-closed 的意义：地址写错时必须响亮地失败，而不是悄悄拿默认端点去合成
        ——那会表现为「部署到服务器后声音/服务不对，但没人知道为什么」。

        `""`（**设了但为空**）与 "   " 同档：`export VOX_TTS_ENDPOINT="$TTS_HOST"` 而 TTS_HOST
        未定义、或 systemd `Environment=VOX_TTS_ENDPOINT=`，都会产生空串。若把空串当成「未设」，
        请求就会**悄悄打到本机默认地址**（静默换地址）。独立复核 2026-09-23 实测发现
        `or 默认值` 写法下空串确实静默回落——已修成 `get(k, 默认值)`，本用例钉住它。
        """
        for bad in ("not-a-url", "ftp://127.0.0.1:10099", "127.0.0.1:10099", "   ", ""):
            with self.subTest(endpoint=bad):
                os.environ["VOX_TTS_ENDPOINT"] = bad
                with self.assertRaises(ValueError) as ctx:
                    OmlxTts()
                self.assertIn("base_url", str(ctx.exception))

    def test_model_whitespace_only_is_rejected(self):
        """model 为纯空白 → ValueError（不得被当成「未指定」而回落默认模型）。"""
        with self.assertRaises(ValueError):
            OmlxTts(model="   ")

    def test_model_env_override_is_honored(self):
        """env VOX_TTS_MODEL 可覆盖模型（不写死；评测方按环境指定）。

        断言的是「请求体里真的带了这个模型」——model_version 在无克隆音色时
        就是请求体 model，所以走 _payload 才能测到真实出口。
        """
        os.environ["VOX_TTS_MODEL"] = "Some-Other-TTS-Model"
        tts = OmlxTts()
        self.assertEqual(tts._payload("任意文本", 1.0)["model"], "Some-Other-TTS-Model")

    def test_timeout_must_be_finite_positive(self):
        """timeout 非有限正数（含 NaN）→ ValueError，不逃出契约。"""
        for bad in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=bad):
                with self.assertRaises(ValueError):
                    OmlxTts(timeout_seconds=bad)


# ============================================================
# 2. rate_value —— 正例 + 负例（不回落，消息含档名）
# ============================================================
class TestRateValue(TmpDir):
    def test_known_gears_return_numeric_speed(self):
        """三档都返回数值型 speed（服务端 speed 参数，不是 wpm）。"""
        tts = self.default_tts()
        for key in ("slow", "normal", "fast"):
            with self.subTest(rate=key):
                value = tts.rate_value(key)
                self.assertIsInstance(value, (int, float))
                self.assertGreater(value, 0)

    def test_normal_is_baseline(self):
        """normal 档是 1.0（服务端原速基线）。"""
        self.assertEqual(self.default_tts().rate_value("normal"), 1.0)

    def test_gears_are_monotonic(self):
        """三档单调：slow < normal < fast（映射方向不能弄反）。"""
        tts = self.default_tts()
        self.assertLess(tts.rate_value("slow"), tts.rate_value("normal"))
        self.assertGreater(tts.rate_value("fast"), tts.rate_value("normal"))

    def test_unknown_rate_raises_tts_error(self):
        """未知档位 → TtsError（不回落 normal，也不吞掉）。"""
        with self.assertRaises(TtsError):
            self.default_tts().rate_value("nonexistent_rate_xyz")

    def test_unknown_rate_message_contains_key(self):
        """错误消息必须含该档名，便于定位是哪个档位配错。"""
        with self.assertRaises(TtsError) as ctx:
            self.default_tts().rate_value("very_slow")
        self.assertIn("very_slow", str(ctx.exception))

    def test_unknown_rate_message_lists_valid_gears(self):
        """错误消息应列出支持的档位，便于一次性改对。"""
        with self.assertRaises(TtsError) as ctx:
            self.default_tts().rate_value("turbo")
        for key in ("slow", "normal", "fast"):
            self.assertIn(key, str(ctx.exception))


# ============================================================
# 3. synthesize —— 空文本 / 缺 ffmpeg（fail-closed，离线可测）
# ============================================================
class TestSynthesizeFailClosed(TmpDir):
    """失败路径必须抛 TtsError，绝不返回静音或空文件。"""

    def test_empty_string_raises_tts_error(self):
        """空字符串 → TtsError，且消息提及空文本。"""
        with self.assertRaises(TtsError) as ctx:
            self.default_tts().synthesize("", self.out())
        self.assertIn("空", str(ctx.exception))

    def test_missing_ffmpeg_raises_tts_error(self):
        """缺 ffmpeg → TtsError（fail-closed）。

        用 ffmpeg 指向一个确定不存在的路径来模拟「机器上没有 ffmpeg」；
        必须真的抛错，而不是悄悄跳过降采样、把 24kHz 原样落盘冒充成功。
        """
        tts = self.default_tts(opener=opener_returning(b"RIFF"),
                               ffmpeg="/definitely/no/such/ffmpeg-binary")
        with self.assertRaises(TtsError) as ctx:
            tts.synthesize("测试句子", self.out())
        self.assertIn("ffmpeg", str(ctx.exception).lower())

    def test_missing_ffmpeg_leaves_no_output_file(self):
        """缺 ffmpeg 时不得留下半成品文件（否则下游会当成合成成功）。"""
        tts = self.default_tts(opener=opener_returning(b"RIFF"),
                               ffmpeg="/definitely/no/such/ffmpeg-binary")
        target = self.out("no-file.wav")
        with self.assertRaises(TtsError):
            tts.synthesize("测试句子", target)
        self.assertFalse(target.exists())

    def test_output_path_is_existing_directory_raises(self):
        """目标路径已是目录 → TtsError，不写进去、不覆盖。"""
        with self.assertRaises(TtsError):
            self.default_tts().synthesize("测试句子", self.tmp)


# ============================================================
# 4. 降级链路形状：24kHz → 16kHz（离线，假 runner 产出契约格式）
# ============================================================
@unittest.skipUnless(FFMPEG_BIN, "找不到可用的占位可执行文件，替身用例无法通过适配器的 ffmpeg 可用性检查")
class TestDownsampleShape(TmpDir):
    """服务出 24 kHz，产物必须落在契约的 16 kHz/单声道/16-bit。

    **本类全部用 `FakeRunner` 替身**：验的是适配器自己的复核逻辑（命令行参数、产物格式校验、
    失败不落盘），**不验真 ffmpeg**——真合成的冒烟在 `TestRealSmoke`（另有 skipUnless）。
    """

    def test_output_is_16k_mono_16bit(self):
        """合成产物必须是 16000Hz / 单声道 / 16-bit 且非空。"""
        runner = FakeRunner()
        tts = self.default_tts(opener=opener_returning(b"RIFF" + b"\x00" * 100),
                               ffmpeg=FFMPEG_BIN, runner=runner)
        out = self.out("16k.wav")
        tts.synthesize("接口一致性测试", out)
        self.assertTrue(out.is_file(), "成功路径必须落盘")
        fmt = read_fmt(out)
        self.assertEqual(fmt[2], 16000, f"采样率应为 16000Hz，实际 {fmt[2]}")
        self.assertEqual(fmt[0], 1, f"应为单声道，实际 {fmt[0]}")
        self.assertEqual(fmt[1], 2, f"应为 16-bit（2 字节），实际 {fmt[1] * 8}-bit")
        self.assertGreater(fmt[3], 0, "帧数应 > 0")

    def test_ffmpeg_invoked_with_16k_contract_args(self):
        """ffmpeg 命令行必须真的带 -ar 16000 与 pcm_s16le（不是只靠事后校验）。"""
        runner = FakeRunner()
        self.default_tts(opener=opener_returning(b"RIFF" + b"\x00" * 100),
                         ffmpeg=FFMPEG_BIN, runner=runner
                         ).synthesize("降采样参数校验", self.out("a.wav"))
        self.assertEqual(len(runner.calls), 1)
        cmd = runner.calls[0]
        self.assertIn("16000", cmd)
        self.assertIn("pcm_s16le", cmd)

    def test_bogus_output_format_raises(self):
        """ffmpeg 产出非契约格式 → TtsError（不信任 ffmpeg 的结果）。"""
        tts = self.default_tts(opener=opener_returning(b"RIFF" + b"\x00" * 100),
                               ffmpeg=FFMPEG_BIN,
                               runner=FakeRunner(output_fmt=(2, 2, 24000)))
        out = self.out("bad.wav")
        with self.assertRaises(TtsError):
            tts.synthesize("格式校验", out)
        self.assertFalse(out.exists(), "产物不合规时不得落盘")

    def test_ffmpeg_nonzero_exit_raises(self):
        """ffmpeg 非 0 退出 → TtsError，消息含退出码。"""
        tts = self.default_tts(opener=opener_returning(b"RIFF" + b"\x00" * 100),
                               ffmpeg=FFMPEG_BIN,
                               runner=FakeRunner(rc=1, stderr="boom"))
        with self.assertRaises(TtsError) as ctx:
            tts.synthesize("ffmpeg 失败", self.out("c.wav"))
        self.assertIn("ffmpeg", str(ctx.exception).lower())


# ============================================================
# 5. 克隆路径：voice 指纹 / model_version 切换 / env 不完整 fail-closed
# ============================================================
class TestClonePath(TmpDir):
    """零样本克隆：voice 不带路径、model_version 切换、缺一即失败。"""

    def test_voice_is_default_without_env(self):
        """无克隆 env 时 voice 必须是 default。"""
        self.assertEqual(self.default_tts().voice, "default")

    def test_voice_is_clone_sha8_without_path(self):
        """克隆时 voice = clone-<参考音频 sha256 前 8 位>，且绝不出现路径。"""
        ref = self.write_ref()
        os.environ["VOX_TTS_REF"] = str(ref)
        os.environ["VOX_TTS_REF_TEXT"] = "参考音频的转写文本"
        tts = OmlxTts()
        digest = hashlib.sha256(ref.read_bytes()).hexdigest()[:8]
        self.assertEqual(tts.voice, f"clone-{digest}")
        self.assertNotIn(str(ref), tts.voice)
        self.assertNotIn(self.tmp, tts.voice)
        self.assertNotIn("/", tts.voice.replace("clone-", ""))

    def test_voice_stable_across_instances(self):
        """同一参考音频 → 同一 voice（指纹是参考内容决定的，不是进程状态）。"""
        ref = self.write_ref()
        os.environ["VOX_TTS_REF"] = str(ref)
        os.environ["VOX_TTS_REF_TEXT"] = "转写"
        self.assertEqual(OmlxTts().voice, OmlxTts().voice)

    def test_voice_changes_when_reference_changes(self):
        """参考音频变了 → voice 变（指纹确实跟着内容走）。"""
        ref1 = self.write_ref("r1.wav")
        os.environ["VOX_TTS_REF"] = str(ref1)
        os.environ["VOX_TTS_REF_TEXT"] = "转写一"
        first = OmlxTts().voice
        ref2 = self.write_ref("r2.wav", frames=2200)
        os.environ["VOX_TTS_REF"] = str(ref2)
        os.environ["VOX_TTS_REF_TEXT"] = "转写二"
        second = OmlxTts().voice
        self.assertNotEqual(first, second)

    def test_clone_changes_model_version(self):
        """克隆音色的 model_version 必须是 clone 专用值（与默认音色资产不互通）。"""
        ref = self.write_ref()
        self.assertEqual(self.default_tts().model_version, "Qwen3-TTS-12Hz-0.6B-Base-bf16")
        os.environ["VOX_TTS_REF"] = str(ref)
        os.environ["VOX_TTS_REF_TEXT"] = "转写"
        self.assertEqual(OmlxTts().model_version, "qwen3-tts-0.6b-base-clone")

    def test_ref_without_ref_text_fails_closed(self):
        """VOX_TTS_REF 有而 VOX_TTS_REF_TEXT 缺 → TtsError（不回落默认音色）。"""
        os.environ["VOX_TTS_REF"] = str(self.write_ref())
        with self.assertRaises(TtsError) as ctx:
            OmlxTts().voice
        self.assertIn("VOX_TTS_REF_TEXT", str(ctx.exception))

    def test_ref_text_whitespace_fails_closed(self):
        """VOX_TTS_REF_TEXT 为纯空白同样算缺（空串不能冒充转写）。"""
        os.environ["VOX_TTS_REF"] = str(self.write_ref())
        os.environ["VOX_TTS_REF_TEXT"] = "   "
        with self.assertRaises(TtsError):
            OmlxTts().voice

    def test_ref_file_missing_fails_closed(self):
        """VOX_TTS_REF 指向不存在的文件 → TtsError。"""
        os.environ["VOX_TTS_REF"] = str(self.out("nope.wav"))
        os.environ["VOX_TTS_REF_TEXT"] = "转写"
        with self.assertRaises(TtsError):
            OmlxTts().voice

    def test_ref_file_empty_fails_closed(self):
        """VOX_TTS_REF 指向 0 字节文件 → TtsError。"""
        os.environ["VOX_TTS_REF"] = str(self.write_ref(empty=True))
        os.environ["VOX_TTS_REF_TEXT"] = "转写"
        with self.assertRaises(TtsError):
            OmlxTts().voice

    def test_payload_carrying_base64_ref_audio(self):
        """克隆请求体必须带 base64 的 ref_audio 与 ref_text，且不带 voice。"""
        ref = self.write_ref()
        os.environ["VOX_TTS_REF"] = str(ref)
        os.environ["VOX_TTS_REF_TEXT"] = "转写文本"
        payload = OmlxTts()._payload("要说的话", 1.0)
        self.assertEqual(payload["ref_audio"], base64.b64encode(ref.read_bytes()).decode("ascii"))
        self.assertEqual(payload["ref_text"], "转写文本")
        self.assertNotIn("voice", payload, "克隆路径不得再发 voice=default")
        self.assertEqual(payload["speed"], 1.0)

    def test_clone_fingerprint_wins_over_voice_env(self):
        """克隆音色优先于 VOX_TTS_VOICE：指纹与请求体都不受 env 影响（不是两条路混着走）。

        否则 VOX_TTS_VOICE 一设，克隆包的 voice / model_version 就会漂到别的含义上，
        等于悄悄换了资产身份。
        """
        ref = self.write_ref()
        os.environ["VOX_TTS_VOICE"] = "Some-Server-Voice"
        os.environ["VOX_TTS_REF"] = str(ref)
        os.environ["VOX_TTS_REF_TEXT"] = "转写"
        tts = OmlxTts()
        self.assertEqual(tts.voice, "clone-" + hashlib.sha256(ref.read_bytes()).hexdigest()[:8])
        self.assertEqual(tts.model_version, "qwen3-tts-0.6b-base-clone")
        self.assertNotIn("voice", tts._payload("要说的话", 1.0))

    def test_payload_default_path_carries_voice_default(self):
        """默认音色请求体必须带 voice=default，且不出现 ref_audio。"""
        payload = self.default_tts()._payload("要说的话", 0.85)
        self.assertEqual(payload["voice"], "default")
        self.assertNotIn("ref_audio", payload)
        self.assertEqual(payload["response_format"], "wav")


# ============================================================
# 6. HTTP 失败收口：非 2xx / 非 WAV 响应 → TtsError
# ============================================================
class TestHttpFailure(TmpDir):
    """服务端故障必须收口为 TtsError，不得让裸异常或半截字节溜出去。"""

    def test_garbage_response_raises(self):
        """服务端返回非 WAV 字节 → TtsError（不能把 HTML 错误页当音频落盘）。"""
        tts = self.default_tts(opener=opener_returning(b"<html>not found</html>"))
        with self.assertRaises(TtsError):
            tts.synthesize("测试", self.out())

    def test_http_error_carries_status_code(self):
        """HTTP 非 2xx → TtsError，消息含状态码（便于区分 400 与 500）。"""
        import urllib.error

        def rejecting(req, timeout=None):
            raise urllib.error.HTTPError(
                url="http://127.0.0.1:10099/v1/audio/speech",
                code=400, msg="Bad Request", hdrs=None, fp=None,
            )

        with self.assertRaises(TtsError) as ctx:
            self.default_tts(opener=rejecting).synthesize("测试", self.out())
        self.assertIn("400", str(ctx.exception))

    def test_connection_refused_raises(self):
        """连接被拒 → TtsError，不冒充成功。"""
        import urllib.error

        def refusing(req, timeout=None):
            raise urllib.error.URLError(
                reason=ConnectionRefusedError(61, "Connection refused"))

        with self.assertRaises(TtsError):
            self.default_tts(opener=refusing).synthesize("测试", self.out())

    def test_overlarge_response_raises(self):
        """响应超过上限 → TtsError（防服务端串流把内存打爆）。"""
        tts = self.default_tts(opener=opener_returning(b"x" * (60 * 1024 * 1024)))
        with self.assertRaises(TtsError):
            tts.synthesize("测试", self.out())


# ============================================================
# 7. 默认音色冒烟（需本机 oMLX 真在跑；不依赖私人录音）
# ============================================================
@unittest.skipUnless(shutil.which("ffmpeg") is not None, "缺 ffmpeg，跳过真实合成冒烟")
class TestDefaultVoiceSmoke(unittest.TestCase):
    """默认音色冒烟：真打 oMLX，产物必须过 runtime.audio.read_wav。

    WHY 允许真实打服务：这是唯一能证明「服务出 24k → 适配器真降到 16k」的路径，
    而默认音色不依赖任何私人录音，可以安全独立跑。服务没在跑时显式 skip，
    不伪造通过。
    """

    def _service_up(self) -> bool:
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 10099), timeout=2):
                return True
        except OSError:
            return False

    def test_default_voice_produces_contract_wav(self):
        """合成一句 → 16kHz/单声道/16-bit WAV，且 runtime.audio.read_wav 能读。"""
        if not self._service_up():
            self.skipTest("本机 oMLX :10099 未运行（默认音色冒烟需要真服务）")
        tmp = tempfile.mkdtemp()
        try:
            # 非克隆 env 全部清掉（含 VOX_TTS_ENDPOINT / VOX_TTS_VOICE）：冒烟必须打本机缺省端点、
            # 用缺省音色，否则开发机上 export 过的地址会把这条用例悄悄指向别处。
            keys = ("VOX_TTS_REF", "VOX_TTS_REF_TEXT", "VOX_TTS_MODEL", "VOX_FFMPEG",
                    "VOX_TTS_ENDPOINT", "VOX_TTS_VOICE")
            saved = {k: os.environ.get(k) for k in keys}
            for k in saved:
                os.environ.pop(k, None)
            try:
                tts = OmlxTts(timeout_seconds=600)
                self.assertEqual(tts.voice, "default")
                out = Path(tmp) / "smoke.wav"
                tts.synthesize("您好，我是测试播报。", out)
            finally:
                for k, v in saved.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            fmt = read_fmt(out)
            self.assertEqual(fmt[2], 16000)
            self.assertEqual(fmt[0], 1)
            self.assertEqual(fmt[1], 2)
            self.assertGreater(fmt[3], 0)
            from runtime.audio import read_wav
            samples, framerate = read_wav(out)
            self.assertEqual(framerate, 16000)
            self.assertEqual(len(samples), fmt[3])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
