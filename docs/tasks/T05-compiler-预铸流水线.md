# T05 · compiler：预铸流水线（源格式 + 质检门 + 预铸账 + 差量重铸）

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有格式规格、阈值与接口约定，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 背景

`compiler/` 把「话术」编译成「资产包」，并在编译期拦住一切不合规。本卡做**第一段**：话术 → 逐句合成（多语速档 × 多变体）→ 质检 → 打包 + 预铸账 + 差量重铸。
（四属性校验器不在本卡，属 **T06**；本卡**不读剧本**。）

**必读**（不得修改）：`AGENTS.md`、`compiler/AGENTS.md`、`docs/06-预铸与执行-补定口径.md`（**阈值与格式的口径都在这里，照做，不要自己改数**）。

依赖（只读其公开 API，不得修改）：`core/protocol.py`（`VALID_RATES`）、`assets/`（`fingerprint` / `load_pack` / `AssetPackError` / 包格式）、`adapters/`（`MacSayTts` 的 `synthesize` / `rate_map` / `voice` / `model_version`）。

## 目标（产物）

```
compiler/__init__.py                  导出 load_source / PackSource / SourceError /
                                      measure / AudioStats / check_quality / QualityError /
                                      prebake / PrebakeReport / PrebakeError
compiler/source.py                    源格式装载与校验
compiler/quality.py                   质检门（纯函数，不依赖 TTS）
compiler/prebake.py                   流水线：枚举 → 合成 → 质检 → 差量复用 → 写包 + 预铸账
compiler/tests/__init__.py
compiler/tests/test_source.py
compiler/tests/test_quality.py
compiler/tests/test_prebake.py
```

### 1. 源格式（`compiler/source.py`）

`load_source(root: Path) -> PackSource`。源在 `packs/<业务>/` 下，两个文件：

**`pack.json`**（必填字段，缺任一 → `SourceError`，消息含字段名）
```json
{"pack_id": "repair", "pack_version": "1", "protocol_version": "0.1",
 "ruleset_version": "v1", "voice": "Tingting", "model_version": "macos-say",
 "rates": ["normal", "slow"]}
```

**`phrases.json`**
```json
{"phrases": [{"key": "greeting", "variants": ["您好，请问需要什么帮助"], "rates": ["slow"]}]}
```

规则（违反 → `SourceError`，消息含 key 与原因）：
- `phrases` 非空列表；`key` 非空字符串且在**同一源内唯一**（重复 key → 报错，不许静默后者覆盖前者）；
- `variants` 非空字符串列表，元素非空；`rates` 缺省取 `pack.json.rates`；
- `rates` 只能取 `core.protocol.VALID_RATES` 里的值（未知档位 → 报错，**不得回落**）；
- **每个 variant 必须是"一句"**：文本中**不得出现两个及以上句末标点**（句末标点 = `。！？!?`）。违反 → `SourceError` 并说明"长流程请拆成多个 key"（依据：设计红线「字模最小粒度 = 一句」）。

`PackSource` 用 `@dataclass(frozen=True)`；`phrases` 里每项含 `key` / `variants: tuple[str, ...]` / `rates: tuple[str, ...]`。

### 2. 质检门（`compiler/quality.py`）

```python
@dataclass(frozen=True)
class AudioStats:
    path, frames, channels, sampwidth, framerate,
    duration_ms, head_silence_ms, tail_silence_ms, peak_dbfs, clipped_samples

def measure(path) -> AudioStats        # 读 WAV；非 WAV / 打不开 → QualityError（消息含路径与原因）
def check_quality(stats) -> list[str]  # 返回问题列表（空列表 = 通过），每条含判据名与实际值
```

判据**逐条照 `docs/06 §6.1.3` 的表**（不要自创数值）：

| 检查项 | 判据 |
|---|---|
| 采样率 / 声道 / 位深 | 16000 Hz / 1 / 2 字节 |
| 帧数 | > 0 且 时长 ≤ 30000 ms |
| 头静音 | ≤ 100 ms（静音判据：`|s| ≤ 328`，即约 −40 dBFS） |
| 尾静音 | ≤ 150 ms（同上判据） |
| 削波 | 满幅样本数（`|s| ≥ 32767`）必须 = 0 |
| 峰值 | −20 dBFS ≤ 峰值 ≤ −1 dBFS |

`measure` 用标准库 `wave` + `array` 实现（**不得引第三方依赖**；注意 Python 3.14 已移除 `audioop`）。

### 3. 流水线（`compiler/prebake.py`）

```python
@dataclass
class PrebakeReport:
    pack_id, pack_version, protocol_version, ruleset_version, voice, model_version, created_at
    total: int                 # 应铸条目数（key × variant × rate）
    synthesized: int           # 本次真正调用 TTS 的条数
    reused: int                # 复用旧资产的条数
    failed: list[dict]         # 合成失败：{"key","part_index","rate_key","variant","reason"}
    quality_issues: list[dict] # 质检不过：{"key","part_index","rate_key","variant","issues":[...]}
    loudness_normalized: bool  # 本轮恒 False，见 docs/06 §6.1.4
    clean: bool                # failed 与 quality_issues 都为空
    tts_calls: int             # 与 synthesized 同值（可断言用）

def prebake(source: PackSource, adapter, out_root: Path, *, allow_partial: bool = False) -> PrebakeReport
```

流程：
1. **枚举**：对每个 phrase、每个 variant（`variant` 序号 = 该 variant 在列表中的下标，从 0 起）、每个 rate，构成一条待铸条目（`part_index` 一律 0，见 `docs/06 §6.1.4`）。
2. **预期指纹**：`assets.fingerprint(text, source.voice, rate_key, source.model_version)` —— **必须调这个函数，不得另写实现**。
3. **差量复用**：若 `out_root/manifest.json` 存在且能被 `assets.load_pack` 装载，则按 `docs/06 §6.1.5` 的四条判据决定复用（同身份 + 指纹相同 + 音频文件存在 + voice/model_version 与 `adapter` 一致）。旧包装载失败 → **不做复用、全量重铸**，并把该情况记进报告（不得静默）。复用条目不调用 TTS。
4. **合成**：未复用条目调 `adapter.synthesize(text, <暂存路径>, rate_key=rate_key)`。**先落到 `out_root/.staging/`**，不要直接写进 `audio/`。
5. **质检**：`measure` + `check_quality`。不过 → 记 `quality_issues`（含判据明细），该条不入包。
6. **失败**：`synthesize` 抛错 → 记 `failed`（`reason` 含异常消息），该条不入包，**继续处理其余条目**（不许中断，也不许静默）。
7. **fail-closed**：若 `clean is False` 且 `allow_partial is False` → **不写 manifest**、清掉暂存、抛 `PrebakeError`（异常对象要带 `report` 属性，让人能拿到失败明细）。
8. **写包**（`clean` 或 `allow_partial=True` 时）：把通过的音频移进 `out_root/audio/<fingerprint>.wav`（**内容寻址**：文件名用指纹），写 `out_root/manifest.json`（**必须是 `assets/` 的冻结包格式**），写预铸账 `out_root/prebake_ledger.json`（`report` 的内容 + 生成时间）。
9. **写出的包必须能过资产层校验**：`assets.load_pack(out_root)` 成功且 `assets.validate_pack(out_root) == []`（`path` 一律写成 `audio/<fingerprint>.wav` 这种**包内相对路径**）。

### 4. 预铸账

`prebake_ledger.json` = `PrebakeReport` 的字段全量落盘（含 `failed` / `quality_issues`），外加 `loudness_normalized: false`（显式标注，见 `docs/06 §6.1.4`）。

## 允许修改的文件（白名单）

```
允许新增：compiler/__init__.py, compiler/source.py, compiler/quality.py, compiler/prebake.py,
          compiler/tests/__init__.py, compiler/tests/test_source.py,
          compiler/tests/test_quality.py, compiler/tests/test_prebake.py
允许修改：无
禁止触碰：其余一切文件（含 core/**、rules/**、assets/**、adapters/**、docs/**、compiler/AGENTS.md、根 AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库）。
- **不得自己实现指纹算法**（只能调 `assets.fingerprint`）；不得自己实现 WAV 解析（用 `wave`）。
- **不得实现 LUFS / 响度归一**（明确推迟，见 `docs/06 §6.1.4`）；预铸账里 `loudness_normalized` 必须为 `false`。
- **不得改判据数值**（照 `docs/06 §6.1.3` 的表；改数就是改契约）。
- 不得往仓库里放样例业务数据（测试用 `tempfile` 自造源与临时输出目录）；不得把音频写进仓内路径。
- 不得改动白名单外文件；不得顺手重构；不得读剧本（`script.json` 不属本卡）。
- 不得实现命中判定 / 播放（那是 `runtime/`）。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s compiler -v` **全绿**；无 `say` 环境下依赖真合成的用例 `skip` 而非失败（用 `shutil.which("say")` 判断）；
2. **产物过资产层校验**（我在真实预铸产物上跑）：`assets.load_pack(out)` 成功、`assets.validate_pack(out) == []`、`out/manifest.json` 里 `assets` 非空且每条 `path` 形如 `audio/<16位hex>.wav`；
3. **幂等**：同一源连续 `prebake` 两次 → 第二次 `synthesized == 0`、`reused == total`、`clean is True`；两次 manifest 的指纹集合相同（我会实测两次调用的返回值）；
4. **差量生效**：改一个 variant 的文本后重铸 → **只有该 variant 的条目重铸**：`synthesized == 该 variant 的语速档数`、`reused == total - synthesized`，且**只有该 variant 各档的指纹变化**（我比对两次 manifest 的条目集合）。
   > 口径更正（2026-09-17 验收时）：本卡初版写的是 `synthesized == 1`，**写错了**——改动一个 variant 的文本会让该 variant 的**每一档语速**都失效，所以正确判据是"等于该 variant 的档数"。实现行为正确，是卡数错。
5. **质检门真会拦**（我手跑）：自造一条头静音 500 ms 的 WAV → `check_quality` 返回非空、消息含判据名与实际值；把它塞进流水线 → `clean is False`，且 fail-closed 下 `manifest.json` **不存在**、抛 `PrebakeError` 且 `e.report.quality_issues` 含该条；
6. **失败不静默**（我手跑）：用 mock 让某条 `synthesize` 抛错 → 该条进 `failed`（`reason` 非空）；`allow_partial=True` 时写包但 `report.clean is False`，且 `manifest.json` 里**没有**那条；
7. **Loudness 标记**：产物 `prebake_ledger.json` 里 `loudness_normalized is False`；
8. **源格式负例**（我会手跑）：缺 `pack.json` / 缺 `phrases` / 重复 key / `rates` 含非法档位 / variant 含两个句末标点 → 均抛 `SourceError` 且消息含 key 或字段名；
9. 含方法级中文注释与关键步骤 WHY 注释（尤其：为何先落暂存、为何内容寻址、为何 fail-closed）；
10. 白名单之外无改动（我用 `git status --porcelain -uall` 核对）。

## 反空转条款（必带）

- 测试必须调用**产品 API**（`load_source` / `measure` / `check_quality` / `prebake`），不得在测试内复制这些逻辑；
- 质检用例用**自造 WAV**（`wave` 写、可精确构造头静音/削波/超长/错采样率），不得只测"正常音频能过"；
- 负例必须断言**异常类型 + 消息含关键值**（key 名、字段名或判据名），不得只写 `assertRaises(Exception)`；
- 断言必须**能失败**：至少对「头静音超阈」「削波」「时长超 30s」「非法 rate」四类各有一条用例，且这些用例喂的是**确实违规**的输入；
- 不得 `except: pass`、不得 `assertTrue(True)`、不得用 `skipTest` 绕过（依赖 `say` 的用例除外）。

## 回滚方式

```
cd （仓库根）
git clean -fd compiler && git checkout -- compiler/AGENTS.md
```

（本卡只新增 `compiler/` 下的文件；`compiler/AGENTS.md` 是已跟踪文件，若被误改用 `git checkout` 还原。）

## 执行方式

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T05-compiler-预铸流水线.md)" --dir （仓库根）
```

这是**非交互环境**：请直接落地代码，不要先写计划再等确认，不要写仓库外的计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要新建或修改任何文件。

## 卡状态

- [x] 已派发（2026-09-17，sense-nova/sensenova-6.8-flash-lite，22m44s）→ [x] 已回收 → [x] 验收通过（提交 f3a456e）
