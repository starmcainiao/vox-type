# T11 · adapters：framework-kefu 接入适配器（把预铸包插进真链路）

## 数据分级（派发前置检查项）

**分级：公开。** 本卡只有接口契约、归一化口径与字段名，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。
**注意一条额外纪律**：kefu-agent 的业务文案（提示语/话术）**不得写进本仓**——本卡所有测试文本一律自造；真链路联调时的话术由运行时抓取并只落 `/tmp`。可派发。

## 背景

八层机制已齐（671 条测试）、`packs/repair` 示例包可用、`bin/vox` 五个命令可跑，`docs/09` 还给出了与真链路的对照数字。**但这个包至今没被任何真实链路用过**——所有 plan 都是手写的。

本卡做**接入那一步**：一个薄适配器，让"用户音频 → ASR → brain → **本层（命中就零合成）** → 音频"这条链真的跑起来，并产出**两档命中率**（上游按 key 说话 / 上游给自由文本）——后者正是 T13（预铸准入）的输入。

**必读**（不得修改）：`docs/10-接入取景与命中口径.md`（**本卡规格，逐条照做**）、`runtime/AGENTS.md §②`（一个接缝 + 一个旁路开关）、`core/metrics_spec.py`（事件字段名）、`docs/09` §二/§十（kefu 侧接口与实测口径）。

依赖（只读其公开 API）：`runtime`（`Executor` / `ExecutionResult` / `RuntimeMissError` / `DuplexParams` / `REASON_*`）、`assets`（`load_pack` / `AssetPack`）、`core.metrics_spec`、`adapters/tts_macsay`（慢路合成器之一）。

## 目标（产物）

```
adapters/framework_kefu/__init__.py        导出 KefuClient / KefuBridge / normalize_text / BridgeResult
adapters/framework_kefu/normalize.py       docs/10 §10.3 的四步归一化（确定性、可单测）
adapters/framework_kefu/kefu_client.py     kefu 侧薄客户端（两个接口，见下）
adapters/framework_kefu/bridge.py          KefuBridge：一轮 = ASR → brain → 命中判定 → 播放/慢路 + 事件
adapters/framework_kefu/tests/__init__.py
adapters/framework_kefu/tests/test_normalize.py
adapters/framework_kefu/tests/test_bridge.py
adapters/framework_kefu/tests/test_failclosed.py
labs/kefu-bridge/run_session.py 真链路联调驱动（抓话术、跑两档、出报告；产物只写 /tmp 与 labs 的 jsonl）
```

### 1. `normalize.py`（**不许加步**，docs/10 §10.3）

```python
def normalize_text(text: str) -> str
    # ① 去首尾与内部所有空白（含全角空格 U+3000）② 全角↔半角（ASCII 可见区 + ，。？！：；（））
    # ③ Unicode NFKC ④ 小写
```
纯函数、无副作用、**禁止**同义改写/去语气词/去标点（那是语义，越界）。

### 2. `kefu_client.py`（**只走 kefu 已有公开接口，不改 kefu 任何文件**）

```python
class KefuClient:
    def __init__(self, *, brain_url="http://127.0.0.1:8092", voice_url="http://127.0.0.1:8096", timeout_s=180.0)
    def ask_brain(self, session_id: str, text: str) -> str        # POST {brain}/chat/turn → reply 文本
    def transcribe(self, wav_bytes: bytes) -> str                 # POST {voice}/api/voice/turn 不适用（会直接播 TTS）
                                                                  # → 用 voice_worker 的 stdio 行协议 op=stt（见 docs/09 §八）
    def synthesize_live(self, text: str) -> tuple[bytes, str]     # 慢路合成器：worker op=tts（say 档）
```
- `transcribe` / `synthesize_live` 走 **voice worker 行协议**（`{"op":"stt","wavB64":…}` / `{"op":"tts","text":…}`，一行请求一行响应，响应带 `elapsedMs`）；worker 由本类按需拉起（`.venv-voice/bin/python voice_worker.py`，路径可用构造参数覆盖），**不得**修改 kefu 的脚本或源码。
- 网络/协议失败一律**抛异常并带类型名**，不得静默返回空串。

### 3. `bridge.py`（本层真正的接缝）

```python
@dataclass(frozen=True)
class BridgeResult:
    state: str                  # "hit" | "miss"
    match_mode: str             # "key" | "text" | ""（miss 时为空）
    audio_path: Path            # 本轮产出的音频
    tts_calls: int              # 本轮真调用慢路合成器的次数（命中必须为 0）
    live_text: str              # 本轮"要说什么"（上游给的自由文本或 key 对应文本）
    first_audio_ms: float
    events: list[dict]          # 事件字段名一律引 core.metrics_spec

class KefuBridge:
    def __init__(self, pack, *, live_tts, allow_fallback: bool = False, duplex: DuplexParams | None = None)
    def run_turn(self, *, session_id: str, wav_bytes: bytes | None = None,
                 text: str | None = None, out_path: Path) -> BridgeResult
```

一轮的顺序（**严格照此**）：
1. `wav_bytes` 给了就 `transcribe` 成文本（否则用 `text`）；
2. `ask_brain(session_id, text)` 拿到"这一轮要说什么"（**自由文本档**）；若调用方直接给 `key=`（**脚本驱动档**）则跳过 brain；
3. **命中判定**（只允许两种，docs/10 §10.2）：
   - key 档：`pack.lookup(key, …)`；
   - 文本档：`normalize_text(回复文本)` 与包内**该 key 各 variant 的归一化文本**逐字相等 → 命中，否则未命中；
   - **禁止**语义/模糊匹配；
4. 命中 → 用 `runtime.Executor` 播包（`allow_fallback=False` 亦可，因为已确认命中）；**未命中 + `allow_fallback=False` → 抛 `RuntimeMissError`（fail-closed，不落盘、不播替代）**；未命中 + `allow_fallback=True` → 调 `live_tts` 合成并出 `miss` 事件（`reason` 区分 `text_not_prebaked` / `key_not_prebaked`）；
5. 引擎不一致（包音色 ≠ 慢路合成器音色）→ `fallback` / `engine_mismatch`，**不播包内音频**。

### 4. `labs/kefu-bridge/run_session.py`（联调驱动，产物只写 `/tmp` 与 `labs/.../raw/`）

```
python3 labs/kefu-bridge/run_session.py --pack <已铸包> --turns <json> --out <dir> [--allow-fallback]
```
- 输入 `--turns`：`[{"user_text": "…"} | {"key": "…"}]` 的 JSON 列表（**我验收时把真链路抓到的固定话术喂进来**）；
- 对每轮：`say` 造用户音频 → `run_turn` → 记录 `{state, match_mode, tts_calls, first_audio_ms, live_text}`；
- 输出 JSONL 到 `--out`，并打印一张汇总（两档各跑一遍：`--mode key` / `--mode text`）；
- **不得**把 kefu 的业务文案写进本仓文件；抓取到的文案只写 `/tmp` 或 `--out`（`labs/.../raw/` 下只放**自造文本**的样本，含真文案的样本写 `/tmp`）。

## 允许修改的文件（白名单）

```
允许新增：adapters/framework_kefu/__init__.py, adapters/framework_kefu/normalize.py,
          adapters/framework_kefu/kefu_client.py, adapters/framework_kefu/bridge.py,
          adapters/framework_kefu/tests/__init__.py, adapters/framework_kefu/tests/test_normalize.py,
          adapters/framework_kefu/tests/test_bridge.py, adapters/framework_kefu/tests/test_failclosed.py,
          labs/kefu-bridge/run_session.py
允许修改：无
禁止触碰：其余一切文件（含 core/**、rules/**、assets/**、compiler/**、runtime/**、eval/**、cli/**、
          packs/**、adapters/tts_macsay/**、docs/**、各层 AGENTS.md、根 AGENTS.md、README.md）；
          **尤其禁止**：修改 `<kefu-agent 仓根>` 下的任何文件（那是另一个仓库）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库 + 本仓已有模块）。
- **不得实现语义/模糊匹配**（embedding、同义词、去语气词、编辑距离阈值都不许）；命中只有 key 直查与归一化逐字相等两种。
- 不得自造事件字段名（一律引 `core.metrics_spec`；需要追加只能加、不得改名）。
- 不得静默降级：未命中且未开降级必须抛错；开了降级必须出 `miss` 事件且 `reason` 可区分。
- 不得把 kefu 的业务文案、真实会话、录音写进本仓任何文件。
- 不得改 kefu-agent 的代码或脚本（联调只经它已有的公开接口）。
- 不得"顺手优化"、不得改与本卡无关的格式、不得改卡。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s adapters -v` **全绿**（T03/T03d 的 36 条**只增不减**）；
2. **归一化可测**：四步各有正反例（含全角空格、全角标点、NFKC 兼容字符、大小写）；同输入同输出；**且证明"多做了一步就不行"**——至少一条用例断言"去标点后比较"**不会**被判命中（防止有人偷偷放宽）；
3. **命中零调用**（离线、用假慢路合成器计数）：key 档命中 → `tts_calls == 0`、事件含 `hit`、`match_mode == "key"`；文本档逐字相等 → 命中且 `match_mode == "text"`；**改动一个字 → 未命中**；
4. **fail-closed 有牙**：未命中 + 默认 → 抛 `RuntimeMissError`，**输出文件不存在**、**假慢路合成器一次都没被调用**；
5. **显式降级留痕**：`allow_fallback=True` → 走假慢路合成器（计数 +1）、事件 `miss` 且 `reason == "text_not_prebaked"`（文本档）/ `"key_not_prebaked"`（key 档）；
6. **引擎不一致**：把包的 `voice` 与慢路合成器的 `voice` 改成不同值 → `fallback`/`engine_mismatch`，且**没有播包内音频**（用峰值或时长区分）；
7. **真链路联调（我手跑，kefu 用云端 brain 起着）**：
   - `run_session.py --mode key`（用我从真链路抓到的固定话术建的包）→ 命中率与我给的 `--turns` 一致、命中轮 `tts_calls == 0`；
   - `run_session.py --mode text`（真实 LLM 回复原文）→ 打印命中率与未命中原因分布；
   - 两档的 `first_audio_ms`（命中 vs 走慢路）并列；
   - 全过程 **kefu 仓库 `git status` 零改动**（我会核对）；
8. **本仓无 kefu 文案**：`grep` 我给的几个 kefu 固定句片段 → 只允许出现在 `/tmp` 产物里，**不得**出现在本仓任何被跟踪文件；
9. 含方法级中文注释与关键步骤 WHY 注释（尤其：为何只允许两种匹配、为何 fail-closed、为何归一化不许加步）；
10. `git status --porcelain -uall` 仅白名单文件；`core/`、`runtime/`、`assets/`、`packs/`、`cli/`、`eval/` 零 diff。

## 反空转条款（必带）

- 测试必须调用**产品 API**（`normalize_text` / `KefuBridge.run_turn` / `runtime.Executor`），不得在测试里复制命中判定或归一化逻辑；
- 慢路合成器在测试里必须是**可计数的假对象**（不许真调 `say`/网络）；真链路只在验收第 7 条出现；
- 负例必须断言**异常类型 + 消息含关键值**（key 名 / 原因码 / 文本片段）；
- 断言必须**能失败**：命中那几条要给出"改成差一个字就变红"的实证（我会独立重做一遍）；
- 不得 `assertTrue(True)`、不得 `except: pass`、不得用 `skipTest` 绕过本卡任何验收项。

## 回滚方式

```
cd （仓库根）
rm -rf adapters/framework_kefu labs/kefu-bridge
```

（本卡只新增文件，未跟踪 → `git clean` 等价可用。）

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（商汤 `sensenova-6.8-flash-lite`；`thoughtLevel` 必须是 `enabled`；**改过 agent 定义后需新开会话**）。
若它不可用，走回落：

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T11-adapters-接入真链路.md)" --dir （仓库根）
```

这是**非交互环境**：请直接落地代码，不要先写计划再等确认，不要往仓库里写计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要新建或修改任何文件。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
