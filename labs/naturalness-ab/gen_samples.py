#!/usr/bin/env python3
"""拼接自然度盲测 · 三臂样本生成（labs 一次性实验区，不进任何层契约）。

三臂同引擎（macOS say / Tingting）、同语速档（normal），**唯一变量是接缝与来源**：

  ① prebaked      走产品快路 `bin/vox run`：拆句资产按序拼接 + 句间静音垫 + 段级淡入淡出
  ② runtime_synth 运行时逐句合成，用**同一套**拼接参数（DuplexParams 缺省 + runtime.audio.concat_wavs）
  ③ one_shot      同一段文本一次合成（无接缝）——本项目自加的参照臂，用来量「拼接的自然度代价」

（`docs/05 §5.3` 的第三产线「端到端模型输出」留第二批，复用 `labs/e2e-vs-cascade` 的 harness。）

条目选择（确定性）：`packs/heat_kefu` 的 14 个多分句源 key 中取**总时长最短的 10 条**
（避免听测疲劳；规则写死在此，同包重跑必得同选择）。

产物：音频落**仓外**（`*.wav` 不进仓）；台账落 `raw/arms.jsonl`（逐条 sha256 + 时长，可复算）。

用法：
    python3 labs/naturalness-ab/gen_samples.py
    python3 labs/naturalness-ab/gen_samples.py --out-root /tmp/vox-naturalness --limit 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from adapters.tts_macsay.adapter import MacSayTts  # noqa: E402
from runtime.audio import SAMPLE_RATE, concat_wavs, read_wav, silence  # noqa: E402
from runtime.duplex import DuplexParams  # noqa: E402

BUILT_PACK = REPO / "packs" / "heat_kefu_build" / "heat-kefu-1"
PACK_SRC = REPO / "packs" / "heat_kefu"
RAW = REPO / "labs" / "naturalness-ab" / "raw"
ARMS = ("prebaked", "runtime_synth", "one_shot")
ARM_LABEL = {
    "prebaked": "①预铸拼接（产品快路）",
    "runtime_synth": "②逐句实时合成",
    "one_shot": "③整段一次合成（无接缝参照）",
}

# 构造的双单元序列（台账里标 constructed=True）：packs/heat_kefu 只有 14 条真实多分句，
# 补齐到 20 条是为了让**每臂样本量达到 `eval.stats.MIN_REPEATS = 20`**——不足 20 不出置信区间、
# 不得出报告（`eval/AGENTS.md §①`，实测 `bootstrap_ci` 会直接抛错）。
# 选法：同一业务步骤的两句相邻话术（两段、一接缝，与拆句条目同构）。
PAIRS = [
    ("repair_ask_userNo", "repair_ask_address"),
    ("pay_ask_user_no", "pay_due_hint"),
    ("work_order_hint", "work_order_ask"),
    ("transfer_ready", "transfer_queued"),
    ("off_hours_repair", "off_hours_urgent"),
    ("realtime_ask", "realtime_empty"),
]


def load_items(limit: int) -> list[dict]:
    """确定性选条目：多分句源 key 按预铸总时长升序取前 limit 条。"""
    if not (BUILT_PACK / "manifest.json").is_file():
        raise SystemExit(
            f"构建产物不存在：{BUILT_PACK}\n"
            f"先跑：sh bin/vox pack build packs/heat_kefu --out {BUILT_PACK}"
        )
    manifest = json.loads((BUILT_PACK / "manifest.json").read_text(encoding="utf-8"))
    by_key = {a["key"]: a for a in manifest["assets"]}
    phrases = json.loads((PACK_SRC / "phrases.json").read_text(encoding="utf-8"))["phrases"]

    groups: dict[str, list[tuple[int, str]]] = {}
    for p in phrases:
        key = p["key"]
        if "__" in key:
            base, _, idx = key.rpartition("__")
            groups.setdefault(base, []).append((int(idx), key))

    items = []
    for base, segs in groups.items():
        segs.sort()
        keys = [k for _, k in segs]
        missing = [k for k in keys if k not in by_key]
        if missing:
            raise SystemExit(f"包内缺资产 {missing}——先重跑 pack build")
        items.append(
            {
                "item": base,
                "seg_keys": keys,
                "texts": [by_key[k]["text"] for k in keys],
                "built_dur_ms": sum(by_key[k]["duration_ms"] for k in keys),
                "constructed": False,
            }
        )
    for a, b in PAIRS:
        missing = [k for k in (a, b) if k not in by_key]
        if missing:
            raise SystemExit(f"构造序列的 key 不在包内：{missing}")
        items.append(
            {
                "item": f"{a}+{b}",
                "seg_keys": [a, b],
                "texts": [by_key[a]["text"], by_key[b]["text"]],
                "built_dur_ms": by_key[a]["duration_ms"] + by_key[b]["duration_ms"],
                "constructed": True,
            }
        )
    items.sort(key=lambda x: (x["built_dur_ms"], x["item"]))
    return items[:limit]


def gen_prebaked(item: dict, out_dir: pathlib.Path) -> pathlib.Path:
    """① 产品快路：多单元 plan 交给 bin/vox run 出音频。"""
    plan = [{"key": k} for k in item["seg_keys"]]
    plan_path = RAW / "plans" / f"{item['item']}.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = out_dir / f"{item['item']}-prebaked.wav"
    cp = subprocess.run(
        ["sh", "bin/vox", "run", str(plan_path), "--pack", str(BUILT_PACK), "--out", str(out)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0:
        raise SystemExit(f"① 预铸拼接失败（rc={cp.returncode}）：{cp.stderr.strip()[:400]}")
    return out


def gen_runtime_synth(item: dict, out_dir: pathlib.Path) -> pathlib.Path:
    """② 运行时逐句合成，再用与产品**同一套**参数拼接（缺省 200ms 静音垫 / 5ms 淡入淡出）。"""
    params = DuplexParams()
    tts = MacSayTts()
    segments = []
    with tempfile.TemporaryDirectory(prefix="nat-ab-") as tmp:
        for i, text in enumerate(item["texts"]):
            one = pathlib.Path(tmp) / f"seg{i}.wav"
            tts.synthesize(text, one, rate_key="normal")
            samples, _ = read_wav(one)
            segments.append(samples)
            if i < len(item["texts"]) - 1:
                segments.append(silence(params.silence_pad_ms, SAMPLE_RATE))
    out = out_dir / f"{item['item']}-runtime_synth.wav"
    concat_wavs(segments, out, framerate=SAMPLE_RATE, fade_ms=params.fade_ms)
    return out


def gen_one_shot(item: dict, out_dir: pathlib.Path) -> pathlib.Path:
    """③ 整段一次合成（无接缝参照臂）。"""
    out = out_dir / f"{item['item']}-one_shot.wav"
    MacSayTts().synthesize("".join(item["texts"]), out, rate_key="normal")
    return out


def 展示路径(p: pathlib.Path) -> str:
    """仓内台账里的路径一律写 `~` 形式（公开预备仓不得出现用户名绝对路径）。"""
    try:
        return "~/" + str(p.relative_to(pathlib.Path.home()))
    except ValueError:
        return str(p)


def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def duration_ms_of(path: pathlib.Path) -> int:
    samples, framerate = read_wav(path)
    return int(round(len(samples) * 1000 / framerate))


def main() -> int:
    ap = argparse.ArgumentParser(description="拼接自然度盲测三臂样本生成")
    ap.add_argument(
        "--out-root",
        default=str(pathlib.Path.home() / "vox-naturalness"),
        help="音频落盘根目录（仓外，缺省 ~/vox-naturalness）",
    )
    ap.add_argument("--limit", type=int, default=20, help="条目数（缺省 20 = MIN_REPEATS 下限）")
    args = ap.parse_args()

    out_root = pathlib.Path(args.out_root).expanduser()
    arms_dir = out_root / "arms"
    arms_dir.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)

    items = load_items(args.limit)
    if len(items) < args.limit:
        raise SystemExit(f"多分句源 key 只有 {len(items)} 条，少于 --limit={args.limit}")

    rows = []
    for it in items:
        for arm, fn in (
            ("prebaked", gen_prebaked),
            ("runtime_synth", gen_runtime_synth),
            ("one_shot", gen_one_shot),
        ):
            path = fn(it, arms_dir)
            dur = duration_ms_of(path)
            if dur <= 0:
                raise SystemExit(f"产出空音频：{path}")
            rows.append(
                {
                    "item": it["item"],
                    "arm": arm,
                    "arm_label": ARM_LABEL[arm],
                    "seg_keys": it["seg_keys"],
                    "texts": it["texts"],
                    "full_text": "".join(it["texts"]),
                    "path": 展示路径(path),
                    "sha256": sha256_of(path),
                    "duration_ms": dur,
                    "built_dur_ms": it["built_dur_ms"],
                    "constructed": it["constructed"],
                }
            )
            print(f"  {it['item']:<30} {arm:<14} {dur / 1000:6.1f}s  {path.name}")

    (RAW / "arms.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )

    total = sum(r["duration_ms"] for r in rows) / 1000
    print(f"\n条目 {len(items)} 条 × 3 臂 = {len(rows)} 段；音频合计 {total:.0f}s（约 {total / 60:.1f} 分钟）")
    print(f"音频根目录：{out_root}")
    print(f"台账：{RAW / 'arms.jsonl'}")
    print("下一步：python3 labs/naturalness-ab/blind.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
