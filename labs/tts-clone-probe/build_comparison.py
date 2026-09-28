#!/usr/bin/env python3
"""音色克隆对照套件：同一批话术 × 多条件（labs 一次性实验区，不进任何层契约）。

做什么：用给定的参考音频克隆音色，对同一批标准话术生成多条件版本，落一个可点开就听的 HTML 索引，
供人评估「换引擎值不值」以及「换引擎后拼接听起来怎样」。

条件（每个条件一条 WAV；文案取自 `packs/heat_kefu` 的拆句话术，与盲测套件同源）：
  A 现状·预铸拼接    —— 走产品快路 `sh bin/vox run`（macOS say / Tingting，当前包内音色）
  B 克隆·整句一次     —— 神经 TTS 一次生成（= 将来预铸的音频）
  C 克隆·拆句拼接     —— 逐句克隆生成，再用与产品**同一套**参数拼接（runtime.audio.concat_wavs）
  D 克隆·16kHz 契约版 —— 把 B 降采样到 16kHz（现有音质契约 `runtime/audio.py: SAMPLE_RATE=16000` 下的样子）
  E 克隆·语速 1.15    —— 语速可调的证据（回应「速度要能灵活适配」）
  F 克隆·0.6B 小模型  —— 仅首个条目，部署成本对照（对照 B 的 1.7B）

隐私纪律：参考音频与产物**一律仓外**；本脚本只走命令行参数，不写死任何录音路径。

用法：
    python3 labs/tts-clone-probe/build_comparison.py \
        --ref-audio ~/vox-naturalness/clone-probe/ref.wav \
        --ref-text "…参考音频转写…" \
        --out-dir ~/vox-naturalness/clone-probe/comparison
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "labs" / "tts-clone-probe"))

from gen import post_json  # noqa: E402  # 复用同一个 oMLX 调用面

from runtime.audio import SAMPLE_RATE, concat_wavs, read_wav, silence  # noqa: E402

BUILT_PACK = REPO / "packs" / "heat_kefu_build" / "heat-kefu-1"
# 本机实测（2026-09-23，同一参考 + 同一 ref_text）：
#   0.6B-Base → 产出可懂（ASR 回读基本正确）；
#   1.7B-Base-8bit → 产出退化（回读乱码、还把 ref_text 内容混进输出）——故默认用 0.6B，
#   1.7B 只留一条对照条目（F）备查。换参考/换参数后应重新验证。
MODEL_CLONE = "Qwen3-TTS-12Hz-0.6B-Base-bf16"
MODEL_BIG = "mlx-community:Qwen3-TTS-12Hz-1.7B-Base-8bit"

# 两个条目：拆句源 key → 展示名（文案从 manifest 读，不写死文本）
ITEMS = [
    ("clarify_work_order", "条目 1 · 澄清工单（陈述 2 句）"),
    ("repair_confirm_question", "条目 2 · 确认提交（陈述 2 句）"),
]


def manifest_texts() -> dict[str, str]:
    """包内 key → 文本（源自 pack build 产物；缺产物直接报错）。"""
    if not (BUILT_PACK / "manifest.json").is_file():
        raise SystemExit(f"构建产物不存在：{BUILT_PACK}——先跑 sh bin/vox pack build packs/heat_kefu --out {BUILT_PACK}")
    data = json.loads((BUILT_PACK / "manifest.json").read_text(encoding="utf-8"))
    return {a["key"]: a["text"] for a in data["assets"]}


def seg_keys(base: str) -> list[str]:
    keys = sorted(k for k in manifest_texts() if k.startswith(base + "__"))
    if not keys:
        raise SystemExit(f"包内找不到拆句键：{base}__*")
    return keys


def clone(text: str, ref_b64: str, ref_text: str, out: pathlib.Path, model: str,
          speed: float | None = None) -> pathlib.Path:
    payload = {
        "model": model,
        "input": text,
        "ref_audio": ref_b64,
        "ref_text": ref_text,
        "response_format": "wav",
        "language": "zh",
    }
    if speed is not None:
        payload["speed"] = speed
    code, body, ctype = post_json("/v1/audio/speech", payload, 300.0)
    if code != 200 or not body.startswith(b"RIFF"):
        raise SystemExit(f"克隆合成失败：HTTP {code} {ctype}\n{body[:300]!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return out


def prebaked_tingting(base: str, dst: pathlib.Path) -> pathlib.Path:
    """A 现状：产品快路出音频（plan = 该条目的全部拆句键）。"""
    plan = [{"key": k} for k in seg_keys(base)]
    plan_path = dst.parent / f"plan-{base}.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cp = subprocess.run(
        ["sh", "bin/vox", "run", str(plan_path), "--pack", str(BUILT_PACK), "--out", str(dst)],
        cwd=REPO, capture_output=True, text=True,
    )
    if cp.returncode != 0:
        raise SystemExit(f"A 现状预铸失败：{cp.stderr.strip()[:300]}")
    return dst


def concat_cloned(seg_files: list[pathlib.Path], dst: pathlib.Path) -> pathlib.Path:
    """C 拼接：逐句克隆产物 → **降到 16kHz（现有契约）** → 与产品同一套参数拼接（200ms 垫 / 5ms 淡）。

    为什么先降采样：`runtime.audio.read_wav` 硬校验 16kHz 且明确拒绝重采样（fail-closed），
    TTS 产物是 24kHz——真流水线里这一步同样绕不开（降采样进 16k，或改冻结区改契约）。
    """
    resampled = []
    for f in seg_files:
        g = f.with_name(f.stem + "-16k.wav")
        to_16k(f, g)
        resampled.append(g)
    segments = []
    for i, g in enumerate(resampled):
        samples, _ = read_wav(g)
        segments.append(samples)
        if i < len(resampled) - 1:
            segments.append(silence(200, SAMPLE_RATE))
    concat_wavs(segments, dst, framerate=SAMPLE_RATE, fade_ms=5)
    return dst


def to_16k(src: pathlib.Path, dst: pathlib.Path) -> pathlib.Path:
    """D 契约版：24kHz → 16kHz（现有 runtime 硬校验的采样率）。"""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(dst)],
        check=True,
    )
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="音色克隆对照套件")
    ap.add_argument("--ref-audio", required=True, help="参考音频 WAV（仓外）")
    ap.add_argument("--ref-text", required=True, help="参考音频的转写（ref_text）")
    ap.add_argument("--out-dir", required=True, help="产物目录（仓外）")
    ap.add_argument("--skip-16k", action="store_true", help="跳过 16kHz 契约版（无 ffmpeg 时）")
    ap.add_argument("--skip-small", action="store_true", help="跳过 0.6B 小模型对照")
    args = ap.parse_args()

    ref = pathlib.Path(args.ref_audio).expanduser()
    if not ref.is_file():
        raise SystemExit(f"参考音频不存在：{ref}")
    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ref_b64 = base64.b64encode(ref.read_bytes()).decode("ascii")
    texts = manifest_texts()

    rows: list[dict] = []
    for base, label in ITEMS:
        keys = seg_keys(base)
        full = "".join(texts[k] for k in keys)
        rows.append({"item": base, "item_label": label, "cond": "A",
                     "cond_label": "现状·预铸拼接（macOS say / Tingting）",
                     "path": prebaked_tingting(base, out_dir / f"{base}-A-tingting.wav"), "text": full})
        rows.append({"item": base, "item_label": label, "cond": "B",
                     "cond_label": "克隆·整句一次生成（Qwen3-TTS-0.6B）",
                     "path": clone(full, ref_b64, args.ref_text, out_dir / f"{base}-B-clone-whole.wav", MODEL_CLONE),
                     "text": full})
        seg_files = [clone(texts[k], ref_b64, args.ref_text, out_dir / f"{base}-C-seg{i}.wav", MODEL_CLONE)
                     for i, k in enumerate(keys)]
        rows.append({"item": base, "item_label": label, "cond": "C",
                     "cond_label": "克隆·拆句拼接（逐句生成 → 16kHz → 200ms 垫 / 5ms 淡）",
                     "path": concat_cloned(seg_files, out_dir / f"{base}-C-clone-spliced.wav"), "text": full})
        if not args.skip_16k:
            b_path = next(r["path"] for r in rows if r["item"] == base and r["cond"] == "B")
            rows.append({"item": base, "item_label": label, "cond": "D",
                         "cond_label": "克隆·16kHz 契约版（现有 SAMPLE_RATE=16000 下的样子）",
                         "path": to_16k(b_path, out_dir / f"{base}-D-clone-16k.wav"), "text": full})
        rows.append({"item": base, "item_label": label, "cond": "E",
                     "cond_label": "克隆·语速 1.15（speed 参数）",
                     "path": clone(full, ref_b64, args.ref_text, out_dir / f"{base}-E-clone-speed115.wav",
                                   MODEL_CLONE, speed=1.15),
                     "text": full})

    if not args.skip_small:
        base0, label0 = ITEMS[0]
        full0 = "".join(texts[k] for k in seg_keys(base0))
        rows.append({"item": base0, "item_label": label0, "cond": "F",
                     "cond_label": "克隆·1.7B 大模型（本机实测退化，留档对照）",
                     "path": clone(full0, ref_b64, args.ref_text, out_dir / f"{base0}-F-clone-17b.wav",
                                   MODEL_BIG),
                     "text": full0})

    ref_copy = out_dir / "REF-原声参考段.wav"
    ref_copy.write_bytes(ref.read_bytes())

    parts = [
        "<!doctype html><meta charset='utf-8'><title>音色克隆对照</title>",
        "<style>body{font-family:-apple-system,sans-serif;max-width:920px;margin:24px auto;line-height:1.6}"
        "h2{margin-top:28px;border-top:1px solid #ddd;padding-top:12px}audio{width:100%;margin:2px 0}"
        ".c{font-weight:600}.t{color:#666;font-size:13px}div{margin:6px 0 10px}</style>",
        "<h1>音色克隆对照（本机 oMLX · 零样本克隆）</h1>",
        f"<p class='t'>参考音频：{html.escape(str(ref))}</p>",
        "<h2>REF · 原声参考段</h2>",
        f"<audio controls src='{ref_copy.name}'></audio>",
        "<p class='t'>下面每条都克隆自这一段；先听它，再听克隆。</p>",
    ]
    for base, label in ITEMS:
        parts.append(f"<h2>{html.escape(label)}</h2>")
        parts.append(f"<p class='t'>文案：{html.escape(''.join(texts[k] for k in seg_keys(base)))}</p>")
        for r in [x for x in rows if x["item"] == base]:
            parts.append(
                f"<div><span class='c'>{r['cond']} · {html.escape(r['cond_label'])}</span>"
                f"<audio controls src='{r['path'].name}'></audio></div>"
            )
    (out_dir / "index.html").write_text("\n".join(parts) + "\n", encoding="utf-8")

    print(f"对照套件就绪：{out_dir}")
    for r in rows:
        print(f"  {r['cond']} {r['item']:<26} {r['path'].name}")
    print(f"  REF 原声参考段             {ref_copy.name}")
    print(f"\n打开：open {out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
