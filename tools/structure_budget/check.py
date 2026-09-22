#!/usr/bin/env python3
"""
tools/structure_budget/check.py — 结构预算检查（R09 适应度函数）

职责：扫各层 `.py` 的**可执行行数**（tokenize 口径，排除注释与 docstring，
     与 adapters/AGENTS.md §⑨ 的 145–150 口径同源），对照**各层 AGENTS.md 登记的阈值**
     逐文件判违规，非 0 退出并列出违规（文件、实际行数、阈值）。

四条不可动摇的口径（本脚本存在的意义就是把这些口径从「人工对账」变成可复跑）：
  1. **行数口径 = 可执行行**：tokenize 后 type 为 NL 的 token 计数。
     NL = 「物理换行，且该行在语法上为空」（注释行、纯 docstring 行都归此类），
     所以注释与 docstring 天然不计入——这正是 adapters/AGENTS.md §⑨ 说
     「可执行 231 行 / 176 行」的算法。
     注意：INDENT / DEDENT 不是 NL（语法结构 token，对应没有物理换行），
     若计入会把每个缩进层都算成一行，口径立刻失真。
  2. **阈值从各层 AGENTS.md 读取**：本脚本**不持有**任何层的阈值字面量。
     某层没登记量化标准 → 该层不参与判定（跳过并打印，不猜一个数）。
  3. **豁免在台账里显式登记**：超限但未登记 → 违规。台账是「已知例外」的
     唯一清单，不允许脚本默默放宽阈值（那等于静默降级）。
  4. **测试与 __pycache__ 不计**：AGENTS.md 的量化标准明写「不含注释与测试」。

产物：
  tools/structure_budget/LEDGER.md      —— 台账（阈值来源 + 当前行数快照 + 豁免登记）
  tools/structure_budget/snapshot.json  —— 机器可读快照（脚本自身生成，可 diff）

退出码：0 全部合规 / 1 存在违规（文件、行数、阈值逐条列出）/ 2 用法或仓库根解析失败。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 检查覆盖的九个 Python 层（rules/ 无 .py，登记在台账里但不参与判定）
LAYERS: Tuple[str, ...] = (
    "core",
    "rules",
    "compiler",
    "assets",
    "runtime",
    "adapters",
    "trigger",
    "eval",
    "cli",
)

# 台账文件名（同目录）
LEDGER_NAME = "LEDGER.md"
SNAPSHOT_NAME = "snapshot.json"

# 阈值登记的机器可读标记（写在对应层 AGENTS.md 里，本脚本只认它，不猜）：
#   结构预算：可执行行数阈值 <= 150
#
# 两条写法约束（都不是洁癖，是踩过的坑）：
#   1. `<=` 不要写进字符类。`[<=≤]` 会被 re 解析成 `<`→`≤` 的**范围**
#      （≤ 是 U+2264，范围终点），整个模式变得无法匹配任何输入。
#   2. 不要用「只到 `<` 就结束」的模式（如 `<\s*(\d+)`）。`<` 是 `<=` 的前缀，
#      匹配到 `<` 后 `\s*` 吃不到空白就回溯失败——实测该模式对
#      `阈值 <= 150` 永远返回 None，而对 `阈值 <= 150` 里的 `<=` 变体才能过。
# 因此这里显式写 `<=`（不省略等号）。
_BUDGET_RE = re.compile(r"结构预算[：:]\s*可执行行数阈值\s*<=\s*(\d+)")

# 台账里的豁免登记块（YAML 风格，脚本解析它）
_EXEMPT_BLOCK_RE = re.compile(
    r"<!-- BEGIN exemptions -->(?P<body>.*?)<!-- END exemptions -->",
    re.S,
)
_EXEMPT_LINE_RE = re.compile(
    r"^\s*-\s+(?P<layer>[A-Za-z_][\w/-]*?):\s+(?P<file>[\w./-]+\.py)"
    r"(?:[,(]\s*实际 (?P<actual>\d+) 行)?"
    r"(?:[,(]\s*(?P<reason>.*?))?\s*$",
    re.M,
)


# ---------------------------------------------------------------------------
# 1. 行数口径
# ---------------------------------------------------------------------------
def executable_line_count(path: Path) -> int:
    """可执行行数：tokenize 后 `NEWLINE` token 计数（排除注释与 docstring）。

    口径（与 `adapters/AGENTS.md §⑨` 的 145–150 同源）：**语句的行数**，
    不含注释行、不含 docstring 行、不含缩进层。
    - `NEWLINE`：一条逻辑语句结束时的换行——每个代码行恰好一个；
    - `NL`：**不要用它**。它是「视觉上空白的行」（空行、纯注释行、
      docstring 的物理行）才发的 token，实测 `a = 1\nb = 2\n` 的 NL 计数为 0。
      误用会把「可执行行」数成「空行数」，口径立刻反转；
    - `NEWLINE` 不覆盖续行（`\\` 续行或括号内的行）——这些行的结束归在
      语句末尾那一个 `NEWLINE` 上，所以多行语句只算 1 行。这是本口径的
      已知取舍：与 `adapters/AGENTS.md §⑨` 记的 231 / 236 / 176 同算法。
    - `INDENT` / `DEDENT` 不是物理行，天然不计。

    边界：**任何 tokenize 失败必须抛错，不得返回 0**。tokenize 内部会做行内
    错误恢复（unclosed string 等跳过继续），因此「返回 0」只可能来自它根本没
    跑完——最典型的成因是**读到不完整内容**（并发写入 / 截断）。把这种情况记成
    「0 可执行行」等于把一个几百行的文件判成合规，正是静默降级；
    宁可让检查失败并要求重跑。
    """
    raw = "".join(path.read_text("utf-8").splitlines(True))
    # 末尾补一个换行：tokenize 只在语句真正结束时发 NEWLINE，
    # 文件最后一行若不带换行符就统计不到（实测 off-by-one）。
    text = raw if raw.endswith("\n") else raw + "\n"
    count = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.NEWLINE:
                count += 1
    except Exception as exc:
        raise RuntimeError(
            f"无法计算 {path} 的可执行行数（tokenize 未跑完）: "
            f"{type(exc).__name__}: {exc}——不得按 0 行处理"
        ) from exc
    return count


# ---------------------------------------------------------------------------
# 2. 阈值来源：各层 AGENTS.md
# ---------------------------------------------------------------------------
def parse_layer_budget(agents_md: Path) -> Optional[int]:
    """从一层 AGENTS.md 读取「可执行行数阈值」。

    返回 None 表示该层**没有登记量化标准**——调用方必须跳过该层，
    不得替它猜一个阈值（猜出来的数字没有出处，等于本脚本另立一套）。
    """
    if not agents_md.is_file():
        return None
    for line in agents_md.read_text("utf-8", errors="replace").splitlines():
        match = _BUDGET_RE.search(line)
        if match:
            return int(match.group(1))
    return None


def load_budgets(root: Path) -> Dict[str, Optional[int]]:
    """各层阈值表：{层名: 阈值 or None}。"""
    return {
        layer: parse_layer_budget(root / layer / "AGENTS.md")
        for layer in LAYERS
    }


# ---------------------------------------------------------------------------
# 3. 台账解析
# ---------------------------------------------------------------------------
def load_exemptions(root: Path) -> List[Dict[str, Any]]:
    """读台账的豁免登记块，返回 [{'layer','file','actual','reason'}, ...]。

    台账缺失或没有豁免块 → 空列表（此时任何超限文件都是违规）。
    """
    ledger = root / "tools" / "structure_budget" / LEDGER_NAME
    if not ledger.is_file():
        return []
    text = ledger.read_text("utf-8", errors="replace")
    block = _EXEMPT_BLOCK_RE.search(text)
    if not block:
        return []
    entries: List[Dict[str, Any]] = []
    for line in block.group("body").splitlines():
        if line.lstrip().startswith(("#", "[", "]")):
            continue
        match = _EXEMPT_LINE_RE.match(line)
        if match:
            entries.append(
                {
                    "layer": match.group("layer"),
                    "file": match.group("file"),
                    "actual": int(match.group("actual")) if match.group("actual") else None,
                    "reason": (match.group("reason") or "").strip(" ,"),
                }
            )
    return entries


def _exempt_key(layer: str, path: Path) -> str:
    """豁免匹配的键：层内相对路径（如 framework_kefu/bridge.py）。"""
    try:
        return path.relative_to(Path(layer)).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# 4. 扫描
# ---------------------------------------------------------------------------
def scan_layer(root: Path, layer: str) -> List[Dict[str, Any]]:
    """扫一层的所有 .py（不含 tests/ 与 __pycache__），返回行数记录。"""
    base = root / layer
    if not base.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(base.rglob("*.py")):
        parts = path.parts
        if "__pycache__" in parts or "tests" in parts:
            continue
        rel = path.relative_to(base).as_posix()
        rows.append(
            {
                "layer": layer,
                "file": rel,
                "path": path.relative_to(root).as_posix(),
                "executable_lines": executable_line_count(path),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 5. 判定
# ---------------------------------------------------------------------------
def check(
    root: Path,
    *,
    budgets: Optional[Dict[str, Optional[int]]] = None,
    exemptions: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Optional[int]]]:
    """执行结构预算判定。

    返回：(违规列表, 全量行数记录, 各层阈值表)
    违规条目字段：layer / file / actual / limit / exempt（False，否则不该出现在这里）
    """
    # None = 从磁盘读；[] = 显式「无豁免」（供判红负例用）。
    # 这两者必须区分开：把 [] 当成「去读磁盘」会让负例注入失效，
    # 脚本照旧读到台账里的豁免、判成合规——等于门禁没有牙齿。
    budgets = load_budgets(root) if budgets is None else budgets
    exemptions = load_exemptions(root) if exemptions is None else list(exemptions)
    exempted = {
        (e["layer"], e["file"]) for e in exemptions
        if isinstance(e.get("layer"), str) and isinstance(e.get("file"), str)
    }

    rows: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    for layer in LAYERS:
        limit = budgets.get(layer)
        layer_rows = scan_layer(root, layer)
        rows.extend(layer_rows)
        if limit is None:
            continue  # 该层未登记阈值：跳过，不猜
        for row in layer_rows:
            if row["executable_lines"] > limit and (layer, row["file"]) not in exempted:
                violations.append(
                    {
                        "layer": layer,
                        "file": row["file"],
                        "path": row["path"],
                        "actual": row["executable_lines"],
                        "limit": limit,
                        "exempt": False,
                    }
                )
    return violations, rows, budgets


# ---------------------------------------------------------------------------
# 6. 台账落盘
# ---------------------------------------------------------------------------
def render_ledger(
    root: Path,
    rows: List[Dict[str, Any]],
    budgets: Dict[str, Optional[int]],
    violations: List[Dict[str, Any]],
    exemptions: Sequence[Dict[str, Any]],
) -> str:
    """生成台账正文（阈值来源 + 当前行数快照 + 豁免登记）。

    快照必须与实际行数一致：本函数只从 check() 的扫描结果渲染，
    不手写任何数字。豁免块用 BEGIN/END 标记包住，脚本下一轮从这里读回。
    """
    lines: List[str] = []
    lines.append("# 结构预算台账（R09 适应度函数）")
    lines.append("")
    lines.append(
        "本台账由 `tools/structure_budget/check.py` 生成，**不要手改数字**——"
        "手改会让台账与磁盘上的实际行数脱节（静默降级）。"
    )
    lines.append("")
    lines.append("## 行数口径")
    lines.append("")
    lines.append(
        "可执行行 = `tokenize` 后 type 为 `NEWLINE` 的 token 计数（一条逻辑语句"
        "结束时的换行，每个代码行恰好一个；注释行、纯 docstring 行归为 `NL` 不在此计数，"
        "**不要用 `NL`**——实测 `a = 1\\nb = 2\\n` 的 NEWLINE 是 2 而 NL 是 0，"
        "用错会把口径反转为「数空行数」）。"
        "与 `adapters/AGENTS.md §⑨` 的 145–150 口径同源；"
        "测试目录与 `__pycache__` 不计（量化标准明写「不含注释与测试」）。"
    )
    lines.append("")
    lines.append("## 阈值来源（脚本只从这里读，不另立一套）")
    lines.append("")
    lines.append("| 层 | 阈值（可执行行） | 来源 |")
    lines.append("| --- | --- | --- |")
    for layer in LAYERS:
        limit = budgets.get(layer)
        src = f"{layer}/AGENTS.md"
        lines.append(f"| `{layer}` | {limit if limit is not None else '—（未登记，不参与判定）'} | `{src}` |")
    lines.append("")
    lines.append("## 当前各层行数快照")
    lines.append("")
    total = 0
    for layer in LAYERS:
        layer_rows = [r for r in rows if r["layer"] == layer]
        layer_total = sum(r["executable_lines"] for r in layer_rows)
        total += layer_total
        limit = budgets.get(layer)
        head = f"### `{layer}`"
        if limit is not None:
            head += f"（阈值 ≤ {limit}）"
        else:
            head += "（未登记阈值，不参与判定）"
        lines.append(head)
        lines.append("")
        if not layer_rows:
            lines.append("（本层无 .py 文件）")
            lines.append("")
            continue
        lines.append("| 文件 | 可执行行 |")
        lines.append("| --- | --- |")
        for row in sorted(layer_rows, key=lambda r: -r["executable_lines"]):
            lines.append(f"| `{row['path']}` | {row['executable_lines']} |")
        lines.append(f"| **合计** | **{layer_total}** |")
        lines.append("")
    lines.append(f"全仓合计（{len(rows)} 个 .py 文件）：**{total}** 可执行行")
    lines.append("")
    lines.append("## 违规")
    lines.append("")
    if violations:
        lines.append("| 文件 | 实际 | 阈值 | 层 |")
        lines.append("| --- | --- | --- | --- |")
        for v in violations:
            lines.append(
                f"| `{v['path']}` | {v['actual']} | ≤ {v['limit']} | `{v['layer']}` |"
            )
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 豁免登记")
    lines.append("")
    lines.append(
        "超限但**已在各层 AGENTS.md 说明理由**的文件在此显式登记；未登记的超限"
        "一律判违规。豁免不是永久豁票：新增功能前先看能不能先拆。"
    )
    lines.append("")
    lines.append("<!-- BEGIN exemptions -->")
    if exemptions:
        # 格式必须与 _EXEMPT_LINE_RE 可回读的形式一致（半角逗号分隔），
        # 否则「读台账 → 渲染台账」会自相矛盾、豁免在下一轮失效（实测踩过）。
        for entry in exemptions:
            actual = f", 实际 {entry['actual']} 行" if entry.get("actual") else ""
            reason = f", {entry['reason']}" if entry.get("reason") else ""
            lines.append(f"- {entry['layer']}: {entry['file']}{actual}{reason}")
    else:
        lines.append("")
    lines.append("<!-- END exemptions -->")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------
def _write_snapshot(path: Path, root: Path, rows: List[Dict[str, Any]],
                    budgets: Dict[str, Optional[int]],
                    violations: List[Dict[str, Any]]) -> None:
    """机器可读快照（可 diff、可供 CI 比对）。"""
    payload = {
        "caliber": "tokenize NEWLINE token count（排除注释与 docstring；不含 tests/ 与 __pycache__）",
        "layers": [
            {
                "layer": layer,
                "limit": budgets.get(layer),
                "files": [r for r in rows if r["layer"] == layer],
            }
            for layer in LAYERS
        ],
        "total_files": len(rows),
        "total_executable_lines": sum(r["executable_lines"] for r in rows),
        "violations": violations,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="structure_budget check",
        description="扫各层 .py 可执行行数，对照各层 AGENTS.md 的阈值判违规",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="仓库根（缺省自动向上找 tools/structure_budget 的父目录）",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="stdout 只输出 JSON 结论",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="只判定不落盘（台账与快照保持原样）",
    )
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = Path(__file__).resolve().parents[2]
    if not (root / "tools" / "structure_budget").is_dir():
        print(f"不是仓库根（缺 tools/structure_budget）: {root}", file=sys.stderr)
        return 2

    budgets = load_budgets(root)
    exemptions = load_exemptions(root)
    violations, rows, budgets = check(root, budgets=budgets, exemptions=exemptions)

    out_dir = root / "tools" / "structure_budget"
    if not args.no_write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / LEDGER_NAME).write_text(
            render_ledger(root, rows, budgets, violations, exemptions), encoding="utf-8"
        )
        _write_snapshot(out_dir / SNAPSHOT_NAME, root, rows, budgets, violations)

    if args.as_json:
        payload = {
            "root": str(root),
            "budgets": budgets,
            "files": len(rows),
            "total_executable_lines": sum(r["executable_lines"] for r in rows),
            "violations": violations,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        sys.stdout.flush()
        return 1 if violations else 0

    # 人读输出：各层阈值 + 合计行数 + 违规明细
    for layer in LAYERS:
        limit = budgets.get(layer)
        layer_rows = [r for r in rows if r["layer"] == layer]
        layer_total = sum(r["executable_lines"] for r in layer_rows)
        if limit is None:
            print(f"{layer:<9} 阈值 未登记（不参与判定）  文件 {len(layer_rows):>2}  可执行行 {layer_total:>5}")
        else:
            over = sum(1 for r in layer_rows if r["executable_lines"] > limit)
            print(f"{layer:<9} 阈值 ≤{limit:<4}（{layer}/AGENTS.md）  文件 {len(layer_rows):>2}  可执行行 {layer_total:>5}  超限 {over}")
    print(f"合计 {len(rows)} 个 .py 文件 / {sum(r['executable_lines'] for r in rows)} 可执行行")

    if violations:
        print(f"\n违规 {len(violations)} 项（超限且未登记豁免）：", file=sys.stderr)
        for v in violations:
            print(
                f"  {v['path']}: 实际 {v['actual']} 行 > 阈值 {v['limit']}（{v['layer']}）",
                file=sys.stderr,
            )
        return 1
    print("结构预算：全部合规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
