#!/usr/bin/env python3
"""拼接自然度盲测 · 盲化与打分表生成（固定种子，可复现）。

做什么：
- 每条目把三臂随机排序编成 c1/c2/c3（**码不含臂信息**），音频复制到 `<out-root>/listen/`；
- 映射表落 `raw/blinding_map.json`（含每段 sha256）——听测完成前不看它即可；
- 生成两张待填打分表（**UTF-8 带 BOM**，Excel / Numbers 可直接打开不乱码）：
    `sheet_clips.csv` 逐片段：整段自然度 MOS(1–5) + 接缝可察觉（无 / 有但不影响 / 明显影响）
    `sheet_items.csv` 逐条目：哪一段最自然（c1 / c2 / c3 / 都一样）

用法：
    python3 labs/naturalness-ab/blind.py
    python3 labs/naturalness-ab/blind.py --out-root /tmp/vox-naturalness --seed 1
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random
import shutil

REPO = pathlib.Path(__file__).resolve().parents[2]
LAB = REPO / "labs" / "naturalness-ab"
RAW = LAB / "raw"


def 展示路径(p: pathlib.Path) -> str:
    """仓内台账/打分表里的路径一律写 `~` 形式（公开预备仓不得出现用户名绝对路径）。"""
    try:
        return "~/" + str(pathlib.Path(p).relative_to(pathlib.Path.home()))
    except ValueError:
        return str(p)


CLIP_COLS = [
    "条目编号",
    "片段编号",
    "音频文件",
    "整段自然度MOS(1-5)",
    "接缝可察觉(无/有但不影响/明显影响)",
    "备注",
]
ITEM_COLS = ["条目编号", "最自然的一段(c1/c2/c3/都一样)", "备注"]


def main() -> int:
    ap = argparse.ArgumentParser(description="盲化三臂样本并生成打分表")
    ap.add_argument(
        "--out-root",
        default=str(pathlib.Path.home() / "vox-naturalness"),
        help="与 gen_samples.py 相同的音频根目录",
    )
    ap.add_argument("--seed", type=int, default=20260923, help="盲化随机种子（固定即复现）")
    ap.add_argument("--force", action="store_true",
                    help="允许覆盖已存在的打分表（默认拒绝——表里可能已有人工填写的听测值）")
    args = ap.parse_args()

    # 防误覆盖：打分表是人工填写的「真人标注」工件，重跑不得静默清空（本目录踩过一次）。
    if not args.force:
        for name in ("sheet_clips.csv", "sheet_items.csv"):
            if (LAB / name).exists():
                raise SystemExit(
                    f"{LAB / name} 已存在——重跑会重写该表（可能清掉已填写的听测值）。"
                    f"确实要重生成请加 --force；只想复核映射请直接读 raw/blinding_map.json。"
                )

    arms_file = RAW / "arms.jsonl"
    if not arms_file.is_file():
        raise SystemExit("raw/arms.jsonl 不存在——先跑 python3 labs/naturalness-ab/gen_samples.py")
    rows = [
        json.loads(line)
        for line in arms_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    out_root = pathlib.Path(args.out_root).expanduser()
    listen_dir = out_root / "listen"
    listen_dir.mkdir(parents=True, exist_ok=True)

    items: list[str] = []
    for r in rows:
        if r["item"] not in items:
            items.append(r["item"])

    rng = random.Random(args.seed)
    mapping: dict[str, dict[str, dict]] = {}
    order: list[tuple[str, str, pathlib.Path]] = []
    for item in items:
        group = [r for r in rows if r["item"] == item]
        if len(group) != 3:
            raise SystemExit(f"条目 {item} 的臂数不是 3（实际 {len(group)}）")
        shuffled = group[:]
        rng.shuffle(shuffled)
        entry: dict[str, dict] = {}
        for code, r in zip(("c1", "c2", "c3"), shuffled):
            src = pathlib.Path(r["path"]).expanduser()
            if not src.is_file():
                raise SystemExit(f"音频不存在：{src}（重跑 gen_samples.py）")
            dst = listen_dir / f"{item}-{code}.wav"
            shutil.copyfile(src, dst)
            entry[code] = {
                "arm": r["arm"],
                "arm_label": r["arm_label"],
                "src": 展示路径(src),
                "sha256": r["sha256"],
            }
            order.append((item, code, dst))
        mapping[item] = entry

    (RAW / "blinding_map.json").write_text(
        json.dumps(
            {"seed": args.seed, "listen_dir": 展示路径(listen_dir), "items": mapping},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with open(LAB / "sheet_clips.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CLIP_COLS)
        for item, code, dst in order:
            w.writerow([item, code, 展示路径(dst), "", "", ""])
    with open(LAB / "sheet_items.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(ITEM_COLS)
        for item in items:
            w.writerow([item, "", ""])

    print(f"盲化完成：{len(items)} 条目 × 3 段 → {listen_dir}")
    print(f"映射表（听测前别看）：{RAW / 'blinding_map.json'}")
    print(f"打分表：{LAB / 'sheet_clips.csv'}（{len(order)} 行）· {LAB / 'sheet_items.csv'}（{len(items)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
