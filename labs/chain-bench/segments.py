#!/usr/bin/env python3
"""段级补齐：say 逐句合成 + 本地 oMLX LLM 热态 TTFT（报告 §三 的两行）。

用法：python3 labs/chain-bench/segments.py [say|llm|all]
输出：<本目录>/raw/tts_say_samples.jsonl、raw/llm_warm_samples.jsonl
"""
import json, pathlib, subprocess, sys, tempfile, time, urllib.request

OUT = pathlib.Path(__file__).resolve().parent / "raw"
OUT.mkdir(parents=True, exist_ok=True)
AUDIO = pathlib.Path(tempfile.mkdtemp(prefix="chain-seg-audio-"))   # 音频只放临时区，不进仓
PACK = pathlib.Path("<仓库根>/packs/repair/phrases.json")
KEYS = ["greeting_welcome", "ask_fault_type", "ask_fault_detail", "ask_fault_urgency",
        "ask_address", "ask_contact", "ask_time_window", "confirm_summary",
        "confirm_again", "ticket_created", "promise_visit", "closing_thank_you"]
TEXTS = {p["key"]: p["variants"][0] for p in json.load(open(PACK, encoding="utf-8"))["phrases"]}
OMLX = "http://127.0.0.1:10099/v1/chat/completions"
MODEL = "mlx-community--Qwen3.5-4B-MLX-4bit"


def say_samples() -> None:
    rows = []
    for i, key in enumerate(KEYS):
        out = AUDIO / f"say_{i:02d}.wav"
        t0 = time.perf_counter()
        subprocess.run(["say", "-v", "Tingting", "-o", str(out), "--file-format=WAVE",
                        "--data-format=LEI16@16000", TEXTS[key]], check=True)
        rows.append({"key": key, "text": TEXTS[key],
                     "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                     "bytes": out.stat().st_size})
    (OUT / "tts_say_samples.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"say 段样本 {len(rows)} 条 → {OUT/'tts_say_samples.jsonl'}")


def _one(msg: str):
    body = json.dumps({"model": MODEL, "stream": True, "max_tokens": 48,
                       "messages": [{"role": "system", "content": "你是报修客服，回答要短。"},
                                    {"role": "user", "content": msg}]}).encode()
    req = urllib.request.Request(OMLX, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer 1111"})
    t0 = time.perf_counter(); ttft = None
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if chunk.get("choices", [{}])[0].get("delta", {}).get("content"):
                ttft = ttft if ttft is not None else (time.perf_counter() - t0) * 1000
    return ttft, (time.perf_counter() - t0) * 1000


def llm_samples() -> None:
    _one("你好")                                   # 预热，不计样本
    rows = []
    for msg in ["我要报修空调不制冷", "家里水管漏水了", "什么时候能上门"]:
        ttft, total = _one(msg)
        rows.append({"user": msg, "ttft_ms": round(ttft, 1), "total_ms": round(total, 1)})
    (OUT / "llm_warm_samples.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"LLM 热态样本 {len(rows)} 条 → {OUT/'llm_warm_samples.jsonl'}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "say"):
        say_samples()
    if which in ("all", "llm"):
        llm_samples()
