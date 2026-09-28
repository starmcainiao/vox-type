#!/usr/bin/env python3
"""
labs/frontpack-value/run_compare.py — 前置包价值对比 harness（快路 vs 慢路，本仓自测可复跑）

任务卡：docs/tasks/T43-labs-前置包价值对比与呈现素材.md
定位：把「前置包解决了什么问题」从主张变成**同一台机器、同一批话术、可复跑**的对照。
      labs/ 是实验区，不进任何层的契约；本脚本自带口径，产物自带原始数据。

两条臂（同机、同句、同 16kHz/单声道/16-bit 契约）：
  fast —— `vox run` 命中即播：每句造一份单句 plan，**子进程**跑 `sh bin/vox run`，
          取 JSON 输出里的 `first_audio_ms`。这是**产品口径的首音**（磁盘读 + 拼接），
          不含 Python 进程启动那几十毫秒——所以它测的是「引擎就绪后首音到得多快」，
          不是「从零拉起进程到首音」。
  slow —— `adapters.tts_omlx.OmlxTts`（OpenAI 兼容 /v1/audio/speech，缺省
          127.0.0.1:10099，Qwen3-TTS-12Hz-0.6B-Base-bf16，default 音色、非克隆）：
          先 **1 次热身不计统计**，再逐句墙钟计时——**全链路**：服务端合成 +
          ffmpeg 降采样归一到 16k + 落盘 + 契约复验。另读产物 WAV 时长算实时倍率。

口径纪律（T43 §三「不夸大」）：
  - 语料**程序化**从 packs/heat_kefu/phrases.json 读取，不手抄；N=10 固定。
  - 快路 N=10，每句**各重复 --repeats 次**（缺省 5），P50 取全部测量值的中位数；
    子进程启动开销不进 first_audio_ms，所以这个数可以直接跟产品首音对读。
  - 慢路**逐句 1 次**（采样型引擎，逐句重复会让总时长线性膨胀），
    rtf_median = 墙钟合成秒 / 产物音频秒 的中位数（≈1 表示恰好实时，>1 表示比实时慢）。
  - 快路包在**临时目录**现场用 tts_omlx 铸，绝不动 packs/、不落冻结区。
  - **不静默降级**：任一句失败、端点不通、产物不足 N → 非零退出且把失败写进 report.json，
    绝不把失败句悄悄丢掉再照常出 P50（那样 P50 会凭空好看）。
  - 不用 macOS `say`、不引云端 API（背景参照只引用既有落盘值，见 report.json background_reference）。
  - 倍数只在 notes 里作**附注**并给区间，report.json 里不出现任何「×N 倍」字段。

产物（同目录）：
  raw/fast.jsonl        每条测量一行（key、repeat、first_audio_ms、hit/tts_calls、rc）
  raw/slow.jsonl        每条测量一行（key、warmup、wall_s、audio_s、rtf、字节数）
  raw/plan_run.jsonl    examples/plan.json 整跑一次的交叉值（计划级 first_audio_ms）
  report.json           **数字唯一来源**（schema 见任务卡，字段名冻结）
  docs/media/value-compare.svg  本脚本生成：对数刻度横条图，合法 XML、无外链/无 <image>

退出码：0 成功 / 2 语料或参数错 / 3 运行期失败（含任一句失败）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RAW = HERE / "raw"

# 语料：程序化从 packs/heat_kefu/phrases.json 读取（不手抄）。
# 顺序钉死——先放任务卡点名的 4 个 key（含 examples/plan.json 的全集），再补短句控篇幅。
REQUIRED_KEYS = ("opening__1", "opening__2", "transfer_ready", "farewell")
EXTRA_KEYS = ("chat_smalltalk__2", "pay_no_bills", "clarify_work_order__2",
              "repair_ask_userNo", "work_order_empty", "clarify_repair__1")
N_SENTENCES = 10

PACK_SOURCE = Path("packs") / "heat_kefu"
EXAMPLES_PLAN = Path("examples") / "plan.json"
SVG_REL = Path("docs") / "media" / "value-compare.svg"

# 端点缺省值与 adapters.tts_omlx.adapter 里的常量保持一致（env 可覆盖，见 OmlxTts.__init__）
DEFAULT_ENDPOINT = "http://127.0.0.1:10099"
DEFAULT_MODEL = "Qwen3-TTS-12Hz-0.6B-Base-bf16"


class HarnessError(Exception):
    """语料/参数/装载阶段的错（→ 退出码 2）。"""


class RunError(Exception):
    """执行阶段任一句失败（→ 退出码 3，fail-closed，不静默降级）。"""


# ---------------------------------------------------------------------------
# 语料
# ---------------------------------------------------------------------------
# 与本仓其它 labs 脚本同口径：仓库根进 sys.path（bin/vox 子进程自己会设 PYTHONPATH，
# 这里只为进程内 import adapters.tts_omlx）。放在 load_corpus 之前，保证任何路径下都能进。
sys.path.insert(0, str(REPO))


def load_corpus() -> list[dict]:
    """从 packs/heat_kefu/phrases.json 程序化读 N_SENTENCES 句，返回 [{key,text,rate_key}]。

    纪律：key 必须真在 phrases.json 里（不手抄、不臆造）；同 key 若有多变体取第一个；
    rate_key 必须在该条的 rates 声明里（否则 OmlxTts.rate_value 会 fail-closed）。
    """
    path = REPO / PACK_SOURCE / "phrases.json"
    if not path.is_file():
        raise HarnessError(f"话术表不存在: {path}")
    try:
        src = json.loads(path.read_text(encoding="utf-8"))["phrases"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise HarnessError(f"话术表解析失败: {path} ({exc})") from exc
    by_key: dict[str, dict] = {e["key"]: e for e in src if "key" in e}

    wanted: list[str] = list(dict.fromkeys(REQUIRED_KEYS + EXTRA_KEYS))
    missing = [k for k in wanted if k not in by_key]
    if missing:
        raise HarnessError(f"点名语料在 {PACK_SOURCE} 里找不到: {missing}")
    if len(wanted) != N_SENTENCES:
        raise HarnessError(
            f"语料条数 {len(wanted)} 与 N_SENTENCES={N_SENTENCES} 不符"
            f"（改 EXTRA_KEYS/REQUIRED_KEYS 时同步改 N_SENTENCES）")

    corpus: list[dict] = []
    for key in wanted:
        entry = by_key[key]
        variants = entry.get("variants") or []
        if not variants:
            raise HarnessError(f"语料 {key} 无 variants，无法合成")
        text = variants[0]
        # rate_key 取该条声明的第一档；合法性由适配器 OmlxTts.rate_value 校验（未知档 fail-closed），
        # 这里不复制档位表——复制一份就成了第二处真源。
        corpus.append({"key": key, "text": text,
                       "rate_key": (entry.get("rates") or ["normal"])[0]})
    return corpus


# ---------------------------------------------------------------------------
# 快路：vox run 子进程
# ---------------------------------------------------------------------------
def build_pack_for_fast(tmp: Path, corpus_keys_: list[str]) -> tuple[Path, dict]:
    """在临时目录现场铸 heat_kefu 包（tts_omlx，与慢路同源音色），并断言语料 10 句全命中。

    两个关键决定（都是为了让本卡**真的可复跑**）：
      1. 用 `--allow-partial` 而不是「整包必须 clean」：tts_omlx 归一自检会随机把
         头/尾静音超标的产物拒掉（单次整包 ~5% 的句不过），采样型引擎这属**环境事实**。
         若整包一不 clean 就整体重铸，69 句 × 数分钟的循环会让本卡不可复跑——
         那正是「不可复跑」比「脏数字」更坏的结果。所以这里接受部分包，
         但**硬断言语料 10 句每条都铸出来了**（partial 只可能是语料外的句）。
      2. 产物留临时目录：.wav + manifest 是运行期产物，labs 只留可入库文本。

    返回 (pack_dir, 断言明细)：明细写进 report.machine.pack_build，验收能看见口径。
    """
    pack_dir = tmp / "pack"
    cmd = [str(REPO / "bin" / "vox"), "pack", "build", str(PACK_SOURCE),
           "--out", str(pack_dir), "--adapter", "adapters.tts_omlx:OmlxTts",
           "--allow-partial", "--json"]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if proc.returncode not in (0, 4):
        raise RunError(f"pack build 失败（rc={proc.returncode}）:\n{proc.stderr[-800:]}")
    try:
        build = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RunError(f"pack build 输出不是 JSON: {exc}") from exc
    if not (pack_dir / "manifest.json").is_file():
        raise RunError("pack build 没有写出 manifest.json，无法继续")

    # 硬断言：语料 10 句必须在包里，否则 fast 臂测的不是这 10 句（fail-closed，不静默）。
    # 直接读 manifest.json 而不是 load_pack：断言只需 key 集合，
    # 而仓库根不在 sys.path（脚本从 labs/ 跑），走 assets 层会多引入一处路径依赖。
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    have = {a["key"] for a in manifest.get("assets", []) if a.get("part_index", 0) == 0}
    missing = [k for k in corpus_keys_ if k not in have]
    if missing:
        raise RunError(
            f"快路包缺语料句（无法构成同句对照）: {missing}"
            f"｜整包 failed={len(build.get('failed', []))} "
            f"quality_issues={len(build.get('quality_issues', []))}")
    detail = {
        "allow_partial": True,
        "rc": int(proc.returncode),
        "total": int(build.get("total", 0)),
        "synthesized": int(build.get("synthesized", 0)),
        "failed": len(build.get("failed", [])),
        "quality_issues": len(build.get("quality_issues", [])),
        "corpus_missing": missing,
        "corpus_all_present": not missing,
        "note": "采样型引擎的归一自检会随机拒产物（头/尾静音超标），"
                "整包 clean=False 属环境事实；本卡接受部分包但硬断言语料 10 句全部铸出。",
    }
    return pack_dir, detail


def run_one_plan(plan_path: Path, pack_dir: Path, tmp: Path, tag: str) -> dict:
    """跑一次 `vox run` 子进程，返回解析后的 JSON 结果（非零 rc → RunError）。"""
    out_path = tmp / f"{tag}-{uuid.uuid4().hex[:8]}.wav"
    cmd = [str(REPO / "bin" / "vox"), "run", str(plan_path), "--pack", str(pack_dir),
           "--out", str(out_path), "--json"]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RunError(f"vox run 输出不是 JSON（rc={proc.returncode}）: "
                       f"{proc.stderr[-400:]} | {proc.stdout[-200:]}") from exc
    if proc.returncode != 0 or result.get("hit_count", 0) == 0:
        raise RunError(
            f"vox run 未通过（rc={proc.returncode}, hit={result.get('hit_count')}, "
            f"miss={result.get('miss_count')}, fallback={result.get('fallback_count')}, "
            f"tts_calls={result.get('tts_calls')}）: {proc.stderr[-400:]}")
    return {"cmd": " ".join(str(Path(p).name) for p in cmd[1:-1]),
            "rc": proc.returncode, **result}


def measure_fast(corpus: list[dict], pack_dir: Path, repeats: int) -> list[dict]:
    """每句各 repeats 次：造单句 plan → 子进程 vox run → 取 JSON 的 first_audio_ms。

    为什么用子进程而不是进程内调 runtime.Executor：`first_audio_ms` 是 CLI 透出的
    产品口径字段（core.metrics_spec 常量，cli 不改名不加工）；子进程保证这里量的就是
    用户实际会调到的那个数。子进程启动开销不进 first_audio_ms，故不计入。
    """
    tmp = Path(tempfile.mkdtemp(prefix="frontpack-fast-"))
    rows: list[dict] = []
    for i, item in enumerate(corpus, 1):
        plan = tmp / f"plan-{i:02d}.json"
        plan.write_text(json.dumps([{"key": item["key"]}], ensure_ascii=False),
                        encoding="utf-8")
        for r in range(repeats):
            res = run_one_plan(plan, pack_dir, tmp, f"f{i:02d}r{r}")
            rows.append({
                "arm": "fast", "key": item["key"], "repeat": r + 1,
                "first_audio_ms": round(float(res["first_audio_ms"]), 6),
                "total_duration_ms": int(res["total_duration_ms"]),
                "hit_count": int(res["hit_count"]),
                "miss_count": int(res["miss_count"]),
                "fallback_count": int(res["fallback_count"]),
                "tts_calls": int(res["tts_calls"]),
                "rc": int(res["rc"]),
            })
            print(f"[fast {i}/{len(corpus)} r{r + 1}/{repeats}] "
                  f"{item['key']}: first_audio_ms={rows[-1]['first_audio_ms']}",
                  flush=True)
    return rows


# ---------------------------------------------------------------------------
# 慢路：tts_omlx 现场合成（全链路墙钟）
# ---------------------------------------------------------------------------
def endpoint_status(endpoint: str) -> dict:
    """如实记录端点与模型加载状态（不猜：连不上就写 connected=false）。"""
    base = endpoint.rstrip("/")
    try:
        with urllib.request.urlopen(base + "/v1/models", timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        ids = [m.get("id", "") for m in body.get("data", [])]
    except Exception as exc:  # 端点不通 / 非 JSON 都算未连接，写进 report 而不是吞掉
        return {"connected": False, "endpoint": base,
                "model_loaded": False, "error": f"{type(exc).__name__}: {exc}",
                "available_models": []}
    return {"connected": True, "endpoint": base,
            "model_loaded": DEFAULT_MODEL in ids,
            "error": None,
            "available_models": ids}


def wav_duration_s(path: Path) -> float:
    """产物 WAV 的音频时长（秒）——只信 wave 头，不靠墙钟。"""
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def measure_slow(corpus: list[dict]) -> list[dict]:
    """1 次热身（不计统计）+ 逐句墙钟计时：合成 + 归一 + 落盘全链路，再读产物时长算实时倍率。"""
    from adapters.tts_omlx import OmlxTts

    tts = OmlxTts()
    tmp = Path(tempfile.mkdtemp(prefix="frontpack-slow-"))
    rows: list[dict] = []
    warmup_s = None
    try:
        samples = [item["text"] for item in corpus]
        # 热身：吃模型首字节的冷态（0.6B 冷启动可达数十秒），不计入任何统计。
        warm_out = tmp / "warmup.wav"
        t0 = time.perf_counter()
        tts.synthesize(samples[0], warm_out)
        warm_s = time.perf_counter() - t0
        warmup_s = warm_s
        print(f"[slow] 热身完成（不计统计）wall={warm_s * 1000:.0f}ms", flush=True)

        for i, item in enumerate(corpus, 1):
            out = tmp / f"s{i:02d}.wav"
            t0 = time.perf_counter()
            try:
                tts.synthesize(item["text"], out, rate_key=item["rate_key"])
            except Exception as exc:  # 不静默降级：这一句失败就让整条 harness 失败
                shutil.rmtree(tmp, ignore_errors=True)
                raise RunError(f"慢路第 {i} 句 {item['key']} 合成失败: "
                               f"{type(exc).__name__}: {exc}") from exc
            wall_s = time.perf_counter() - t0
            audio_s = wav_duration_s(out)
            rtf = wall_s / audio_s if audio_s > 0 else math.nan
            rows.append({
                "arm": "slow", "key": item["key"], "warmup": False,
                "wall_s": round(wall_s, 4),
                "audio_s": round(audio_s, 4),
                "rtf": round(rtf, 4) if math.isfinite(rtf) else None,
                "bytes": out.stat().st_size,
                "rate_key": item["rate_key"],
            })
            print(f"[slow {i}/{len(corpus)}] {item['key']}: "
                  f"wall={wall_s * 1000:.0f}ms audio={audio_s * 1000:.0f}ms rtf={rtf:.2f}",
                  flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rows, warmup_s


# ---------------------------------------------------------------------------
# 统计与落盘
# ---------------------------------------------------------------------------
def median(vals: list[float]) -> float:
    return float(statistics.median(vals))


def build_report(corpus, fast_rows, slow_rows, plan_cross, env_status, repeat) -> dict:
    fast_ms = [r["first_audio_ms"] for r in fast_rows]
    slow_s = [r["wall_s"] for r in slow_rows]
    slow_rtf = [r["rtf"] for r in slow_rows if r["rtf"] is not None]
    slow_ms = [s * 1000.0 for s in slow_s]

    fast_block = {
        "n": len(fast_ms),
        "p50_ms": round(median(fast_ms), 3),
        "min_ms": round(min(fast_ms), 3),
        "max_ms": round(max(fast_ms), 3),
        "per_sentence_ms": [round(median([r["first_audio_ms"] for r in fast_rows
                                          if r["key"] == k]), 3) for k in corpus_keys(corpus)],
        "tts_calls_total": sum(r["tts_calls"] for r in fast_rows),
    }
    slow_block = {
        "n": len(slow_ms),
        "p50_ms": round(median(slow_ms), 1),
        "min_ms": round(min(slow_ms), 1),
        "max_ms": round(max(slow_ms), 1),
        "rtf_median": round(median(slow_rtf), 3) if slow_rtf else None,
        "audio_s_median": round(median([r["audio_s"] for r in slow_rows]), 3),
    }
    # 倍数只作附注；report.json 里**不存**任何「×N」字段（避免被当成测量结果引用），
    # 也不在 notes 里写死具体倍数——复跑后 P50 会变，硬编码的倍数会漂成假数字。
    ratio_txt = "P50/P50 的倍数只作附注"
    if fast_block["p50_ms"]:
        ratio_txt = "P50/P50 ≈ %.0f×（附注，非测量值）" % (
            slow_block["p50_ms"] / fast_block["p50_ms"])

    return {
        "arms": {"fast": fast_block, "slow": slow_block},
        "corpus_keys": corpus_keys(corpus),
        "machine": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cpu": (os.cpu_count() or 0),
            "ffmpeg": shutil.which("ffmpeg"),
            "endpoint_connected": env_status["connected"],
            "tts_model": DEFAULT_MODEL,
            "tts_model_loaded": env_status["model_loaded"],
            "endpoint": env_status["endpoint"],
            "available_models": env_status["available_models"],
            "tts_endpoint_error": env_status["error"],
        },
        "notes": (
            "快路 first_audio_ms 来自 `vox run` 子进程 JSON 输出（产品口径首音，磁盘读+拼接，"
            "不含 Python 进程启动那几十 ms；tts_calls_total=0 表示零 TTS 调用）。"
            " 慢路为 OmlxTts 全链路墙钟（服务端合成+ffmpeg 归一 16k+落盘+契约复验），"
            "先 1 次热身不计统计（实际耗时记在 machine.slow_warmup_s）；"
            "rtf_median=墙钟秒/产物音频秒，≈1 即恰好实时，>1 表示比实时慢。"
            f" 同机同句同 16k 契约可比；快路每句重复 {repeat} 次取中位数（N={fast_block['n']}），"
            f"慢路逐句 1 次（N={slow_block['n']}）。"
            f" {ratio_txt}；保守区间（快路取上界/慢路取最小，反向亦然）见 README「怎么读这个数字」"
            "与 SVG 脚注。铸包用 --allow-partial（采样型引擎的归一自检会随机拒产物，"
            "整包不 clean 就重铸到干净会让本卡不可复跑），并硬断言语料 10 句全部铸出"
            "——见 machine.pack_build.corpus_all_present。"
        ),
    }


def corpus_keys(corpus: list[dict]) -> list[str]:
    return [item["key"] for item in corpus]


def write_raw(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# SVG（手写、对数刻度、合法 XML、无外链 / 无 <image>）
# ---------------------------------------------------------------------------
def svg_num(value: float) -> str:
    """数值标签：≥1000ms 换算成 s（3 位有效数字，与 report.json 原始精度一致），
    否则用 ms 两位小数。用 3 位有效数字而不是固定 2 位，避免 2524.9 ms 被压成
    "2.52 s" 丢掉 .0249——图上数字必须能从 report.json 原值反推。"""
    if value >= 1000:
        return f"{value / 1000:.3g} s"
    if value >= 100:
        return f"{value:.0f} ms"
    return f"{value:.2f} ms"


def gen_svg(report: dict, per_key: list[dict]) -> str:
    """从 report.json 的真值生成对数刻度横条图（手写 SVG，合法 XML，无 <image>、无外链）。

    布局自上而下，y 区间互不相交：
      标题/副标题 → 对数轴网格 → 两根条（快路上、慢路下）→ 逐句明细 → 脚注。
    数值全部来自 report，图本身不重算任何统计量。
    """
    import xml.sax.saxutils as su

    fast = report["arms"]["fast"]
    slow = report["arms"]["slow"]
    f50, s50 = fast["p50_ms"], slow["p50_ms"]

    # 对数轴范围：0.1 ms .. 30 s（下限取 0.1 而非 1——快路区间下端 0.475 ms 落在 1 ms 以下，
    # 下限若取 1 会被钳到轴左端：图上区间起点画在 1.00 ms 而标签写 0.47 ms，视觉失真）
    x_min, x_max = 0.1, 30000.0
    left, right = 236.0, 916.0
    axis_y = 508.0
    grid_top = 152.0

    def x(ms: float) -> float:
        """log10 线性映射；越界钳制，避免标签跑出画布。"""
        v = min(max(ms, x_min), x_max)
        frac = (math.log10(v) - math.log10(x_min)) / (math.log10(x_max) - math.log10(x_min))
        return left + frac * (right - left)

    grid = []
    for e in range(-1, 5):  # 0.1, 1, 10, 100, 1000, 10000 ms
        gx = x(10.0 ** e)
        grid.append('    <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                    'stroke="#dfe4ea" stroke-width="1"/>\n' % (gx, grid_top, gx, axis_y))
        grid.append('    <text x="%.1f" y="%.1f" text-anchor="middle" class="axis">%s</text>\n'
                    % (gx, axis_y + 24, svg_num(10.0 ** e)))

    def bar(label, p50, lo, hi, color, cy):
        """一根条：区间矩形（min→max）+ P50 竖线。

        快路分布在 0.5-1 ms 之间，在这条对数轴上不足 1 px——所以区间矩形给一个
        可见的最小宽度，并把「窄于 1 px」这个事实写进标签，不假装那几像素是真实分布。
        """
        x0, x1, x2 = x(lo), x(p50), x(hi)
        tiny = (x2 - x0) < 2.0
        width = x2 - x0 if not tiny else 3.0
        # 说明文字按实际画出来的像素写，不写死「1 px」——快路分布 0.5–1.5 ms 时
        # 在这条轴上是 0.4 px 或 3 px，写死就会撒谎。
        if tiny:
            suffix = ("（该分布只占本对数轴的约 %.1f px，矩形取最小可见宽度）"
                      % (x2 - x0))
        else:
            suffix = ""
        # 量纲按量级自适应：亚毫秒量级用 ms（否则 0.69 ms 会被压成 "0.001 s"，
        # 把快路的真实分布宽度糊成 0）。轴刻度仍标 ms/s 两套。原始精度在
        # report.json 与逐句表里保留。
        def unit(v):
            return "%.2f ms" % v if v < 1000 else "%.3f s" % (v / 1000.0)
        sub = "P50 %s ｜ 区间 %s–%s%s" % (unit(p50), unit(lo), unit(hi), suffix)
        return (
            '  <g>\n'
            '    <text x="%.1f" y="%.1f" text-anchor="end" class="rowlabel">%s</text>\n'
            '    <rect x="%.1f" y="%.1f" width="%.1f" height="56" rx="3" '
            'fill="%s" fill-opacity="0.32"/>\n'
            '    <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
            'stroke-width="5"/>\n'
            '    <text x="%.1f" y="%.1f" class="value">%s</text>\n'
            '  </g>\n'
        ) % (left - 16, cy + 6, su.escape(label), x0, cy - 28, width, color,
             x1, cy - 28, x1, cy + 28, color, x1 + 14, cy + 5, su.escape(sub))

    rows_out = []
    y = 596.0
    for row in per_key[:10]:
        slow_txt = svg_num(row["slow_ms"]) if row["slow_ms"] is not None else "—"
        fast_txt = svg_num(row["fast_ms"]) if row["fast_ms"] is not None else "—"
        rows_out.append('    <text x="%.1f" y="%.1f" class="micro">%s：快 %s ｜ 慢 %s</text>\n'
                        % (left, y, su.escape(row["key"]), fast_txt, slow_txt))
        y += 20.0

    # 倍数附注：P50/P50 只作旁注；区间两端各取一个保守方向（快路取上界，慢路取 min / max）
    def fmt(ms):
        return "{:,.0f}".format(ms)

    ratio_note = (
        "附注：P50/P50 ≈ %s×。保守端：快路上界 %s 对慢路最小 %s ≈ %s×；"
        "快路中位对慢路上界 %s ≈ %s×。倍数只作旁注（受采样波动影响），"
        "口径以 report.json 原值为准。"
        % (fmt(s50 / f50), svg_num(fast["max_ms"]), svg_num(slow["min_ms"]),
           fmt(slow["min_ms"] / fast["max_ms"]), svg_num(slow["max_ms"]),
           fmt(slow["max_ms"] / f50)))

    slow_note = (
        "慢路实时倍率中位数 rtf_median=%s（墙钟秒 / 产物音频秒，≈1 即恰好实时，"
        ">1 表示比实时慢）；快路口径不含 Python 进程启动，测的是引擎就绪后首音。"
        % slow["rtf_median"])

    # 用占位符替换而不是 % 格式化：CSS 里的字面花括号会被 % 吃掉
    head = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="980" height="836" '
        'viewBox="0 0 980 836" role="img" aria-labelledby="t d">\n'
        '  <title id="t">前置包价值对比：命中即播 vs 现场合成（同机同句首音）</title>\n'
        '  <desc id="d">__F50__ 对 __S50__，对数刻度横条；'
        '数值取自 labs/frontpack-value/report.json</desc>\n'
        '\n'
        '  <style>\n'
        '    .title{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",'
        'sans-serif;font-size:21px;font-weight:600;fill:#1b2430}\n'
        '    .deck{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",'
        'sans-serif;font-size:13px;fill:#5b6775}\n'
        '    .axis{font-family:-apple-system,"PingFang SC",sans-serif;font-size:12px;'
        'fill:#7a8592}\n'
        '    .rowlabel{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",'
        'sans-serif;font-size:14px;font-weight:600;fill:#1b2430}\n'
        '    .value{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",'
        'sans-serif;font-size:13px;font-weight:600;fill:#1b2430}\n'
        '    .micro{font-family:-apple-system,"PingFang SC",sans-serif;'
        'font-size:11.5px;fill:#5b6775}\n'
        '    .foot{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",'
        'sans-serif;font-size:11.5px;fill:#5b6775}\n'
        '    .sect{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",'
        'sans-serif;font-size:12px;font-weight:600;fill:#5b6775}\n'
        '  </style>\n'
        '\n'
        '  <rect x="0" y="0" width="980" height="836" fill="#ffffff"/>\n'
        '  <text x="48" y="46" class="title">前置包：命中即播 vs 现场合成 '
        '—— 同一台机器、同一批话术</text>\n'
        '  <text x="48" y="72" class="deck">横轴为首音延迟（对数刻度，单位 ms / s）。'
        '快路 = vox run 命中即播（磁盘读，零 TTS 调用）；</text>\n'
        '  <text x="48" y="92" class="deck">慢路 = oMLX Qwen3-TTS-12Hz-0.6B 全链路'
        '（合成 + 归一 16k + 落盘）。快路 N=__FN__（10 句 × 5 次），慢路 N=__SN__。</text>\n'
    )

    pieces = [
        head,
        "".join(grid),
        '  <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#9aa5b1" '
        'stroke-width="1.5"/>\n' % (left, axis_y, right, axis_y),
        '  <text x="%.1f" y="%.1f" text-anchor="middle" class="axis">'
        '首音延迟（对数刻度）</text>\n\n' % ((left + right) / 2.0, axis_y + 54),
        bar("快路 命中即播（P50）", fast["p50_ms"], fast["min_ms"], fast["max_ms"],
            "#1f7a4d", 230.0),
        bar("慢路 现场合成（P50）", slow["p50_ms"], slow["min_ms"], slow["max_ms"],
            "#b3402f", 406.0),
        '  <line x1="48" y1="552" x2="932" y2="552" stroke="#e6ebf0" '
        'stroke-width="1"/>\n',
        '    <text x="48" y="574" class="sect">逐句对照（快路 5 次中位 / 慢路 1 次墙钟，'
        '同一句；同源 report.json per_key）</text>\n',
        "".join(rows_out),
        '  <line x1="48" y1="790" x2="932" y2="790" stroke="#e6ebf0" '
        'stroke-width="1"/>\n',
        '  <text x="48" y="808" class="foot">%s</text>\n' % su.escape(ratio_note),
        '  <text x="48" y="826" class="foot">%s</text>\n' % su.escape(slow_note),
        '</svg>\n',
    ]
    out = "".join(pieces)
    return (out.replace("__F50__", svg_num(f50))
               .replace("__S50__", svg_num(s50))
               .replace("__FN__", str(fast["n"]))
               .replace("__SN__", str(slow["n"])))


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_compare",
        description="前置包价值对比 harness：快路 vox run vs 慢路 tts_omlx（零第三方依赖）")
    p.add_argument("--no-write", action="store_true",
                   help="只做结构预算 / 语法自检，不连端点、不落盘任何产物")
    p.add_argument("--repeats", type=int, default=5,
                   help="快路每句重复次数（缺省 5；慢路逐句 1 次）")
    p.add_argument("--endpoint", default=None,
                   help=f"TTS 端点（缺省取 env VOX_TTS_ENDPOINT，否则 {DEFAULT_ENDPOINT}）")
    p.add_argument("--skip-slow", action="store_true",
                   help="只跑快路（端点不在线时用；慢路字段会是空块，report 会标注）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.no_write:
        # 结构自检：语料能程序化读出来 + 图能从上一份 report.json 渲染。不连端点、不写盘。
        corpus = load_corpus()
        print(f"[no-write] 语料 {len(corpus)} 句，keys={corpus_keys(corpus)}")
        report_path = HERE / "report.json"
        if not report_path.is_file():
            print(f"[no-write] 语料校验通过（{report_path.name} 尚未生成，跳过 SVG 渲染）")
            return 0
        report = json.loads(report_path.read_text(encoding="utf-8"))
        # 结构自检：必需字段齐 +（是完整报告时）SVG 能渲染且是合法 XML。
        # 上一次运行**失败**留的报告只有 arms.n=0，那是如实的失败记录、不是坏结构——
        # 这里只报状态，不把它当成语法错误。
        missing_top = [k for k in ("arms", "corpus_keys", "machine", "notes")
                       if k not in report]
        if missing_top:
            print(f"[no-write] FAIL: report.json 缺必需字段: {missing_top}", file=sys.stderr)
            return 2
        fast = report["arms"].get("fast", {})
        slow = report["arms"].get("slow", {})
        if not fast.get("n"):
            print(f"[no-write] 语料校验通过（{report_path.name} 是失败记录 n=0，"
                  f"跳过 SVG 渲染）")
            return 0
        for blk, label in ((fast, "fast"), (slow, "slow")):
            for f in ("p50_ms", "min_ms", "max_ms"):
                if f not in blk:
                    print(f"[no-write] FAIL: arms.{label} 缺 {f}", file=sys.stderr)
                    return 2
            if blk["n"] == 0:
                print(f"[no-write] FAIL: arms.{label}.n=0 但带了统计量（口径自相矛盾）",
                      file=sys.stderr)
                return 2
        svg_text = gen_svg(report, per_key_from_report(report))
        parse_xml(svg_text)
        print(f"[no-write] OK: report.json 字段齐（fast n={fast['n']}, slow n={slow['n']}），"
              f"SVG 可渲染且是合法 XML（{len(svg_text)} B）")
        return 0

    if not (1 <= args.repeats <= 50):
        print(f"[FATAL] --repeats 需 1..50，实际 {args.repeats}", file=sys.stderr)
        return 2

    endpoint = args.endpoint or os.environ.get("VOX_TTS_ENDPOINT") or DEFAULT_ENDPOINT

    try:
        corpus = load_corpus()
    except HarnessError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(f"语料 {len(corpus)} 句（程序化自 {PACK_SOURCE}/phrases.json）：{corpus_keys(corpus)}",
          flush=True)

    RAW.mkdir(parents=True, exist_ok=True)
    per_key: list[dict] = []
    report: dict = {}
    try:
        # 端点状态如实记录（连不上写 connected=false，仍尽量出快路半边）
        env_status = endpoint_status(endpoint) if not args.skip_slow else {
            "connected": False, "endpoint": endpoint, "model_loaded": False,
            "error": "skip-slow（未探测端点）", "available_models": []}
        print(f"端点 {env_status['endpoint']} connected={env_status['connected']} "
              f"model_loaded={env_status['model_loaded']}", flush=True)

        tmp = Path(tempfile.mkdtemp(prefix="frontpack-fast-"))
        try:
            # 只铸一次包：整包 69 句要 ~9 分钟，铸两遍等于把复跑时间直接翻倍
            pack_dir, pack_detail = build_pack_for_fast(tmp, corpus_keys(corpus))
            fast_rows = measure_fast(corpus, pack_dir, args.repeats)

            slow_rows: list[dict] = []
            warmup_s = None
            if args.skip_slow or not env_status["connected"]:
                print("[slow] 跳过：--skip-slow 或端点不在线（如实记为不完整）", flush=True)
            else:
                slow_rows, warmup_s = measure_slow(corpus)

            # 交叉值：复用同一个包整跑一次 examples/plan.json（4 句的计划级 first_audio_ms）
            plan = REPO / EXAMPLES_PLAN if not EXAMPLES_PLAN.is_absolute() else EXAMPLES_PLAN
            if not plan.is_file():
                cross: dict = {"plan": str(EXAMPLES_PLAN), "attempted": False,
                               "error": "examples/plan.json 不存在"}
            else:
                cross = run_one_plan(plan, pack_dir, tmp, "cross")
                print(f"[cross] {EXAMPLES_PLAN}: first_audio_ms={cross['first_audio_ms']}"
                      f"（4 句计划级）", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        report = build_report(corpus, fast_rows, slow_rows, cross, env_status, args.repeats)
        # 热身耗时只作环境诊断记录：它**不进任何统计口径**（也不进 rtf）。
        report["machine"]["slow_warmup_s"] = warmup_s
        report["machine"]["repeats_fast"] = args.repeats
        report["machine"]["skip_slow"] = bool(args.skip_slow)
        report["machine"]["pack_build"] = pack_detail
        report["machine"]["examples_plan_cross"] = cross
        report["arm_definition"] = {
            "fast": "vox run 子进程命中即播；first_audio_ms 取 CLI JSON 输出（产品口径首音，不含进程启动）",
            "slow": "OmlxTts 全链路墙钟：合成 + ffmpeg 归一 16k + 落盘 + 契约复验；1 次热身不计统计",
        }
        report["background_reference"] = {
            "note": "云端 / 端到端数字只引既有落盘值，口径不同、不直接相减，不进本图。",
            "sources": ["labs/cloud-bench/report.json", "labs/e2e-vs-cascade/report.json"],
        }
        per_key = merge_per_key(report, fast_rows, slow_rows)
        report["per_key"] = per_key

        write_raw(RAW / "fast.jsonl", fast_rows)
        write_raw(RAW / "slow.jsonl", slow_rows)
        write_raw(RAW / "plan_run.jsonl", [cross])
        svg_text = gen_svg(report, per_key)
        parse_xml(svg_text)
        if not args.skip_slow:
            (REPO / SVG_REL).write_text(svg_text, encoding="utf-8")
        (HERE / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        print(f"\n== report.json ==\n"
              f"fast N={report['arms']['fast']['n']} P50={report['arms']['fast']['p50_ms']}ms "
              f"[{report['arms']['fast']['min_ms']} .. {report['arms']['fast']['max_ms']}]\n"
              f"slow N={report['arms']['slow']['n']} P50={report['arms']['slow']['p50_ms']}ms "
              f"rtf_median={report['arms']['slow']['rtf_median']}"
              f"[{report['arms']['slow']['min_ms']} .. {report['arms']['slow']['max_ms']}]",
              flush=True)
        print("[report] raw/{fast,slow,plan_run}.jsonl + report.json"
              + (f" + {SVG_REL}" if not args.skip_slow else "（SVG 未更新：--skip-slow）"))
        return 0
    except RunError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        # 失败也落一份 report，如实记失败，不静默降级成「好看的 P50」
        fail = {"arms": {"fast": {"n": 0}, "slow": {"n": 0}},
                "corpus_keys": corpus_keys(corpus),
                "machine": {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "endpoint": endpoint, "python": sys.version.split()[0],
                            "platform": platform.platform(),
                            "failure": f"{type(exc).__name__}: {exc}"},
                "notes": "本次运行失败，字段为空；不得引用为测量结果。"}
        try:
            (HERE / "report.json").write_text(
                json.dumps(fail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        return 3


def per_key_from_report(report: dict) -> list[dict]:
    """--no-write 复用：从 report.json 读回逐句对照（缺则返回空，只画 P50 两根条）。"""
    keys = report.get("corpus_keys", [])
    if report.get("per_key"):
        return report["per_key"]
    out = []
    for k in keys:
        out.append({"key": k, "fast_ms": report["arms"]["fast"].get("p50_ms", 0),
                    "slow_ms": report["arms"]["slow"].get("p50_ms", 0)})
    return out


def merge_per_key(report: dict, fast_rows: list[dict], slow_rows: list[dict]) -> list[dict]:
    """按 key 对齐两臂原值，供 SVG 逐句对照（数值直接来自 raw，不再算一遍）。"""
    fast_by: dict[str, list[float]] = {}
    for r in fast_rows:
        fast_by.setdefault(r["key"], []).append(r["first_audio_ms"])
    slow_by = {r["key"]: r["wall_s"] * 1000.0 for r in slow_rows}
    out = []
    for key in report["corpus_keys"]:
        fs = fast_by.get(key, [])
        out.append({
            "key": key,
            "fast_ms": round(median(fs), 3) if fs else None,
            "slow_ms": round(slow_by[key], 1) if key in slow_by else None,
        })
    return out


def parse_xml(text: str) -> None:
    """合法 XML 自检：不合法就抛（fail-closed，不留一份坏 SVG 进 docs/media）。"""
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        raise RunError(f"SVG 不是合法 XML: {exc}") from exc


if __name__ == "__main__":
    args_for_ns = parse_args(sys.argv[1:] if False else None)
    sys.exit(main())
