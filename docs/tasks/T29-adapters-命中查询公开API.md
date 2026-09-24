# T29 · adapters：命中查询公开 API（`find_hit`，为 kefu 内嵌旁路钩子供数）

## 背景（只写必需）

内嵌旁路（用户 2026-09-19 拍板：kefu 允许最小侵入）的接缝已定：kefu `LocalCascadeAdapter.synthesize(text)`
是 TTS 唯一入口，钩子查预铸包——命中播包内音频、未命中走原路、双环境变量默认关。
但钩子需要的「key/text → 命中条目」查询面在 `adapters/framework_kefu/bridge.py` 里全是**私有方法**
（`_lookup_key` / `_pick_by_text` / `_confirm` / `_text_for_key` / `_text_index`）——kefu 侧不能依赖他人私有面。

本卡把这套判定逻辑**提取为模块级公开函数**（语义零变化，`KefuBridge` 方法改委托），
并组合出单一查询入口 `find_hit`。这是「留口子优先于大重构」：不重写判定，只换归属。

语义权威（实现必须与它们逐条一致，不得加松）：
- key 档：`_lookup_key`——`part_index=0` + rate 档优先 + `pack.lookup` 指纹复核；
- text 档：`normalize_text`（docs/10 §10.3 四步）→ `_pick_by_text`（rate 档优先 → 索引首条，顺序稳定）→ `_confirm`；
- 引擎一致性检查**不在本 API**（那是 bridge↔live_tts 的关系，钩子侧自校验，T30 卡写明）；
- 未命中**返回不抛**（fail-closed 的抛错留在 run_turn 层——查询 API 的语义是"报结论"）。

## 目标（可验收的产物）

1. 新文件 `adapters/framework_kefu/hit_query.py`：
   - `HitResult`（frozen dataclass）：`entry: Optional[AssetEntry]`、`mode`（`MODE_KEY`/`MODE_TEXT`）、
     `key: Optional[str]`、`text: str`（命中条目原文或输入文本）、`miss_reason: Optional[str]`
     （复用既有 `REASON_KEY_NOT_PREBAKED` / `REASON_TEXT_NOT_PREBAKED`，命中时为 None）；
   - 模块级函数：`build_text_index(pack)`、`lookup_key(pack, key, rate_key)`、
     `pick_by_text(pack, norm, rate_key)`、`confirm(pack, cand)`、`text_for_key(pack, key, rate_key)`——
     **函数体提取自 bridge 同名私有方法**（`self.pack`→`pack` 参数化，逻辑逐语句一致）；
   - 组合入口 `find_hit(pack, *, key=None, text=None, rate_key="normal") -> HitResult`：
     恰好一种输入表示（两种都给或都没给 → `ValueError`，消息含具体输入）；key 档走 `lookup_key` +
     `text_for_key` 补原文；text 档归一化后走 `pick_by_text`；空归一串按未命中（`REASON_TEXT_NOT_PREBAKED`）；
2. `bridge.py`：五个私有方法改为对 hit_query 模块函数的**委托**（一行调用 + self 参数代入）；
   `_text_index` 的**缓存逻辑留在 bridge**（缓存字段与惰性判断不迁移），索引构建部分委托 `build_text_index`；
3. `__init__.py`：`__all__` 追加 `find_hit`、`HitResult` 及五个模块函数（`normalize_text` 等既有导出不动）；
4. 新测试 `adapters/framework_kefu/tests/test_hit_query.py`。

## 允许修改的文件（白名单）

```
允许新增：adapters/framework_kefu/hit_query.py
允许新增：adapters/framework_kefu/tests/test_hit_query.py
允许修改：adapters/framework_kefu/bridge.py（仅五个私有方法体改委托；run_turn / _play_pack / _slow_path / 判定顺序零改动）
允许修改：adapters/framework_kefu/__init__.py（仅 __all__ 追加与对应 import）
禁止触碰：其他一切文件（尤其 core/ assets/ runtime/ eval/ cli/ packs/ trigger/ tools/ docs/）
```

## 禁止事项

- **判定语义零变化**：提取是逐语句搬迁，不得加归一步骤、不得放宽指纹校验、不得改 rate 档优先策略、
  不得让 `find_hit` 未命中时抛错或返回裸 None（必须带 miss_reason 的 HitResult）；
- 不得动 `run_turn` 的事件流 / slow_path / 引擎一致性检查段；
- 不得改 `normalize_text` 一行；不得 `git add` / `git commit`；不得顺手重构 bridge 其他部分。

## 验收标准（逐条可判定，验收方会逐条核对）

1. **bridge 既有测试零改动全绿**：`python3 -m unittest discover -s adapters` → **≥152 条全绿**
   （bridge 既有测试文件**一个字符不许改**——委托后它们必须原样通过，这是「语义零变化」的机器证明）；
2. **新 API 正例**（测试须真实构造包 fixture——复用 `tests/` 既有 fixture 工厂，不得自造第二套包构造逻辑）：
   - key 档命中：`find_hit(pack, key="<存在key>", rate_key="normal")` → `entry is not None`、
     `mode==MODE_KEY`、`miss_reason is None`；
   - text 档命中：用某 variant 原文（含全角/空白扰动，如加前后空格与全角标点）→ 命中同一 key 的条目
     （证明四步归一化生效）；rate 档优先：同句多语速档时返回 normal 档条目；
   - `find_hit` 与 `KefuBridge.run_turn` 判定一致性：同一 fixture 包、同一输入，`find_hit` 的 entry
     与 `run_turn(..., allow_fallback=True)` 播包轮的事件结论一致（测试内对比，不许复制判定逻辑）；
3. **新 API 负例**（消息含具体非法值）：
   - text 未命中 → `entry is None` 且 `miss_reason==REASON_TEXT_NOT_PREBAKED`；
   - key 不存在 → `miss_reason==REASON_KEY_NOT_PREBAKED`；
   - 指纹不符条目（构造 text 与索引一致的包但 lookup 校验失败的场景）→ 未命中（走 `confirm` 的 None 分支）；
   - `find_hit(pack)` 与 `find_hit(pack, key="a", text="b")` → `ValueError` 且消息含输入表示数量；
   - `normalize_text(123)` 仍 TypeError（未被本卡波及）；
4. **AST 级语义对照**：`bridge.py` 改动后，五个被委托方法的公开行为与 HEAD 版一致——验收方用
   `git diff adapters/framework_kefu/bridge.py` 逐行核对（只允许方法体→委托行与对应 import）；
5. `git status --porcelain -uall` 改动全部在白名单内。

## 反空转条款（每张卡必带，T01 教训）

- 测试必须调用 `find_hit` / 模块函数产品 API，不得在测试内复制归一化或索引逻辑（违者整卡退回）；
- 包 fixture 必须复用 `tests/` 既有工厂（先看 `tests/` 里既有测试怎么构造包——T11 系测试有现成的），
  不得手搓第二套；
- 正例必须含「全角/空白扰动后仍命中」（证明归一化真在工作）与「rate 档优先」两条，缺一退回。

## 回滚方式

`git checkout -- adapters/framework_kefu/` + `git clean -fd adapters/framework_kefu/hit_query.py adapters/framework_kefu/tests/test_hit_query.py`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。**数据分级：公开级**——纯代码，fixture 自造。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19，第十四批；提交 `29e5582`）**

### 验收记录（验收方独立复核）

- adapters **188 全绿**（152 既有零改动原样通过 = 语义零变化机器证明 + 36 新增）；bridge 既有测试文件与
  `normalize.py` 一个字符未改（git status 核对）；
- **AST 级对照（验收方亲跑）**：四个函数 vs HEAD 私有方法——差异全部为参数化（`self.pack`→`pack`）、
  docstring 缩进、`_text_index()`→`build_text_index()` 委托（缓存留 bridge），For 循环体逐语句一致；
- **裁定 2 条执行方取舍（均接受）**：① 常量真源下沉 hit_query（bridge→hit_query→bridge 循环导入的必然解，
  `assertIs` 锁三处同源）——策划未预判处，同 T28 教训；② `REASON_KEY_NOT_PREBAKED` 追加导出（钩子断言需要）；
- 执行方报告的破坏性验证只做了 rate 优先一处——验收方以 152 既有测试原样通过 + AST 对照补强，接受。
