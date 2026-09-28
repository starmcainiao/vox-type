---
name: Bug 报告
about: 复现问题（请附完整命令与输出）
labels: bug
---

## 你走的哪条路径

> 本仓三条路径的环境假设**完全不同**，混报会浪费来回。先勾一项：

- [ ] **clone 本仓快照后自己跑**（`git clone` → `python3 tools/run_all_tests.py`）
- [ ] **`examples/prebuilt-pack`**（零引擎自证包，clone 完直接播，不需要 TTS 服务）
- [ ] **自建 pack**（`packs/<业务>/` 源码包 → `sh bin/vox pack build`）

**具体是什么**（路径 / 版本 / 命令，能贴就贴）：

## 环境

> `cli` 根走 macOS 自带的 `say`，**没有 `say` 时 `cli` 根会红，属已知平台限制，不是回归**。
> 详见 `CONTRIBUTING.md` 的「平台前置条件」。

- OS（`uname -s`）：
- 架构（`uname -m`）：
- Python（`python3 --version`）：
- `command -v say`：
- `command -v ffmpeg`：
- `VOX_TTS_ENDPOINT` 可达 / 不可达 / 没测：

## 命令（完整，不要截断）

```sh
```

## 期望

## 实际（含完整输出与退出码）

```
```

## 最小复现

> 越小越好。只留触发问题的最少参数与最少单元。
> 本仓是**公开仓**：复现样例里不要出现用户录音、真实会话、内部地址、token / 密钥。
