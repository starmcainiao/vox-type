# adapters.tts_omlx.probe — 可用性探针（ffmpeg → 端点 → 模型名，按 synthesize 的检查顺序）
# 为什么独立成文件（T79，2026-10-10）：把探活从 adapter.py 拆出来，才放得下第三条前置条件
#   而不撞 adapters/AGENTS.md §② 的 150 行硬上限（拆之前 115 行，加了模型检查变 166 行、
#   被 tools/structure_budget 当场拦下）。这正是 §⑨.1 早就写下的「拆『HTTP 传输/探活』
#   两层可自然回 150 内」——本文件是那条建议的兑现，不是新开的豁免。
# 权威定义在 adapter.py：BASE_URL / DEFAULT_MODEL / DEFAULT_FFMPEG / FFMPEG_MISSING 一律
#   **由调用方传入**，本模块不复制第二份。两处各写一个默认地址，迟早会漂
#   （本仓已因「同一件事记在多处」栽过）。import 方向单向 adapter → probe。
# 本层只做**判定**：能探到就报「可用」，探不到就报「不可用 + 缺什么 + 怎么补」。
#   探针只负责给出**确定的**不该跑，不负责替服务方猜测——查不到证据时一律判「可用」，
#   让 synthesize 去抛它自己的响亮错误（探针本身绝不能成为新的失败源）。
import json
import shutil
import socket
import urllib.request
from urllib.parse import urlsplit

PROBE_TIMEOUT = 2.0  # 探活超时（秒）：与 adapter.PROBE_TIMEOUT 同值，实测同源


def endpoint_reachable(base: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """TCP 能不能连上 `base` 的主机端口。判据与仓内既有探活同源，不另发明一套。"""
    try:
        host, port = urlsplit(base).hostname, urlsplit(base).port
    except ValueError:
        return False
    if not (host and port):
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def model_absent(base: str, model: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """查 `GET {base}/v1/models` 里有没有 `model`。**三态**返回，避免把「查不到」误判成「不在」。

    · `True`  —— **确认**不在：拿到清单了，且确实没有这一项
    · `False` —— 确认在，**或拿不到清单**（老服务无此端点 / 超时 / 非 JSON）

    后者刻意与「在」同侧：探针只负责给出确定的不该跑，不负责替服务方猜测。
    网络异常一律吞成 `False`——**探针的失败绝不能升级成套件的失败**。
    """
    try:
        with urllib.request.urlopen(f"{base}/v1/models", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — 见上：探针只降级，不抛
        return False
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return False
    ids = {e.get("id") for e in entries if isinstance(e, dict)}
    return bool(ids) and model not in ids


def probe(base: str, ffmpeg: str, model: str, ffmpeg_missing_template: str) -> tuple[bool, str]:
    """按 synthesize 的检查顺序逐条探，返回 `(可用, 原因)`；多条原因用 `"; "` 分隔。

    **顺序与 synthesize 完全一致**（先 ffmpeg、后端点、再模型）：ffmpeg 是确定性失败，
    synthesize 在发任何请求之前就查它。只探一半的探针比没有探针更坏——
    T77 漏了 ffmpeg（T78 补），T78 补齐后仍漏模型（T79 补），两次都是同一族缺陷。

    多条原因**全报**而不是只报第一条：只报一条会让人补完一个再跑一遍才发现另一个。
    """
    reasons = []
    if shutil.which(ffmpeg) is None:
        reasons.append(ffmpeg_missing_template.format(name=ffmpeg))
    if not endpoint_reachable(base):
        # 解析不出主机端口（env 设了空串之类的非法值）也走这里：同样记不可用并把值点出来，
        # 不猜端点。无参构造下这条走不到——__init__ 会先抛 ValueError，套件以 error 响亮失败。
        reasons.append(f"端点不可用：{base}")
    elif model_absent(base, model):
        # T79：端点通不代表模型在。DEFAULT_MODEL 是写死的默认值，服务端装了什么模型是
        # 宿主机状态，两者会漂（实测 2026-10-10：本机 oMLX 上默认那个模型已不在清单里）。
        # 点名修法：模型名进 env 的唯一入口就是 VOX_TTS_MODEL（与 voice / endpoint 同一套约定）。
        reasons.append(
            f"模型不在服务端清单：{model}——用 env 指定服务端实际提供的模型："
            "export VOX_TTS_MODEL=<model-id>"
        )
    return (not reasons, "; ".join(reasons))