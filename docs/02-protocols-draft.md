# 02 · 三契约草案

> 契约先定死，实现随便换。以下为 v0.1 草案，欢迎质疑。

## 2.1 plan（控制面）

模型或对话状态机**不再输出自由文本**，而是输出结构化的「播报计划」：

```json
{
  "plan_id": "intake.v3",
  "utterances": [
    {"key": "intake.greet", "variant": "auto", "rate": "normal"},
    {"key": "intake.ask_phone", "slots": {"expect": "phone"}, "rate": "slow"},
    {"key": "intake.readback_phone", "slots": {"value": "<asr.phone>"}, "rate": "slow"},
    {"key": "intake.freeform", "text": "<cloud-generated>"}
  ],
  "duplex": {"patience_ms": 900, "backchannel": "on", "barge_in": "allow"}
}
```

约定：

- `key` 存在 → 渲染器查资产表；**key 缺失/资产失效 → 走未命中分支（本地 TTS 或云端生成），
  不允许静默降级到任意话术**（fail-closed 精神：宁可慢、不可错）。
- `slots` 中的值来自 ASR/业务系统；渲染器负责**模板框架（预铸）+ 槽值（现场合成）+ 停顿垫**的拼接。
- `rate` 为语速档（normal/slow/fast），用于多档预铸资产的寻址。
- `variant: auto` 表示由渲染器从变体池轮换/随机选取（防复读机）。

## 2.2 asset registry（数据面）

| 字段 | 说明 |
|---|---|
| `key` | 稳定标识（状态机状态名或意图名），版本化 `key@v3` |
| `asset` | 音频文件引用（opus/wav/mp3），**字节不进 JSON-RPC 载荷**，走文件或音频流 |
| `text` | 该资产对应的文本（用于审核与指纹） |
| `text_hash` | 文本指纹：**text 变了 → hash 变 → 资产自动标 stale**（防「文本与音频不一致」这类致命 bug） |
| `rate` / `variant` | 语速档 × 变体序号（1–5） |
| `ttl` / `invalid_at` | 陈旧失效（TTL / 事件触发 / 版本标签三选一或组合） |
| `meta` | 音色 ID、生成模型版本、采样率、响度（LUFS）——**一致性靠这些字段钉死** |

存储形态：端上 = SQLite + 文件目录；服务端 = 任意 KV（Redis 等）+ 对象存储。

## 2.3 duplex controller（行为面）

| 参数 | 取值示例 | 说明 |
|---|---|---|
| `patience_ms` | 400 / 900 / 1800 | 端点判断耐心窗：快语速用户短、慢思考用户长 |
| `rate_band` | ±15% | 语速向用户收敛的上限；**关键信息（数字/地址/金额）强制 slow 档** |
| `backchannel` | on/off + 密度 | 慢思考/长沉默时给「嗯，我在听」类背景回应 |
| `barge_in` | allow / confirm | 允许打断 / 需确认后打断 |
| `silence_pad_ms` | 120–300 | 片段之间的停顿垫（还原自然说话节奏） |

原则：**趋同在带内、关键信息反向（放慢）、极端不镜像**——
跟随焦虑用户飙语速会让 Agent 听起来也焦虑。

## 2.4 渲染器（执行面）

```
render(plan) →
  for each utterance:
    if key 命中 and 无槽位 → 播资产（零延迟）
    if key 命中 and 有槽位 → 播框架资产 + 槽值合成 + 停顿垫拼接
    if key 未命中            → 本地 TTS（短文本）或云端流式（长文本/开放域）
  全程执行 duplex 参数（耐心窗在输入侧生效，语速档/留白在输出侧生效）
```

三种后端同一接口，可对拍（快路 vs 慢路）——这是「验证数据」的生产线。
