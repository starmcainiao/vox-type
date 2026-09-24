#!/usr/bin/env bash
# labs/duplex-quality/run.sh — 双工质量跑批脚本（T20）
#
# 只测「系统主动开口」侧：场景全部由固定种子生成，不读任何真实语料/录音。
# 三指标（打断准确率 / 假阳性 / 轮转延迟）全部从 runtime 的 policy_stream 事件流算，
# 不需要真实音频、不依赖实时播放、不碰麦克风。
#
# 口径与复现命令见 eval/AGENTS.md ⑧；报告字段口径见 report.json 的 caliber 块。
#
# 用法（在仓库根目录执行，路径一律相对）：
#   bash labs/duplex-quality/run.sh
#
# 前置：包已 build（同一命令，幂等）。
# 纪律：本脚本只写仓内相对路径，不含任何绝对路径。

set -eu

REPO_ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$REPO_ROOT"

PACK=packs/heat_kefu_build/heat-kefu-1
OUT=labs/duplex-quality
SEED=20260920

# ① 前置：构建资产包（若已存在则覆盖重建，保证 provenance 里的 manifest sha256 与包一致）
sh bin/vox pack build packs/heat_kefu --out "$PACK" >/dev/null

# ② 跑批（默认三预设：baseline-allow / confirm-patience400 / backchannel-off）
python3 -m eval.duplex_quality \
    --pack "$PACK" \
    --out "$OUT" \
    --seed "$SEED" \
    --plan-unit-count 8 \
    --n-plans 2 \
    --num-actions 60 \
    2>&1 | grep -v RuntimeWarning

# ③ 可复现自检：同一 (参数集, 种子, 包) 再跑一次，除 generated_at 外必须逐字节一致。
#    两次命令的 --out 故意写成同一路径，避免命令行里夹带不同的绝对路径
#    而让报告出现与指标无关的差异（报告里的 command 字段是字面量回显）。
TMP_A=$(mktemp -d)
TMP_B=$(mktemp -d)
TMP_C=$(mktemp -d)
trap 'rm -rf "$TMP_A" "$TMP_B" "$TMP_C"' EXIT HUP INT TERM

cp "$OUT/report.json" "$TMP_A/report.json"
python3 -m eval.duplex_quality --pack "$PACK" --out "$OUT" --seed "$SEED" >/dev/null 2>&1
cp "$OUT/report.json" "$TMP_B/report.json"

if diff <(grep -v '"generated_at"' "$TMP_A/report.json") \
        <(grep -v '"generated_at"' "$TMP_B/report.json") >/dev/null; then
    echo "[ok] 可复现：两次报告除 generated_at 外逐字节一致（seed=${SEED}）"
else
    echo "[fail] 可复现性破坏：同种子两次报告出现差异" >&2
    diff <(grep -v '"generated_at"' "$TMP_A/report.json") \
         <(grep -v '"generated_at"' "$TMP_B/report.json") | head -20 >&2
    exit 3
fi

# ④ 换种子必须变（证明种子真起作用）
python3 -m eval.duplex_quality --pack "$PACK" --out "$TMP_C" --seed 111 >/dev/null 2>&1
if diff <(grep -v '"generated_at"' "$TMP_A/report.json") \
        <(grep -v '"generated_at"' "$TMP_C/report.json") >/dev/null; then
    echo "[fail] 换种子后报告未变化——种子未生效" >&2
    exit 3
else
    echo "[ok] 换种子（111）后报告变化：种子确实起作用"
fi

echo "报告：$OUT/report.json"
