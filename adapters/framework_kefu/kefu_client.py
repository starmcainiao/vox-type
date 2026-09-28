"""
adapters.framework_kefu.kefu_client — kefu-agent 侧薄客户端（只走它已有的公开接口）

职责：给 KefuBridge 提供三样上游能力，全部经 kefu-agent 现有对外接口取，
      不修改 kefu-agent 的任何文件或脚本。
不负责：不做命中判定、不做归一化、不做拼接、不解释业务话术。

三个接口（docs/09 §二/§八/§十 的实测口径）：
    ask_brain(session_id, text) -> str        POST {brain_url}/chat/turn（brain）
    transcribe(wav_bytes)       -> str        voice worker 的 stdio 行协议 op=stt
    synthesize_live(text)       -> (bytes,id) voice worker 的 stdio 行协议 op=tts（say 档）

WHY transcribe / synthesize_live 不走 voice_url：
    POST {voice_url}/api/voice/turn 是"整轮"接口——它自己会把回复播出去，
    本层拿不到"只识别""只合成"这两段，也就没法把预铸命中插进接缝里。
    所以走 voice_worker.py 的 stdio 行协议（一行请求一行响应，响应带 elapsedMs），
    worker 由本类按需拉起、用完即关。voice_url 保留在构造函数里只做记录与报错定位。
"""

import base64
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

# kefu-agent 的位置：环境变量优先（KEFU_AGENT_ROOT = kefu-agent 仓根）；
# 未设置时回退到「仓根同机邻居目录」的约定布局（kefu-agent 与本仓并列克隆）。
# 开源说明：不内置任何人的本机绝对路径——用哪个 kefu，配哪个根。
KEFU_AGENT_ROOT = Path(os.environ.get("KEFU_AGENT_ROOT", Path(__file__).resolve().parents[2].parent / "kefu-agent"))
DEFAULT_WORKER_PYTHON = str(KEFU_AGENT_ROOT / ".venv-voice" / "bin" / "python")
DEFAULT_WORKER_SCRIPT = str(
    KEFU_AGENT_ROOT / "organs" / "客服" / "channel-voice" / "voice_worker.py"
)
DEFAULT_WORKER_CWD = str(KEFU_AGENT_ROOT / "organs" / "客服" / "channel-voice")

# brain 响应里可能出现"回复文本"的字段名（顺序即优先级）
_REPLY_KEYS = ("reply", "replyText", "text", "content", "output", "message", "answer")
_LIST_KEYS = ("choices", "messages", "replies", "parts")


# ---------------------------------------------------------------------------
# 异常（类型名都会出现在报错里，便于脚本按类型分流）
# ---------------------------------------------------------------------------
class KefuError(Exception):
    """kefu 侧调用失败的基类。"""


class KefuBrainError(KefuError):
    """brain（/chat/turn）网络或协议失败。"""


class KefuWorkerError(KefuError):
    """voice worker（stdio 行协议）启动、超时或协议失败。"""


# ---------------------------------------------------------------------------
# 响应解析（纯函数，便于单测；只做"取文本/取音频"，不做语义判断）
# ---------------------------------------------------------------------------
def extract_reply(payload) -> Optional[str]:
    """从 brain 响应里取出回复文本；取不到返回 None（由调用方决定报错）。

    兼容三种常见形状：裸字符串、{"reply"/"text"/...: "..."}、
    {"choices": [{"message": {"content": "..."}}]}（OpenAI 风格）。
    递归深度由结构决定，天然有界（JSON 有限深）。
    """
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, list):
        parts = [p for p in (extract_reply(x) for x in payload) if p]
        return "".join(parts) or None
    if isinstance(payload, dict):
        for k in _REPLY_KEYS:
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for k in _LIST_KEYS:
            v = payload.get(k)
            if isinstance(v, list):
                got = extract_reply(v)
                if got:
                    return got
        for k in _REPLY_KEYS:
            v = payload.get(k)
            if isinstance(v, dict):
                got = extract_reply(v)
                if got:
                    return got
    return None


def extract_wav(resp: dict) -> bytes:
    """从 worker 的 op=tts 响应里取出 WAV 字节；取不到抛 KefuWorkerError。

    接受两种形状：wavB64（base64）或 wavPath/path/file（本地路径）。
    """
    b64 = resp.get("wavB64")
    if isinstance(b64, str) and b64.strip():
        try:
            return base64.b64decode(b64)
        except (ValueError, TypeError) as exc:
            raise KefuWorkerError(
                f"op=tts 响应的 wavB64 不是合法 base64: {type(exc).__name__}: {exc}"
            ) from exc
    for k in ("wavPath", "path", "file", "audio"):
        v = resp.get(k)
        if isinstance(v, str) and v.strip():
            p = Path(v)
            if p.is_file():
                return p.read_bytes()
    raise KefuWorkerError(
        f"op=tts 响应里没有音频（只见字段 {sorted(resp)}）——"
        f"拒绝静默返回空音频"
    )


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class KefuClient:
    """kefu-agent 薄客户端：brain 走 HTTP，ASR/TTS 走 voice worker 行协议。

    属性：
        brain_url / voice_url / timeout_s: 上游地址与超时（voice_url 只用于定位与记录）
        worker_python / worker_script / worker_cwd: worker 拉起方式（可覆盖，测试用）
    """

    def __init__(
        self,
        *,
        brain_url: str = "http://127.0.0.1:8092",
        voice_url: str = "http://127.0.0.1:8096",
        timeout_s: float = 180.0,
        worker_python: Optional[str] = None,
        worker_script: Optional[str] = None,
        worker_cwd: Optional[str] = None,
    ):
        """初始化地址与超时；worker 不在此拉起（按需启动，见 _worker）。"""
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or timeout_s <= 0:
            raise TypeError(f"timeout_s 必须是正数，实际为 {timeout_s!r}")

        self.brain_url = str(brain_url).rstrip("/")
        self.voice_url = str(voice_url).rstrip("/")
        self.timeout_s = float(timeout_s)
        self.worker_python = worker_python or DEFAULT_WORKER_PYTHON
        self.worker_script = worker_script or DEFAULT_WORKER_SCRIPT
        self.worker_cwd = worker_cwd or DEFAULT_WORKER_CWD

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()      # worker 是单会话，请求必须串行

    # ------------------------------------------------------------------
    # brain
    # ------------------------------------------------------------------
    def ask_brain(self, session_id: str, text: str) -> str:
        """把用户文本发给 brain，返回"这一轮要说什么"（自由文本档的输入）。

        请求体沿用 voice 端的字段命名（sessionId），channel 标 voice。
        异常：
            TypeError:      session_id / text 非非空字符串
            KefuBrainError: 网络失败 / 非 JSON / 响应里取不到回复文本
        """
        if not isinstance(session_id, str) or not session_id.strip():
            raise TypeError(f"session_id 必须是非空字符串，实际为 {session_id!r}")
        if not isinstance(text, str) or not text.strip():
            raise TypeError(f"问句 text 必须是非空字符串，实际为 {text!r}")

        url = f"{self.brain_url}/chat/turn"
        body = json.dumps(
            {"sessionId": session_id, "text": text, "channel": "voice"},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # WHY 单独捕获：HTTPError 是 URLError 的子类，必须先判，否则类型名会失真
            raise KefuBrainError(
                f"brain {url} 返回 HTTP {exc.code}: {exc.reason}"
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise KefuBrainError(
                f"brain {url} 连不上: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KefuBrainError(
                f"brain 响应不是合法 JSON（{type(exc).__name__}）: {raw[:120]!r}"
            ) from exc

        reply = extract_reply(payload)
        if not reply:
            raise KefuBrainError(
                "brain 响应里取不到回复文本，字段为 "
                + (f"{sorted(payload)}" if isinstance(payload, dict)
                   else type(payload).__name__)
                + "——禁止静默返回空回复"
            )
        return reply

    # ------------------------------------------------------------------
    # voice worker（stdio 行协议）
    # ------------------------------------------------------------------
    def _worker(self) -> subprocess.Popen:
        """按需拉起 worker；已死则重新拉起（worker 崩溃不该让整条链路静默失效）。"""
        if self._proc is not None and self._proc.poll() is None:
            return self._proc

        py, script = Path(self.worker_python), Path(self.worker_script)
        # 校验顺序：脚本先于解释器——脚本是使用者意图的直接对象（覆盖最常见：
        # 只传了 script 做测试/替换），且在未部署 kefu 的机器上缺省解释器必然不存在，
        # 先报解释器会把「还没配 kefu」误报成「解释器坏了」。
        if not script.is_file():
            raise KefuWorkerError(f"voice worker 脚本不存在: {self.worker_script}")
        if not py.is_file():
            raise KefuWorkerError(f"voice worker 解释器不存在: {self.worker_python}")
        cwd = Path(self.worker_cwd)
        if not cwd.is_dir():
            raise KefuWorkerError(f"voice worker 工作目录不存在: {self.worker_cwd}")

        # WHY：离线开关对齐 probe.py——本机 HF 缓存已就位，禁止联网下载模型
        env = dict(os.environ)
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"

        self._proc = subprocess.Popen(
            [str(py), str(script)],
            cwd=str(cwd), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        return self._proc

    def _readline(self, proc: subprocess.Popen, timeout: float) -> str:
        """读一行响应，带墙钟超时（行协议 lock-step：一次请求一行响应）。

        用线程实现超时：readline 阻塞在 fd 上无法用 select 跨平台覆盖，
        线程 + join(timeout) 是最省依赖的做法。超时的读线程是 daemon，
        会随进程退出回收；代价是超时后 worker 那一次请求仍在排队——
        这属于上游行为，本层只负责把超时如实报错。
        """
        box: dict = {}

        def _reader():
            try:
                box["line"] = proc.stdout.readline()
            except Exception as exc:          # 读端异常必须上浮，不许吞掉
                box["err"] = exc

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            raise KefuWorkerError(
                f"voice worker 超过 {timeout:.0f}s 未返回一行响应（协议无响应/上游阻塞）"
            )
        if "err" in box:
            raise KefuWorkerError(
                f"voice worker 读取响应失败: {type(box['err']).__name__}: {box['err']}"
            )
        line = box.get("line", "")
        if not line:
            raise KefuWorkerError(
                "voice worker 已退出（读到 EOF）——检查 .venv-voice 与 HF 缓存是否就位"
            )
        return line.rstrip("\r\n")

    def _call(self, req: dict, timeout: Optional[float] = None) -> dict:
        """发一行请求、读一行响应、解析并检查 ok 标志。"""
        with self._lock:
            proc = self._worker()
            started = time.perf_counter()
            try:
                proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise KefuWorkerError(
                    f"写 voice worker 失败（worker 可能已退出）: {type(exc).__name__}: {exc}"
                ) from exc

            line = self._readline(proc, timeout or self.timeout_s)
            wall_ms = (time.perf_counter() - started) * 1000

            try:
                resp = json.loads(line)
            except json.JSONDecodeError as exc:
                raise KefuWorkerError(
                    f"voice worker 响应不是合法 JSON（{type(exc).__name__}）: {line[:160]!r}"
                ) from exc

            if not isinstance(resp, dict):
                raise KefuWorkerError(
                    f"voice worker 响应必须是对象，实际为 {type(resp).__name__}"
                )
            # WHY：ok=False 也必须报错。行协议本身"成功返回了一行"，
            #      若当成功用，失败就会被静默吞成空结果（红线：静默降级）
            if resp.get("ok") is False:
                raise KefuWorkerError(
                    f"voice worker {req.get('op')} 失败: "
                    f"{str(resp.get('error'))[:240]}（wall={wall_ms:.0f}ms）"
                )
            return resp

    def transcribe(self, wav_bytes: bytes) -> str:
        """把用户音频转成文本（op=stt）。

        异常：
            TypeError:      wav_bytes 非 bytes 或为空
            KefuWorkerError: worker 失败 / 未返回文本
        """
        if not isinstance(wav_bytes, (bytes, bytearray)) or not wav_bytes:
            raise TypeError(
                f"wav_bytes 必须是非空 bytes，实际为 {type(wav_bytes).__name__}"
            )

        resp = self._call({
            "op": "stt",
            "wavB64": base64.b64encode(bytes(wav_bytes)).decode("ascii"),
            "lang": "zh",
        })
        text = resp.get("text")
        if not isinstance(text, str) or not text.strip():
            raise KefuWorkerError(
                f"op=stt 未返回文本（只见字段 {sorted(resp)}）——禁止静默返回空串"
            )
        return text.strip()

    def synthesize_live(self, text: str) -> Tuple[bytes, str]:
        """慢路合成（op=tts，say 档），返回 (wav 字节, 引擎标识)。

        异常：
            TypeError:      text 非非空字符串
            KefuWorkerError: worker 失败 / 响应里没有音频
        """
        if not isinstance(text, str) or not text.strip():
            raise TypeError(f"synthesize_live 需要非空文本，实际为 {text!r}")

        resp = self._call({"op": "tts", "text": text})
        return extract_wav(resp), str(resp.get("engine") or "unknown")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def close(self) -> None:
        """关掉 worker（幂等）。"""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    def __enter__(self) -> "KefuClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
