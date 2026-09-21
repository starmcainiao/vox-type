# guided-decoding · 约束解码支持度勘察（`docs/17 §二` 近期项的前置勘察）

> **一句话结论**：kefu brain 当前用的 LLM 运行时（商汤 OpenAI 兼容端点）**不支持 key 集合的物理约束**——
> `json_schema` 被明确拒绝（400），`guided_json` **被静默忽略**（200 但毫无约束效果）。
> 「brain 按 key 说话」的完全体（Trie/FSM 约束解码）**必须换运行时**（vLLM + XGrammar / SGLang / Outlines）。
> 当前可用档只有 `json_object`（保证"是 JSON"，**不保证 key 合法**）→ 仍需显式校验（或维持现有的 yaml 直取方案）。

## 为什么勘察这个

`docs/17 §一 坐标 2` 把「约束解码」定为「让 brain 按 key 说话」的下一跳正解：把 `packs/heat_kefu`
的 40 个 key 编译成 FSM/文法，在解码期把 LLM 的采样限制在合法 key 空间内——非法 key **物理上不可能**。
但该路线的前提是**运行时支持**。`docs/17 §二` 因此留了一条「当前 .env 云端 API 是否支持 guided_json 需勘察」。

本目录就是那次勘察：**实测三变体 + 一条判决测试**。

## 方法

| 变体 | 请求 | 期望 |
|---|---|---|
| 基线 | 无约束 | 模型正常输出（对照） |
| ① | `response_format={"type":"json_object"}` | 只保证"是 JSON"，不约束 schema/enum |
| ② | `response_format={"type":"json_schema", …, "strict": true}` | 真正的 schema 级约束 |
| ③ | `guided_json={…enum…}` | vLLM/Outlines 风格额外参数（OpenAI 兼容端点上常见"收下但忽略"） |
| **★判决** | 把 enum 设成**模型绝不会自然输出**的值（`zzz_impossible_key_9x7`） | 输出该值 = 约束生效；输出自然倾向值 = **参数被静默忽略** |

判决测试是关键：③ 单看"请求成功"不能证明约束生效——**必须用一个"不可能值"来区分"真约束"与"收下就扔"**。

## 实测结果（2026-09-21，`probe.py` 单次运行，rc=0）

| 变体 | HTTP | finish | 输出（截断） | 判读 |
|---|---|---|---|---|
| 基线 | 200 | stop | `{"key":"opening"}` | 模型可用 ✓ |
| ① `json_object` | 200 | stop | `{"key":"opening"}` | **可用**，但只保证 JSON 形态 |
| ② `json_schema`（strict） | **400** | — | `{"error":{"message":"inference request is invalid",…}}` | **不支持**（响亮的失败 ✓） |
| ③ `guided_json`（enum=合法三值） | 200 | stop | `{"key":"opening"}` | 看不出区别（无约束时也这么答） |
| **★判决 `guided_json`（enum=不可能值）** | 200 | stop | `你好！我是商量（SenseNova），由商汤科技开发的多模态大模型。…` | **参数被彻底忽略**（连 JSON 都不是了） |
| **★判决 `json_schema`（不可能值）** | **400** | — | 同上 | 一致拒绝（无歧义） |

**判定**：`guided_json_constrains = false`、`json_schema_supported = false`、`json_object_available = true`。

## 结论与含义

1. **物理约束在这条运行时上不可行**：`json_schema` 不支持、`guided_json` 无效。
   → `docs/17` 的「T 未来·key 约束解码」需要**运行时换代**（vLLM + XGrammar / SGLang / Outlines）
   才能落地；在那之前，这条路线**不能作为「brain 按 key 说话」的实现手段**。
2. **`guided_json` 的静默忽略是一个坑（本仓红线场景）**：请求 200、无报错、无警告，但约束**完全没生效**。
   任何"传了 `guided_json` 就以为输出被约束"的实现，都是**静默降级**——本仓明令禁止。
   → 无论走哪条路，**输出侧必须显式校验**（`key ∈ 包内 key 集合`），不能依赖运行时保证。
3. **当前可用档 = `json_object` + 显式校验**：它能保证"是 JSON"（比自由文本好解析），
   但仍要自己校验 key 合法性——**与现状（brain 直取 yaml 预设话术）相比没有本质增益**，
   所以短期没有理由为它改动 brain。
4. **对 `docs/16` 的对外叙事无影响**：本仓的差异化一直是"准入/质检/指纹/实测边界"这套工程体系，
   不依赖"约束解码"；后者是**上游改造的备选路线**，勘察结论只是把它从"待验证"变成"待换运行时"。

## 复现

```sh
cd <vox-type 仓根>
SENSENOVA_API_KEY=<key> python3 labs/guided-decoding/probe.py
# 可选：GUIDED_BASE_URL / GUIDED_MODEL 覆盖端点与模型（默认取商汤 OpenAI 兼容端点与 sensenova-6.8-flash-lite）
```

退出码：`0` 探测完成（无论结论）/ `2` 缺凭据 / `3` 运行期失败。
**凭据只从环境变量读**（kefu 仓 `.env` 的 `OPENAI_API_KEY` 同源），不进仓、不进产物。

## 边界（不吹的部分）

- **一档模型、一家厂商、一个时间点**：结论只对 `sensenova-6.8-flash-lite` @ 商汤端点 @ 2026-09-21 成立；
  该端点若升级（或换模型），**本勘察要重跑**（一条命令，见上）；
- **不代表其他运行时**：vLLM/SGLang/Outlines 的约束解码是成熟技术（`docs/17` 有引用），
  本勘察只回答"**当前这条**支不支持"；
- 未测流式/工具调用等其他约束形态（本仓不依赖）。
