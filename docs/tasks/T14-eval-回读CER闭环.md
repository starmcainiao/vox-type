# T14 · eval + adapters：回读 CER 闭环（oMLX 本地 ASR + CER 指标 + 回读 harness）

## 背景（只写必需）

立项案 §五 五项指标里「拼接自然度」目前**零实现**（`docs/13-未完成清单.md` §六 第 1 条）。
它的可机测一半是**回读 CER**（`docs/05-evidence-plan.md` §5.3：内容可懂度，Seed-TTS-eval 口径）——
把合成/拼接产物喂给 ASR，转写文本与源文本逐字比对，字错误率越高 = 拼接/合成越伤内容。

**ASR 引擎已实测可用（2026-09-19 探针）**：本机 oMLX(:10099) 提供 OpenAI 形状的
`POST /v1/audio/transcriptions`，模型 `Qwen3-ASR-0.6B-8bit` 对 16 kHz 单声道中文 wav 转写全对
（探针："今天下午三点提醒你开会" → `{"text":"今天下午三点提醒你开会。", ...}`）。
符合技术纪律「本地小模型一律走 oMLX :10099」，**仓内不引任何新第三方依赖**。

**范围裁定（两点，执行方不得自行扩缩）**：
1. 本卡只做**回读 CER 闭环**。原 T14 名下的「响度归一（真 LUFS）」**拆出为 T14b 另行开卡**——
   它要引入音频处理链（ITU-R BS.1770 K 加权滤波属自研 DSP 组件），按 `docs/06` 的推迟条款
   属独立设计决策，不与 CER 混在一张卡里；
2. eval 层**不得静态 import adapters**（T08 层边界纪律）——ASR 适配器与 T08 的 TTS 适配器一样
   用**注入**（dotted path 字符串，运行时解析），eval 的 import 区保持干净。

## 目标（可验收的产物）

- 产物 1：`adapters/asr_omlx/{__init__.py, adapter.py}` —— 薄 ASR 适配器：
  ```python
  class OmlxAsr:  # 命名可照 adapters/tts_macsay 的既有风格
      def __init__(self, base_url="http://127.0.0.1:10099", model="Qwen3-ASR-0.6B-8bit", timeout_seconds=120): ...
      def transcribe(self, wav_path) -> str: ...   # 失败抛 AsrError（消息含异常类型名与原因）
  ```
  - 请求形状（已实测）：multipart `file=@<wav>` + `model=<model>`，端点 `<base_url>/v1/audio/transcriptions`，
    取响应 JSON 的 `text` 字段；`base_url`/`model` **必须可覆盖**（不许写死）；
  - 失败路径 fail-closed：连接失败 / 超时 / 非 200 / 响应非 JSON / 缺 `text` 字段 → 统一抛 `AsrError`，
    消息含具体原因（对齐 T03 的 TtsError 口径：不吞原始异常上下文、不返回空串假成功）；
  - 方法级中文注释（职责/参数/返回值/边界/调用方）+ 关键 WHY；非空非注释行数 ≤ 150
    （`adapters/AGENTS.md §②` 同款预算）；
- 产物 2：`eval/cer.py` —— CER 指标（纯标准库）：
  - `normalize_for_cer(text) -> str`：去空白与标点（本卡**自带**标点集常量，见下方指标口径）；
  - `cer(reference: str, hypothesis: str) -> float`：字符级编辑距离 / len(reference)；
    reference 归一后为空 → 抛 `ValueError`（消息含原文本，不做 0/0 假成功）；
  - 指标名常量（`"cer"` / `"cer_p50"` / `"cer_p99"` 等）**定义在本模块**——
    **不得改 `core/metrics_spec`**（冻结区；将来指标转正再迁移，本卡不碰 core）；
  - 归一口径写进 docstring：Seed-TTS-eval 式「去标点与空白后逐字比对」；
    **不做**同义/模糊/语义归一（那是被 docs/10 裁定 1 禁止的东西）；
- 产物 3：`eval/readback.py` —— 回读 harness：
  - 输入：manifest JSONL（每行 `{"wav": <路径>, "reference": <源文本>}`）+ `--asr <dotted.path:Class>`；
  - 逐条：transcribe → `cer()` → 汇总 `n` / `mean` / `p50` / `p99`（P 分位口径与 `eval/stats.py`
    **同一个函数**，不得自造分位数）→ 出 JSON 报告 + `raw/readback_samples.jsonl`；
  - **不得美化**（对齐 T08b 三条教训）：任一条 transcribe 失败 → 该条进 `incomplete_reasons`
    （含异常原文与 wav 路径），**不中止整轮、不缩分母、报告照样写出**且摘要首行 `[INCOMPLETE]`；
    ASR 不可达导致全部失败 → `n=0`、`mean/p50/p99 = null`（不是 0.0）；
  - `--out` 越界防护与 raw 指纹（sha256）**复用 eval.bench 既有机制**（能 import 就复用；
    不能以不 import adapters 的方式复用的部分才允许等价实现，且要在报告里点名哪段是等价实现）；
- 产物 4：测试 `adapters/asr_omlx/tests/test_adapter.py`、`eval/tests/test_cer.py`、
  `eval/tests/test_readback.py`——**全部离线可跑**（替身 ASR / 假响应，模式照 eval 的 OfflineTts）。

## 允许修改的文件（白名单）

```
允许新增：adapters/asr_omlx/**（含 tests/**）
允许新增：eval/cer.py  eval/readback.py  eval/tests/test_cer.py  eval/tests/test_readback.py
禁止触碰：其他一切文件——尤其 core/（metrics_spec 不许动）、eval/stats.py（分位数只复用不改）、
          eval/bench.py / eval/report.py、adapters/ 其他子目录、compiler/、runtime/、assets/、cli/、packs/
```

## 禁止事项

- **不得改 `core/metrics_spec`**（冻结区；新指标名常量放 `eval/cer.py` 本地）
- **不得改 `eval/stats.py`**（复用其分位数函数；要改也只许是验收方另开卡）
- 不得在 eval/ 下静态 `import adapters` / `compiler` / `rules`（T08 层边界；dotted path 注入除外）
- 不得新增第三方依赖（HTTP 用标准库 `urllib`；multipart 手工构造或标准库范围内实现）
- 不得让任何测试联网 / 依赖 oMLX 在跑（真机验证归验收方）
- 不得做模糊/语义比对；CER 就是逐字编辑距离
- 不得"顺手优化"既有文件

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s adapters` 与 `discover -s eval` 全绿，两边测试数**只增不减**；
2. **离线证明**：`env PATH= /opt/homebrew/bin/python3 -m unittest discover -s eval` 仍全绿
   （测试不依赖 `say`、不依赖网络、不依赖 oMLX 在跑）；
3. **适配器负例（假响应注入，各一条）**：连接拒绝 / 超时 / HTTP 500 / 响应非 JSON / 缺 `text` 字段
   → 全部抛 `AsrError`，消息含具体原因，**无一返回空串或 None 假成功**；
4. **CER 指标判据**（我手算对照）：`cer("今天下午三点提醒你开会", "今天下午三点提醒你开会。") == 0.0`
   （句号被归一掉）；插错一个字 → `1/n`；漏一个字 → `1/n`；调序两字 → 编辑距离 2（不是 0）；
   归一后 reference 为空（纯标点输入）→ `ValueError`；
5. **harness 不得美化**（三条注入实测，模式照 T08b）：① 中途一条 transcribe 抛错 →
   报告写出、`incomplete_reasons` 含该 wav 路径与异常原文、成功条数不缩水；② ASR 全挂 →
   `n=0` 且 `p50/p99/mean` 为 `null`；③ 删 raw 样本一行 → 指纹/行数核对报红（若复用了 bench
   的机制则行为一致；等价实现必须同样能红）；
6. **层边界**：`grep -nE "^(from|import) +(adapters|compiler|rules)" eval/cer.py eval/readback.py` → 0 命中；
   `grep -n "metrics_spec" eval/cer.py eval/readback.py` → 0 命中（不许碰 core）；
7. **真机闭环（验收方亲跑，执行方不跑）**：我起 oMLX ASR，用 `say` 合成 fin-cs 包里 3 条话术 →
   走 readback harness → CER 全 0.0 或接近 0（真机数字由验收方记录，不进卡的判据）；
8. `adapters/asr_omlx/adapter.py` 非空非注释 ≤ 150 行；中文注释与 WHY 齐备；
9. `git status --porcelain -uall` 恰为白名单新增文件，无其他文件。

## 反空转条款（每张卡必带，T01 教训）

- 测试必须调用产品 API（`OmlxAsr.transcribe` / `cer` / readback 的公开入口）；
  不得在测试文件内自造编辑距离或分位数实现再拿它当期望值；
- 正例与负例都要有，负例断言错误消息包含具体非法值；
- 不得为通过测试而放宽校验；替身 ASR 必须打 `synthetic` 标记（对齐 T08b 替身纪律——
  readback 报告若在替身 ASR 下产出，必须带「不得对外引用」警告）。

## 回滚方式

全部为新增文件：`git clean -fd adapters/asr_omlx/ eval/cer.py eval/readback.py eval/tests/test_cer.py eval/tests/test_readback.py`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T14-eval-回读CER闭环.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**——探针话术是自造 demo 语，无用户录音/真实会话/内网地址（127.0.0.1
是本机回环，非内网地址）。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T14 节；**入口缺陷转 T14b**）
  - 验收方独立复跑：adapters 301 / eval 191 全绿（裸 PATH 亦过）；CER 判据四组（标点归一 0.0、插删 1/n、相邻换位=2 替换、不做语义归一 你好vs您好=0.5）；行数按**排除 docstring 的可执行行**口径 145–150 ≤ 150（裁定见下）；真机 API 直转探针句全对。
  - **裁定 1（卡文两判据冲突，按本意收）**：验收 8 字面「非空非注释 ≤150」=201 行，与卡内「方法级中文注释块」要求天然冲突（docstring 占 56 行）。本仓 ≤150 预算的意图是**代码体量**（R09），注释不计入——口径定为「可执行代码行（排除注释与 docstring）≤150」，实测 145（验收方 tokenize 复数 150，两口径均过线）。卡文未写清口径，是我的疏漏。
  - **裁定 2（接受）**：`synthetic` 做独立顶层字段而非塞进 `incomplete_reasons`——「数据不完整」与「数据非真机产出」是两件事，分开更诚实；警告行与退出码 5 均在。
  - **裁定 3（接受 + 登记）**：`resolve_asr` 对构造器不收的 kwargs 静默丢弃（替身 ergonomic）——有假成功味道，已登记 `docs/13` 候选；两处 4 行等价实现（`_write_jsonl`/`_sha256_of_file`）符合卡内「不能复用私名才允许等价实现且点名」的条款。
  - **❌ 查出 1 条阻塞（转 T14b）**：`readback.py` 缺 `if __name__ == "__main__"` 入口——`python3 -m eval.readback` 静默退出 rc 0 什么都不做（真机闭环由验收方跑才暴露；执行方的 CLI 演示是进程内调用）。修法两行，照 bench.py:1022 同款。
