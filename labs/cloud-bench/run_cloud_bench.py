#!/usr/bin/env python3
"""
labs/cloud-bench/run_cloud_bench.py — 云端语音链路（阿里百炼）延迟分解 vs 预铸命中对照

三臂设计（docs/17 缓存分层论证的实测补全）：
  臂A LLM 生成：qwen-turbo（kefu persona，stream）→ TTFT + 总时长（n 轮用户话术）
  臂B TTS 合成：CosyVoice REST（一次性返回，口径注明）→ TTFA≈总时长
       · B1: 对臂A生成的回复合成（真口径：生成什么合成什么）
       · B2: 对 heat_kefu 40 条预铸句合成（若未命中，这些话术要花的 TTS 成本）
  臂C 预铸命中：find_hit 读包（本地，T29 API）
  未命中轮生产口径 ≈ 臂A(总) + 臂B(总)；命中轮 = 臂C。

凭据：labs/cloud-bench/.env.local（gitignored）读 DASHSCOPE_API_KEY；不回显、不入报告。
产物：raw/*.jsonl + report.json（同目录）。失败 fail-closed：无 Key / 端点不通 → 非零退出。
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

RAW = HERE / "raw"
BASE = "https://dashscope.aliyuncs.com"
TTS_URL = BASE + "/api/v1/services/aigc/multimodal-generation/generation"
LLM_MODEL = "qwen-turbo"
TTS_MODEL = "qwen-tts"
TTS_VOICE = "Cherry"

USER_TURNS = [  # 用户轮次（公开 demo 文案，触发流程回复）
    "我要查一下账单", "我家暖气不热", "帮我报修", "转人工", "怎么缴费",
    "查一下我的工单", "户号是 NO20250001", "供暖温度标准是多少", "报停供暖怎么办理", "你好",
]
PERSONA = ("你是供热公司智能客服。礼貌、简洁、口语化，一句话回复（不超过60字），"
           "不承诺时效，不编造数字。用户问业务时给出流程引导。")


def load_key() -> str:
    envf = HERE / ".env.local"
    if not envf.is_file():
        print(f"[FATAL] 凭据文件不存在: {envf}（见 README——DASHSCOPE_API_KEY=...）", file=sys.stderr)
        sys.exit(2)
    for line in envf.read_text().splitlines():
        if line.strip().startswith("DASHSCOPE_API_KEY="):
            k = line.split("=", 1)[1].strip()
            if k:
                return k
    print("[FATAL] .env.local 里没有 DASHSCOPE_API_KEY", file=sys.stderr)
    sys.exit(2)


def post(url: str, key: str, body: dict, timeout: float = 60) -> tuple:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    r = urllib.request.urlopen(req, timeout=timeout)
    data = r.read()
    return data, time.perf_counter() - t0


def arm_a_llm(key: str, n: int) -> list:
    """臂A：LLM 生成（stream 首字节 = TTFT）。"""
    rows = []
    for i in range(n):
        turn = USER_TURNS[i % len(USER_TURNS)]
        body = {"model": LLM_MODEL, "stream": True,
                "messages": [{"role": "system", "content": PERSONA},
                             {"role": "user", "content": turn}]}
        req = urllib.request.Request(
            f"{BASE}/compatible-mode/v1/chat/completions", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t0 = time.perf_counter(); ttft = None; chunks = 0; text = []
        with urllib.request.urlopen(req, timeout=60) as r:
            for line in r:
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunks += 1
                if ttft is None:
                    ttft = time.perf_counter() - t0
                try:
                    delta = json.loads(payload)["choices"][0]["delta"].get("content")
                    if delta: text.append(delta)
                except Exception:
                    pass
        total = time.perf_counter() - t0
        rows.append({"arm": "A_llm", "turn": turn, "ttft_s": round(ttft, 4),
                     "total_s": round(total, 4), "chunks": chunks,
                     "reply": "".join(text)})
        print(f"[A {i+1}/{n}] ttft={ttft*1000:.0f}ms total={total*1000:.0f}ms "
              f"reply={rows[-1]['reply'][:24]!r}", flush=True)
    return rows


def arm_b_tts(key: str, texts: list, tag: str) -> list:
    """臂B：qwen-tts REST 合成（两段：generation 响应 + OSS url 下载；口径=非流式）。"""
    rows = []
    for i, text in enumerate(texts):
        body = {"model": TTS_MODEL,
                "input": {"text": text, "voice": TTS_VOICE},
                "parameters": {"format": "wav"}}
        t0 = time.perf_counter()
        try:
            gen = None
            for attempt in range(4):          # 429 退避重试（限流是环境事实，如实等待不掩盖）
                try:
                    req = urllib.request.Request(TTS_URL, data=json.dumps(body).encode(),
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                    r = urllib.request.urlopen(req, timeout=120)
                    gen = json.loads(r.read())
                    break
                except urllib.error.HTTPError as he:
                    if he.code == 429 and attempt < 3:
                        time.sleep(3 * (attempt + 1))
                        t0 = time.perf_counter()   # 重试后重新计时，不把等限流的时间算进延迟
                        continue
                    raise
            if gen is None:
                raise RuntimeError("TTS 连续 429，退避耗尽")
            gen_s = time.perf_counter() - t0
            audio_url = gen["output"]["audio"]["url"]
            dl0 = time.perf_counter()
            audio = urllib.request.urlopen(audio_url, timeout=120).read()
            dl_s = time.perf_counter() - dl0
            total = time.perf_counter() - t0
            rows.append({"arm": tag, "text": text, "gen_s": round(gen_s, 4),
                         "download_s": round(dl_s, 4), "total_s": round(total, 4),
                         "bytes": len(audio), "head": audio[:4].hex()})
            print(f"[{tag} {i+1}/{len(texts)}] gen={gen_s*1000:.0f}ms dl={dl_s*1000:.0f}ms "
                  f"total={total*1000:.0f}ms bytes={len(audio)}", flush=True)
        except Exception as exc:
            rows.append({"arm": tag, "text": text, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{tag} {i+1}/{len(texts)}] ERROR {exc}", flush=True)
        time.sleep(1.2)   # 主动节流：qwen-tts 有 QPS 限制，避免撞 429
    return rows


def arm_c_precast() -> list:
    """臂C：预铸命中（find_hit 读包；包从 packs/heat_kefu 现场铸到临时目录）。"""
    import tempfile, subprocess
    from assets import load_pack
    from adapters.framework_kefu import find_hit
    tmp = Path(tempfile.mkdtemp(prefix="cloudbench-pack-"))
    subprocess.run([str(REPO / "bin" / "vox"), "pack", "build", "packs/heat_kefu",
                    "--out", str(tmp / "heat-kefu-1"), "--json"],
                   check=True, capture_output=True, cwd=REPO)
    pack = load_pack(tmp / "heat-kefu-1")
    src = json.load(open(REPO / "packs" / "heat_kefu" / "phrases.json"))["phrases"]
    rows = []
    for e in src:
        text = e["variants"][0]
        t0 = time.perf_counter()
        hit = find_hit(pack, text=text, rate_key="normal")
        dt = time.perf_counter() - t0
        rows.append({"arm": "C_precast", "text": text, "query_s": round(dt, 6),
                     "hit": hit.entry is not None and hit.miss_reason is None})
    import shutil; shutil.rmtree(tmp, ignore_errors=True)
    print(f"[C] {len(rows)} 条查询，命中 {sum(r['hit'] for r in rows)}", flush=True)
    return rows


def main() -> int:
    key = load_key()
    RAW.mkdir(exist_ok=True)
    n_llm = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print(f"== 臂A：LLM 生成 ×{n_llm}（{LLM_MODEL} stream）==", flush=True)
    a = arm_a_llm(key, n_llm)
    print(f"== 臂B1：生成回复合成 ×{len(a)}（{TTS_MODEL} REST）==", flush=True)
    b1 = arm_b_tts(key, [r["reply"] for r in a if r.get("reply")], "B1_gen_tts")
    print("== 臂B2：预铸句合成 ×40（未命中假想成本）==", flush=True)
    src = json.load(open(REPO / "packs" / "heat_kefu" / "phrases.json"))["phrases"]
    b2 = arm_b_tts(key, [e["variants"][0] for e in src], "B2_prebaked_tts")
    print("== 臂C：预铸命中（本地读包）==", flush=True)
    c = arm_c_precast()

    def blk(vals, unit="s"):
        vals = [v for v in vals if v is not None]
        if not vals: return {"n": 0}
        return {"n": len(vals), "mean": round(statistics.mean(vals), 4),
                "P50": round(statistics.median(vals), 4),
                "min": round(min(vals), 4), "max": round(max(vals), 4)}

    ok_b1 = [r["total_s"] for r in b1 if "total_s" in r]
    ok_b2 = [r["total_s"] for r in b2 if "total_s" in r]
    report = {
        "probe": "cloud-bench-dashscope",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "caliber": {
            "llm": f"{LLM_MODEL} stream，TTFT=首个 delta chunk；total=全部 chunk（含 [DONE]）",
            "tts": f"{TTS_MODEL} REST 两段口径：gen（服务端合成响应）+ download（OSS 音频拉取）；总 TTFA=gen+download，生产流式 WS 会更早，本口径为上界",
            "miss_chain": "未命中轮生产口径 ≈ 臂A.total + 臂B.total（不含 ASR；ASR 为独立流式段）",
            "hit_chain": "命中轮 = find_hit 读包（微秒级）；含磁盘/索引缓存的进程内口径",
            "credentials": "Key 从 gitignored .env.local 读取；本报告不含任何凭据内容",
        },
        "armA_llm": {"ttft_s": blk([r["ttft_s"] for r in a]),
                     "total_s": blk([r["total_s"] for r in a])},
        "armB1_gen_tts": {"total_s": blk(ok_b1), "errors": sum(1 for r in b1 if "error" in r)},
        "armB2_prebaked_tts": {"total_s": blk(ok_b2), "errors": sum(1 for r in b2 if "error" in r)},
        "armC_precast": {"query_s": blk([r["query_s"] for r in c]),
                         "hits": sum(1 for r in c if r["hit"]), "n": len(c)},
        "chain_compare": {},
        "env": {"llm_model": LLM_MODEL, "tts_model": TTS_MODEL, "tts_voice": TTS_VOICE,
                "n_llm_turns": len(a)},
    }
    a_tot = report["armA_llm"]["total_s"]
    b_tot = report["armB2_prebaked_tts"]["total_s"]
    c_q = report["armC_precast"]["query_s"]
    if a_tot.get("n") and b_tot.get("n") and c_q.get("n"):
        miss_p50 = round(a_tot["P50"] + b_tot["P50"], 4)
        hit_p50 = c_q["P50"]
        report["chain_compare"] = {
            "miss_chain_P50_s": miss_p50, "hit_chain_P50_s": hit_p50,
            "ratio": round(miss_p50 / hit_p50, 1) if hit_p50 else None,
            "note": "P50 口径相加（近似；分布之和的 P50 ≤ 各自 P50 之和，此处为保守上界）",
        }
    (RAW / "armA_llm.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in a) + "\n")
    (RAW / "armB_tts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in b1 + b2) + "\n")
    (RAW / "armC_precast.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in c) + "\n")
    (HERE / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["chain_compare"], ensure_ascii=False, indent=1))
    print("[report] report.json + raw/*.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
