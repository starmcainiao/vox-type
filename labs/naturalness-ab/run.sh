#!/bin/sh
# 拼接自然度盲测 · 一键顺序：生成 → 盲化 →【你听测填表】→ 报告
#
# 用法：sh labs/naturalness-ab/run.sh            # 音频落 ~/vox-naturalness
#       sh labs/naturalness-ab/run.sh --out-root /tmp/vox-naturalness
set -e
cd "$(dirname "$0")/../.."

python3 labs/naturalness-ab/gen_samples.py "$@"
python3 labs/naturalness-ab/blind.py "$@"

echo
echo "下一步（人工）："
echo "  1. 打开 ~/vox-naturalness/listen/ 逐条听（每个条目 3 段，顺序已随机、码不含臂信息）"
echo "  2. 填 labs/naturalness-ab/sheet_clips.csv（每段 MOS 1-5 + 接缝三档）"
echo "  3. 填 labs/naturalness-ab/sheet_items.csv（每条哪段最自然）"
echo "  4. 跑 python3 labs/naturalness-ab/report.py 出报告"
