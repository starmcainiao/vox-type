#!/usr/bin/env python3
"""
labs/kefu-bridge/run_session.py — 真链路联调驱动（T11 验收第 7 条）

把预铸包插进 kefu 真链路，跑两档命中率并把数字并排放出来：

    --mode key   脚本驱动档：turns 里给 key，跳过 brain，直接查包
    --mode text  自由文本档：turns 里给 user_text，说一次用户音频 → ASR → brain → 逐字比对

用法：
    python3 labs/kefu-bridge/run_session.py \
        --pack <已铸包> --turns <turns.json> --out <dir> \
        [--mode key|text|all] [--allow-fallback] [--offline] \
        [--brain-url http://127.0.0.1:8092] [--voice-url http://127.0.0.1:8096] \
        [--user-voice Tingting] [--timeout 180]

turns.json（本仓不存放真实业务文案，样例只放自造文本）：
    [{"user_text": "我要报修空调不制冷"}, {"key": "greeting"}]
    文本档离线联调（--offline）可另带 "reply"：本轮 brain 要说的原文，
    这样不用起 brain 也能量出"真实 LLM 回复"的逐字命中率。

产物只写 --out 指定目录（JSONL + summary.json + 每轮音频）；
仓库内不允许出现 kefu 的业务文案，--out 落在仓库内会打警告。
"""

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]                     # labs/kefu-bridge-*/run_session.py → 仓库根
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from assets import load_pack                                   # noqa: E402
from adapters.framework_kefu import KefuBridge, KefuClient     # noqa: E402
from adapters.tts_macsay.adapter import MacSayTts              # noqa: E402


# ---------------------------------------------------------------------------
# 离线档的假 brain（不调 kefu，回复文本来自 turns JSON 的 reply 字段）
# ---------------------------------------------------------------------------
class OfflineBrain:
    """--offline 用：不调 brain/ASR，只吐 turns 里人工抓好的回复原文。"""

    def __init__(self, reply):
        self.reply = reply
        self.asks = []

    def ask_brain(self, session_id, text):
        self.asks.append((session_id, text))
        if not isinstance(self.reply, str) or not self.reply.strip():
            raise RuntimeError(
                "离线档需要 turns 条目带非空 reply 字段（本轮要说什么）"
            )
        return self.reply

    def transcribe(self, wav_bytes):
        raise RuntimeError("--offline 不调 ASR：请把 user_text 直接喂给 text 档")


class AlwaysFailBrain:
    """占位 client：只用于 key 档（key 档根本不调 brain）。"""

    def ask_brain(self, session_id, text):
        raise RuntimeError("key 档不应调用 brain")

    def transcribe(self, wav_bytes):
        raise RuntimeError("key 档不应调用 ASR")


def say_wav(text, path, voice):
    """用 macOS say 造一段 16k/单声道/16-bit 音频，当"用户说话"的输入。"""
    subprocess.run(
        ["say", "-v", voice, "-o", str(path),
         "--file-format=WAVE", "--data-format=LEI16@16000", text],
        check=True, capture_output=True, text=True,
    )
    return path


def pct(vals, q):
    """分位数（线性插值，口径与 labs/chain-bench 的 probe.py 一致）。"""
    if not vals:
        return None
    s = sorted(vals)
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 2)


def run_mode(mode, turns, pack, args, out_dir):
    """跑一档，返回逐轮记录列表（错误轮也记录，不静默跳过）。"""
    audio_dir = out_dir / f"audio_{mode}"
    audio_dir.mkdir(parents=True, exist_ok=True)

    if args.offline and mode == "text":
        client = None                        # 每轮单独构造 OfflineBrain
    elif args.offline:
        client = AlwaysFailBrain()
    else:
        client = KefuClient(
            brain_url=args.brain_url, voice_url=args.voice_url, timeout_s=args.timeout
        )

    records = []
    started = time.perf_counter()
    for i, turn in enumerate(turns):
        out_wav = audio_dir / f"turn_{i:03d}.wav"
        rec = {"turn": i, "mode": mode, "offline": bool(args.offline)}

        try:
            if mode == "key":
                if "key" not in turn:
                    raise ValueError(f"key 档的 turns 条目必须带 key 字段: {sorted(turn)}")
                session = f"key-{i:03d}"
                bridge = KefuBridge(
                    pack, live_tts=MacSayTts(),
                    allow_fallback=args.allow_fallback, client=client,
                )
                res = bridge.run_turn(session_id=session, key=turn["key"], out_path=out_wav)
            else:
                user_text = turn.get("user_text")
                if not isinstance(user_text, str) or not user_text.strip():
                    raise ValueError(
                        f"text 档的 turns 条目必须带非空 user_text: {sorted(turn)}"
                    )
                if args.offline:
                    client = OfflineBrain(turn.get("reply"))
                    bridge = KefuBridge(
                        pack, live_tts=MacSayTts(),
                        allow_fallback=args.allow_fallback, client=client,
                    )
                    res = bridge.run_turn(
                        session_id=f"text-{i:03d}", text=user_text, out_path=out_wav
                    )
                else:
                    # 真链路：先造用户音频，走 ASR → brain → 命中判定
                    tmp = Path(tempfile.mkdtemp(prefix="kefu-user-"))
                    try:
                        wav = say_wav(user_text, tmp / "user.wav", args.user_voice)
                        bridge = KefuBridge(
                            pack, live_tts=MacSayTts(),
                            allow_fallback=args.allow_fallback, client=client,
                        )
                        res = bridge.run_turn(
                            session_id=f"text-{i:03d}",
                            wav_bytes=wav.read_bytes(), out_path=out_wav,
                        )
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)

            rec.update({
                "session_id": f"{'key' if mode == 'key' else 'text'}-{i:03d}",
                "state": res.state,
                "match_mode": res.match_mode,
                "tts_calls": res.tts_calls,
                "first_audio_ms": round(res.first_audio_ms, 3),
                "live_text": res.live_text,
                "audio": str(out_wav),
                "reason": res.events[0].get("reason", ""),
            })
        except Exception as exc:
            # WHY 记录而不是跳过：联调要能看出"是链路不通还是没命中"，
            #      跳过会让命中率虚高（分母缩水）。
            rec.update({
                "state": "error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:400],
            })
        records.append(rec)

    if client is not None and isinstance(client, KefuClient):
        client.close()
    return records, (time.perf_counter() - started) * 1000.0


def summarize(mode, records, elapsed_ms):
    """出一档的汇总：命中数/率、原因分布、命中与慢路的 first_audio_ms 并列。"""
    total = len(records)
    by_state = {}
    reasons = {}
    first_hit, first_slow = [], []
    tts_total = 0
    for r in records:
        state = r["state"]
        by_state[state] = by_state.get(state, 0) + 1
        if state == "error":
            reasons[f"error:{r.get('error_type')}"] = (
                reasons.get(f"error:{r.get('error_type')}", 0) + 1
            )
        elif state in ("miss", "fallback"):
            key = r.get("reason") or "(no reason)"
            reasons[key] = reasons.get(key, 0) + 1
        tts_total += int(r.get("tts_calls") or 0)
        first = r.get("first_audio_ms")
        if first is None:
            continue
        (first_hit if state == "hit" else first_slow).append(first)

    hits = by_state.get("hit", 0)
    rate = round(hits / total, 4) if total else None
    summary = {
        "mode": mode,
        "turns": total,
        "hit": hits,
        "miss": by_state.get("miss", 0),
        "fallback": by_state.get("fallback", 0),
        "error": by_state.get("error", 0),
        "hit_rate": rate,
        "tts_calls_total": tts_total,
        "first_audio_ms_hit": {
            "n": len(first_hit), "p50": pct(first_hit, 0.5), "max": pct(first_hit, 1.0),
        },
        "first_audio_ms_slow": {
            "n": len(first_slow), "p50": pct(first_slow, 0.5), "max": pct(first_slow, 1.0),
        },
        "reasons": reasons,
        "elapsed_ms": round(elapsed_ms, 1),
    }
    return summary


def print_summary(summaries, args):
    """把两档数字并排打印（验收第 7 条要看的表）。"""
    print("\n" + "=" * 74)
    print(f"kefu-bridge 联调汇总   pack={args.pack}   allow_fallback={args.allow_fallback}"
          f"   offline={args.offline}")
    print("=" * 74)
    for s in summaries:
        print(f"\n[{s['mode']}] n={s['turns']} hit={s['hit']} miss={s['miss']} "
              f"fallback={s['fallback']} error={s['error']} hit_rate={s['hit_rate']} "
              f"tts_calls={s['tts_calls_total']}  耗时={s['elapsed_ms']}ms")
        hit = s["first_audio_ms_hit"]
        slow = s["first_audio_ms_slow"]
        print(f"    首帧音频(命中, 零合成) n={hit['n']} P50={hit['p50']}ms max={hit['max']}ms")
        print(f"    首帧音频(慢路/未命中) n={slow['n']} P50={slow['p50']}ms max={slow['max']}ms")
        print(f"    原因分布: {s['reasons'] or '{}'}")
    if len(summaries) == 2:
        print("\n并列：")
        for s in summaries:
            hit = s["first_audio_ms_hit"]
            slow = s["first_audio_ms_slow"]
            hit_s = f"{hit['p50']}ms" if hit['n'] else "—"
            slow_s = f"{slow['p50']}ms" if slow['n'] else "—"
            print(f"  {s['mode']:>5}  命中率={s['hit_rate']}  命中={hit_s}  慢路={slow_s}")
    print("=" * 74)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="kefu 真链路联调：预铸包接入 + 两档命中率",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--pack", required=True, help="已铸资产包目录（含 manifest.json）")
    ap.add_argument("--turns", required=True, help="turns JSON 文件路径")
    ap.add_argument("--out", required=True, help="产物目录（JSONL / 音频 / summary）")
    ap.add_argument("--mode", choices=("key", "text", "all"), default="all")
    ap.add_argument("--allow-fallback", action="store_true",
                    help="显式降级：未命中走慢路并出 miss 事件（默认 fail-closed）")
    ap.add_argument("--offline", action="store_true",
                    help="不起 kefu：文本档用 turns 里的 reply 字段当 brain 回复")
    ap.add_argument("--brain-url", default="http://127.0.0.1:8092")
    ap.add_argument("--voice-url", default="http://127.0.0.1:8096")
    ap.add_argument("--user-voice", default="Tingting",
                    help="造用户音频用的 say 音色")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 数据红线：kefu 业务文案只允许落在 /tmp 或调用方指定的产物目录里
    if REPO == out_dir or REPO in out_dir.resolve().parents:
        print(
            f"[warn] --out 落在仓库内（{out_dir}）：确认其中只有自造文本，"
            f"真实 kefu 话术请写到 /tmp",
            file=sys.stderr,
        )

    pack = load_pack(Path(args.pack))
    turns = json.loads(Path(args.turns).read_text(encoding="utf-8"))
    if not isinstance(turns, list) or not turns:
        print(f"[error] --turns 必须是非空 JSON 列表: {Path(args.turns)}", file=sys.stderr)
        return 2

    modes = ("key", "text") if args.mode == "all" else (args.mode,)
    summaries, all_records = [], {}
    for mode in modes:
        sub = [t for t in turns
               if ("key" in t if mode == "key" else "user_text" in t)]
        if not sub:
            print(f"[skip] mode={mode}：--turns 里没有可跑的条目（需要 "
                  f"{'key' if mode == 'key' else 'user_text'} 字段）")
            continue
        records, elapsed = run_mode(mode, sub, pack, args, out_dir)
        jsonl = out_dir / f"turns_{mode}.jsonl"
        jsonl.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )
        summary = summarize(mode, records, elapsed)
        # 未跑（链路不通）：全错就明说原因，不编造命中率
        if summary["turns"] and summary["error"] == summary["turns"]:
            first_err = next(r for r in records if r["state"] == "error")
            summary["status"] = (
                f"未跑（原因：{first_err['error_type']}: {first_err['error'][:160]}）"
            )
            print(f"[mode={mode}] {summary['status']}")
        else:
            summary["status"] = "ok"
        summaries.append(summary)
        all_records[mode] = records

    (out_dir / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print_summary(summaries, args)

    if not summaries:
        print("[error] 没有可跑的模式", file=sys.stderr)
        return 2
    if all(s.get("status") and s["status"].startswith("未跑") for s in summaries):
        return 5      # 链路不通，明确非零退出
    return 0


if __name__ == "__main__":
    sys.exit(main())
