# T04 · assets：资产包格式与校验器（含指纹算法）

## 背景

`assets/` 是**数据层**：定义资产包格式（冻结）、提供格式校验器与**文本指纹算法**。
指纹是整套方案的保险丝：话术改了但资产没重铸 → 指纹不匹配 → 运行时视为未命中（而不是念错版本）。

依赖：`core/`（T01/T01b 已验收）。必读：`AGENTS.md`、`assets/AGENTS.md`。**不得修改这两个文件。**

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有包格式规格与指纹算法定义，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 目标（产物）

1. `assets/__init__.py` — 导出 `fingerprint` / `AssetPack` / `load_pack` / `validate_pack` 等公共 API
2. `assets/fingerprint.py` — 指纹算法（**唯一实现**，其他层只能调它）
3. `assets/pack.py` — 包结构定义（dataclass）+ 装载 + 校验
4. `assets/tests/test_fingerprint.py` + `assets/tests/test_pack.py` — 测试
5. `assets/示例/` 不需要；测试用 `tempfile` 自造包。

### 指纹算法（照抄，不得改动）

```
fingerprint(text, voice, rate_value, model_version) -> str
    = sha256(f"{text}\x00{voice}\x00{rate_value}\x00{model_version}".encode("utf-8")).hexdigest()[:16]
```

**语义要求**：`text` / `voice` / `rate_value` / `model_version` 任一变化 → 指纹必变（四个正交性都要有测试）。

### 包结构（格式冻结）

```json
{
  "pack_id": "string", "pack_version": "string", "protocol_version": "string",
  "ruleset_version": "string", "voice": "string", "model_version": "string",
  "created_at": "ISO8601",
  "assets": [
    {"key": "string", "part_index": 0, "rate_key": "slow|normal|fast", "variant": 0,
     "text": "string", "fingerprint": "16位hex", "path": "audio/xxx.wav",
     "duration_ms": 1200}
  ]
}
```

装载 API：
- `load_pack(root: Path) -> AssetPack`：读 `<root>/manifest.json`，逐条校验，任一条不合格 → 抛 `AssetPackError`（**不得跳过坏条目继续**）；
- `AssetPack.lookup(key, part_index, rate_key, variant, expected_text, engine_meta) -> AssetEntry | None`：**指纹不匹配 / 文件不存在 → 返回 None**（由调用方决定降级；这里只做判定，不做降级）；
- `validate_pack(root) -> list[str]`：返回问题列表（空列表=通过），用于 CLI `vox verify`。

## 允许修改的文件（白名单）

```
允许新增：assets/__init__.py, assets/fingerprint.py, assets/pack.py,
          assets/tests/__init__.py, assets/tests/test_fingerprint.py, assets/tests/test_pack.py
允许修改：无
禁止触碰：其余一切文件（含 core/**、AGENTS.md、README.md、docs/**）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库）。
- 不得实现音频合成、命中判定、降级策略（分别属 `adapters/`、`runtime/`）。
- **不得在多个位置重复实现指纹算法**（只能在 `fingerprint.py` 一处）。
- 不得改动白名单外文件。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s assets -v` 全绿；
2. **指纹正交性（4 条负例）**：改 `text` / 改 `voice` / 改 `rate_value` / 改 `model_version` → 指纹均变化；相同输入的指纹稳定可复现；
3. **负例：坏包必拒**——缺 `manifest.json`、缺必填字段、`assets[].fingerprint` 与实际重算不符、`path` 指向不存在的文件 → `load_pack` 抛 `AssetPackError` 或（`validate_pack`）返回非空问题列表；
4. **lookup 语义**：指纹不匹配 → 返回 `None`（不得抛错、不得返回过期条目）；
5. **包内不得含用户数据**：`validate_pack` 必须检查并拒绝出现疑似用户数据字段（如 `slots`/`phone`/`user_utterance` 出现在 manifest 或条目中）——本卡可只做字段名黑名单检查，但**必须存在且被测试**；
6. 含方法级中文注释与关键步骤 WHY 注释。

## 反空转条款（必带）

- 测试必须调用产品 API（`fingerprint` / `load_pack` / `lookup` / `validate_pack`），不得在测试内重写这些逻辑；
- 负例必须断言**失败原因**（异常类型 + 消息含关键值，或问题列表内容），不得只断言"抛了异常"；
- 不得为过测试放宽校验。

## 回滚方式

只新增文件 → `git clean -fd assets`（保留已跟踪的 `assets/AGENTS.md`）。

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T04-assets-包格式与校验器.md)" --dir （仓库根）
```

## 卡状态

- [x] 已派发（2026-09-17）→ [x] 已回收 → [x] 部分退回（AssetPack.lookup 缺失 + 指纹口径不一致 → T04b 已修，经 T04c 后整体验收通过）
