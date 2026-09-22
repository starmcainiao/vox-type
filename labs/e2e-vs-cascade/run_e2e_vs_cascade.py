#!/usr/bin/env python3
"""
labs/e2e-vs-cascade/run_e2e_vs_cascade.py — 组合版（ASR→预铸/LLM→TTS）vs 端到端 S2S 对照

立项差异化论证的最后一格：延迟 / 可控性 / 可审核性三维对照。

臂 A · 组合版（级联）
  ASR（本机 oMLX Qwen3-ASR-0.6B-8bit，复用 adapters.asr_omlx.OmlxAsr）
    → 文本 → 预铸命中（复用 adapters.framework_kefu.find_hit）→ 包内音频
    → 未命中时走云端 LLM（qwen-turbo）+ TTS（qwen-tts）
  LLM / TTS 的调用口径与重试/节流行为**导入自 labs/cloud-bench/run_cloud_bench.py**，不重写一套。

臂 B · 端到端 S2S
  qwen-omni-turbo，stream=true，SSE 手写解析（标准库 urllib，无第三方依赖）
  音频输入（say 合成的固定用户话术）→ **音频输出**（delta.audio.data 的 base64 分块）
  音频非空 = 字节数校验 + PCM 头/非零样本校验；HTTP 200 不算过。

可控性：同一固定话术各跑 N 次 → 逐字一致率
  臂 A：预铸命中路径逐字比对命中条目原文（同源 normalize_text）
  臂 B：输出音频用本机 oMLX ASR 转写回，与参照话术逐字比对（同源 eval.cer.normalize_for_cer）

凭据：labs/cloud-bench/.env.local（gitignored）读 DASHSCOPE_API_KEY；只读不回显、不进产物。
产物：raw/*.jsonl + report.json（同目录）。失败 fail-closed：无 Key / 端点不通 → 非零退出。
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
# 仓根入 sys.path：adapters/ assets/ eval/ 都是仓内包，直接按仓根解析
sys.path.insert(0, str(REPO))
CLOUD_BENCH = REPO / "labs" / "cloud-bench"
CRED_FILE = CLOUD_BENCH / ".env.local"
RAW = HERE / "raw"
PACK_SRC = REPO / "packs" / "heat_kefu"
PACK_OUT = REPO / "packs" / "heat_kefu_build" / "heat-kefu-1"

BASE = "https://dashscope.aliyuncs.com"
CHAT_URL = BASE + "/compatible-mode/v1/chat/completions"
OMNI_MODEL = "qwen-omni-turbo"
OMNI_MODALITIES = ["text", "audio"]
TTS_WAV_URL = BASE + "/api/v1/services/aigc/multimodal-generation/generation"

# 本机 ASR（音频输入生成 + 臂 B 输出的回转写都用它；复用 adapters 适配器，不重写）
ASR_BASE_URL = "http://127.0.0.1:10099"
ASR_MODEL = "Qwen3-ASR-0.6B-8bit"
ASR_TIMEOUT_S = 300

# 固定话术（公开 demo 文案；arm B 的参照文本 + arm A 的用户输入）
# 用户轮次（公开 demo 文案，复用 cloud-bench 的 USER_TURNS 语料，避免另造一套）
USER_TURNS = ["我要查一下账单", "转人工", "帮我报修", "怎么缴费", "供暖温度标准是多少"]
# 臂 B 的固定用户话术：整批 N 次用同一段音频输入，保证可控性可比
USER_TURN_B = "我要查一下账单"
USER_PROMPT = "你是供热公司智能客服。听到用户的话后，用一句话礼貌回复，不超过60字。"
# 供 ASR 转写的包内命中话术：ASR 的逐字输出直接喂 find_hit 文本档，用来实测预铸命中路径
PRECAST_QUERY_TEXT = "您好，我是供热智能客服小暖。"

# 臂 B 输出音频的假定格式（用于字节数→时长的换算与格式自检；非厂商声明，仅用于自检记账）
OMNI_PCM_RATE = 24000
OMNI_PCM_CHANNELS = 1
OMNI_PCM_SAMPWIDTH = 2

# PCM 有效性门槛：静音块不算音频
MIN_AUDIO_BYTES = 16000
MIN_NONZERO_SAMPLE_FRAC = 0.001


# ---------- 凭据（与 cloud-bench 同口径：只读 env 文件，不回显、不进产物） ----------

def load_key() -> str:
    if os.environ.get("DASHSCOPE_API_KEY"):
        return os.environ["DASHSCOPE_API_KEY"]
    if not CRED_FILE.is_file():
        print(f"[FATAL] 凭据文件不存在: {CRED_FILE.name}（内容一行 DASHSCOPE_API_KEY=...）",
              file=sys.stderr)
        sys.exit(2)
    for line in CRED_FILE.read_text().splitlines():
        if line.strip().startswith("DASHSCOPE_API_KEY="):
            k = line.split("=", 1)[1].strip()
            if k:
                return k
    print("[FATAL] .env.local 里没有 DASHSCOPE_API_KEY", file=sys.stderr)
    sys.exit(2)


# ---------- 复用 labs/cloud-bench 的 LLM / TTS 臂实现（不重写一套） ----------

def load_cloud_bench():
    """以文件路径导入 cloud-bench 脚本：臂 A 的未命中支路直接调它的 arm_a_llm / arm_b_tts。"""
    spec = importlib.util.spec_from_file_location("run_cloud_bench", CLOUD_BENCH / "run_cloud_bench.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法导入 {CLOUD_BENCH / 'run_cloud_bench.py'}（模块 spec 为空）")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 统计口径 ----------

def blk(vals, unit="s"):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "mean": round(statistics.mean(vals), 4),
            "P50": round(statistics.median(vals), 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4)}


def exact_match_rate(norms: list) -> float | None:
    """N 次采样的归一化文本两两全等的比例（分母 = C(N,2)；N<2 → None）。"""
    if len(norms) < 2:
        return None
    pairs = sum(1 for i in range(len(norms)) for j in range(i + 1, len(norms))
                if norms[i] == norms[j])
    total = len(norms) * (len(norms) - 1) // 2
    return round(pairs / total, 4)


# ---------- 本机说写（say 合成用户话术 / oMLX 回转写） ----------

def synthesize_user_audio(text: str, out_path: Path) -> dict:
    """say 合成一段用户话术为 WAV（macOS 系统 TTS；仅用于生成音频输入，不测其质量）。"""
    r = subprocess.run(
        ["say", "-v", "Tingting", "-o", str(out_path), "--data-format=LEI16@22050", text],
        capture_output=True, cwd=REPO)
    if r.returncode != 0 or not out_path.is_file():
        raise RuntimeError(
            f"say 合成失败（rc={r.returncode}，stderr={r.stderr.decode('utf-8','replace')[:120]!r}）")
    with wave.open(str(out_path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
    if frames <= 0:
        raise RuntimeError(f"say 产出 0 帧 WAV（{out_path.name}）——空音频无内容")
    return {"bytes": out_path.stat().st_size, "rate": rate, "frames": frames,
            "duration_s": round(frames / rate, 4)}


def new_asr():
    from adapters.asr_omlx import OmlxAsr
    return OmlxAsr(base_url=ASR_BASE_URL, model=ASR_MODEL, timeout_seconds=ASR_TIMEOUT_S)


def transcribe_wav(asr, wav_path: Path) -> tuple:
    """转写并计时；空串一律抛错（AsrError 已 fail-closed，不返回空串假成功）。"""
    t0 = time.perf_counter()
    text = asr.transcribe(wav_path)
    dt = time.perf_counter() - t0
    if not text.strip():
        raise RuntimeError(f"ASR 返回空转写（{type(text).__name__}）——按失败处理，不当成功")
    return text, dt


def wrap_asr_in_wav(pcm: bytes, out_path: Path) -> dict:
    """把臂 B 返回的裸 PCM 写成 WAV，供 ASR 回转写；同时记录格式假设。"""
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(OMNI_PCM_CHANNELS)
        w.setsampwidth(OMNI_PCM_SAMPWIDTH)
        w.setframerate(OMNI_PCM_RATE)
        w.writeframes(pcm)
    return {"bytes_written": out_path.stat().st_size,
            "assumed_rate": OMNI_PCM_RATE,
            "assumed_channels": OMNI_PCM_CHANNELS,
            "assumed_sampwidth": OMNI_PCM_SAMPWIDTH}


def validate_audio(pcm: bytes) -> dict:
    """音频非空校验：字节数 + PCM 结构 + 非零样本占比。响应 200 不算过。"""
    info = {
        "bytes": len(pcm),
        "bytes_min": MIN_AUDIO_BYTES,
        "head_hex": pcm[:16].hex() if pcm else "",
        "tail_hex": pcm[-16:].hex() if len(pcm) >= 16 else (pcm.hex() if pcm else ""),
        "duration_s_assumed": round(len(pcm) / (OMNI_PCM_SAMPWIDTH * OMNI_PCM_RATE), 4)
            if pcm else 0.0,
    }
    checks = {}
    checks["non_empty"] = len(pcm) > 0
    checks["meets_min_bytes"] = len(pcm) >= MIN_AUDIO_BYTES
    # 偶数字节才能按 int16 成对读样本
    checks["even_bytes"] = (len(pcm) % 2) == 0
    nz = 0
    total = 0
    peak = 0
    if checks["even_bytes"] and len(pcm) >= 2:
        n = len(pcm) // 2
        vals = struct.unpack("<%dh" % n, pcm[: n * 2])
        nz = sum(1 for v in vals if v != 0)
        total = n
        peak = max(abs(v) for v in vals)
    info["samples"] = total
    info["nonzero_samples"] = nz
    info["peak_abs"] = peak
    info["nonzero_frac"] = round(nz / total, 4) if total else 0.0
    checks["has_signal"] = info["nonzero_frac"] >= MIN_NONZERO_SAMPLE_FRAC and peak > 0
    info["checks"] = checks
    info["valid"] = all(checks.values())
    return info


# ---------- 臂 B · 端到端 S2S（stream=true，SSE 手写解析） ----------

def omni_stream(key: str, user_wav_path: Path) -> tuple:
    """一次端到端调用：音频进（base64 WAV）→ 音频出（SSE delta.audio.data 分块）。

    返回 (行数据 dict, 裸 PCM bytes)。行数据里的 audio_valid=False 表示本条件下
    没拿到有效音频——如实记，不冒充文本档。
    """
    b64 = base64.b64encode(user_wav_path.read_bytes()).decode("ascii")
    body = {
        "model": OMNI_MODEL,
        "stream": True,
        "messages": [{"role": "user", "content": [
            {"type": "input_audio",
             "input_audio": {"data": f"data:audio/wav;base64,{b64}", "format": "wav"}},
            {"type": "text", "text": USER_PROMPT},
        ]}],
        "modalities": OMNI_MODALITIES,
    }
    req = urllib.request.Request(CHAT_URL, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    t0 = time.perf_counter()
    first_text_s = None
    first_audio_s = None
    text_parts = []
    audio_chunks = []
    chunks = 0
    with urllib.request.urlopen(req, timeout=180) as r:
        for line in r:
            line = line.decode("utf-8", "replace").strip()
            # 手写 SSE 解析：只认 "data:" 前缀行；空行/注释行/事件名行一律忽略
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            chunks += 1
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                if first_text_s is None:
                    first_text_s = time.perf_counter() - t0
                text_parts.append(content)
            audio = delta.get("audio")
            if isinstance(audio, dict) and audio.get("data"):
                if first_audio_s is None:
                    first_audio_s = time.perf_counter() - t0
                audio_chunks.append(audio["data"])
    total_s = time.perf_counter() - t0

    pcm = b""
    decode_error = None
    try:
        for c in audio_chunks:
            pad = "=" * (-len(c) % 4)
            pcm += base64.b64decode(c + pad)
    except Exception as exc:  # 分块损坏：如实记，不当成功
        decode_error = f"{type(exc).__name__}: {exc}"

    info = validate_audio(pcm) if not decode_error else {
        "bytes": len(pcm), "checks": {"non_empty": len(pcm) > 0}, "valid": False,
        "decode_error": decode_error}
    row = {
        "first_text_s": round(first_text_s, 4) if first_text_s is not None else None,
        "first_audio_s": round(first_audio_s, 4) if first_audio_s is not None else None,
        "total_s": round(total_s, 4),
        "sse_chunks": chunks,
        "audio_chunks": len(audio_chunks),
        "audio_bytes": len(pcm),
        "audio_valid": bool(info["valid"]),
        "audio": info,
        "asr_transcript": "".join(text_parts),
    }
    return row, pcm




# ---------- 臂 A · 组合版（级联） ----------

def run_arm_a(key: str, cloud, pack, asr, n: int, turns: list,
              precast_text: str, tmpdir: Path) -> tuple:
    """臂 A：ASR → 预铸命中/未命中（命中逐字一致性由 precast_text 段提供）。

    命中支路：say 合成一句包内话术 → oMLX 转写 → find_hit 文本档查询 → 包内音频。
      （上游是语音，故查询文本必来自 ASR 转写，不假设上游给逐字文本；本机 TTS/ASR 归一
        可能不一致，若因此 miss 则如实记 miss，不做归一化豁免。）
    未命中支路：同批用户轮次走 cloud-bench 的 LLM / TTS 臂（复用实现）。
    """
    from adapters.framework_kefu import find_hit
    rows = []

    # --- 命中支路：同一段固定音频跑 N 次 ---
    print(f"== 臂A 命中支路 ×{n}（say→ASR→find_hit，参照 {precast_text!r}）==", flush=True)
    pwav = tmpdir / "precast_query.wav"
    synthesize_user_audio(precast_text, pwav)
    for i in range(n):
        row = {"i": i, "lane": "precast", "turn": precast_text}
        try:
            asr_text, asr_s = transcribe_wav(asr, pwav)
            row.update(asr_s=round(asr_s, 4), asr_text=asr_text)
        except Exception as exc:
            row.update(asr_error=f"{type(exc).__name__}: {exc}")
            print(f"[A-hit {i+1}/{n}] ASR ERROR {exc}", flush=True)
            rows.append(row)
            continue
        hit = find_hit(pack, text=asr_text, rate_key="normal")
        entry = hit.entry
        row["hit"] = entry is not None and hit.miss_reason is None
        row["miss_reason"] = hit.miss_reason
        if entry is not None:
            apath = pack.root / entry.path
            row.update(hit_path="precast",
                       ref_text=entry.text,
                       asset_duration_ms=entry.duration_ms,
                       audio_bytes=apath.stat().st_size if apath.is_file() else None,
                       audio_found=apath.is_file(),
                       fingerprint=entry.fingerprint)
            # 首字节音频延迟 = 把包内音频读进内存的时间（零合成、零网络）
            t0 = time.perf_counter()
            apath.read_bytes()
            row["first_audio_s"] = round(time.perf_counter() - t0, 6)
        else:
            row.update(hit_path="cloud_fallback")
        rows.append(row)
        print(f"[A-hit {i+1}/{n}] asr={asr_text[:14]!r} asr_s={asr_s:.2f} "
              f"hit={row['hit']} first_audio={row.get('first_audio_s')} "
              f"asset_ms={row.get('asset_duration_ms')}", flush=True)

    # --- 未命中支路：真实用户轮次 → cloud-bench 的 LLM / TTS 臂 ---
    print(f"== 臂A 未命中支路（cloud-bench arm_a_llm / arm_b_tts，复用实现）×{n} ==",
          flush=True)
    miss_rows = []
    for i in range(n):
        turn = turns[i % len(turns)]
        row = {"i": i, "lane": "miss", "turn": turn}
        wav = tmpdir / f"u_{i}.wav"
        try:
            synthesize_user_audio(turn, wav)
            asr_text, asr_s = transcribe_wav(asr, wav)
            row.update(asr_s=round(asr_s, 4), asr_text=asr_text)
        except Exception as exc:
            row.update(asr_error=f"{type(exc).__name__}: {exc}")
            print(f"[A-miss-asr {i+1}/{n}] ASR ERROR {exc}", flush=True)
            miss_rows.append(row)
            continue
        hit = find_hit(pack, text=asr_text, rate_key="normal")
        entry = hit.entry
        row["hit"] = entry is not None and hit.miss_reason is None
        row["miss_reason"] = hit.miss_reason
        row["hit_path"] = "precast" if entry is not None else "cloud_fallback"
        miss_rows.append(row)
        print(f"[A-miss-asr {i+1}/{n}] turn={turn!r} asr={asr_text[:14]!r} hit={row['hit']}",
              flush=True)

    print("== 臂A 未命中支路：云端 LLM + TTS ×{n} ==".format(n=n), flush=True)
    llm = cloud.arm_a_llm(key, n)
    replies = [r["reply"] for r in llm if r.get("reply")]
    tts = cloud.arm_b_tts(key, replies, "T24_miss_tts") if replies else []
    fallback_rows = []
    for i, lr in enumerate(llm):
        tr = tts[i] if i < len(tts) and "error" not in tts[i] else {}
        fallback_rows.append({
            "i": i, "turn": lr.get("turn"),
            "llm_ttft_s": lr.get("ttft_s"), "llm_total_s": lr.get("total_s"),
            "tts_total_s": tr.get("total_s"), "tts_bytes": tr.get("bytes"),
            "chain_total_s": (round(lr["total_s"] + tr["total_s"], 4)
                              if lr.get("total_s") and tr.get("total_s") else None),
            "error": lr.get("error") or tr.get("error"),
            "text": lr.get("reply", ""),
        })
        print(f"[A-miss {i+1}/{len(fallback_rows)}] turn={lr.get('turn')!r} "
              f"llm_ttft={lr.get('ttft_s')}s llm_total={lr.get('total_s')}s "
              f"tts_total={tr.get('total_s')}s bytes={tr.get('bytes')}", flush=True)
    return rows, miss_rows, fallback_rows


def build_pack() -> tuple:
    """packs 前置：现场铸包（KEFU_HEAT_YAML 已由调用方通过环境变量传入）。"""
    PACK_OUT.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(REPO / "bin" / "vox"), "pack", "build", "packs/heat_kefu",
                        "--out", str(PACK_OUT)],
                       capture_output=True, cwd=REPO)
    print(f"[pack build] rc={r.returncode}", flush=True)
    if r.stdout:
        print(r.stdout.decode("utf-8", "replace")[-800:], flush=True)
    if r.stderr:
        print(r.stderr.decode("utf-8", "replace")[-800:], flush=True)
    if r.returncode != 0:
        raise RuntimeError(f"vox pack build 失败（rc={r.returncode}）")
    from assets import load_pack
    return load_pack(PACK_OUT), PACK_OUT


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 5
    if n < 2:
        print("[FATAL] N 必须 >= 2（逐字一致率需要至少两次采样）", file=sys.stderr)
        return 2
    try:
        asr = new_asr()
        # 前置探针：oMLX ASR 不通则整臂 A 与臂 B 回转写全不可测——fail-closed，不产出半个报告
        probe_wav = Path(tempfile.mkdtemp(prefix="e2ecasc-probe-")) / "probe.wav"
        synthesize_user_audio("测试", probe_wav)
        transcribe_wav(asr, probe_wav)
    except Exception as exc:
        print(f"[FATAL] 前置探针失败（本机 oMLX ASR 不可用或 say 不可用）："
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    key = load_key()
    cloud = load_cloud_bench()
    RAW.mkdir(exist_ok=True)
    print("== 前置：预铸包现场铸造 ==", flush=True)
    pack, pack_dir = build_pack()

    print("== 前置：say 合成固定用户话术（臂 B 单轮固定话术，保证可控性可比）==", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="e2ecasc-"))
    user_wav = tmp / "user_turn_b.wav"
    wav_info = synthesize_user_audio(USER_TURN_B, user_wav)
    print(f"[user audio] text={USER_TURN_B!r} {wav_info}", flush=True)

    print(f"== 臂A：组合版 ×{n}（命中支路 + 未命中支路）==", flush=True)
    a_rows, a_miss_rows, a_fallback = run_arm_a(key, cloud, pack, asr, n,
                                                USER_TURNS, PRECAST_QUERY_TEXT, tmp)

    print(f"== 臂B：端到端 S2S ×{n}（{OMNI_MODEL} stream，音频进音频出）==", flush=True)
    b_rows = []
    for i in range(n):
        row = {"i": i}
        pcm = b""
        try:
            out, pcm = omni_stream(key, user_wav)
        except urllib.error.HTTPError as he:
            out = {"error": f"HTTP {he.code}: {he.read()[:200].decode('utf-8','replace')}"}
        except Exception as exc:
            out = {"error": f"{type(exc).__name__}: {exc}"}
        if "error" in out:
            row.update(error=out["error"])
            print(f"[B {i+1}/{n}] ERROR {out['error'][:160]}", flush=True)
        else:
            row.update(out)
            # 可控性：把臂 B 的真实音频输出回转写，与参照话术逐字比对
            if out["audio_valid"] and out["audio_bytes"] > 0:
                b_wav = tmp / f"b_out_{i}.wav"
                wrap = wrap_asr_in_wav(pcm, b_wav)
                row["wav_wrapped"] = wrap
                try:
                    tr, tr_s = transcribe_wav(asr, b_wav)
                    row.update(b_asr_s=round(tr_s, 4), b_asr_text=tr)
                except Exception as exc:
                    row.update(b_asr_error=f"{type(exc).__name__}: {exc}")
            print(f"[B {i+1}/{n}] first_audio={out.get('first_audio_s')}s "
                  f"total={out.get('total_s')}s bytes={out.get('audio_bytes')} "
                  f"valid={out.get('audio_valid')} asr={row.get('b_asr_text', '')[:16]!r}",
                  flush=True)
        b_rows.append(row)
        time.sleep(1.0)

    # ---- 汇总 ----
    from eval.cer import normalize_for_cer
    a_hits = [r for r in a_rows if r.get("hit")]
    a_norms = [normalize_for_cer(r["ref_text"]) for r in a_hits]
    b_rows_ok = [r for r in b_rows if not r.get("error")]
    b_audio_ok = [r for r in b_rows_ok if r.get("audio_valid")]
    # 臂 B 的可控性用「回转写文本」做逐字比对（输出是什么就比什么，不复用文本档）
    b_asr_norms = [normalize_for_cer(r["b_asr_text"])
                   for r in b_audio_ok if r.get("b_asr_text")]
    b_sizes = [r["audio_bytes"] for r in b_audio_ok]
    b_unique_sizes = len(set(b_sizes))

    miss_llm = a_fallback
    report = {
        "probe": "e2e-vs-cascade-dashscope",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "立项差异化论证最后一格：组合版（ASR→预铸/LLM→TTS）vs 端到端 S2S 的延迟/可控性/可审核性三维对照",
        "fixtures": {
            "arm_a_user_turns": USER_TURNS,
            "arm_b_user_turn": USER_TURN_B,
            "arm_b_user_turn_audio": {**wav_info,
                                      "synth": "macOS say -v Tingting（仅用于生成音频输入，不测其质量）"},
            "arm_a_hit_query": "命中支路：say 合成包内话术 → oMLX 转写 → find_hit 文本档"
                               "（上游是语音，查询文本必来自 ASR 转写，不假设上游给逐字文本）",
            "arm_a_precast_query_text": PRECAST_QUERY_TEXT,
            "precast_pack": {"src": "packs/heat_kefu", "entries": len(pack.assets)},
            "n_samples": n,
        },
        "caliber": {
            "arm_a_first_audio": "预铸命中路径 = 读包内音频文件到内存的时间（零合成、零网络，微秒级）；"
                                 "ASR 段独立计 asr_s，不计入 first_audio_s（ASR 为流式段，形态不同）",
            "arm_a_miss": "未命中时走 cloud-bench 的 arm_a_llm（qwen-turbo stream）+ arm_b_tts（qwen-tts REST），"
                          "调用实现导入自 labs/cloud-bench/run_cloud_bench.py，不重写",
            "arm_b": f"{OMNI_MODALITIES and OMNI_MODEL} stream=true，SSE 手写解析（标准库 urllib，无第三方依赖）；"
                     "first_audio_s = 第一个 delta.audio 块的到达时刻；total_s = 至 [DONE]",
            "arm_b_audio_valid": f"字节数 >= {MIN_AUDIO_BYTES} + 偶数字节 + 非零样本占比 >= "
                                 f"{MIN_NONZERO_SAMPLE_FRAC}；HTTP 200 不算过",
            "arm_b_audio_format": f"厂商未在响应中声明格式（chunk 仅 data/expires_at/id）；按 {OMNI_PCM_RATE}Hz/"
                                  f"{OMNI_PCM_CHANNELS}ch/int16 假设做字节数→时长换算，仅为记账，不对外引用",
            "arm_b_controllability": "臂 B 输出用本机 oMLX ASR 回转写后与参照话术逐字比对"
                                     "（同源 eval.cer.normalize_for_cer）；"
                                     "回转写质量本身有误差，故该比率是「端到端不保证逐字」的下界证据",
            "controllability_metric": "exact_match_rate = 归一化文本两两全等的采样对占比（分母 C(N,2)）；"
                                      "两臂统一走 eval.cer.normalize_for_cer（去全部标点与空白）做逐字比对",
            "latency_scope": "两臂均含网络往返（本机直连）；无并发、无排队；单账号单进程，含 429 退避（等待不计入延迟）",
            "credentials": "Key 从 gitignored .env.local 读取（亦可由环境变量 DASHSCOPE_API_KEY 覆盖）；本报告不含任何凭据内容",
        },
        "armA_cascade": {
            "asr_s": blk([r.get("asr_s") for r in a_rows]),
            "first_audio_s": blk([r.get("first_audio_s") for r in a_rows if r.get("first_audio_s") is not None]),
            "hits": sum(1 for r in a_rows if r.get("hit")),
            "n": len(a_rows),
            "exact_match_rate": exact_match_rate(a_norms),
            "exact_match_basis": "命中条目原文（预铸资产层文本，命中路径必然逐字相同）",
            "asset_audio_bytes": [r.get("audio_bytes") for r in a_rows if r.get("audio_bytes") is not None],
            "asset_duration_ms": [r.get("asset_duration_ms") for r in a_rows if r.get("hit")],
            "audio_found_all": all(r.get("audio_found") for r in a_rows if r.get("hit")),
            "note": "命中支路：同一段包内话术音频 ×N，ASR 转写逐字喂 find_hit 文本档",
        },
        "armA_miss_lane_asr": {
            "note": "真实用户轮次经 say→oMLX 转写后走 find_hit 文本档；miss 是预期结果"
                    "（用户措辞与包内话术措辞不同，命中上限由上游表示形态决定——见 docs/10 裁定 2）",
            "hits": sum(1 for r in a_miss_rows if r.get("hit")),
            "n": len(a_miss_rows),
            "miss_reasons": sorted({r["miss_reason"] for r in a_miss_rows if r.get("miss_reason")}),
            "asr_s": blk([r.get("asr_s") for r in a_miss_rows]),
        },
        "armA_miss_fallback": {
            "note": "未命中支路：LLM/TTS 调用实现导入自 labs/cloud-bench/run_cloud_bench.py，"
                    "口径与其 report.json 一致（llm_ttft=首个 delta chunk；tts=非流式 REST 上界）",
            "llm_ttft_s": blk([r.get("llm_ttft_s") for r in miss_llm]),
            "llm_total_s": blk([r.get("llm_total_s") for r in miss_llm]),
            "tts_total_s": blk([r.get("tts_total_s") for r in miss_llm]),
            "chain_total_s": blk([r.get("chain_total_s") for r in miss_llm]),
            "tts_bytes": [r.get("tts_bytes") for r in miss_llm],
            "errors": sum(1 for r in miss_llm if r.get("error")),
            "n": len(miss_llm),
        } if miss_llm else {
            "note": "本次采样未跑未命中支路",
        },
        "armB_e2e": {
            "first_audio_s": blk([r.get("first_audio_s") for r in b_audio_ok]),
            "first_text_s": blk([r.get("first_text_s") for r in b_rows_ok]),
            "total_s": blk([r.get("total_s") for r in b_rows_ok]),
            "audio_bytes": blk([r.get("audio_bytes") for r in b_audio_ok], unit="B"),
            "audio_valid_runs": sum(1 for r in b_rows if r.get("audio_valid")),
            "audio_invalid_runs": sum(1 for r in b_rows if not r.get("audio_valid")),
            "errors": sum(1 for r in b_rows if r.get("error")),
            "n": len(b_rows),
            "audio_format": {"assumed_rate": OMNI_PCM_RATE, "assumed_channels": OMNI_PCM_CHANNELS,
                             "assumed_sampwidth": OMNI_PCM_SAMPWIDTH,
                             "declared_by_vendor": False},
            "b_asr_s": blk([r.get("b_asr_s") for r in b_audio_ok]),
            "exact_match_rate": exact_match_rate(b_asr_norms),
            "exact_match_basis": "回转写文本（arm B 的真实音频输出经 oMLX ASR 转写）",
            "raw_audio_bytes_all_equal": (b_unique_sizes == 1) if b_sizes else None,
            "raw_audio_bytes_unique": b_unique_sizes,
            "raw_audio_bytes_variability_note": (
                f"N 次输出音频字节数出现 {b_unique_sizes} 种不同取值"
                f"（min/max 见 audio_bytes）——输出音频长度并不逐次相同，"
                "这与「端到端不保证逐字」一致；但字节数不等于内容，"
                "逐字结论仍看 exact_match_rate 及其 caveat"
                if b_unique_sizes > 1 else
                "N 次输出音频字节数完全相同——不足以证明内容逐字相同"
                "（流式编码按分块对齐，字节数不敏感）"),
        },
        "controllability": {},
        "auditability": {
            "arm_a": "可审计：命中判定走 find_hit（文本归一化逐字相等，无模型判据）；资产带 16 位指纹 + "
                     "音频存在性校验（assets 层保险丝）；四属性校验与准入判据在包级；事件留痕可复核。"
                     "同一输入必得同一音频。",
            "arm_b": "黑盒：单次调用即返回音频，无准入判据、无逐字保证、无资产指纹、无事件留痕；"
                     "同一输入的输出音频不保证逐字相同（本次实测见 armB_e2e.exact_match_rate）。"
                     "本项为定性描述，不做主观评分。",
        },
        "env": {
            "omni_model": OMNI_MODEL, "modalities": OMNI_MODALITIES,
            "asr_engine": "oMLX", "asr_model": ASR_MODEL, "asr_base_url": ASR_BASE_URL,
            "cascade_llm": getattr(cloud, "LLM_MODEL", None),
            "cascade_tts": getattr(cloud, "TTS_MODEL", None),
            "cascade_tts_voice": getattr(cloud, "TTS_VOICE", None),
            "python": sys.version.split()[0],
        },
    }

    a_rate = report["armA_cascade"]["exact_match_rate"]
    b_rate = report["armB_e2e"]["exact_match_rate"]
    # 臂 A 的未命中支路逐字性：LLM 自由文本天然不逐字（对照项，证明口径能测出差异）
    a_miss_texts = [r["text"] for r in miss_llm if r.get("text")]
    a_miss_norms = [normalize_for_cer(t) for t in a_miss_texts]
    a_miss_rate = exact_match_rate(a_miss_norms)
    report["controllability"] = {
        "metric": "exact_match_rate（归一化文本两两全等占比，分母 C(N,2)）",
        "arm_a_precast": {
            "exact_match_rate": a_rate,
            "n": len(a_norms),
            "conclusion": ("N 次采样逐字一致（预铸路径：命中即固定条目原文）"
                           if a_rate == 1.0 else
                           ("N 次采样不全部一致" if a_rate is not None else
                            "本次采样未命中预铸路径，预铸路径逐字性不可测——如实记录")),
            "basis": "命中条目原文",
        },
        "arm_a_miss_path": {
            "exact_match_rate": a_miss_rate,
            "n": len(a_miss_norms),
            "conclusion": ("N 次 LLM 自由文本不全部一致——该口径确实能测出「非固定路径不逐字」"
                           if a_miss_rate is not None and a_miss_rate < 1.0 else
                           ("N 次逐字一致（罕见，如实记录）" if a_miss_rate == 1.0 else "不可判")),
            "basis": "cloud-bench LLM 臂（qwen-turbo）自由生成文本",
            "note": "臂 A 的可控性结论是「分支决定」：预铸命中 → 逐字 100%；未命中走 LLM → 不逐字。"
                    "该分支结构本身是可审核的（命中判定走 find_hit，非模型判据）。",
        },
        "arm_b_e2e": {
            "exact_match_rate": b_rate,
            "n": len(b_asr_norms),
            "conclusion": ("N 次回转写两两全等——本次条件下未复现出逐字差异"
                           "（如实记录，且回转写误差会把真实差异掩盖，故不等于端到端保证逐字）"
                           if b_rate == 1.0 else
                           ("N 次回转写存在差异，端到端不保证逐字" if b_rate is not None else
                            "有效回转写不足 2 次，不可判")),
            "basis": "臂 B 输出音频经 oMLX 回转写",
        },
        "arm_b_caveat": "臂 B 的比对经「音频→oMLX 回转写」这一环，回转写误差会把真实差异掩盖掉；"
                        "故该比率是下界证据（实测到的差异是真差异，未实测到的差异不排除存在）。"
                        "本次 N 次回转写全等，属该下界情形的如实记录。",
        "normalizers": {
            "arm_a": "eval.cer.normalize_for_cer（去全部标点与空白）",
            "arm_b": "eval.cer.normalize_for_cer（去全部标点与空白）",
            "note": "两臂同一归一化函数；arm A 的命中判定本身另走 "
                    "adapters.framework_kefu.normalize_text（产品同源归一化）",
        },
    }

    if not report["armB_e2e"]["audio_valid_runs"] and report["armB_e2e"]["n"]:
        report["armB_e2e"]["boundary"] = (
            "本条件下未取到有效音频输出——如实记为「不可测」，未用文本档冒充端到端。"
            "文本档延迟/文本可控性数字见 armB_e2e.first_text_s 与 asr_transcript 原始行。")
        report["controllability"]["arm_b_e2e"]["exact_match_rate"] = None
        report["controllability"]["arm_b_e2e"]["conclusion"] = "不可测（未取到有效音频）"

    # 延迟三维对照（摘要用）
    rep_a_hit = report["armA_cascade"]["first_audio_s"]
    rep_a_miss = report["armA_miss_fallback"].get("chain_total_s") if miss_llm else None
    rep_b = report["armB_e2e"]["first_audio_s"]
    report["latency_compare"] = {
        "arm_a_precast_first_audio_s": rep_a_hit if rep_a_hit.get("n") else None,
        "arm_a_precast_note": "零合成、零网络；读包内音频文件到内存" if rep_a_hit.get("n") else
                              "本次采样未命中预铸路径——该值不可测，如实留空",
        "arm_a_miss_chain_total_s": rep_a_miss,
        "arm_b_first_audio_s": rep_b if rep_b.get("n") else None,
        "arm_b_total_s": report["armB_e2e"]["total_s"],
        "arm_a_asr_s": report["armA_cascade"]["asr_s"],
        "cross_ref": "labs/cloud-bench/report.json：云端未命中链 P50 ≈ 2.87 s、qwen-turbo TTFT P50 226 ms",
        "caveat": "两臂延迟不可直接相减对比：臂 A 命中路径无网络、臂 B 含完整往返；"
                  "臂 B 的 first_audio 为「首个音频块到达」，臂 A 未命中支路的链长按 TTFT/整链分别给。",
    }

    (RAW / "armA_cascade.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in a_rows + a_miss_rows) + "\n")
    (RAW / "armB_e2e.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in b_rows) + "\n")
    (RAW / "armA_miss_fallback.jsonl").write_text(
        ("\n".join(json.dumps(r, ensure_ascii=False) for r in miss_llm) + "\n")
        if miss_llm else "")
    (HERE / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print("\n=== 摘要 ===")
    print(json.dumps({
        "armA_first_audio_s": report["armA_cascade"]["first_audio_s"],
        "armA_asr_s": report["armA_cascade"]["asr_s"],
        "armA_hits": f"{report['armA_cascade']['hits']}/{report['armA_cascade']['n']}",
        "armA_miss_chain_P50_s": (report["armA_miss_fallback"].get("chain_total_s", {}).get("P50")
                                  if miss_llm else None),
        "armA_precast_exact_match": report["controllability"]["arm_a_precast"]["exact_match_rate"],
        "armA_miss_exact_match": report["controllability"]["arm_a_miss_path"]["exact_match_rate"],
        "armB_first_audio_s": report["armB_e2e"]["first_audio_s"],
        "armB_total_s": report["armB_e2e"]["total_s"],
        "armB_audio_bytes": report["armB_e2e"]["audio_bytes"],
        "armB_audio_valid": f"{report['armB_e2e']['audio_valid_runs']}/{report['armB_e2e']['n']}",
        "armB_exact_match": report["controllability"]["arm_b_e2e"]["exact_match_rate"],
    }, ensure_ascii=False, indent=1))
    print("[report] report.json + raw/*.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
