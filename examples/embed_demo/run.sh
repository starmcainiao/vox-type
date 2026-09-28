#!/bin/sh
# run.sh — 从任意 cwd 跑 embed demo（POSIX sh，零第三方依赖）
#
# 定位仓库根 → 塞进 PYTHONPATH → exec python3 demo.py
# 与 bin/vox 同一范式：仓库根由本脚本自身位置推导，不依赖 cwd、不依赖安装。
#
# 用法：
#   sh examples/embed_demo/run.sh
# 退出码：0 成功 / 2 用法或参数错误（仓库根解析失败、缺 manifest.json）

set -eu

SELF="$0"
while [ -h "$SELF" ]; do
    TARGET="$(readlink "$SELF")"
    case "$TARGET" in
        /*) SELF="$TARGET" ;;
        *) SELF="$(cd "$(dirname "$SELF")" && pwd)/$TARGET" ;;
    esac
done

# examples/embed_demo/run.sh → 往上两层是仓库根
VOX_ROOT="$(cd "$(dirname "$SELF")/../.." && pwd)"

if [ ! -d "$VOX_ROOT/core" ] || [ ! -d "$VOX_ROOT/runtime" ]; then
    echo "run.sh: $VOX_ROOT 不是 vox-type 仓库根（缺 core/ 或 runtime/）" >&2
    exit 2
fi

if [ -n "${PYTHONPATH:-}" ]; then
    PYTHONPATH="${VOX_ROOT}:${PYTHONPATH}"
else
    PYTHONPATH="${VOX_ROOT}"
fi
export PYTHONPATH

exec python3 "$(dirname "$SELF")/demo.py"
