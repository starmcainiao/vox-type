# piper-linux · Linux 容器里的本地模型 TTS 全链路验证（labs 实验区）

> 为什么有它：`adapters/tts_macsay` 离开 macOS 即死、`adapters/tts_omlx` 要服务在线——
> Linux/CI 上想跑真链路一直缺一个**零服务依赖**的引擎。本实验用 piper
> （rhasspy/piper，本地 ONNX、CPU 可跑）补齐这一格，并留下可复跑的容器验证。
> 定位：`labs/` 一次性实验区，**不进任何层的契约**；模型不进仓（放容器卷/仓外）。

## 一、结论（2026-10-10 实测）

在 `python:3.12-slim` + Ubuntu 24.04 容器内：

| 验证项 | 命令 | 结果 |
|---|---|---|
| 适配器自测 | `python3 -m unittest adapters.tts_piper.tests.test_adapter` | **17/17 OK**（含 3 档语速真合成） |
| `adapters` 全根 | `python3 -m unittest discover -s adapters` | **397 OK**（skipped=14：macsay/omlx 缺件，诚实 skip） |
| 端到端预铸 | `sh bin/vox pack build ... --adapter adapters.tts_piper:PiperTts` | **`total=3 synthesized=3 clean=True passed=True`** |
| 产物质检 | `sh bin/vox verify /tmp/pe/built` | **rc=0** |
| 零引擎播放 | `sh bin/vox run ... --adapter adapters.tts_piper:PiperTts` | **`hit=2 miss=0 fallback=0 tts_calls=0`** |
| 全量测试（对照） | `python3 tools/run_all_tests.py` | **11 根全过**（T82 后复跑，`failed=0`；此前 `cli` 根 22 条红系夹具依赖 macOS `say` 的耦合 `docs/13 §五#27`，已解耦） |

## 二、复现：构建容器与模型

```sh
# 1) 构建镜像（piper + ffmpeg；换国内源加速）
docker build -t vox-piper:local -f Dockerfile .

# 2) 模型下载（63MB，落容器卷；ZhiyuTTS zh_CN-huayan-medium / rhasspy piper-voices）
docker volume create vox-piper-models
docker run --rm -v vox-piper-models:/models vox-piper:local python3 - <<'PY'
import urllib.request, pathlib
base = "https://hf-mirror.com/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium"
for f in ("zh_CN-huayan-medium.onnx", "zh_CN-huayan-medium.onnx.json"):
    out = pathlib.Path("/models") / f
    if not out.exists():
        req = urllib.request.Request(f"{base}/{f}", headers={"User-Agent": "Mozilla/5.0"})
        out.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    print(f, out.stat().st_size)
PY
```

## 三、复现：全链路验证

```sh
export DOCKER_HOST="unix://$COLIMA_HOME/docker.sock"   # colima 环境；Docker Desktop 则免
docker run --rm \
  -v "$PWD":/repo -v vox-piper-models:/models \
  -e VOX_PIPER_MODEL=/models/zh_CN-huayan-medium.onnx \
  -w /repo vox-piper:local bash -c '
set -e
# 极小源包（voice/model_version 必须对齐 PiperTts 推导值，否则运行时引擎一致性闸拒绝）
rm -rf /tmp/pe && mkdir -p /tmp/pe/src
python3 - <<PY
import json, pathlib
d = pathlib.Path("/tmp/pe/src")
json.dump({"pack_id": "piper-demo", "pack_version": "1", "protocol_version": "0.1",
  "ruleset_version": "v1", "voice": "zh_CN-huayan-medium",
  "model_version": "piper-zh_CN-huayan-medium", "rates": ["normal"]},
  open(d/"pack.json","w"), ensure_ascii=False, indent=2)
json.dump({"phrases": [
  {"key": "greeting", "variants": ["您好，欢迎致电客户服务中心。", "您好，请问有什么可以帮您？"], "rates": ["normal"]},
  {"key": "closing", "variants": ["感谢您的来电，再见。"], "rates": ["normal"]}]},
  open(d/"phrases.json","w"), ensure_ascii=False, indent=2)
json.dump({"script_version": 1, "terminal_keys": ["closing"], "live_whitelist": [], "max_retry": 3,
  "units": [{"key": "greeting", "rate": "normal"}, {"key": "closing", "rate": "normal"}]},
  open(d/"script.json","w"), ensure_ascii=False, indent=2)
PY
sh bin/vox pack check /tmp/pe/src
sh bin/vox pack build /tmp/pe/src --out /tmp/pe/built --adapter adapters.tts_piper:PiperTts
sh bin/vox verify /tmp/pe/built
printf "[{\"key\":\"greeting\"},{\"key\":\"closing\"}]" > /tmp/pe/plan.json
sh bin/vox run /tmp/pe/plan.json --pack /tmp/pe/built \
  --adapter adapters.tts_piper:PiperTts --out /tmp/pe/out.wav
'
```

## 四、踩过的坑（都写进了代码/注释）

1. **归一顺序不可换**（`adapters/tts_piper/normalize.py` docstring）：piper 输出有数十毫秒
   渐入斜坡；若先裁静音后归一，边界样本按未缩放幅值判「非静音」、缩放后跌回阈值以下，
   自检以「头静音 159.8ms 超限」拒绝整批。正确顺序：**先峰值归一、再裁静音**。
2. **容器的 `/tmp` 每人一份**：不同 `docker run` 之间不共享——验证脚本要么一次跑完，
   要么把中间产物放挂载卷。
3. **两个镜像加速位置**：Docker daemon 的 `registry-mirrors` 要写进 **colima 配置**
   （`docker.registry-mirrors`），直接改 VM 里 `/etc/docker/daemon.json` 会在重启时被覆盖。
4. **Ubuntu 24.04 minimal 无 DNS**：in-VM `apt` 需要显式 `nameserver`（本实验用
   `223.5.5.5`）与 apt 直连配置，否则 `apt-get update` 挂死。

## 五、模型与许可

- 引擎：rhasspy/piper（MIT）。模型：`zh_CN-huayan-medium`（来源 rhasspy/piper-voices）。
- **模型文件不进仓**（63.2MB）：容器卷/仓外持有，仓库只含适配器代码与测试。
- 本目录是验证 harness（Dockerfile + 本文件），产物与模型均已排除分发。
