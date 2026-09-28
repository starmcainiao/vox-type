#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pack_phrase_audit.py —— 发布树话术比对门禁（只读，零第三方依赖）。

为什么要有这个脚本
------------------
`docs/13 §五#29` 自己的验收标准第 ① 条就要求：「四类字样的**判据与计数**写进
仓内**可复跑脚本或卡面**（不接受『人工看过』）」。而现状是**只在文字里** ——
独立验收人独立复跑得到 13 敏感 / 11 公开，与 T55 的 12/12 不符，分歧项还被
一边判敏感一边判公开。**同一判据两边不打架**的前提是判据能从它自己声明的规则
机械复现。

本脚本把那套判据落成可复跑的代码。**只输出 key 名与类别，绝不输出任何原文**
（`docs/13 #29` 验收标准 ② 与「产出物里出现的是 key 名、计数与定性判断，不是原文」）。

四类分级（卡面 §二D）
---------------------
| 类别                | 判据                          | 退出码 |
|---------------------|-------------------------------|--------|
| `credential`        | 命中 token/密钥/口令特征      | **1**  |
| `internal-endpoint` | 命中内网地址/私有仓绝对路径/内网服务名 | **1**  |
| `verbatim-overlap`  | 与仓外 yaml 逐字重合 ≥ 阈值   | 0（只报告）|
| `structured-label`  | 同上，但该 key 是结构化标签    | 0（只报告）|

前两类**无论业务方怎么裁定都必须失败**——凭据与内网地址进公开仓没有「可公开」一说。
后两类出报告、退出 0，并带 `policy="pending-business-decision"`：业务方拍板后
改这一个字段即可切成硬失败，**不需要改判据、不需要改 CI**。

阈值（可机械复现，不靠人眼）
---------------------------
- `MIN_PHRASE_RUN = 8`：`verbatim-overlap` —— 判据与 T55/T57 同形，
  **逐行取** `admission.md` §2/§3 表的「yaml 文本（节选）」列，
  在**该行自己的 key** 对应的 yaml 值里找**最长逐字连续重合**，
  达到 8 字即算命中。

  **为什么必须逐行 + 同 key 比**：拿「某 key 的话术」和「整份 admission.md」
  求最长公共子串，会把两句话共有的客套话（8 个汉字的重合在中文里是常事）
  算成逐字泄漏。本机实测：不做同 key 约束时命中 34 个 key，其中 12 个
  的重合占比 ≤ 0.33，明显是碰巧；加上同 key 约束后命中数与 T55 登记的
  24 条**逐条对上**（其中 22 条是整段逐字，2 条是节选前缀）。
- `MIN_LABEL_RUN = 4`：`structured-label` —— yaml 里**值类型非 `str`** 的 key
  （dict/list，典型如键名表与覆盖表）及其下标量，出现在文档里即算命中。

  这正是 T57 记录的那条口径差异：「把比对阈值降到任意长度会多出
  `faq_overrides` / `slot_labels` 两个 key——**它们是结构化标签不是话术正文**」。
  本脚本用**值的类型**（`str` vs 非 `str`）把这两类分开，判据写死在代码里；
  卡面点名的两个 key 另在 `named_structured_label_keys` 里单列，便于对账。

环境与只读保证
--------------
- 仓内输入：`packs/heat_kefu/admission.md`（`--admission` 覆盖，默认按仓根相对定位）。
- 仓外 yaml：**只读**，路径走 `KEFU_HEAT_YAML`（本仓既有约定）或 `--yaml`。
  **脚本里不写死任何机器绝对路径**。
- **yaml 取不到时不得静默返回 0 假绿**：此时逐字/结构化两类**无法评估**，
  输出里 `yaml.status="unavailable"` + `degraded=true`，并往 **stderr** 打一条
  响亮提示。`credential` / `internal-endpoint` 两类**不依赖 yaml**，照跑照判。
  与本仓既有纪律一致（`packs/heat_kefu/tests/test_source_of_truth.py`：公开 CI
  没有 kefu 仓，skip 是正确行为而不是失败）。要「取不到即失败」用 `--require-yaml`。
- 本脚本**不写任何文件**；`--json` 只输出到 stdout。

退出码
------
    0  无硬失败类命中（`credential` / `internal-endpoint`）
    1  有硬失败类命中
    2  用法/环境错误，或 `--require-yaml` 下 yaml 不可达
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ---- 阈值（写死在这份代码里；文档里只留调用，正则不出现在被扫描的文档中）----
MIN_PHRASE_RUN = 8   # 话术正文逐字重合的最小长度
MIN_LABEL_RUN = 4    # 结构化标签逐字重合的最小长度

# 卡面点名的两个结构化标签 key（判据另加一条更宽的：值类型非 str 即结构化标签）
NAMED_STRUCTURED_LABEL_KEYS = frozenset({"faq_overrides", "slot_labels"})

# ---- 硬失败类判据（一律不依赖仓外 yaml）--------------------------------
# credential：凭据特征。正则全部藏在代码里，不写进任何被扫描的文档。
CREDENTIAL_PATTERNS = (
    ("pem-private-key", re.compile(r"-{5,}\s*BEGIN[ A-Z]*PRIVATE KEY")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b")),
    ("url-embedded-credential", re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")),
    ("secret-assignment", re.compile(
        r"(?i)\b(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
        r"private[_-]?key|credential|authorization|bearer)\b\s*[:=]\s*[\"']?[^\s\"',;]{4,}")),
)

# internal-endpoint：内网地址 / 私有仓绝对路径 / 内网服务名。
# 「内网服务名」刻意**要求出现在网络语境里**（scheme://、host:port、内网域名后缀、
# 常见中间件 scheme）—— 否则「智能客服小暖」这类人设代号会被误判，那属
# docs/13 #29 的另一套四类字样判据，不是本脚本的职责，也不是「内网地址」。
INTERNAL_ENDPOINT_PATTERNS = (
    ("private-ipv4", re.compile(
        r"(?<![\w.])(?:10(?:\.\d{1,3}){3}|127(?:\.\d{1,3}){3}|169\.254(?:\.\d{1,3}){2}|"
        r"192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\w.])")),
    ("loopback-host", re.compile(r"(?i)\b(?:localhost|127\.0\.0\.1)\b")),
    ("internal-domain-suffix", re.compile(
        r"(?i)(?<![\w.-])[\w-]+(?:\.[\w-]+)*\.(?:internal|local|lan|corp|intranet|svc|cluster\.local)"
        r"(?![\w-])")),
    ("host-port", re.compile(
        r"(?i)\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b(?::\d{2,5})\b")),
    ("internal-scheme", re.compile(
        r"(?i)\b(?:redis|rediss|mysql|mariadb|postgres(?:ql)?|mongo(?:db)?|amqp|amqps|kafka|"
        r"memcached|etcd|consul|nacos|zookeeper|grpc|ldap|ldaps)://")),
    ("private-repo-absolute-path", re.compile(
        r"(?<![\w:/.-])/(?:Users|Volumes|opt|srv|mnt|media|private|var|home|Applications|"
        r"Library|etc|usr|tmp|workspace|work|data)(?:/[\w.\-一-鿿]+)+")),
)


def _flatten_scalars(value, prefix):
    """把 dict/list 里所有字符串标量连同它的键路径吐出来（结构化标签的候选单元）。"""
    out = []
    if isinstance(value, str):
        out.append((prefix, value))
    elif isinstance(value, dict):
        for k, v in value.items():
            out.append((str(k), str(k)))
            out.extend(_flatten_scalars(v, f"{prefix}.{k}"))
    elif isinstance(value, (list, tuple)):
        for idx, v in enumerate(value):
            out.extend(_flatten_scalars(v, f"{prefix}[{idx}]"))
    return out


# --------------------------------------------------------------------------
# admission.md 解析：只取 §2/§3 两张表的 key 与「yaml 文本（节选）」列
# --------------------------------------------------------------------------
def parse_admission_tables(path: Path):
    """返回 [{'key','text','metadata_only','line'}]。

    表格行形如 `| `key` | … | yaml 文本（节选） |`；末列以 `⚠️` 起首的是
    T53 留下的「已撤下正文、只剩元数据」行 —— 标 `metadata_only=True`，
    它本来就不含正文，不该参与逐字比对。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    sec = None
    rows = []
    for idx, line in enumerate(lines, 1):
        if re.match(r"^##\s+2\.\s", line):
            sec = "2"
            continue
        if re.match(r"^##\s+3\.\s", line):
            sec = "3"
            continue
        if re.match(r"^##\s+\d", line):
            sec = None
            continue
        if sec is None or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        key = cells[0].strip("`").strip()
        if not key or key == "key" or set(key) <= set("-: "):
            continue  # 表头 / 分隔行
        text = cells[-1]
        rows.append({
            "key": key,
            "section": sec,
            "text": text,
            "metadata_only": text.startswith("⚠️"),
            "line": idx,
        })
    return rows


# --------------------------------------------------------------------------
# 最长逐字连续重合
# --------------------------------------------------------------------------
def _rolling_set(text: str, size: int):
    return {text[i:i + size] for i in range(len(text) - size + 1)}


def longest_common_run(unit: str, doc: str, threshold: int):
    """返回 unit 与 doc 的最长逐字连续重合长度（>= threshold 才有值）。

    先用「长度恰为 threshold 的子串集合」做成员判定（O(len(unit)) 查表），
    命中后再左右扩张取最长值 —— 避免 O(len(doc)*len(unit)) 的逐格 DP。
    """
    if not unit or len(unit) < threshold:
        return 0
    if len(doc) < threshold:
        return 0
    grams = _rolling_set(doc, threshold)
    best = 0
    seen = set()
    for i in range(len(unit) - threshold + 1):
        g = unit[i:i + threshold]
        if g not in grams or g in seen:
            continue
        seen.add(g)
        for j, ch in enumerate(doc):
            if doc.startswith(g, j):
                run = len(g)
                # 向右扩张
                while (i + run < len(unit)) and (j + run < len(doc)) and unit[i + run] == doc[j + run]:
                    run += 1
                if run > best:
                    best = run
                break
    return best


# --------------------------------------------------------------------------
# 硬失败类（与 yaml 无关）
# --------------------------------------------------------------------------
def scan_hard_categories(path: Path):
    """扫 admission.md 全文，返回 [{key/位置, category, rule}]。**不回显任何原文**。"""
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for category, patterns in (("credential", CREDENTIAL_PATTERNS),
                                   ("internal-endpoint", INTERNAL_ENDPOINT_PATTERNS)):
            for rule, rx in patterns:
                if rx.search(line):
                    hits.append({
                        "line": lineno,
                        "category": category,
                        "rule": rule,
                    })
    return hits


# --------------------------------------------------------------------------
# 逐字 / 结构化标签比对
# --------------------------------------------------------------------------
def yaml_candidates(repo_root: Path, env=None):
    """与 `packs/heat_kefu/tests/test_source_of_truth.py` 同一套候选顺序。

    刻意**不写死任何绝对路径**：显式指定 → 仓旁 → 仓旁的上级 → `$HOME`。
    """
    env = os.environ if env is None else env
    rel = Path("organs", "客服", "brain", "prompts", "供热预设.yaml")
    cands = []
    explicit = env.get("KEFU_HEAT_YAML")
    if explicit:
        return [Path(explicit)], True
    cands.append(repo_root.parent / "kefu-agent" / rel)
    cands.append(repo_root.parent.parent / "kefu-agent" / rel)
    home = env.get("HOME")
    if home:
        cands.append(Path(home) / "kefu-agent" / rel)
    return cands, False


def load_yaml(path: Path):
    """只读加载。**刻意不 import PyYAML**：零第三方依赖是本仓硬约束。

    这里只需要「顶层 key → 值」与「标量字符串」，用一个极小的行扫描器就够：
    顶层 key 是行首非空白的 `key:` 或 `key: value`。嵌套结构（dict/list）
    逐行收集其下的标量。不还原完整 YAML 语义，只还原本判据需要的形状。
    """
    top = {}          # key -> ("str", value) | ("node", None)
    node_depth = {}   # key -> 子标量列表
    cur = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if indent == 0:
            m = re.match(r"^([^:#]+):\s*(.*)$", line)
            if not m:
                cur = None
                continue
            key = m.group(1).strip().strip("'\"")
            val = m.group(2).strip()
            if val:
                top[key] = ("str", _unquote(val))
                node_depth.setdefault(key, [])
                cur = None
            else:
                top[key] = ("node", None)
                node_depth.setdefault(key, [])
                cur = key
        elif cur is not None:
            m = re.match(r"^-\s*(.+)$", line)
            if m:
                node_depth[cur].append(_unquote(m.group(1).strip()))
                continue
            m = re.match(r"^([^:#]+):\s*(.*)$", line)
            if m:
                k = m.group(1).strip().strip("'\"")
                node_depth[cur].append(k)
                if m.group(2).strip():
                    node_depth[cur].append(_unquote(m.group(2).strip()))
    return top, node_depth


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _norm_cell(text: str) -> str:
    """归一化表格单元：去 markdown 反引号与所有空白。

    空白必须去：markdown 表格里换行/缩进会插进中文正文中间，
    不归一就会把「其实逐字相同」判成不命中（假阴性比假阳性更难发现）。
    """
    return re.sub(r"\s+", "", text.replace("`", ""))


def compare_rows_with_yaml(rows, top: dict, admission_text: str):
    """§2/§3 表逐行比对（`verbatim-overlap`）。**只回 key 名 / 类别 / 长度**。

    逐行 + 同 key 约束：拿本行 key 的 yaml 值当参照物，找本行单元格与它的
    最长逐字连续重合。跨 key 比对会把客套话的重合算成泄漏（见文件头注释）。
    """
    out = []
    for row in rows:
        if row["metadata_only"]:
            continue  # T53 已撤下正文、只剩元数据的行，本就不含正文
        entry = top.get(row["key"])
        if entry is None or entry[0] != "str":
            out.append({
                "key": row["key"],
                "category": "no-verbatim-match",
                "basis": "key-not-a-str-value-in-yaml",
                "section": row["section"],
            })
            continue
        cell, value = _norm_cell(row["text"]), _norm_cell(entry[1] or "")
        run = longest_common_run(cell, value, MIN_PHRASE_RUN) if cell and value else 0
        if not run:
            out.append({
                "key": row["key"],
                "category": "no-verbatim-match",
                "basis": "run-below-threshold",
                "section": row["section"],
            })
            continue
        out.append({
            "key": row["key"],
            "category": "verbatim-overlap",
            "basis": "whole-cell-verbatim" if run >= len(cell) else "excerpt-prefix-verbatim",
            "longest_verbatim_run_chars": run,
            "section": row["section"],
        })
    return out


def compare_structured_labels(admission_text: str, top: dict, node_scalars: dict):
    """结构化标签比对（`structured-label`）。**只回 key 名 / 类别 / 长度**。

    判据：yaml 里**值类型非 `str`** 的 key —— 它们是键名表、覆盖表这类结构，
    不是话术正文；它们的名字出现在仓内文档里不构成「话术原文泄漏」。
    卡面点名的两个（`faq_overrides` / `slot_labels`）由本脚本单列在
    `named_structured_label_keys`，便于与 T57 的记录对账。
    """
    out = []
    for key, (kind, _value) in top.items():
        if kind == "str" and key not in NAMED_STRUCTURED_LABEL_KEYS:
            continue
        best, basis = longest_common_run(key, admission_text, MIN_LABEL_RUN), "key-name"
        for scalar in node_scalars.get(key, []):
            r = longest_common_run(scalar, admission_text, MIN_LABEL_RUN)
            if r > best:
                best, basis = r, "nested-scalar"
        if not best:
            continue
        out.append({
            "key": key,
            "category": "structured-label",
            "basis": basis,
            "longest_verbatim_run_chars": best,
            "named_in_card": key in NAMED_STRUCTURED_LABEL_KEYS,
        })
    return out


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pack_phrase_audit.py",
        description="发布树话术比对门禁（只读；输出恒为 JSON 到 stdout）",
    )
    ap.add_argument("--json", action="store_true",
                    help="显式声明输出格式（恒为 JSON，保留此开关只为命令行自解释）")
    ap.add_argument("--root", default="", help="仓根（缺省由本文件位置向上推）")
    ap.add_argument("--admission", default="",
                    help="admission.md 路径（缺省 <仓根>/packs/heat_kefu/admission.md）")
    ap.add_argument("--yaml", default="",
                    help="仓外 yaml 路径（缺省读 $KEFU_HEAT_YAML，再按候选顺序探测）")
    ap.add_argument("--require-yaml", action="store_true",
                    help="yaml 不可达即失败（退出码 2）。缺省是响亮降级，不是静默 0。")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    admission = Path(args.admission) if args.admission else root / "packs" / "heat_kefu" / admission_name()
    if not admission.is_file():
        print(json.dumps({"tool": "pack_phrase_audit", "passed": False,
                          "error": f"admission 文件不存在：{args.admission or '(推导)'}"},
                         ensure_ascii=False, indent=2))
        return 2

    admission_text = admission.read_text(encoding="utf-8")
    hard = scan_hard_categories(admission)
    rows = parse_admission_tables(admission)

    # 逐字 / 结构化标签：需要仓外 yaml
    if args.yaml:
        yaml_path, explicit = Path(args.yaml), True
    else:
        cands, explicit = yaml_candidates(root)
        yaml_path = cands[0]
        for c in cands:
            if c.is_file():
                yaml_path = c
                break
    yaml_status = "available" if yaml_path.is_file() else "unavailable"

    overlap, structured, unmatched, yaml_keys = [], [], [], 0
    if yaml_status == "available":
        top, node_scalars = load_yaml(yaml_path)
        yaml_keys = len(top)
        for row in compare_rows_with_yaml(rows, top, admission_text):
            bucket = {"verbatim-overlap": overlap,
                      "structured-label": structured}.get(row["category"], unmatched)
            bucket.append(row)
        structured.extend(compare_structured_labels(admission_text, top, node_scalars))

    counts = {
        "credential": sum(1 for h in hard if h["category"] == "credential"),
        "internal-endpoint": sum(1 for h in hard if h["category"] == "internal-endpoint"),
        "verbatim-overlap": len(overlap),
        "structured-label": len(structured),
        "no-verbatim-match": len(unmatched),
    }
    hard_failed = counts["credential"] + counts["internal-endpoint"]

    report = {
        "tool": "pack_phrase_audit",
        "passed": hard_failed == 0,
        # 业务方拍板后：把 pending-business-decision 换成 hard-fail 语义，
        # 并把 counts 里的 policy_scope 两类并入 hard_failed 判定即可。
        # 判据与 CI 都不必动——这是本脚本分级设计的目的。
        "policy": "pending-business-decision",
        "policy_scope": ["verbatim-overlap", "structured-label"],
        "hard_categories": ["credential", "internal-endpoint"],
        "admission": admission.relative_to(root).as_posix() if admission.is_relative_to(root)
                     else str(admission),
        "yaml": {
            "status": yaml_status,
            "source": "cli" if args.yaml else ("env:KEFU_HEAT_YAML" if explicit else "probe"),
            "top_level_keys": yaml_keys,
        },
        "degraded": yaml_status != "available",
        "thresholds": {"phrase_run": MIN_PHRASE_RUN, "label_run": MIN_LABEL_RUN},
        "table_rows": {
            "total": len(rows),
            "metadata_only": sum(1 for r in rows if r["metadata_only"]),
        },
        "named_structured_label_keys": sorted(NAMED_STRUCTURED_LABEL_KEYS),
        "counts": counts,
        "hard_findings": hard,
        "verbatim_overlap": sorted(overlap, key=lambda r: r["key"]),
        "structured_label": sorted(structured, key=lambda r: r["key"]),
        "no_verbatim_match": sorted(unmatched, key=lambda r: r["key"]),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if yaml_status != "available":
        which = "--yaml 未给" if not args.yaml else "显式指定的路径不存在"
        msg = (f"[pack_phrase_audit] 仓外 yaml 不可达（{which}）："
               f"verbatim-overlap / structured-label **未评估**（degraded=true）。"
               f"credential / internal-endpoint 不依赖 yaml，已照常判定。"
               f"公开 CI 没有 kefu 仓，这与既有 skip 纪律同形，不是静默通过。")
        print(msg, file=sys.stderr)
        if args.require_yaml:
            return 2
    return 1 if hard_failed else 0


def admission_name() -> str:
    return "admission.md"


if __name__ == "__main__":
    sys.exit(main())
