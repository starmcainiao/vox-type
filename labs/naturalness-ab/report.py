#!/usr/bin/env python3
"""拼接自然度盲测 · 报告生成（完整性门禁 + bootstrap 置信区间）。

门禁（fail-closed：任一条不满足即非零退出、不产出报告）：
- 两张打分表与盲化映射逐行对齐；MOS ∈ {1..5} 填齐；接缝三档填齐；条目级偏好填齐；
- 打分表 sha256 记入报告——数字必须有出处：固定话术集 + 固定种子 + 原始表可复算。

统计口径：
- 每臂 MOS：**中位数 + 95% 百分位 bootstrap 区间**（`eval.stats.bootstrap_ci`，seed=20260923、
  2000 次重采样）；均值作为描述量一并给出。n = 条目数；**单听测者、无跨人一致性**（见报告 §局限）。
- 接缝可察觉率 = （有但不影响 + 明显影响）/ 该臂片段数；明显影响率单列。
- 条目级三选一偏好计数；①−③ 的中位数差 = 「拼接的自然度代价」（同引擎同音色，唯一变量是接缝）。

用法：
    python3 labs/naturalness-ab/report.py
    python3 labs/naturalness-ab/report.py --lab /tmp/x --out-dir /tmp/x   # 用假表试跑（不污染仓内）
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import statistics
import sys
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval.stats import bootstrap_ci  # noqa: E402

LAB = REPO / "labs" / "naturalness-ab"
RAW = LAB / "raw"
SEAM_LEVELS = ("无", "有但不影响", "明显影响")
PREF_CHOICES = ("c1", "c2", "c3", "都一样")
ARM_ORDER = ("prebaked", "runtime_synth", "one_shot")
BOOT_SEED = 20260923


def read_csv(path: pathlib.Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_problems(clips: list[dict], items: list[dict], mapping: dict) -> list[str]:
    problems: list[str] = []
    for row in clips:
        item = (row.get("条目编号") or "").strip()
        code = (row.get("片段编号") or "").strip()
        entry = mapping["items"].get(item, {})
        if code not in entry:
            problems.append(f"片段行对不上盲化映射：{item}/{code}")
        mos = (row.get("整段自然度MOS(1-5)") or "").strip()
        if mos not in {"1", "2", "3", "4", "5"}:
            problems.append(f"MOS 未填或越界：{item}/{code} = {mos!r}")
        seam = (row.get("接缝可察觉(无/有但不影响/明显影响)") or "").strip()
        if seam not in SEAM_LEVELS:
            problems.append(f"接缝档未填或越界：{item}/{code} = {seam!r}")
    for row in items:
        item = (row.get("条目编号") or "").strip()
        if item not in mapping["items"]:
            problems.append(f"条目行对不上盲化映射：{item}")
        pref = (row.get("最自然的一段(c1/c2/c3/都一样)") or "").strip()
        if pref not in PREF_CHOICES:
            problems.append(f"条目级偏好未填或越界：{item} = {pref!r}")
        if pref in ("c1", "c2", "c3") and pref not in mapping["items"].get(item, {}):
            problems.append(f"偏好码不在该条目映射内：{item} = {pref}")
    if len(clips) != 3 * len(items):
        problems.append(f"片段表行数 {len(clips)} != 3 × 条目数 {len(items)}")
    if len(items) == 0:
        problems.append("条目表为空")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="拼接自然度盲测报告生成")
    ap.add_argument("--lab", default=str(LAB), help="打分表所在目录（缺省 labs/naturalness-ab）")
    ap.add_argument("--out-dir", default=None, help="报告落盘目录（缺省同 --lab）")
    args = ap.parse_args()
    lab = pathlib.Path(args.lab)
    out_dir = pathlib.Path(args.out_dir) if args.out_dir else lab

    map_file = RAW / "blinding_map.json"
    if not map_file.is_file():
        raise SystemExit("raw/blinding_map.json 不存在——先跑 blind.py")
    mapping = json.loads(map_file.read_text(encoding="utf-8"))

    clips_path = lab / "sheet_clips.csv"
    items_path = lab / "sheet_items.csv"
    if not clips_path.is_file() or not items_path.is_file():
        raise SystemExit(f"打分表不存在：{clips_path} / {items_path}")
    clips = read_csv(clips_path)
    items = read_csv(items_path)

    problems = collect_problems(clips, items, mapping)
    if problems:
        print("打分表未通过完整性门禁，未产出报告：", file=sys.stderr)
        for p in problems[:20]:
            print("  - " + p, file=sys.stderr)
        return 2

    by_arm: dict[str, dict[str, list]] = {a: {"mos": [], "seam": []} for a in ARM_ORDER}
    for row in clips:
        item = row["条目编号"].strip()
        code = row["片段编号"].strip()
        arm = mapping["items"][item][code]["arm"]
        by_arm[arm]["mos"].append(float(row["整段自然度MOS(1-5)"]))
        by_arm[arm]["seam"].append(row["接缝可察觉(无/有但不影响/明显影响)"].strip())

    arm_stats: dict[str, dict] = {}
    for arm in ARM_ORDER:
        vals = by_arm[arm]["mos"]
        seams = by_arm[arm]["seam"]
        lo, hi = bootstrap_ci(vals, 0.5, seed=BOOT_SEED)
        n = len(vals)
        arm_stats[arm] = {
            "n": n,
            "mos_median": statistics.median(vals),
            "mos_median_ci95": [round(lo, 3), round(hi, 3)],
            "mos_mean": round(statistics.fmean(vals), 3),
            "mos_values": vals,
            "seam_detect_rate": round(sum(1 for s in seams if s != "无") / n, 4),
            "seam_obvious_rate": round(sum(1 for s in seams if s == "明显影响") / n, 4),
        }

    pref_count = {c: 0 for c in PREF_CHOICES}
    pref_by_arm = {a: 0 for a in ARM_ORDER}
    for row in items:
        p = row["最自然的一段(c1/c2/c3/都一样)"].strip()
        pref_count[p] += 1
        if p in ("c1", "c2", "c3"):
            pref_by_arm[mapping["items"][row["条目编号"].strip()][p]["arm"]] += 1

    gap = round(arm_stats["one_shot"]["mos_median"] - arm_stats["prebaked"]["mos_median"], 3)

    # ① 与 ② 在本批逐字节相同（同引擎同参数、say 确定性）→ ② 充当**听测一致性对照**：
    # 同一段音频以两个码出现两次，逐条 |MOS(①)−MOS(②)| 即本批评分噪声下限。
    per_item: dict[str, dict[str, float]] = {}
    for row in clips:
        item = row["条目编号"].strip()
        code = row["片段编号"].strip()
        arm = mapping["items"][item][code]["arm"]
        per_item.setdefault(item, {})[arm] = float(row["整段自然度MOS(1-5)"])
    consistency: list[float] = []
    for item, entry in mapping["items"].items():
        by_arm = {v["arm"]: v for v in entry.values()}
        a = by_arm.get("prebaked")
        b = by_arm.get("runtime_synth")
        if a and b and a["sha256"] == b["sha256"]:
            d = per_item.get(item, {})
            if "prebaked" in d and "runtime_synth" in d:
                consistency.append(abs(d["prebaked"] - d["runtime_synth"]))
    consistency_stat = {
        "n_pairs": len(consistency),
        "mean_abs_mos_diff": round(statistics.fmean(consistency), 3) if consistency else None,
        "max_abs_mos_diff": max(consistency) if consistency else None,
        "note": "① 与 ② 逐字节相同的条目上，同一音频两次评分的绝对差——评分噪声下限",
    }

    # 条目构成：真实多分句 vs 构造双单元序列（构造项在 raw/arms.jsonl 标 constructed=true）
    arms_ledger = [
        json.loads(line)
        for line in (RAW / "arms.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    constructed = {r["item"]: bool(r.get("constructed")) for r in arms_ledger}
    type_rows = []
    for tlabel, tflag in (("真实多分句", False), ("构造双单元", True)):
        cells = []
        for arm in ARM_ORDER:
            vals = [
                per_item[i][arm]
                for i in per_item
                if constructed.get(i, False) is tflag and arm in per_item[i]
            ]
            cells.append(f"{statistics.median(vals)}（n={len(vals)}）" if vals else "—")
        type_rows.append(f"| {tlabel} | " + " | ".join(cells) + " |")
    composition = {
        "real": sum(1 for v in constructed.values() if not v),
        "constructed": sum(1 for v in constructed.values() if v),
    }

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_items": len(items),
        "n_clips": len(clips),
        "listeners": 1,
        "blinding_seed": mapping.get("seed"),
        "bootstrap": {"q": 0.5, "level": 0.95, "resamples": 2000, "seed": BOOT_SEED},
        "arm_stats": arm_stats,
        "preference": {"by_code": pref_count, "by_arm": pref_by_arm},
        "seam_cost_median_gap_oneshot_minus_prebaked": gap,
        "consistency_check": consistency_stat,
        "item_composition": composition,
        "mos_median_by_item_type": type_rows,
        "sheet_sha256": {"sheet_clips.csv": sha256_of(clips_path), "sheet_items.csv": sha256_of(items_path)},
        "arms_ledger_sha256": sha256_of(RAW / "arms.jsonl") if (RAW / "arms.jsonl").is_file() else None,
    }
    (out_dir / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    def row_of(arm: str) -> str:
        s = arm_stats[arm]
        return (
            f"| {arm} | {s['n']} | {s['mos_median']} | "
            f"[{s['mos_median_ci95'][0]}, {s['mos_median_ci95'][1]}] | {s['mos_mean']} | "
            f"{s['seam_detect_rate']:.0%} | {s['seam_obvious_rate']:.0%} |"
        )

    md = [
        "# 拼接自然度盲测 · 报告（pilot）",
        "",
        f"- 生成时间：{result['generated_at']}；条目 {len(items)} 条 × 3 臂 = {len(clips)} 段；**听测人数 1（pilot）**",
        "- 判据与协议：`docs/05 §5.1`（拼接自然度 = 回读 CER + 盲测）与 `§5.3`（三产线协议）——本批是盲测的 pilot 实现",
        f"- 盲化种子 {result['blinding_seed']}（`blind.py`）；复现链：`gen_samples.py` → `blind.py` → 人工听测填表 → `report.py`",
        f"- 输入指纹（sha256）：`sheet_clips.csv` {result['sheet_sha256']['sheet_clips.csv'][:16]}…、"
        f"`sheet_items.csv` {result['sheet_sha256']['sheet_items.csv'][:16]}…（完整值见 `report.json`）",
        "",
        "## 一、三臂 MOS（同引擎同音色，唯一变量 = 接缝/来源）",
        "",
        "| 臂 | n | 中位数 | 95% bootstrap 区间 | 均值 | 接缝可察觉率 | 明显影响率 |",
        "|---|---|---|---|---|---|---|",
        row_of("prebaked"),
        row_of("runtime_synth"),
        row_of("one_shot"),
        "",
        f"主口径 = 中位数（含 bootstrap 区间，`eval.stats.bootstrap_ci` 口径）；均值为描述量。"
        f"每臂 n = {len(items)}（= `eval.stats.MIN_REPEATS` 下限 20）。",
        "",
        "按条目构成分型（构造项是补齐到 20 条的产物，别与真实多分句混读）：",
        "",
        "| 条目类型 | ① 预铸拼接 | ② 逐句实时合成 | ③ 整段一次合成 |",
        "|---|---|---|---|",
        *type_rows,
        f"（构成：真实多分句 {composition['real']} 条 + 构造双单元 {composition['constructed']} 条）",
        "",
        "## 二、条目级偏好（三选一，按码计数后回映射到臂）",
        "",
        f"- 按码：{pref_count}",
        f"- 回映射到臂：{pref_by_arm}",
        "",
        "## 三、结论",
        "",
        f"- **拼接的自然度代价**（①−③ 中位数差，正数=拼接更差）：**{gap}**",
        f"- ① vs ② 中位数差：{round(arm_stats['prebaked']['mos_median'] - arm_stats['runtime_synth']['mos_median'], 3)}"
        "（同接缝结构、只差「预铸资产 vs 运行时合成」——接近 0 说明预铸本身不额外损伤自然度）",
        f"- 接缝可察觉率：① {arm_stats['prebaked']['seam_detect_rate']:.0%} / ② {arm_stats['runtime_synth']['seam_detect_rate']:.0%}"
        f" / ③ {arm_stats['one_shot']['seam_detect_rate']:.0%}（③ 无接缝，若有检出即为误报/其他不自然感）",
        f"- **听测一致性对照**（① 与 ② 在本批逐字节相同，同引擎同参数）：n={consistency_stat['n_pairs']} 对，"
        f"平均 |ΔMOS| = {consistency_stat['mean_abs_mos_diff']}，最大 {consistency_stat['max_abs_mos_diff']}"
        "——这是本批的**评分噪声下限**；上面各差若小于它，就不能当结论。",
        "",
        "## 四、局限（必须随数字一起引用）",
        "",
        "- **单听测者（n=1）**：无跨人一致性，MOS 为 pilot 级，不得对外当作基准；补 2 位听测者后才能升格。",
        f"- **① 与 ② 逐字节相同**（本批实测 {consistency_stat['n_pairs']}/{len(items)}：同引擎、同参数、`say` 确定性）→ ② 不构成独立产线，"
        "仅作一致性对照；`docs/05 §5.3` 的第三产线（端到端模型）不在本批。",
        "- 引擎为 macOS `say` / Tingting（零第三方依赖前提），结论**不外推**到其他 TTS。",
        f"- 条目 {len(items)} 条 = 真实多分句 {composition['real']} 条 + **构造双单元序列 {composition['constructed']} 条**"
        "（`packs/heat_kefu` 只有 14 条真实多分句；构造项为凑满 `MIN_REPEATS=20` 而加，台账里标 `constructed=true`，"
        "分型对照见 §一 副表）；槽位接缝与端到端臂未覆盖。",
        "- 固定种子 + 原始表可复算：`report.json` 里的 sha256 与 `raw/` 台账即出处。",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")

    print(f"报告已生成：{out_dir / 'report.json'} 与 {out_dir / 'report.md'}")
    print(f"①−③ 中位数差（拼接代价）= {gap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
