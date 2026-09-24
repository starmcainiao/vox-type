#!/usr/bin/env python3
"""
labs/heat-kefu-seq/run_seq_bench.py — 多分句整段的序列命中与 plan 拼接实测（T33）

链路（严格止于「拼接产物 + 耗时」，不起任何服务、不联网）：
    packs/heat_kefu（40 单句 + 29 拆句，T31b + T33）
      → adapters.framework_kefu.find_hit（现状档：整段 → 预期 miss，对照臂）
      → adapters.framework_kefu.find_hit_sequence（新：整段逐段覆盖 → 预期命中）
      → 命中序列拼成 plan → runtime.Executor 播（预期 tts_calls == 0）
      → 慢路对照：同一整段用 adapters.tts_macsay.say 合成一遍（"走原路"的成本）
      → report.json（逐条明细 + P50 + 汇总）

口径（全部引 docs/10 §10.7，不自造判据）：
    1. 命中只在**归一化空间**比对（§10.3 四步），逐段仍逐字相等；
    2. 最长优先、同长度多候选取索引首条；任一段覆盖不上 → **整体未命中**，
       entries 为空元组（不播半句）；
    3. 命中臂零 TTS 调用是硬口径（docs/10 §10.4：命中 = 用包内音频拼接播放）。

不做的事：
    - 不联网、不起服务、不改任何层的产品代码；
    - 音频产物只落系统临时目录，**不入仓**；
    - 不写 yaml 路径/内网地址/token；yaml 仅经 KEFU_HEAT_YAML 环境变量门控读取。

依赖纪律：
    - 只用 Python 标准库 + 本仓 API（assets / adapters / runtime / compiler）；
      产品代码经 sys.path 兜底 import，不复制任何一份等价逻辑；
    - 整段文本来自 yaml 运行时读取（KEFU_HEAT_YAML），不写死——
      yaml 不在时诚实 skip 并返回 rc 2，不产出「看起来通过」的空报告。

用法（裸跑即可，无需 PYTHONPATH）：
    python3 labs/heat-kefu-seq/run_seq_bench.py
    python3 labs/heat-kefu-seq/run_seq_bench.py --repeat 3

退出码（契约，全部收口，不以裸 traceback 逃出）：
    0 成功 / 2 用法或前置缺失（含 yaml 未配置）/ 3 运行期失败（stderr 带异常类型名）
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUT_DIR = Path(__file__).resolve().parent
REPORT = OUT_DIR / "report.json"

PACK_ID = "heat-kefu"
PACK_VERSION = "1"
PACK_DIR = REPO_ROOT / "packs" / "heat_kefu"
# build 的 --out 禁止落在包源目录内（cli.assert_not_within，docs/08 §8.5），
# 所以预铸产物落在包目录**旁边**：
BUILT_DIR = REPO_ROOT / "packs" / "heat_kefu_build" / "heat-kefu-1"
MANIFEST = BUILT_DIR / "manifest.json"

# 14 条多分句整段的 yaml top-level key（与 packs/heat_kefu/tests 的同名清单同源）
MULTI_CLAUSE_KEYS = [
    "opening", "chat_smalltalk", "clarify_work_order", "clarify_repair",
    "fallback_internal", "lifeboat_fallback", "user_no_unknown",
    "repair_confirm_question", "work_order_no_unknown", "internal_chat_hint",
    "stop_warm_plan", "inject_refuse", "repair_ask_natural_address",
    "repair_ask_natural_userNo",
]


# ---------------------------------------------------------------------------
# 前置
# ---------------------------------------------------------------------------

def _p50(values):
    """P50（中位数）。样本为 0 时返回 None，不返回 0 —— 0 会被误读成「零延迟」。"""
    if not values:
        return None
    return float(statistics.median(values))


def _fail(msg, rc=3):
    print(f"[run_seq_bench] {msg}", file=sys.stderr)
    return rc


def _load_yaml():
    path = os.environ.get("KEFU_HEAT_YAML")
    if not path:
        return None, "KEFU_HEAT_YAML 未设置——整段原文只能从 yaml 读，不写死"
    p = Path(path)
    if not p.is_file():
        return None, f"KEFU_HEAT_YAML 指向的文件不存在: {p}"
    try:
        import yaml
    except ImportError as e:
        return None, f"缺少 PyYAML（{type(e).__name__}）——整段原文只能从 yaml 读"
    return yaml.safe_load(p.read_text(encoding="utf-8")), None


def _sha256_file(path):
    """文件的 sha256 摘要（报告只记摘要，不记路径与全文——沿用既有卫生纪律）。"""
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git_commit(cwd):
    """`git rev-parse HEAD`；git 不可用/不在仓内时返回 (None, 原因)——不静默省略。"""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=True,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"{type(e).__name__}"
    commit = out.stdout.decode("utf-8", "replace").strip()
    return commit or None, None


# ---------------------------------------------------------------------------
# 主体
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="多分句整段的序列命中与 plan 拼接实测（T33）")
    ap.add_argument("--repeat", type=int, default=3,
                    help="每条整段的重复轮数（用于 P50；默认 3）")
    ap.add_argument("--skip-say", action="store_true",
                    help="跳过慢路 say 合成对照（仅本机缺 say 时用；报告会标注 skipped）")
    args = ap.parse_args(argv)
    if args.repeat < 1:
        return _fail("--repeat 必须 >= 1", rc=2)

    yaml_doc, err = _load_yaml()
    if err:
        return _fail(err, rc=2)

    if not MANIFEST.is_file():
        return _fail(
            f"未找到预铸产物 {MANIFEST}——请先 "
            "`bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1`",
            rc=2)

    # 本仓 API（不复制任何判定逻辑）
    from assets import load_pack
    from adapters.framework_kefu import find_hit, find_hit_sequence
    from runtime import Executor

    pack = load_pack(BUILT_DIR)

    # 执行器需要一个 TTS 适配器对象才能构造（runtime.Executor 的契约），
    # 但命中臂不应触发任何合成：用真实 MacSayTts 实例 + tts_calls 计数
    # 反证「零 TTS 调用」（tts_calls != 0 即报告 problems）。
    live = None
    try:
        from adapters.tts_macsay import MacSayTts
        live = MacSayTts()
    except Exception as e:
        print(f"[run_seq_bench] 构造 MacSayTts 失败（{type(e).__name__}: {e}）——"
              "命中臂无法端到端验证，本脚本退出 3（不静默降级）", file=sys.stderr)
        return 3

    if not args.skip_say:
        say = live
    else:
        say = None

    tmp_root = Path(tempfile.mkdtemp(prefix="heat-kefu-seq-"))
    details = []
    problems = []

    # 溯源（P2-c）：让报告能自证语料与代码版本——只记摘要与 commit，
    # 不记本机绝对路径 / yaml 全文（yaml 路径经 KEFU_HEAT_YAML 门控，不进产物）。
    git_commit, git_error = _git_commit(REPO_ROOT)

    try:
        for n, key in enumerate(MULTI_CLAUSE_KEYS, start=1):
            text = str(yaml_doc[key])
            total_chars = len(text)

            # ① 现状档（对照臂）：整段 → find_hit 预期 miss
            arm_old = find_hit(pack, text=text, rate_key="normal")
            old_hit = arm_old.entry is not None

            # ② 新入口：整段逐段覆盖 → 预期命中
            res = find_hit_sequence(pack, text, rate_key="normal")
            hit = res.miss_reason is None and len(res.entries) > 0
            keys = [e.key for e in res.entries]
            n_segments = len(res.entries)

            # ③ 端到端：命中序列拼成 plan → Executor 播（产物落临时目录，不入仓）
            plan = [{"key": k, "rate": "normal"} for k in keys]
            exec_info = None
            if hit:
                out_path = tmp_root / f"{n:02d}_{key}.wav"
                t0 = time.perf_counter()
                result = Executor(pack, live).execute(
                    plan, plan_id=f"seq-{n}", turn_id=f"t-{n}", out_path=out_path)
                exec_ms = (time.perf_counter() - t0) * 1000.0
                exec_info = {
                    "tts_calls": result.tts_calls,
                    "first_audio_ms": round(result.first_audio_ms, 3),
                    "total_duration_ms": result.total_duration_ms,
                    "hit_count": result.hit_count,
                    "miss_count": result.miss_count,
                    "fallback_count": result.fallback_count,
                    "wall_ms": round(exec_ms, 3),
                    "out_bytes": out_path.stat().st_size if out_path.is_file() else 0,
                }
                if result.tts_calls != 0:
                    problems.append(f"{key}: 命中臂 tts_calls={result.tts_calls} != 0")
            else:
                problems.append(
                    f"{key}: 整段未序列命中（miss_reason={res.miss_reason!r}, "
                    f"uncovered={res.uncovered[:60]!r}）")

            # ④ 慢路对照：同一整段用 say 合成一遍（"走原路"的成本）
            say_ms = None
            if say is not None and not args.skip_say:
                say_path = tmp_root / f"{n:02d}_{key}_say.wav"
                t0 = time.perf_counter()
                say.synthesize(text, say_path, rate_key="normal")
                say_ms = round((time.perf_counter() - t0) * 1000.0, 3)

            details.append({
                "key": key,
                "n_segments": n_segments,
                "segment_keys": keys,
                "total_chars": total_chars,
                "old_arm": {
                    "hit": old_hit,
                    "miss_reason": arm_old.miss_reason,
                },
                "seq_arm": {
                    "hit": hit,
                    "miss_reason": res.miss_reason,
                    "uncovered": res.uncovered,
                },
                "executor": exec_info,
                "slow_arm_say_ms": say_ms,
            })

            # 重复轮（P50 采样）
            repeats = []
            for r in range(args.repeat):
                t0 = time.perf_counter()
                if hit:
                    Executor(pack, live).execute(
                        plan, plan_id=f"seq-{n}-r{r}", turn_id=f"t-{n}-r{r}",
                        out_path=tmp_root / f"{n:02d}_{key}_r{r}.wav")
                    mode = "hit"
                else:
                    find_hit_sequence(pack, text, rate_key="normal")
                    mode = "miss"
                repeats.append(round((time.perf_counter() - t0) * 1000.0, 3))
            details[-1]["repeat_wall_ms"] = repeats

    finally:
        # 清理临时音频（不入仓）
        for f in tmp_root.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        tmp_root.rmdir()

    # ---- 汇总 ----
    hit_n = sum(1 for d in details if d["seq_arm"]["hit"])
    miss_n = len(details) - hit_n
    old_hit_n = sum(1 for d in details if d["old_arm"]["hit"])
    exec_samples = [d["executor"]["wall_ms"] for d in details if d["executor"]]
    say_samples = [d["slow_arm_say_ms"] for d in details if d["slow_arm_say_ms"] is not None]
    repeat_samples = [x for d in details for x in d["repeat_wall_ms"]]
    seg_counts = [d["n_segments"] for d in details]

    report = {
        "spec": "docs/10 §10.7（序列命中：逐字覆盖、最长优先、任一段不中即整体未命中）",
        "card": "docs/tasks/T33-packs-多分句整段序列命中与plan拼接.md",
        "pack": {"pack_id": PACK_ID, "pack_version": PACK_VERSION,
                 "assets": len(pack.assets),
                 # 相对仓库根记录（验收修正 2026-09-21）：绝对路径会把本机目录结构带进公开仓产物，
                 # 且换机器就不可复现——报告里一律用相对路径。
                 "built_dir": str(BUILT_DIR.relative_to(REPO_ROOT))},
        "yaml_env": "KEFU_HEAT_YAML",
        "provenance": {
            "yaml_sha256": _sha256_file(os.environ["KEFU_HEAT_YAML"]),
            "manifest_sha256": _sha256_file(MANIFEST),
            "git_commit": git_commit,
        },
        "env": {
            "python": sys.version.split()[0],
            "repeat": args.repeat,
            "slow_arm_skipped": args.skip_say,
            "git_error": git_error,
        },
        "summary": {
            "n_multi_clause": len(details),
            "seq_hit": hit_n,
            "seq_miss": miss_n,
            "old_arm_hit": old_hit_n,
            "segment_count_p50": _p50(seg_counts),
            "segment_count_min": min(seg_counts) if seg_counts else None,
            "segment_count_max": max(seg_counts) if seg_counts else None,
            "executor_wall_ms_p50": _p50(exec_samples),
            "executor_wall_ms_min": min(exec_samples) if exec_samples else None,
            "executor_wall_ms_max": max(exec_samples) if exec_samples else None,
            "executor_first_audio_ms_p50": _p50(
                [d["executor"]["first_audio_ms"] for d in details if d["executor"]]),
            "executor_total_duration_ms_p50": _p50(
                [d["executor"]["total_duration_ms"] for d in details if d["executor"]]),
            "tts_calls_sum": sum(d["executor"]["tts_calls"] for d in details if d["executor"]),
            "slow_arm_say_ms_p50": _p50(say_samples),
            "slow_arm_say_ms_min": min(say_samples) if say_samples else None,
            "slow_arm_say_ms_max": max(say_samples) if say_samples else None,
            "hit_vs_slow_delta_ms_p50": (
                _p50(exec_samples) - _p50(say_samples)
                if exec_samples and say_samples else None),
            "repeat_wall_ms_p50": _p50(repeat_samples),
            "n_repeat_samples": len(repeat_samples),
        },
        "details": details,
        "problems": problems,
        "passed": miss_n == 0 and old_hit_n == 0
                  and all(d["executor"]["tts_calls"] == 0 for d in details if d["executor"]),
    }

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(f"[run_seq_bench] 序列命中 {hit_n}/{len(details)}；现状档命中 {old_hit_n}/{len(details)}；"
          f"tts_calls 合计 {report['summary']['tts_calls_sum']}")
    print(f"[run_seq_bench] 命中臂 P50 = {report['summary']['executor_wall_ms_p50']} ms；"
          f"慢路 say P50 = {report['summary']['slow_arm_say_ms_p50']} ms；"
          f"差值 P50 = {report['summary']['hit_vs_slow_delta_ms_p50']} ms")
    print(f"[run_seq_bench] 报告落盘：{REPORT}")
    if problems:
        print(f"[run_seq_bench] 存在 {len(problems)} 条异常（详见 report.json.problems）",
              file=sys.stderr)
        return 3
    return 0 if report["passed"] else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print(f"[run_seq_bench] 运行期失败：{type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(3)
