#!/usr/bin/env python3
"""本地神经 TTS 探针：零样本音色克隆 + 多条件对照（labs 一次性实验区，不进任何层契约）。

接口（本机实测，2026-09-23）：oMLX `:10099` 的 `/v1/audio/speech` 支持
`ref_audio`（**base64**，不是路径）+ `ref_text`（参考音频的转写）→ 零样本音色克隆。
模型表里可用：`Qwen3-TTS-12Hz-0.6B-Base-bf16`、`mlx-community:Qwen3-TTS-12Hz-1.7B-Base-8bit`
（Base = 克隆变体）、`Voxtral-4B-TTS-2603-mlx-4bit`、`mlx-community--Breeze-TTS-2-mlx`、`openbmb:VoxCPM2`。
另有 `/v1/audio/transcriptions`（ASR，项目 readback 已在用）可用来给参考音频出 `ref_text`。

**隐私纪律**：参考音频与全部产物**一律落仓外**（本仓是公开预备仓，真人录音永不入库；
本脚本只收命令行参数、不写死任何录音路径）。

用法：
    # 1) 给参考音频出转写（ref_text 必须与音频吻合；小模型对英文技术词会糊，可换更大 ASR）
    python3 labs/tts-clone-probe/gen.py --transcribe ref.wav --asr-model mlx-community:VibeVoice-ASR-4bit

    # 2) 克隆合成
    python3 labs/tts-clone-probe/gen.py --ref-audio ref.wav --ref-text "…" \
        --model mlx-community:Qwen3-TTS-12Hz-1.7B-Base-8bit \
        --text "您好，我是供热智能客服小暖。" --out out.wav [--speed 1.0]
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import urllib.request
import wave

BASE = "http://127.0.0.1:10099"


def post_json(path: str, payload: dict, timeout: float) -> tuple[int, bytes, str]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:  # 4xx/5xx 也把 body 带回来（便于定位参数问题）
        return e.code, e.read(), e.headers.get("Content-Type", "")


def post_multipart(path: str, file_path: pathlib.Path, fields: dict, timeout: float) -> bytes:
    boundary = "----voxcloneboundary"
    body = bytearray()
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{file_path.name}\"\r\nContent-Type: audio/wav\r\n\r\n"
    ).encode()
    body += file_path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + path,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def wav_info(path: pathlib.Path) -> str:
    try:
        with wave.open(str(path), "rb") as w:
            return f"{w.getnframes() / w.getframerate():.2f}s / {w.getframerate()}Hz / {w.getnchannels()}ch"
    except Exception as exc:  # noqa: BLE001
        return f"（不是合法 WAV：{exc}）"


def main() -> int:
    ap = argparse.ArgumentParser(description="本地神经 TTS 音色克隆探针")
    ap.add_argument("--transcribe", metavar="WAV", help="只做转写：给参考音频出 ref_text")
    ap.add_argument("--asr-model", default="mlx-community:VibeVoice-ASR-4bit",
                    help="ASR 模型（缺省 VibeVoice-ASR-4bit；小模型 Qwen3-ASR-0.6B-8bit 更快但英文词会糊）")
    ap.add_argument("--ref-audio", help="参考音频 WAV（仓外）——内部转 base64 传给 ref_audio")
    ap.add_argument("--ref-text", help="参考音频的转写（ref_text；不给则只传 ref_audio）")
    ap.add_argument("--text", help="要合成的文本")
    ap.add_argument("--out", help="输出 WAV 路径（仓外）")
    ap.add_argument("--model", default="mlx-community:Qwen3-TTS-12Hz-1.7B-Base-8bit")
    ap.add_argument("--speed", type=float, default=None, help="语速（1.0 原速；>1 更快）")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--instructions", default=None, help="风格/音色描述（VoiceDesign 类模型才吃）")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    if args.transcribe:
        p = pathlib.Path(args.transcribe).expanduser()
        raw = post_multipart("/v1/audio/transcriptions", p,
                             {"model": args.asr_model, "language": "zh"}, args.timeout)
        print(raw.decode("utf-8", "replace"))
        return 0

    if not (args.ref_audio and args.text and args.out):
        ap.error("合成模式需要 --ref-audio / --text / --out（或只给 --transcribe）")

    ref = pathlib.Path(args.ref_audio).expanduser()
    if not ref.is_file():
        raise SystemExit(f"参考音频不存在：{ref}")
    payload = {
        "model": args.model,
        "input": args.text,
        "ref_audio": base64.b64encode(ref.read_bytes()).decode("ascii"),
        "response_format": "wav",
        "language": args.language,
    }
    if args.ref_text:
        payload["ref_text"] = args.ref_text
    else:
        # 无参考音频时必须显式给 voice（本机实测：缺 voice 直接 400）
        payload["voice"] = "default"
    if args.speed is not None:
        payload["speed"] = args.speed
    if args.instructions:
        payload["instructions"] = args.instructions

    code, body, ctype = post_json("/v1/audio/speech", payload, args.timeout)
    out = pathlib.Path(args.out).expanduser()
    if code != 200 or not body.startswith(b"RIFF"):
        raise SystemExit(f"合成失败：HTTP {code} {ctype}\n{body[:300]!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    print(f"{out.name}  {len(body) / 1024:.0f}KB  {wav_info(out)}  model={args.model}"
          + (f" speed={args.speed}" if args.speed is not None else "")
          + ("（含 ref_text）" if args.ref_text else "（仅 ref_audio）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
