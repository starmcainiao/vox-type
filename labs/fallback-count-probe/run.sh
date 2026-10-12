#!/bin/sh
# T89 · 复现脚本：--allow-fallback 时 tts_calls=1 而 fallback_count=0 的口径取证
#
# 纪律（见 labs/fallback-count-probe/README.md）：
#   - raw/*.json = 命令 stdout 的**原样**落盘（`>` 重定向，不经任何加工）；
#   - rc 与 stderr 逐条落 raw/_rc-and-stderr.txt（原样，不加工）；
#   - 本脚本**只读**：不写任何产品代码、不写包目录，只写 labs/fallback-count-probe/raw/。
#
# 用法（仓库根）：
#   sh labs/fallback-count-probe/run.sh
#
# 前置：实验 a/d/c 需要本机 TTS 端点在线（缺省 http://127.0.0.1:10099）。

set -u

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
LAB="$REPO_ROOT/labs/fallback-count-probe"
RAW="$LAB/raw"
PLAN="$LAB/plan_unknown_key.json"
PACK="examples/prebuilt-pack"
OMLX="adapters.tts_omlx:OmlxTts"
WAV_DIR=$(mktemp -d /tmp/vox-t89-wav.XXXXXX)

mkdir -p "$RAW"

# 每条实验：stdout 原样进 raw/<name>.json，rc + stderr 原样追加进 raw/_rc-and-stderr.txt
run_case() {
    name="$1"; shift
    rc=0
    sh "$REPO_ROOT/bin/vox" run "$@" --json \
        > "$RAW/$name.json" \
        2> "$WAV_DIR/$name.stderr"
    rc=$?
    {
        printf '%s\n' "===== $name ====="
        printf 'cmd: sh bin/vox run'
        for a in "$@"; do printf ' %s' "$a"; done
        printf '%s\n' ' --json'
        printf '%s\n' "rc: $rc"
        printf '%s\n' '--- stderr ---'
        cat "$WAV_DIR/$name.stderr"
        printf '\n'
    } >> "$RAW/_rc-and-stderr.txt"
    printf '[%s] rc=%s stdout_bytes=%s\n' "$name" "$rc" "$(wc -c < "$RAW/$name.json" | tr -d ' ')"
}

: > "$RAW/_rc-and-stderr.txt"

# a) 未知 key + --allow-fallback（与 T84 记录的疑点同一条命令）
run_case "a-unknown-key-allow-fallback" \
    "$PLAN" --pack "$PACK" --adapter "$OMLX" --allow-fallback \
    --out "$WAV_DIR/a.wav"

# b) 未知 key + 不带 --allow-fallback → 期望 rc=5（fail-closed）
run_case "b-unknown-key-fail-closed" \
    "$PLAN" --pack "$PACK" --adapter "$OMLX" \
    --out "$WAV_DIR/b.wav"

# c) 引擎不一致（省略 --adapter → 缺省 adapters.tts_macsay:MacSayTts）
#    + --allow-fallback：用来区分「按 key 未命中」与「按引擎降级」两种语义
run_case "c-engine-mismatch-allow-fallback" \
    "$PLAN" --pack "$PACK" --allow-fallback \
    --out "$WAV_DIR/c.wav"

# d) 复跑 a)，确认结果稳定（不是偶发）
run_case "d-unknown-key-allow-fallback-rerun" \
    "$PLAN" --pack "$PACK" --adapter "$OMLX" --allow-fallback \
    --out "$WAV_DIR/d.wav"

printf '\nraw/ 目录：\n'
ls -l "$RAW"
printf '\nWAV 目录（不入仓）：%s\n' "$WAV_DIR"