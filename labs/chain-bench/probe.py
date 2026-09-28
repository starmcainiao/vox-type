#!/usr/bin/env python3
"""链路级语音段测探针（直驱 kefu-agent 的 voice_worker 行协议）。

为什么直驱 worker：段级时延（ASR/TTS）是本项目的对拍对象，而起整套
brain/PG/HTTP（8096）只会引入与结论无关的噪声与失败面。
口径：每段一次请求一行响应，耗时取 worker 自报 elapsedMs + 调用方墙钟，
      预热单独一轮不计入样本（对齐 kefu-agent 自己的 bench 口径）。
"""
import base64, json, os, pathlib, subprocess, sys, tempfile, time

REPO = pathlib.Path("<kefu-agent 仓根>")
CV = REPO / "organs" / "客服" / "channel-voice"
VENV_PY = REPO / ".venv-voice" / "bin" / "python"
WORKER = CV / "voice_worker.py"
OUT = pathlib.Path("/tmp/chain-probe")          # 样本 JSONL 落这里
OUT.mkdir(parents=True, exist_ok=True)
AUDIO = pathlib.Path(tempfile.mkdtemp(prefix="chain-probe-audio-"))  # 音频只放临时区，不进仓

# packs/repair 的 12 条话术（每组取第一个 variant）
PHRASES = json.load(open("<仓库根>/packs/repair/phrases.json", encoding="utf-8"))["phrases"]
KEYS = ["greeting_welcome", "ask_fault_type", "ask_fault_detail", "ask_fault_urgency",
        "ask_address", "ask_contact", "ask_time_window", "confirm_summary",
        "confirm_again", "ticket_created", "promise_visit", "closing_thank_you"]
TEXTS = {p["key"]: p["variants"][0] for p in PHRASES}


class Worker:
    def __init__(self):
        env = dict(os.environ)
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        self.p = subprocess.Popen(
            [str(VENV_PY), str(WORKER)], cwd=str(CV), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )

    def call(self, req: dict, timeout: float = 200.0) -> tuple[dict, float]:
        t0 = time.perf_counter()
        self.p.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()      # 行协议 lock-step
        wall = (time.perf_counter() - t0) * 1000
        if not line:
            err = self.p.stderr.read()[:400]
            return {"ok": False, "error": f"worker 无响应/退出: {err}"}, wall
        return json.loads(line), wall

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def say_wav(text: str, path: pathlib.Path) -> pathlib.Path:
    """用 macOS say 造一段 16k 单声道 wav（当"用户说话"的输入）。"""
    subprocess.run(["say", "-v", "Tingting", "-o", str(path), "--file-format=WAVE",
                    "--data-format=LEI16@16000", text], check=True)
    return path


def pct(vals, q):
    s = sorted(vals)
    if not s:
        return None
    pos = (len(s) - 1) * q
    lo = int(pos); hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 1)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    w = Worker()
    print("== 0. 存活 ==")
    r, wall = w.call({"op": "ping"})
    print(f"  ping -> {r} ({wall:.0f}ms)")
    r, wall = w.call({"op": "warmup"})
    print(f"  warmup -> ok={r.get('ok')} sttState={r.get('sttState')} ({wall:.0f}ms)")

    if which in ("all", "stt"):
        print("\n== 1. ASR（mlx-whisper large-v3-turbo，本机 MLX）==")
        rows = []
        for i, key in enumerate(KEYS):
            text = TEXTS[key]
            wav = say_wav(text, AUDIO / f"user_{i:02d}.wav")
            b64 = base64.b64encode(wav.read_bytes()).decode("ascii")
            r, wall = w.call({"op": "stt", "wavB64": b64, "lang": "zh"})
            rows.append({"key": key, "text": text, "wall_ms": round(wall, 1),
                         "elapsedMs": r.get("elapsedMs"), "ok": r.get("ok"),
                         "heard": (r.get("text") or "")[:40]})
            print(f"  {i:2d} {key:18s} wall={wall:7.1f}ms worker={r.get('elapsedMs')} ok={r.get('ok')} 听到={row_heard if (row_heard:=(r.get('text') or '')[:24]) else ''}")
        (OUT / "stt_samples.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
        good = [x["wall_ms"] for x in rows if x.get("ok")]
        if good:
            print(f"  → ASR 墙钟 P50={pct(good,0.5)}ms P99={pct(good,0.99)}ms n={len(good)}")

    if which in ("all", "tts"):
        print("\n== 2. TTS：Breeze（mlx-audio 子进程，worker 内 120s 超时）==")
        r, wall = w.call({"op": "tts", "text": TEXTS["greeting_welcome"]}, timeout=200)
        print(f"  一次尝试 -> ok={r.get('ok')} engine={r.get('engine')} wall={wall:.0f}ms")
        if not r.get("ok"):
            print(f"  error={str(r.get('error'))[:200]}")
        (OUT / "tts_breeze_attempt.json").write_text(
            json.dumps({"ok": r.get("ok"), "engine": r.get("engine"),
                        "wall_ms": round(wall, 1), "error": str(r.get("error"))[:400]},
                       ensure_ascii=False, indent=2), encoding="utf-8")

    w.close()
    print("\n(worker 已关闭)")
