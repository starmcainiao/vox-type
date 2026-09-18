# `packs/fin-cs/golden/` —— 真人标注与验收用例（**不进公开分发**）

依据：`packs/AGENTS.md:9`（`golden/` 不进公开分发）、`eval/AGENTS.md:30`（真人标注单独存放，只入本地仓库）、
`docs/11 §11.5`（golden set 的两个集合）与 `§11.7`（分发排除清单）。

## ⚠️ 分发排除：本目录不得随公开版本一起发布

本目录里的 `corpus_derived.jsonl` **逐字复制**了第三方语料的用户话术原文
（`user_utterance` / `assistant_reply_free` 两个字段）。
虽然来源语料是宽松许可（见下表），但：

1. `docs/11 §11.7` 明确要求**入库的派生话术必须改写、不得逐字复制语料原文**——本目录是不改写的原始用语，
   因此归入「本地留用、不对外分发」那一档；
2. **对外分发的产物是 `packs/fin-cs/phrases.json`（改写版）**，不是本目录。

**发版前检查**：`git archive` / 打包脚本必须排除 `packs/*/golden/**`。

## 内容

| 文件 | 集合 | 说明 |
|---|---|---|
| `corpus_derived.jsonl` | **对照集 A**（半自动） | 2433 条，`labeled_by=corpus-strategy-label`——key 由语料自带的策略标签映射而来，**不是真人逐条判的** |
| `acceptance.jsonl` | **验收集 B**（真人） | **尚未创建**。`docs/11 §11.5` 要求它必须 `labeled_by=human:*`，且是业务上线门槛的判定依据 |

## 来源与许可（**选 License 时要用到**）

| 字段 | 值 |
|---|---|
| `source_id` | `tongyi_dianjin/DianJin-CSC-Data` |
| 平台 | ModelScope |
| 许可 | **MIT**（README front-matter 与平台字段一致，审计记录见 `labs/corpus-harvest-2026-09-18/corpus.lock.json`） |
| 许可证据 | `https://modelscope.cn/datasets/tongyi_dianjin/DianJin-CSC-Data/blob/master/README.md` |
| 原始语料落盘 | `/Volumes/macos/corpus/tongyi_dianjin__DianJin-CSC-Data/`（**仓外**） |
| 派生脚本 | `labs/corpus-harvest-2026-09-18/derive_golden.py` |

**仓库 License 决定时要考虑**：仓里现有派生自 MIT 语料的 `packs/fin-cs/phrases.json` 与
本目录的逐字用例；选一个与之兼容的分发 License（这是待用户决策的第 ① 条）。

## 复现

```bash
cd labs/corpus-harvest-2026-09-18
python3 fetch.py                                   # 重下语料（含 sha256 交叉核对）
python3 derive_golden.py --corpus /Volumes/macos/corpus/tongyi_dianjin__DianJin-CSC-Data/data/CSConv.json \
                         --out ../../packs/fin-cs/golden/corpus_derived.jsonl
python3 baseline_literal.py --pack ../../packs/fin-cs --out raw/literal_baseline.json
```

## 一条读数据时必须知道的区分（`docs/11 §11.9.3`）

`assistant_reply_free` 与 `user_utterance` 是**两个不同实验的输入**：

- 逐字档（现状基线）比的是 **`assistant_reply_free`**（助手打算说的那句话）与预铸 variant——
  `docs/10 §10.2` 的「逐字比对」说的是这个，**不是**拿用户话术去比；
- 语义档（T15）才用 **`user_utterance`**。

拿 `user_utterance` 去逐字比客服 variant 是**范畴错误**，两臂实测都是 0/2433，但成因完全不同。
