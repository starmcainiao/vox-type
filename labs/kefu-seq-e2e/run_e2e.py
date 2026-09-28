#!/usr/bin/env python3
"""
labs/kefu-seq-e2e/run_e2e.py — kefu 预铸钩子接序列档：真链路实测驱动（T34 产物 3）

问题：钩子 T34 新加的**序列档 / key 档整段展开**，在 kefu 真链路上能不能命中、
拼出来的音频和 vox-type 侧用同一 plan 播一遍**是不是逐字节相同**？

本脚本**不起任何服务**。起停命令在 README.md 里，由验收方执行（用后即关）。
本脚本只做两件事：
  1. 用 **vox-type 侧同一个 runtime.Executor** 复现钩子的拼接（同包、同 plan、同
     双工参数）→ 得到参照音频；
  2. 把「臂 A / 臂 B / 对照臂」的驱动方式固化成 JSON，并核对「钩子结果 vs 参照」
     是否逐字节相同（sha256 比对）。

三档查询顺序（卡 §1，顺序冻结）：
    ① key 档：`key:<k>` → 1a 单段 | 1b 整段展开 `<k>__1..__N`（连续完整） | 1c None
    ② 文本档单段 find_hit
    ③ 文本档序列 find_hit_sequence（entries ≥ 2 才播）

产物：--out 目录下的 report.json（**骨架 + 实测数值**）。报告里的字段名与 README
一致；未取的臂（臂 B 服务未起时）如实记 `"taken": false` 与 reason，不造假。

用法（不联网、不起服务）：
    KEFU_HEAT_YAML=/path/to/供热预设.yaml \\
    KEFU_KEFU_REPO=/path/to/kefu-agent \\
    python3 labs/kefu-seq-e2e/run_e2e.py --out labs/kefu-seq-e2e/out

退出码：0 成功 / 2 前置缺失（包未构建 / yaml 或 kefu 仓未给）/ 3 运行期失败或口径被打破
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]                       # labs/kefu-seq-e2e/run_e2e.py → 仓库根
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# heat_kefu 的 14 条多分句整段（T33 已确认 14/14 序列命中；此处只作场景清单）
MULTI_CLAUSE_KEYS = [
    "opening", "chat_smalltalk", "clarify_work_order", "clarify_repair",
    "fallback_internal", "lifeboat_fallback", "user_no_unknown",
    "repair_confirm_question", "work_order_no_unknown", "internal_chat_hint",
    "stop_warm_plan", "inject_refuse", "repair_ask_natural_address",
    "repair_ask_natural_userNo",
]

# 对照臂：一轮必然 miss 的自由文本（**不取自 kefu 业务文案**）
CONTROL_TEXT = "今天天气真不错。"

DEFAULT_PACK_REL = "packs/heat_kefu_build/heat-kefu-1"


def _fail(msg: str, rc=3):
    print(f"[kefu-seq-e2e] {msg}", file=sys.stderr)
    return rc


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _git_commit(cwd: Path):
    """`git rev-parse HEAD`；git 不可用返回 (None, 原因)——不静默省略。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
        commit = out.stdout.decode("utf-8", "replace").strip()
        return commit or None, None
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}"


def _frames(path: Path) -> int:
    """标准库 wave 读帧数（时长核算唯一口径；不依赖 Executor 自报值）。"""
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes()


def _duration_ms(frames: int, framerate: int) -> float:
    return round(frames * 1000.0 / framerate, 3)


def _load_yaml():
    path = os.environ.get("KEFU_HEAT_YAML")
    if not path:
        return None, "KEFU_HEAT_YAML 未设置——整段原文只能从 yaml 读，不写死"
    p = Path(path)
    if not p.is_file():
        return None, f"KEFU_HEAT_YAML 指向的文件不存在: {p}"
    try:
        import yaml
    except ImportError as exc:
        return None, f"缺少 PyYAML（{type(exc).__name__}）"
    return yaml.safe_load(p.read_text(encoding="utf-8")), None


def _no_synth_adapter(pack):
    """与钩子里的 _NoSynthAdapter 同构的替身（满足 Executor 契约，调用即抛）。"""
    class _NoSynth:
        def __init__(self, p):
            self.voice = p.voice
            self.model_version = p.model_version

        def synthesize(self, *a, **k):
            raise RuntimeError("实测参照臂不做合成：命中路径必须零 TTS")

    return _NoSynth(pack)


def _plan_for(keys):
    """plan 单元显式传 variant（避免 'auto' 的稳定散列选到别的变体）。"""
    return [{"key": k, "rate": "normal", "variant": 0} for k in keys]


def _hook_driver(kefu_repo: Path, vox_repo: Path, pack_dir: Path):
    """惰性导入 kefu 钩子；返回 (module, hook_cls) 或 (None, 原因)。"""
    if not kefu_repo.is_dir():
        return None, f"kefu 仓不在（KEFU_KEFU_REPO={kefu_repo}）"
    pkg = kefu_repo / "organs" / "客服" / "channel-voice"
    for d in (str(pkg), str(vox_repo)):
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        from blackiron_kefu_voice.precast_hook import PrecastHook
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    return PrecastHook, None


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="kefu 钩子接序列档：真链路实测驱动（不起服务）")
    ap.add_argument("--out", default="labs/kefu-seq-e2e/out",
                    help="产物目录（report.json + 音频引用）")
    ap.add_argument("--pack", default=DEFAULT_PACK_REL, help="已铸包目录（仓内相对或绝对）")
    ap.add_argument("--max-clause-keys", type=int, default=0,
                    help="只跑前 N 条整段（调试用；0 = 全部）")
    ap.add_argument("--write-wavs", action="store_true",
                    help="把参照音频落盘到 out/wavs（默认只算 sha256 与时长，不留音频）")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    pack_dir = Path(args.pack)
    if not pack_dir.is_absolute():
        pack_dir = REPO / pack_dir

    yaml_doc, err = _load_yaml()
    if err:
        return _fail(err, rc=2)
    if not (pack_dir / "manifest.json").is_file():
        return _fail(
            f"未找到预铸产物 {pack_dir / 'manifest.json'}——请先 "
            f"`sh bin/vox pack build packs/heat_kefu --out {DEFAULT_PACK_REL}`", rc=2)

    from assets import load_pack
    from adapters.framework_kefu import find_hit, find_hit_sequence
    from runtime import Executor
    from runtime.audio import SAMPLE_RATE
    from runtime.duplex import DuplexParams

    pack = load_pack(pack_dir)
    duplex = DuplexParams.default()
    pad_frames = int(round(duplex.silence_pad_ms * SAMPLE_RATE / 1000))

    problems = []
    details = []

    for n, base in enumerate(
            MULTI_CLAUSE_KEYS if args.max_clause_keys == 0
            else MULTI_CLAUSE_KEYS[:args.max_clause_keys], start=1):
        text = yaml_doc.get(base)
        if not isinstance(text, str):
            problems.append(f"{base}: yaml 里没有该键（跳过）")
            continue

        # ① 现状档：整段单段命中（预期 miss，证明「整段本来查不到」这个前提没被改掉）
        old = find_hit(pack, text=text, rate_key="normal")

        # ② 序列档
        seq = find_hit_sequence(pack, text=text, rate_key="normal")
        keys = [e.key for e in seq.entries]
        hit = seq.miss_reason is None and len(keys) >= 2

        row = {
            "key": base,
            "n_segments": len(keys),
            "segment_keys": keys,
            "total_chars": len(text),
            "old_arm": {"hit": old.entry is not None, "miss_reason": old.miss_reason},
            "seq_arm": {"hit": hit, "miss_reason": seq.miss_reason,
                        "uncovered": seq.uncovered, "blocked_by": list(seq.blocked_by)},
        }

        if not hit:
            problems.append(
                f"{base}: 整段未序列命中（miss_reason={seq.miss_reason!r}, "
                f"uncovered={seq.uncovered[:60]!r}）")
            details.append(row)
            continue

        # ③ 参照臂：同 plan 走 runtime.Executor 播一遍（命中路径零 TTS）
        plan = _plan_for(keys)
        tmp = Path(tempfile.mkdtemp(prefix="kefu-seq-e2e-"))
        ref_path = tmp / "ref.wav"
        try:
            t0 = time.perf_counter()
            result = Executor(pack, _no_synth_adapter(pack), duplex=duplex,
                              allow_fallback=False).execute(
                plan, plan_id=f"ref-{n}", turn_id=f"ref-{n}/{len(keys)}",
                out_path=ref_path)
            wall_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            ref_bytes = ref_path.read_bytes()
            ref_frames = _frames(ref_path)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

        # 逐段时长核算（卡 §1 的口径）：Σ 段帧 + (段数−1)×pad
        # fade_ms 只缩幅不缩长（runtime.apply_fade 返回长度不变），不进帧数公式
        seg_frames = [_frames(pack.root / e.path) for e in seq.entries]
        expected = sum(seg_frames) + (len(keys) - 1) * pad_frames

        row.update({
            "plan": plan,
            "segment_frames": seg_frames,
            "pad_frames": pad_frames,
            "expected_frames": expected,
            "ref": {
                "sha256": _sha256_bytes(ref_bytes),
                "bytes": len(ref_bytes),
                "frames": ref_frames,
                "duration_ms": _duration_ms(ref_frames, SAMPLE_RATE),
                "tts_calls": result.tts_calls,
                "hit_count": result.hit_count,
                "miss_count": result.miss_count,
                "fallback_count": result.fallback_count,
                "first_audio_ms": round(result.first_audio_ms, 3),
                "wall_ms": wall_ms,
            },
        })
        if result.tts_calls != 0:
            problems.append(f"{base}: 命中臂 tts_calls={result.tts_calls} != 0")
        if ref_frames != expected:
            problems.append(f"{base}: 拼接帧数不符 actual={ref_frames} expected={expected}")

        if args.write_wavs:
            wavs = out_dir / "wavs"
            wavs.mkdir(parents=True, exist_ok=True)
            (wavs / f"{n:02d}_{base}.wav").write_bytes(ref_bytes)

        details.append(row)

    # 对照臂：必然 miss 的自由文本
    ctrl_old = find_hit(pack, text=CONTROL_TEXT, rate_key="normal")
    ctrl_seq = find_hit_sequence(pack, text=CONTROL_TEXT, rate_key="normal")
    control = {
        "text_sha256": _sha256_bytes(CONTROL_TEXT.encode("utf-8")),
        "note": "自造闲聊文本，不取自 kefu 业务文案；预期单档与序列档都 miss → 走原路",
        "single_arm": {"hit": ctrl_old.entry is not None, "miss_reason": ctrl_old.miss_reason},
        "seq_arm": {"hit": ctrl_seq.miss_reason is None and len(ctrl_seq.entries) >= 2,
                    "miss_reason": ctrl_seq.miss_reason, "uncovered": ctrl_seq.uncovered},
    }
    if control["single_arm"]["hit"] or control["seq_arm"]["hit"]:
        problems.append("对照臂应当 miss，实际命中了（语料污染？）")

    # 钩子侧核对（需要 kefu 仓；不需要服务）
    hook_error = None
    kefu_repo = os.environ.get("KEFU_KEFU_REPO", "")
    if not kefu_repo:
        kefu_repo = str(REPO.parent / "kefu-agent")   # 本机两仓并列的常见布局，仅作默认
    kefu_repo = Path(kefu_repo)
    cls, hook_error = _hook_driver(kefu_repo, REPO, pack_dir)
    hook_matrix = []
    if cls is not None:
        hook = cls(str(REPO), str(pack_dir),
                   live_voice=pack.voice, live_model_version=pack.model_version)
        cases = [
            # (arm, label, input)
            ("A", "key_expand:opening", "key:opening"),
            ("A", "key_single:opening__1", "key:opening__1"),
            ("A", "key_unknown", "key:不存在"),
            ("B", "text_whole:opening", yaml_doc.get("opening", "")),
            ("C", "control:chit-chat", CONTROL_TEXT),
        ]
        for arm, label, text in cases:
            got = hook.try_precast(text) if text else None
            entry = {"arm": arm, "label": label, "took_audio": got is not None}
            if got is not None:
                entry["sha256"] = _sha256_bytes(got[0])
                entry["bytes"] = len(got[0])
                entry["engine"] = got[1]

                # 参照 = vox-type 侧用**同一 plan** 播一遍。
                # 逐字节比对只成立于「钩子走了 Executor 拼接」的路径：
                #   多段（1b 整段展开 / ③ 序列档）→ 参照就是 details 里那条整段的 ref；
                #   单段（1a）→ 钩子**直接读包内音频字节**（不经 Executor），
                #     而 Executor 会把单段再写一遍（apply_fade 就地改样本 + 重写容器），
                #     所以两者 sha256 必然不同、时长相同。这类用例记"字节相同 = 不适用"，
                #     并单独核对「钩子字节 == 包内音频文件字节」这个 1a 的真实判据。
                kind, ref_sha = None, None
                if label.startswith("key_expand:") or label.startswith("text_whole:"):
                    base = label.split(":", 1)[1]
                    hit = next((d for d in details if d["key"] == base), None)
                    kind = "multi_unit"
                    if hit is not None:
                        ref_sha = hit["ref"]["sha256"]
                elif label.startswith("key_single:"):
                    kind = "single_unit_1a"
                    k = label.split(":", 1)[1]
                    e = next((x for x in pack.assets if x.key == k), None)
                    if e is not None:
                        ref_sha = _sha256_file(pack.root / e.path)

                if ref_sha is None:
                    problems.append(f"{label}: 没有可比对的参照（口径被打破）")
                else:
                    entry["compare_kind"] = kind
                    entry["ref_sha256"] = ref_sha
                    entry["byte_identical_to_ref"] = (
                        entry["sha256"] == entry["ref_sha256"])
                    if not entry["byte_identical_to_ref"]:
                        if kind == "multi_unit":
                            problems.append(
                                f"多段拼接结果与参照不同（{label}）：hook={entry['sha256'][:12]} "
                                f"ref={ref_sha[:12]}")
                        else:
                            # 单段：预期不同（见上），如实记录原因
                            entry["byte_diff_reason"] = (
                                "1a 单段不经 Executor：钩子读包内字节，"
                                "Executor 参照经 apply_fade+重写成容器后字节不同（时长相同）")
            hook_matrix.append(entry)
        hook_stats = hook.stats()
    else:
        hook_stats = None

    vox_commit, vox_git_err = _git_commit(REPO)
    kefu_commit, kefu_git_err = _git_commit(kefu_repo) if kefu_repo.is_dir() else (None, "kefu 仓不存在")

    report = {
        "card": "docs/tasks/T34-kefu-钩子接序列档与真链路实测.md",
        "spec": "docs/10 §10.7（序列命中）+ runtime/AGENTS.md §③（拼接规程）",
        "pack": {
            "pack_id": pack.pack_id, "pack_version": pack.pack_version,
            "assets": len(pack.assets), "voice": pack.voice,
            "model_version": pack.model_version,
        },
        "duplex": {"silence_pad_ms": duplex.silence_pad_ms, "fade_ms": duplex.fade_ms,
                   "slot_pad_ms": duplex.slot_pad_ms, "patience_ms": duplex.patience_ms},
        "provenance": {
            "manifest_sha256": _sha256_file(pack_dir / "manifest.json"),
            "yaml_sha256": _sha256_file(Path(os.environ["KEFU_HEAT_YAML"])),
            "vox_git_commit": vox_commit, "vox_git_error": vox_git_err,
            "kefu_git_commit": kefu_commit, "kefu_git_error": kefu_git_err,
        },
        "arms": {
            "A": {
                "name": "key 档整段展开",
                "taken": hook_stats is not None,
                "rows": [e for e in hook_matrix if e["arm"] == "A"],
                "requirement": "至少 1 轮实弹：钩子返回音频与同 plan 参照**逐字节相同**",
            },
            "B": {
                "name": "文本档整段（脑说整段预设）",
                "taken": False,
                "reason": ("待验收方起 brain 8092 + 语音端 8096 后执行；"
                           "本脚本不起服务（卡内边界）"),
                "candidates": ["repair_confirm_question", "inject_refuse", "user_no_unknown"],
                "rows": [],
            },
            "C": {
                "name": "对照臂（必然 miss → 走原路）",
                "taken": True,
                "rows": [e for e in hook_matrix if e["arm"] == "C"],
                "control": control,
            },
        },
        "hook": {
            "module_available": cls is not None,
            "load_error": hook_error,
            "stats": hook_stats,
        },
        "summary": {
            "n_multi_clause": len(details),
            "seq_hit": sum(1 for d in details if d["seq_arm"]["hit"]),
            "seq_miss": sum(1 for d in details if not d["seq_arm"]["hit"]),
            "old_arm_hit": sum(1 for d in details if d["old_arm"]["hit"]),
            "tts_calls_sum": sum(d.get("ref", {}).get("tts_calls", 0)
                                 for d in details if "ref" in d),
            "frame_assertions_failed": sum(
                1 for d in details if "ref" in d
                and d["ref"]["frames"] != d["expected_frames"]),
        },
        "problems": problems,
        "details": details,
        "env": {"python": sys.version.split()[0], "wavs_written": bool(args.write_wavs)},
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[kefu-seq-e2e] 报告 → {out_dir / 'report.json'}")
    s = report["summary"]
    print(f"[kefu-seq-e2e] 整段 {s['seq_hit']}/{s['n_multi_clause']} 序列命中；"
          f"现状档命中 {s['old_arm_hit']}（应为 0）；tts_calls 合计 {s['tts_calls_sum']}")
    print(f"[kefu-seq-e2e] 帧数断言失败 {s['frame_assertions_failed']}；问题 {len(problems)}")
    for p in problems:
        print(f"[kefu-seq-e2e]   - {p}", file=sys.stderr)

    if report["hook"]["load_error"] is not None:
        print(f"[kefu-seq-e2e] 钩子不可用（{report['hook']['load_error']}）——"
              "臂 A 只能由验收方在服务内取数", file=sys.stderr)

    return 3 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
