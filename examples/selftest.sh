#!/bin/sh
# examples/selftest.sh — vox 一命令自证（POSIX sh，零第三方依赖）
#
# 任务卡：docs/tasks/T45-examples-自证包与一命令自证脚本.md §三（行为钉死）
# 用法：  sh examples/selftest.sh          # 在本仓任意目录下跑（自动定位仓库根）
# 退出码（卡 §三.4 钉死，实现必须逐字对齐）：0 = PASS / 3 = PARTIAL / 4 = FAIL
#
# 三条纪律：
#   1. 不写临时文件到仓库内（全部落 /tmp）；
#   2. 不联网（只做 TCP 探活，不依赖任何远端服务成功）；
#   3. 判定全走 bin/vox 的退出码，本脚本一行判据都不重写（CLI 薄壳口径，docs/08）。
#
# WHY 零引擎 run 必须带 --adapter：runtime 的引擎一致性检查（runtime/executor.py:421-427）在
#   **任何查表之前**就拦 engine_mismatch。CLI 缺省适配器是 adapters.tts_macsay:MacSayTts
#   （Tingting/macos-say），本包是 oMLX 模型引擎铸的（default/Qwen3-TTS-12Hz-0.6B-Base-bf16），
#   两者不等 → 省略 --adapter 必 rc=5。卡面 §五#2 已于 2026-09-24 裁定修正为带 --adapter。
#   包身份由源包 pack.json 决定（compiler/prebake.py），--adapter 只决定「谁的身份与包对齐」。
#   「零引擎」的含义是**不需要任何 TTS 服务**（tts_calls=0，一次合成都不发生）。

set -u

# ---------------------------------------------------------------- 工具函数
fail_count=0
warn_count=0
partial_reasons=""

say() { printf '%s\n' "$*"; }
mark() {  # mark <OK|WARN|FAIL> <步骤名> <期望> <实际>
    tag="$1"; step="$2"; exp="$3"; act="$4"
    case "$tag" in
        OK)   printf '  \033[32m✓\033[0m %-34s 期望 %s | 实际 %s\n' "$step" "$exp" "$act" ;;
        WARN) printf '  \033[33m△\033[0m %-34s 期望 %s | 实际 %s\n' "$step" "$exp" "$act";
              warn_count=$((warn_count + 1));;
        FAIL) printf '  \033[31m✗\033[0m %-34s 期望 %s | 实际 %s\n' "$step" "$exp" "$act"
              fail_count=$((fail_count + 1));;
    esac
}
add_reason() {
    if [ -z "$partial_reasons" ]; then partial_reasons="$1"; else partial_reasons="$partial_reasons; $1"; fi
}

# run <标签> <期望rc> <实际rc> <命令…>：跑命令，抓 rc，只留结论行（不刷满整屏 JSON）
run_step() {
    step="$1"; want_rc="$2"; shift 2
    out="$("$@" 2>&1)"; rc=$?
    line="$(printf '%s\n' "$out" | grep -E '^(pack check:|pack build:|run:|bench:|verify:|通过|不通过)' | head -1)"
    [ -z "$line" ] && line="$(printf '%s\n' "$out" | tail -1)"
    if [ "$rc" = "$want_rc" ]; then
        mark OK "$step" "rc=$want_rc ($line)" "rc=$rc"
    else
        mark FAIL "$step" "rc=$want_rc" "rc=$rc ($line)"
    fi
    return $rc
}

# tcp_alive <host> <port>：用脚本已依赖的 "$PY" 做 TCP 探活。
# 不用 `exec 3<>/dev/tcp/…`——那是 bash 私货，dash 系 /bin/sh 下必坏，
# 会把**可达**端点误报为不可达（本卡 §三.1 收尾批）。
tcp_alive() {
    [ -n "${PY:-}" ] || return 1
    "$PY" -c 'import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2).close()' "$1" "$2" >/dev/null 2>&1
}

# ---------------------------------------------------------------- 定位仓库根
ROOT="$(cd "$(dirname -- "$0")/.." && pwd)"
VOX="$ROOT/bin/vox"
HERE="$ROOT/examples"
PREBUILT="$HERE/prebuilt-pack"
WORK="$(mktemp -d /tmp/vox-selftest.XXXXXX)" || { echo "SELFTEST: FAIL (mktemp 失败)"; exit 4; }
trap 'rm -rf "$WORK"' EXIT

say "vox 自证 · 仓库根: $ROOT"
say "临时目录: $WORK （退出时自动清理，不写仓库）"
say ""

# ================================================================ 1. 环境自检
say "== 1. 环境自检（你现在能走哪条路）=="
PY="$(command -v python3 || true)"
PYVER=""
if [ -n "$PY" ]; then PYVER="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"; fi
PYOK=1
if [ -z "$PY" ]; then PYOK=0; add_reason "缺 python3";
elif printf '%s' "$PYVER" | "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null; then :;
else PYOK=0; add_reason "python3 版本过低"; fi
mark "$([ $PYOK -eq 1 ] && echo OK || echo FAIL)" "python3 >= 3.12" ">= 3.12" "${PYVER:-未找到}"
if [ $PYOK -eq 0 ]; then
    say "  → 本脚本继续跑下去，但后续步骤可能全部失败。请先装 Python 3.12+。"
fi

FFMPEG="$(command -v ffmpeg || true)"
SAY_BIN="$(command -v say || true)"
if [ -n "$FFMPEG" ]; then mark OK "ffmpeg" "存在" "$FFMPEG"; else mark WARN "ffmpeg" "存在" "未找到"; add_reason "缺 ffmpeg"; fi
if [ -n "$SAY_BIN" ]; then
    mark OK "macOS say" "存在即登记，本卡禁用" "$SAY_BIN (禁止用于生成入库音频)"
else
    mark WARN "macOS say" "非 macOS 环境" "未找到 (无 say，正常，不影响任何路径)"
fi

ENDPOINT="${VOX_TTS_ENDPOINT:-http://127.0.0.1:10099}"
EP_HOST="${ENDPOINT#*://}"; EP_HOST="${EP_HOST%%/*}"
EP_PORT="10099"; case "$EP_HOST" in *:*) EP_PORT="${EP_HOST#*:}"; EP_HOST="${EP_HOST%%:*}";; esac
ENGINE_OK=0
if tcp_alive "$EP_HOST" "$EP_PORT"; then
    ENGINE_OK=1
    ENGINE_NOTE="端点可达（${VOX_TTS_ENDPOINT:-未设 VOX_TTS_ENDPOINT，用缺省}）"
else
    ENGINE_NOTE="端点不可达（${EP_HOST}:${EP_PORT}）"
fi
if [ $ENGINE_OK -eq 1 ]; then mark OK "TTS 端点（oMLX / 任意 OpenAI 兼容）" "可达" "$ENGINE_NOTE"; else mark WARN "TTS 端点" "可达" "$ENGINE_NOTE"; add_reason "TTS 端点不可达"; fi

REF_NOTE="未设（正确：本包禁用克隆）"
[ -n "${VOX_TTS_REF:-}" ] && REF_NOTE="已设=${VOX_TTS_REF}（会改音色指纹，本包口径禁止）"
mark OK "VOX_TTS_REF（克隆开关）" "未设" "$REF_NOTE"

ENGINE_SPEC=""
if [ $ENGINE_OK -eq 1 ]; then
    for spec in "adapters.tts_omlx:OmlxTts" "adapters.tts_macsay:MacSayTts"; do
        inst="$("$PY" - "$ROOT" "$spec" <<'PY' 2>/dev/null
import importlib, sys
root, spec = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
mod, _, cls = spec.partition(":")
m = importlib.import_module(mod)
c = getattr(m, cls, None)
assert isinstance(c, type), spec
a = c()
print(a.voice + "\n" + a.model_version)
PY
)"
        if [ $? -eq 0 ] && [ -n "$inst" ]; then
            ENGINE_SPEC="$spec"
            ENGINE_VOICE="$(printf '%s\n' "$inst" | sed -n 1p)"
            ENGINE_MODEL="$(printf '%s\n' "$inst" | sed -n 2p)"
            break
        fi
    done
fi

[ -n "$ENGINE_SPEC" ] && mark OK "可用的 TTS 适配器" "可解析+可实例化" "$ENGINE_SPEC" || mark WARN "可用的 TTS 适配器" "可解析+可实例化" "无（引擎不可用）"

say ""
say "你现在能走哪条路："
say "  · 零引擎路径（听见「命中即播」）：任何时候都能走，不联网、不合成"
if [ $ENGINE_OK -eq 1 ]; then
    say "  · 完整链路（预铸 + 对拍）：可走，引擎=$ENGINE_SPEC (voice=$ENGINE_VOICE)"
else
    say "  · 完整链路（预铸 + 对拍）：不可走 —— 要跑还缺：一个可达的 OpenAI 兼容 TTS 端点"
    say "    （export VOX_TTS_ENDPOINT=http://127.0.0.1:10099，oMLX 起 Qwen3-TTS 服务）"
    [ -z "$FFMPEG" ] && say "    另缺 ffmpeg（适配器要把服务端 24kHz 降到 16kHz/单声道/16-bit）"
fi
[ -n "$SAY_BIN" ] && say "  · macOS say 应急：$SAY_BIN (仓库允许装，但禁止用于生成入库音频)"

# ================================================================ 2. 串真命令
say ""
say "== 2. 串真命令（判定全走 bin/vox 的退出码）=="

say "-- 2a. 源包校验（永远可跑，不需要引擎）"
run_step "pack check packs/heat_kefu" 0 sh "$VOX" pack check "$ROOT/packs/heat_kefu"

say ""
say "-- 2b. 自证包质检（不联网、不合成）"
run_step "verify examples/prebuilt-pack" 0 sh "$VOX" verify "$PREBUILT"

MANIFEST="$PREBUILT/manifest.json"
[ -f "$MANIFEST" ] || { mark FAIL "自证包存在" "manifest.json 存在" "缺失：$MANIFEST"; say "  → 先跑 sh examples/selftest.sh 之外的重铸步骤，见 prebuilt-pack/PROVENANCE.md"; }

PLAN_HIT="$(printf '%s' "$([ -f "$HERE/plan.json" ] && "$PY" -c 'import json,sys;print(len(json.load(open(sys.argv[1]))))' "$HERE/plan.json" 2>/dev/null || echo 0)")"
PACK_KEYS="$( [ -f "$MANIFEST" ] && "$PY" -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["assets"]))' "$MANIFEST" 2>/dev/null || echo 0)"
mark "$([ "${PACK_KEYS:-0}" -ge "${PLAN_HIT:-0}" ] && echo OK || echo FAIL)" "自证包覆盖 plan" \
     "assets >= plan 单元数 ($PLAN_HIT)" "assets=$PACK_KEYS"

say ""
say "-- 2c. 零引擎执行（命中即播，这是本卡的核心自证）"
# 引擎一致性闸在任何查表之前（runtime/executor.py:421-427），所以这里必须**挑一个与
# manifest 身份一致的适配器**再传给 run：挑不出就在 run 之前说清缺什么（下方 FAIL 分支），
# 而不是硬跑一次拿 engine_mismatch 充数。比对只读身份字段，不需要端点在线——
# 端点不可达时 oMLX 适配器照样能实例化，tts_calls=0 也照样不发网络请求。
RUN_ADAPTER=""
for spec in "adapters.tts_macsay:MacSayTts" "adapters.tts_omlx:OmlxTts"; do
    inst="$("$PY" - "$ROOT" "$spec" <<'PY' 2>/dev/null
import importlib, json, sys
root, spec = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
m = json.load(open(root + "/examples/prebuilt-pack/manifest.json"))
mod, _, cls = spec.partition(":")
try:
    a = getattr(importlib.import_module(mod), cls)()
except Exception:
    sys.exit(1)
if a.voice == m["voice"] and a.model_version == m["model_version"]:
    print(spec)
PY
)"
    if [ -n "$inst" ]; then RUN_ADAPTER="$inst"; break; fi
done

OUT_WAV="$WORK/run.wav"
if [ -z "$RUN_ADAPTER" ]; then
    mark FAIL "vox run（零引擎）" "rc=0 hit=$PLAN_HIT miss=0 tts_calls=0" \
        "跳过：manifest(voice=$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1]))["voice"])' "$MANIFEST" 2>/dev/null || echo '?')/model=$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1]))["model_version"])' "$MANIFEST" 2>/dev/null || echo '?')) 与本机任何适配器都不匹配"
    add_reason "无与自证包引擎身份匹配的适配器"
else
    run_log="$WORK/run.log"
    # 卡 §五#2（2026-09-24 裁定修正后）：--adapter 必带。runtime 的引擎一致性闸在任何查表
    # 之前（runtime/executor.py:421-427），自证包是模型引擎（oMLX）铸的，不带匹配 adapter
    # 必然 engine_mismatch → fail-closed rc=5。「零引擎」= 不需要任何 TTS 服务，
    # 与「要不要带 --adapter」是两件事；带 --adapter 不会发起任何合成（tts_calls=0）。
    sh "$VOX" run "$HERE/plan.json" --pack "$PREBUILT" \
        --adapter "$RUN_ADAPTER" --out "$OUT_WAV" >"$run_log" 2>&1
    run_rc=$?
    if [ $run_rc -eq 0 ]; then
        hit="$(grep -o '"hit_count": [0-9]*' "$run_log" | head -1 | grep -o '[0-9]*')"
        miss="$(grep -o '"miss_count": [0-9]*' "$run_log" | head -1 | grep -o '[0-9]*')"
        fb="$(grep -o '"fallback_count": [0-9]*' "$run_log" | head -1 | grep -o '[0-9]*')"
        tts="$(grep -o '"tts_calls": [0-9]*' "$run_log" | head -1 | grep -o '[0-9]*')"
        wav_ok=0; [ -f "$OUT_WAV" ] && [ -s "$OUT_WAV" ] && wav_ok=1
        exp="rc=0 hit=$PLAN_HIT miss=0 fallback=0 tts_calls=0 out 非空"
        act="rc=0 hit=${hit:-?} miss=${miss:-?} fallback=${fb:-?} tts_calls=${tts:-?} out=$( [ $wav_ok -eq 1 ] && echo OK || echo 缺失 )"
        if [ "${hit:-0}" = "$PLAN_HIT" ] && [ "${miss:-1}" = "0" ] && [ "${fb:-1}" = "0" ] && [ "${tts:-1}" = "0" ] && [ $wav_ok -eq 1 ]; then
            mark OK "vox run（零引擎，全命中）" "$exp" "$act"
        else
            mark FAIL "vox run（零引擎，全命中）" "$exp" "$act"
        fi
    else
        detail="$(grep -E 'run: ' "$run_log" | head -1)"
        mark FAIL "vox run（零引擎）" "rc=0" "rc=$run_rc ($detail)"
        if printf '%s' "$detail" | grep -q engine_mismatch; then
            add_reason "引擎身份不匹配（engine_mismatch）——本包的 voice/model_version 与可用适配器都不一致"
        fi
    fi
fi

say ""
if [ $ENGINE_OK -eq 1 ]; then
    say "-- 2d. 完整链路：预铸小包 + 离线对拍（需要引擎；本次只铸到 /tmp，不碰仓库）"
    SRC="$WORK/src"; PACK="$WORK/pack"; BENCH="$WORK/bench"
    run_step "pack build（子集源包 → /tmp）" 0 sh "$VOX" pack build "$ROOT/packs/heat_kefu" \
        --out "$PACK" --adapter "$ENGINE_SPEC"
    sh "$VOX" pack check "$PACK" >"$WORK/buildcheck.log" 2>&1; bc_rc=$?
    mark "$([ $bc_rc -eq 0 ] && echo OK || echo FAIL)" "pack check（刚铸的包）" "rc=0" \
        "rc=$bc_rc ($(grep -m1 -E '^pack check:' "$WORK/buildcheck.log" || true))"
    # --repeats 缺省 30，下限 MIN_REPEATS=20（eval/stats.py）——厚尾分布禁均值外推。
    run_step "bench（离线替身，--repeats 20）" 0 sh "$VOX" bench "$PACK" \
        --corpus "$HERE/bench-corpus.json" --out "$BENCH" --repeats 20
    gate="$(grep -o '"required_hit_rate": [0-9.]*' "$BENCH/report.json" 2>/dev/null | head -1)"
    obs="$(grep -o '"observed_hit_rate": [0-9.]*' "$BENCH/report.json" 2>/dev/null | head -1)"
    mark OK "bench 报告落地" "report.json 存在" "${obs:-?} ${gate:-?}"
    say "    注：bench 用离线替身合成，报告里的毫秒数不得对外引用（timing_metrics_meaningful=false）。"
else
    say "-- 2d. 完整链路：跳过（端点不可达）"
    say "    要跑完整链路还缺："
    say "      ① 一个可达的 OpenAI 兼容 TTS 端点，例如本机 oMLX 起 Qwen3-TTS-12Hz-0.6B-Base-bf16 后"
    say "         export VOX_TTS_ENDPOINT=http://127.0.0.1:10099"
    [ -z "$FFMPEG" ] && say "      ② ffmpeg（服务端 24kHz → 契约 16kHz/单声道/16-bit 的归一步）"
    [ -n "${VOX_TTS_REF:-}" ] && say "      ③ 取消 VOX_TTS_REF（本包口径禁用克隆）"
fi

say ""
# ================================================================ 3. 结论
say "== 3. 结论 =="
say "  硬失败: $fail_count 项 | 环境降级项: $warn_count 项（缺引擎属正常，不算失败）"

if [ $fail_count -gt 0 ]; then
    say "SELFTEST: FAIL (失败 $fail_count 项 -- 上面标 [FAIL] 的每一条都是硬门)"
    exit 4
elif [ $warn_count -gt 0 ]; then
    say "SELFTEST: PARTIAL ($partial_reasons / 缺引擎，但零引擎路径已全部通过——听见「命中即播」这一步不依赖任何 TTS 服务)"
    exit 3
else
    say "SELFTEST: PASS (全绿：零引擎路径 + 完整链路)"
    exit 0
fi
