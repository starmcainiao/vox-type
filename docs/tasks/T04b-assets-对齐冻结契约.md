# T04b · assets：对齐冻结契约（AssetPack.lookup 方法 + 指纹口径统一）

> 本卡是 T04 的**修订卡**：T04 的六条编号验收标准全部通过（38 条测试全绿、坏包四类全拒、lookup 语义正确、用户数据黑名单生效），但验收时对照卡内「装载 API」契约实测出**两处偏差**。按纪律退回对齐，不做"差不多过"。

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有接口契约与指纹口径说明，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 偏差一：`AssetPack.lookup` 方法不存在（契约形状不符）

T04 卡「目标（产物）」一节写的是：

```
- `AssetPack.lookup(key, part_index, rate_key, variant, expected_text, engine_meta) -> AssetEntry | None`
```

即 **`lookup` 是 `AssetPack` 上的方法**。实测落地成了**模块级函数** `assets.pack.lookup(pack, key, ...)`：

```
$ python3 -c "from assets import AssetPack; print(hasattr(AssetPack, 'lookup'))"
False
```

`assets/` 是**冻结区**，它的公共 API 就是下游（T07 执行器 / T08 评测 / T10 CLI）要照着写的契约。形状不符会让下游照卡写的代码直接 `AttributeError`，也让冻结契约与 `docs/` 设计文档悄悄漂移。

## 偏差二：指纹口径三处不一致（会造成"装载成功但永远 lookup 不中"）

指纹的四个要素里，`voice` / `model_version` 在**三处实现里取的不是同一套值**：

| 位置 | 现在取的 voice / model_version |
|---|---|
| `load_pack` → `_validate_asset_entry` | `raw.get("voice", manifest_voice)`、`raw.get("model_version", manifest_model_version)` ← **支持条目级覆盖** |
| `validate_pack` | `raw_entry.get("voice", manifest.get("voice"))` ← **同样支持条目级覆盖** |
| `lookup` | 固定用 `pack.voice`、`pack.model_version` ← **不支持** |

后果：一个条目带了自己的 `voice` 字段时，`load_pack` 能装载成功，但 `lookup` 永远算不出同一个指纹 → **永远返回 None**。这是「装载时说没问题、运行时永远不中」的死角，属于静默失效，必须消除。

**口径裁定（本卡给定，照做）**：格式冻结里**没有**条目级 `voice` / `model_version` 字段，所以指纹四要素中的 `voice` / `model_version` **一律取 manifest 级**。三处实现统一按这个口径。

## 目标（修复要求）

1. **`AssetPack.lookup(...)` 必须是方法**，签名与卡一致：
   `lookup(self, key: str, part_index: int, rate_key: str, variant: int, expected_text: str, engine_meta: Optional[Dict[str, Any]] = None) -> Optional[AssetEntry]`
   - 语义不变：指纹不匹配 / 文件不存在 / 未命中 → 返回 `None`；**不得抛错、不得返回过期条目**。
   - 允许把现有模块级 `lookup(pack, ...)` 保留为**薄委托**（一行转调方法），以不破坏现有调用；但方法本身必须存在且是同一实现，**不得两套逻辑各写一遍**。
2. **指纹口径统一**：`_validate_asset_entry` 与 `validate_pack` 里对条目级 `voice` / `model_version` 的覆盖读取**删掉**，一律用 manifest 级的值（与 `lookup` 一致）。
   - 效果：条目自带不同 `voice` 时，重算指纹与包内指纹不符 → `load_pack` 抛 `AssetPackError`（fail-closed，正确）；`validate_pack` 返回非空问题列表。
3. **不得放宽任何既有校验**：必填字段、类型检查、rate_key 合法性、用户数据黑名单、指纹一致性、音频文件存在性、`assets` 非空——全部保持原样。
4. `assets/__init__.py` 的导出保持可用（`fingerprint` / `AssetPack` / `AssetEntry` / `AssetPackError` / `load_pack` / `lookup` / `validate_pack`）。
5. 方法级中文注释与 WHY 注释保留。

## 允许修改的文件（白名单）

```
允许修改：assets/pack.py, assets/__init__.py, assets/tests/test_pack.py, assets/tests/test_fingerprint.py
允许新增：无
禁止触碰：其余一切文件（含 core/**、assets/AGENTS.md、core/AGENTS.md、rules/**、adapters/**、docs/**、根 AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库）。
- 不得删既有测试用例、不得放宽既有断言（既有 38 条必须逐条仍在）。
- 不得改动白名单外文件；不得顺手重构 `fingerprint.py`（指纹算法与实现**一个字都不许动**）。
- 不得实现音频合成 / 命中判定 / 降级策略。

## 验收标准（我会逐条核对）

1. `python3 -c "from assets import AssetPack; print(hasattr(AssetPack,'lookup'))"` → `True`；`AssetPack.lookup` 与 `assets.lookup` **行为一致**（同一批输入给出同一结果）；
2. `python3 -m unittest discover -s assets -v` 全绿，且用例数 **≥ 38**（只增不减）；
3. **口径一致性负例（新增测试，我会复现）**：构造一个条目自带 `voice`（与 manifest 不同）的包 → `load_pack` **抛 `AssetPackError`**（消息含指纹不匹配），`validate_pack` 返回**非空**问题列表；
4. **方法可用性正例（新增测试，我会复现）**：`load_pack` 出来的包，`pack.lookup(key, part_index, rate_key, variant, expected_text)` 命中时返回 `AssetEntry`；`expected_text` 改一个字 → `None`；删掉音频文件 → `None`；
5. 既有的坏包四类（缺 manifest / 缺必填字段 / 指纹不符 / 音频不存在）仍全部被拒，且 `validate_pack` 仍返回非空（我不接受"顺手放宽"）；
6. 白名单之外无改动（我用 `git status --porcelain` 核对）。

## 反空转条款（必带）

- 新增测试**必须调用产品 API**（`load_pack` / `validate_pack` / `AssetPack.lookup`），不得在测试内重写校验逻辑；
- 负例必须断言**异常类型 + 消息含关键值**（如 `AssetPackError` 且消息含"指纹不匹配"），不得只断言"抛了异常"；
- 不得用 `skipTest` 或 `assertTrue(True)` 之类占位手段凑绿。

## 回滚方式

```
cd （仓库根）
git checkout -- assets/pack.py assets/__init__.py assets/tests/test_pack.py assets/tests/test_fingerprint.py
```

（T04 产物已在上一提交入库为**已跟踪**基线，`git checkout` 有效。）

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T04b-assets-对齐冻结契约.md)" --dir （仓库根）
```

**非交互环境**：请直接改代码，不要先写计划再等确认，不要写仓库外的计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要修改或新建任何文件。

## 卡状态

- [x] 已派发（2026-09-17）→ [x] 已回收 → [x] 验收通过（提交 1a8e749）
