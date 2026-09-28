#!/usr/bin/env python3
"""labs.omlx-reliability.probe — oMLX /v1/audio/speech 端点可靠性基线探针（T69）。

定位（**只取证，不修产品代码**）：
  对 packs/heat_kefu/ 的**固定句集**跑固定轮次，逐句记录
  请求墙钟耗时 / 返回音频时长 / 是否触发 quality 拒绝 / 失败时的完整错误文本。
  产物 = 本目录 report.json。

为什么这个探针存在（卡 §二 的事实前提）：
  Quick Start 4 次里 3 次失败，失败形态是某句音频约 327 秒、被 compiler/quality.py
  的 MAX_DURATION_MS=30000 判 rc=4。但写卡前人工直接探端点 6/6 都正常（1.4–8.0 s），
  复现不出来 → 本探针把「猜」变成「不猜」，只给尾部证据（max / P99），不给单点结论。

请求体与 adapters/tts_omlx 的默认路径**完全一致**（无 ref_audio）：
  {"model": ..., "input": text, "response_format": "wav", "speed": 1.0, "voice": "default"}
  常量抄自 adapters/tts_omlx/adapter.py（BASE_URL / DEFAULT_MODEL / rate_map["normal"]）
  与 postprocess.build_payload()。本探针**不 import 任何产品模块**——import 产品代码
  会把 labs 绑到冻结区，且 transport.fetch() 自带 5 次重试与 600 s 超时，会**掩盖**
  尾部而不是暴露它（见 README「我做的取舍」）。

统计口径（与 core 无关，labs 自持；README 有全表）：
  近秩百分位：p(N, q) = 排序后下标 ceil(q/100*N) - 1，取 clamp>=0 后的值。
  每轮请求之间不 sleep：探针测的是「连续单请求无排队」的端点行为，
  引入人为间隔会让指标脱离真实预铸序列（compiler/prebake.py 串行、无 sleep）。

退出码（**labs 自持约定，与 bin/vox CLI 冻结退出码无关**）：
  0  全部请求成功且 quality 零拒绝
  2  至少一条 quality 拒绝（duration_over_30s / head_silence_over / tail_silence_over）
  3  跑批断言失败（有失败请求、或成功数为 0）—— 假绿比红更坏，不许 0 退出

依赖：只用 python 标准库。不加依赖（本卡明令不许 pip install）。
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from http.client import HTTPException as HttpLibError
from pathlib import Path

# --- 常量：全部抄自产品代码 / 规格，不自创 -------------------------------------

SPEECH_ENDPOINT = "/v1/audio/speech"
BASE_URL = "http://127.0.0.1:10099"           # adapters/tts_omlx/adapter.py:29
DEFAULT_MODEL = "Qwen3-TTS-12Hz-0.6B-Base-bf16"   # adapters/tts_omlx/adapter.py:31
SPEED_NORMAL = 1.0                            # adapter.rate_map["normal"]
VOICE = "default"                             # postprocess.build_payload 的默认音色分支

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = REPO_ROOT / "packs" / "heat_kefu" / "phrases.json"
OUT_PATH = Path(__file__).resolve().parent / "report.json"

ROUNDS = 3
SENTENCES = 40

# 卡 §三 的硬上限（「单请求超过硬上限就打标记并继续，不许挂死整轮」）。
# 600 是 transport 的默认 socket timeout——用它会和「无上限」一样把整轮挂死，故显式下调。
# 327 s 的异常样本 120 s 内到不了（到不了即被标记），仍能进 max/P99。
HARD_LIMIT_S = 120.0

# 尾部观测带：墙钟超过这个值就单独列出（不是拒绝条件，只是让慢请求看得见）。
SLOW_MARK_S = 20.0

# quality 判据，抄自 compiler/quality.py（数值不得自创）：
# 26: MAX_DURATION_MS=30000 / 27: MAX_HEAD_SILENCE_MS=100.0 / 28: MAX_TAIL_SILENCE_MS=150.0
MAX_DURATION_MS = 30000
MAX_HEAD_SILENCE_MS = 100.0
MAX_TAIL_SILENCE_MS = 150.0
# 本探针不做 ffmpeg 归一（零依赖）；head/tail 静音在 adapter 的生产路径里会被
# postprocess.normalize_from 处理，所以这两条拒绝理由对**原始端点输出**而言偏严，
# 报告里按理由拆开计数，不让它们淹没时长信号。
RAW_DURATION_OVER_S = MAX_DURATION_MS / 1000.0   # 30.0 s

# 质量拒绝（quality_rejected）在 AC4 下视为「可复现根因入口」，落盘留证。
CAPTURE_MAX_B = 25 * 1024 * 1024

REJECT_REASONS = ("duration_over_30s", "head_silence_over", "tail_silence_over")


# --- WAV 探测：不解析 ffmpeg，不读全文件 --------------------------------------


def audio_stats(path: Path, limit_b: int = 16 * 1024 * 1024) -> dict:
    """从 WAV 头取时长/采样率；再用**有限流**统计首尾静音与峰值。

    只读前 limit_b 字节（16MB @24k/16bit/mono ≈ 337 s，够覆盖 327 s 异常样本），
    超限时置 truncated=True 并如实标注——绝不把「没读全」当成「后面没声」。
    静音判据 |sample| <= 328 与 compiler/quality.py 的 SILENCE_THRESHOLD 同值，
    只在单声道 16-bit 下成立（多声道会按声道块读，结果失真，故只支持 1 声道）。
    """
    with wave.open(str(path), "rb") as w:
        nframes = w.getnframes()
        rate = w.getframerate()
        chans = w.getnchannels()
        bits = w.getsampwidth()
        if chans != 1 or bits != 2:
            return {"ok": False,
                    "error": f"非单声道 16-bit WAV（channels={chans} width={bits}），"
                             f"无法按 quality.py 判据统计静音"}
        total_seconds = nframes / float(rate) if rate else 0.0
        max_b = min(nframes * 2, limit_b)
        data = w.readframes(min(max_b, 1 << 20))
        chunks = []
        while data and sum(len(c) for c in chunks) < max_b:
            chunks.append(data)
            data = w.readframes(min(max_b - sum(len(c) for c in chunks), 1 << 20))
    raw = b"".join(chunks)
    n = len(raw) // 2
    if n == 0:
        return {"ok": False, "error": "无 PCM 样本可分析"}
    lo, hi = n, 0
    for i in range(n):
        v = raw[i * 2] | (raw[i * 2 + 1] << 8)
        if v >= 1 << 15:
            v -= 1 << 16      # 16-bit 补码 → 有符号
        if v <= 328 and v >= -328:   # quality.py 的判据是 |s| <= 328
            if lo > i:
                lo = i
        else:
            hi = i
            break
    ro, peak = n, 0
    for i in range(n - 1, -1, -1):
        v = raw[i * 2] | (raw[i * 2 + 1] << 8)
        if v >= 1 << 15:
            v -= 1 << 16
        peak = max(peak, abs(v))
        if v <= 328 and v >= -328:
            if ro < i:
                ro = i
        else:
            break
    return {"ok": True, "duration_ms": round(total_seconds * 1000.0, 1),
            "duration_s": round(total_seconds, 3), "sample_rate": rate,
            "channels": chans, "bits": bits, "pcm_frames_analyzed": n,
            "truncated": bool(n < nframes),
            "head_silence_ms": round(lo / float(rate) * 1000.0, 1),
            "tail_silence_ms": round((n - ro) / float(rate) * 1000.0, 1),
            "peak_abs": peak,
            "all_silent": lo == hi + 1}


def is_wav(raw: bytes) -> bool:
    return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"


def _repo_relative(path: Path) -> str:
    """把语料路径压成相对仓根的形式；压不回去就只留文件名。

    报告是提交的产物，写绝对路径等于把跑批机器的卷布局（卷名、用户名、挂载点）
    一起提交进公开仓。仓内相对路径既不泄露、又跨机器可复现。
    """
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return path.name


# --- 统计 --------------------------------------------------------------------


def nearest_rank(values, q):
    """近秩百分位：空列表返回 None，否则 ceil(q/100*N)-1 处的值。"""
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q / 100.0 * len(s)) - 1))]


def summary_field(name, values):
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": round(min(values), 4),
            "p50": round(nearest_rank(values, 50), 4),
            "p99": round(nearest_rank(values, 99), 4),
            "max": round(max(values), 4),
            "mean": round(sum(values) / len(values), 4)}


def quality_gate(stats):
    """按 compiler/quality.py 的三条判据对**原始端点输出**做时长/静音门。"""
    reasons = []
    if stats["duration_ms"] > MAX_DURATION_MS:
        reasons.append("duration_over_30s")
    if stats["head_silence_ms"] > MAX_HEAD_SILENCE_MS:
        reasons.append("head_silence_over")
    if stats["tail_silence_ms"] > MAX_TAIL_SILENCE_MS:
        reasons.append("tail_silence_over")
    return reasons


# --- 取证落盘 ----------------------------------------------------------------


def socket_timeout_exc():
    import socket
    return socket.timeout


def one_request(url, payload, tmp_dir, attempt_no, attempt_ns, timeout, retries, max_bytes):
    """一次合成请求：返回 {status: success|failed, ...}。

    超时**硬自杀**：单请求超过 timeout 就打 timeout=True 并继续，绝不挂死整轮（AC2）。
    失败默认不重试（端点可靠性测试要看真实失败率）；retries>0 才按指数退避补试，
    且每次尝试都单独落盘计时，失败时 error 字段带全部尝试的完整错误文本。
    """
    total_t0 = time.monotonic()
    timeout_hit = False
    errors = []
    body = b""
    status, headers = None, {}
    tmp = None
    last_attempt_s = None

    for attempt in range(retries + 1):
        t0 = time.monotonic()
        tmp = tmp_dir / f"tts-{attempt_ns}-{attempt}.wav"
        last_attempt_s = round(time.monotonic() - t0, 4)
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status = r.status
                headers = {k: v for k, v in r.getheaders()}
                body = r.read(max_bytes)
            tmp.write_bytes(body)
            if not is_wav(body):
                raise ValueError(f"响应不是 WAV（前 16 字节={body[:16]!r}）")
            break
        except Exception as exc:
            partial = body      # 已读到的部分字节（读中断类失败常有完整或半截 WAV）
            try:
                tmp.unlink()
            except OSError:
                pass
            if isinstance(exc, (socket_timeout_exc(), TimeoutError)):
                timeout_hit = True
            errors.append({"attempt": attempt + 1, "elapsed_s": last_attempt_s,
                           "type": type(exc).__name__, "text": str(exc)})
            if attempt < retries:
                time.sleep(min(2.0 * (2 ** attempt), 30.0))
                continue
            body = partial

    wall = round(time.monotonic() - total_t0, 4)
    rec = {"attempt_no": attempt_no, "wall_clock_s": wall, "retries_used": len(errors)}
    if errors:
        rec["status"] = "failed"
        rec["timeout"] = timeout_hit
        rec["audio_duration_ms"] = None
        rec["quality_rejected"] = False
        rec["response_bytes"] = None
        rec["error"] = "\n".join(f"[attempt {e['attempt']}] {e['type']}: {e['text']}"
                                 for e in errors)
        rec["error_types"] = [e["type"] for e in errors]
        if is_wav(body):
            rec["error_response_is_wav"] = True   # 失败响应里还带着可解析的音频
    else:
        stats = audio_stats(tmp)
        rec["status"] = "success"
        rec["timeout"] = False
        rec["http_status"] = status
        rec["response_bytes"] = len(body)
        rec["error"] = None
        rec["content_type"] = headers.get("Content-Type")
        rec["content_length"] = headers.get("Content-Length")
        if not stats.get("ok"):
            rec["audio_duration_ms"] = None
            rec["quality_rejected"] = False
            rec["decode_error"] = stats.get("error")
        else:
            reasons = quality_gate(stats)
            rec["audio_duration_ms"] = stats["duration_ms"]
            rec["quality_rejected"] = bool(reasons)
            rec["rejection_reasons"] = reasons
            rec["head_silence_ms"] = stats["head_silence_ms"]
            rec["tail_silence_ms"] = stats["tail_silence_ms"]
            rec["sample_rate"] = stats["sample_rate"]
            rec["all_silent"] = stats["all_silent"]
            rec["truncated_analysis"] = stats["truncated"]
        rec["tmp_wav"] = tmp.name if tmp is not None else None
    return rec


# --- 主流程 ------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description="oMLX /v1/audio/speech 可靠性基线探针（T69）")
    p.add_argument("--rounds", type=int, default=ROUNDS)
    p.add_argument("--sentences", type=int, default=SENTENCES)
    p.add_argument("--retries", type=int, default=0,
                   help="失败重试次数（默认 0：端点可靠性测试要看真实失败率）")
    p.add_argument("--corpus", default=str(CORPUS_PATH))
    p.add_argument("--url", default=BASE_URL + SPEECH_ENDPOINT)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--timeout", type=float, default=HARD_LIMIT_S,
                   help="单请求硬上限秒数，超时打标记并继续（AC2）")
    p.add_argument("--slow-mark", type=float, default=SLOW_MARK_S)
    p.add_argument("--out", default=str(OUT_PATH))
    p.add_argument("--keep-tmp", action="store_true", help="不清理 /tmp 临时音频")
    a = p.parse_args()

    started = round(time.time(), 3)
    base_dir = Path(tempfile.mkdtemp(prefix="vox-t69-"))
    tmp_dir = base_dir / "tmp"
    tmp_dir.mkdir()

    corpus = json.loads(Path(a.corpus).read_text(encoding="utf-8"))["phrases"]
    # 固定句集：phrases.json 原顺序（不排序、不随机），取前 N 句——轮次之间逐字相同，
    # 保证「同句多轮」可比。固定种子 = 无随机性（本探针不掷骰子，可复现靠的就是这点）。
    picked = [(i, ph["key"], ph["variants"][0], len(ph["variants"]))
              for i, ph in enumerate(corpus[:a.sentences])]

    payload_base = {"model": a.model, "response_format": "wav",
                    "speed": SPEED_NORMAL, "voice": VOICE}
    plan_total = a.rounds * len(picked)

    results, captured = [], []
    print(f"[probe] url={a.url} model={a.model} voice={VOICE} speed={SPEED_NORMAL} "
          f"ref_audio=NO rounds={a.rounds} sentences={len(picked)} plan={plan_total} "
          f"hard_limit={a.timeout}s tmp={base_dir}", flush=True)

    for r in range(1, a.rounds + 1):
        ok_n = fail_n = rej_n = to_n = 0
        for idx, key, text, variant_count in picked:
            payload = {**payload_base, "input": text}
            res = one_request(a.url, payload, tmp_dir, r * 1000 + idx,
                              f"r{r}-{idx:03d}", a.timeout, a.retries, CAPTURE_MAX_B)
            res.update({"round": r, "key": key, "variant_index": idx,
                        "variant_count": variant_count, "text": text, "payload": payload})
            results.append(res)
            ok_n += res["status"] == "success"
            fail_n += res["status"] == "failed"
            rej_n += bool(res.get("quality_rejected"))
            to_n += bool(res.get("timeout"))
            if res["status"] == "failed":
                print(f"[{len(results):>3}/{plan_total}] R{r} {key} FAIL "
                      f"{res['error_types']} {res['wall_clock_s']}s", flush=True)
            elif res.get("quality_rejected"):
                print(f"[{len(results):>3}/{plan_total}] R{r} {key} REJECT "
                      f"{res['rejection_reasons']} audio={res['audio_duration_ms']}ms", flush=True)
            if res.get("quality_rejected") or (res["status"] == "failed"
                                               and res.get("error_response_is_wav")):
                # AC4：质量拒绝（或失败响应里还带着可解析 WAV）时原样留证。
                cap_dir = base_dir / "captured"
                cap_dir.mkdir(parents=True, exist_ok=True)
                cap_key = f"r{r}-{idx:03d}-{key}"
                raw = b""
                if res["status"] == "failed" and res.get("error_response_is_wav"):
                    # 失败路径的原始字节没留盘，只能落元数据（音频字节已丢，如实标注）。
                    (cap_dir / f"{cap_key}.json").write_text(
                        json.dumps({"note": "失败响应带 WAV 头，但重试路径已丢弃原始字节；"
                                            "本条仅留元数据", **res},
                                   ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")
                else:
                    res_wav = tmp_dir / res["tmp_wav"] if res.get("tmp_wav") else None
                    if res_wav is not None and res_wav.exists():
                        res_wav.replace(cap_dir / f"{cap_key}.wav")
                        raw = (cap_dir / f"{cap_key}.wav").read_bytes()
                meta = {"key": cap_key, "variant_index": idx, "text": text,
                        "payload": res["payload"], "status": res["http_status"],
                        "response_bytes": res.get("response_bytes"),
                        "wav": f"captured/{cap_key}.wav" if raw else None,
                        "wall_clock_s": res["wall_clock_s"],
                        "quality_rejected": bool(res.get("quality_rejected")),
                        "rejection_reasons": res.get("rejection_reasons"),
                        "captured_at_epoch": round(time.time(), 3)}
                (cap_dir / f"{cap_key}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")
                captured.append(meta)
        # 临时音频逐轮清掉（留证音频已复制进 captured/），/tmp 不堆积。
        for f in sorted(tmp_dir.glob("tts-*.wav")):
            f.unlink(missing_ok=True)
        print(f"[probe] round {r}/{a.rounds} done: ok={ok_n} fail={fail_n} "
              f"reject={rej_n} timeout={to_n}", flush=True)

    # 收尾清理
    if not a.keep_tmp:
        shutil.rmtree(base_dir, ignore_errors=True)

    ok = [x for x in results if x["status"] == "success" and x["audio_duration_ms"] is not None]
    decoded_fail = [x for x in results if x["status"] == "success" and x.get("decode_error")]
    failed = [x for x in results if x["status"] == "failed"]
    walled = [x["wall_clock_s"] for x in results]
    ok_walled = [x["wall_clock_s"] for x in ok]
    durs = [x["audio_duration_ms"] for x in ok]

    report = {
        "schema": "vox.labs.omlx-reliability/1",
        "card": "T69",
        "started_at_epoch": started,
        "finished_at_epoch": round(time.time(), 3),
        "total_wall_s": round(time.time() - started, 2),
        "endpoint": a.url,
        "model": a.model,
        "voice": VOICE,
        "speed": SPEED_NORMAL,
        "request_carries_ref_audio": False,
        "request_body_keys": sorted(payload_base) + ["input"],
        "corpus": {
            # 写仓内相对路径，不写绝对路径：report.json 是**要提交的产物**，
            # 绝对路径会把跑批机器的卷布局带进公开仓（tools/check_no_machine_paths.py 会拦）。
            # 换机器重跑时这个字段仍可复现——相对仓根的路径不随机器变。
            "source_file": _repo_relative(Path(a.corpus)),
            "selection": "phrases.json 原顺序前 N 句，每句取 variants[0]",
            "available_sentences": len(corpus),
            "sentences_used": len(picked),
            "keys": [k for _, k, _, _ in picked],
        },
        "config": {
            "rounds_planned": a.rounds,
            "rounds_completed": len({x["round"] for x in results}),
            "retries": a.retries,
            "hard_timeout_s": a.timeout,
            "slow_mark_s": a.slow_mark,
            "seed": "fixed-corpus-no-randomness",
            "concurrency": 1,
        },
        "counts": {
            "planned": plan_total,
            "executed": len(results),
            "not_run": plan_total - len(results),
            # 失败率分母 = 已跑数（不是计划数）：跑不完时绝不把部分结果写成全量。
            "failure_rate": f"{len(failed) + len(decoded_fail)}/{len(results)}"
            if results else "0/0",
            "success": len(ok) + len(decoded_fail),
            "failed": len(failed),
            "timeout": sum(1 for x in results if x["timeout"]),
            "decoded_fail": len(decoded_fail),
            "quality_rejected": sum(1 for x in results if x.get("quality_rejected")),
            "quality_rejected_by_reason": {k: sum(1 for x in ok if k in x.get("rejection_reasons", []))
                                           for k in REJECT_REASONS},
            "slow_over_mark": sum(1 for x in results if x["wall_clock_s"] > a.slow_mark),
            "audio_over_30s": sum(1 for x in ok if x["audio_duration_ms"] > MAX_DURATION_MS),
            "audio_all_silent": sum(1 for x in ok if x.get("all_silent")),
        },
        # AC1：max 与 P99 必须都在（只给均值会掩盖尾部——327 s 在均值里看不见）。
        "wall_clock_s": summary_field("wall_clock_s", walled),
        "wall_clock_s_success_only": summary_field("wall_clock_s_success_only", ok_walled),
        "audio_duration_ms": summary_field("audio_duration_ms", durs),
        "slow_outliers": [
            {"round": x["round"], "key": x["key"], "wall_clock_s": x["wall_clock_s"],
             "audio_duration_ms": x["audio_duration_ms"],
             "quality_rejected": bool(x.get("quality_rejected")),
             "status": x["status"]}
            for x in results if x["wall_clock_s"] > a.slow_mark
        ],
        "quality_rejections": [
            {"round": x["round"], "key": x["key"], "audio_duration_ms": x["audio_duration_ms"],
             "head_silence_ms": x.get("head_silence_ms"),
             "tail_silence_ms": x.get("tail_silence_ms"),
             "rejection_reasons": x.get("rejection_reasons")}
            for x in results if x.get("quality_rejected")
        ],
        "failures": [
            {"round": x["round"], "key": x["key"], "wall_clock_s": x["wall_clock_s"],
             "timeout": bool(x.get("timeout")), "error_types": x.get("error_types"),
             "error": x.get("error")}
            for x in results if x["status"] == "failed"
        ],
        "decoded_failures": [
            {"round": x["round"], "key": x["key"], "response_bytes": x.get("response_bytes"),
             "decode_error": x.get("decode_error")}
            for x in results if x.get("decode_error")
        ],
        "captured": captured,
        "results": results,
    }

    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                           encoding="utf-8")
    print(f"[probe] wrote {a.out}", flush=True)

    # AC3：跑批断言 + 非零退出码。断言失败也先把报告落盘再退出（上面已写）。
    problems = []
    if len(results) != plan_total:
        problems.append(f"executed={len(results)} != planned={plan_total}")
    if len(failed) + len(decoded_fail) != 0:
        problems.append(f"失败请求 {len(failed) + len(decoded_fail)} 条"
                        f"（成功 {len(ok)}/{len(results)}）——成功数不等于已跑数")
    if not ok:
        problems.append("成功数为 0：没有可用于统计的样本")
    if problems:
        print("[probe] ASSERT FAILED:", "; ".join(problems), file=sys.stderr)
        return 3
    if report["counts"]["quality_rejected"]:
        print(f"[probe] {report['counts']['quality_rejected']} 条 quality 拒绝"
              f"（理由分布 {report['counts']['quality_rejected_by_reason']}）")
        return 2
    print(f"[probe] {len(results)}/{plan_total} 全成功，0 拒绝；"
          f"wall max={report['wall_clock_s']['max']}s p99={report['wall_clock_s']['p99']}s "
          f"audio max={report['audio_duration_ms']['max']}ms "
          f"p99={report['audio_duration_ms']['p99']}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
