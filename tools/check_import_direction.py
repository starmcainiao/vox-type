#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_import_direction.py —— import 方向闸门（只读，零第三方依赖）

为什么
------
D3「适配器不改内核」与 D8「宿主无关」此前**只活在文档里**
（`docs/13 §四` 决策 8 + 项目级 `AGENTS.md §二`）：`tools/structure_budget/`
只管行数，这条纪律没有任何一行代码在守，靠人读代码评审。本脚本把它落成
可复跑的判据。只判两类：

  R1  frozen-import     冻结区文件（`core/ rules/ compiler/ assets/ runtime/
                        eval/ cli/`）出现 `import` / `from` 扩展区
                        （`adapters/ packs/ trigger/`）—— 内核反向依赖业务侧，
                        这正是 D3 与 D8 的直接反面（「换宿主 = 新写一个适配器，
                        compiler/ assets/ runtime/ 一行不改」）。
  R2  private-name      跨包 `from x import _y` —— 跨包引用下划线私有名。
                        `adapters/AGENTS.md` §⑤ 写的是「边界仍硬：不得 import
                        内部私有名；只走公开 `__all__`」。

判据与例外都必须住在脚本里（T58/T60 的判据自指）。正则与前缀一律不写在被扫描
的文档中——写在文档里的判据会扫到写它的那一行，于是任何扫描恒非零，门禁事实上
不存在。文档里只准留一句调用。

判据实现（本卡禁止用正则扫 import 行）
--------------------------------------
用 `ast` 解析真实语法树。正则分不清 `import adapters` 与字符串里的 `"import"`，
也不分注释；AST 里字符串与注释根本不进节点树，天然不误判。

相对导入**解析出目标后再判**：`ast.ImportFrom.level` 给出相对层级，
按它从导入方所在包向上推导目标模块。包内（同包或其子包）的私有引用**不判**
——包内互访私有是包自己的实现细节，不是跨包越界；跨包的相对导入仍判 R2。

范围（**这一条必须写在明面上，不允许是静默漏判**）
-------------------------------------------------
R1 与 R2 **都不在 `tests/` 目录内判**。判据的边界是**产品的依赖方向**，
不是「文件里出现了 import 这个动作」：

- R2：测试要断言内部实现，跨包引用下划线私有名正是它的正当手段
  （`tools/README.md` 明文要求「归一化与单句判据一律 import 产品代码，不复制」）。
- R1：测试要拿**具体适配器**当被测对象（`compiler/tests` 造 `MacSayTts`、
  `eval/tests` 造 `OmlxAsr`、`runtime/tests` 造 `MacSayTts`）。那是**测试夹具**，
  不是产品代码依赖扩展区——夹具依赖被测对象的实现，是测试的正当形态。

真实口径因此是「**排除 `tests/` 后是否还有违规**」。本仓非测试产品代码的
R1 违规数为 **0**，那才是 D3「适配器不改内核」真正要守的东西。
与 `tools/structure_budget/check.py` 同口径——它也排除 `tests/`。

（R1 早期版本不做这个豁免，一挂上就抓出 4 处**全在 `tests/` 内**的夹具 import。
那是判据没区分产品与夹具，不是代码有错。**判据错就改判据**，不是把代码改成迎合判据。）

**但「不判」不等于「不查」**：测试目录内本该命中的条目全部收进结果的
`test_scoped` 字段（带 file:line 与原文），人读输出里也打印条数。
否则「把判据挂上、实际什么都没扫到」就无从察觉——本仓已在 `check_no_machine_paths`
上因这类假绿吃过亏。

输出定位到 `file:line`（取语句的**结束行**：多行 import 的基线因此稳定）并附上
该行原文，直接能改，不必再回翻 AST。

基线
----
`--baseline <file>` 登记**已知例外**：已登记的命中记为「已登记例外」并豁免，
未登记的命中即违规。缺省基线是与本脚本同目录的 `import_direction_baseline.txt`
（缺省时按 `--root` 重定位到同位置的相对目录，不硬编码目录名）；**不存在视为
空表**——不是失败，而是「没有任何豁免」，任何违规都直接红（fail-closed）。

**为什么必须配基线而不是放宽判据**：当前仓已有一处既存破口
（`adapters/framework_kefu/hit_query.py` 的 `from adapters.textmatch.hit import
_sequence_candidates`，见 T63）。为了让 CI 挂上即绿而改判据，等于把纪律写宽——
那比没有门禁更坏（假绿）。本脚本的做法是**判据不动、例外显式登记并写明理由**：
破口仍然可见、可数、可追。

基线不可随便加（基线本身也在门禁之下，否则「往基线里随便塞一条」就是后门）：

  * 每条必须有**非空理由** —— 缺理由 → 退出 2（比违规更硬的失败：
    这是一条坏条目，不是一条登记）；
  * 每条必须能对上**真实存在的违规** —— 对不上的是「陈旧的 / 凭空写的」，
    退出 2（防「先塞一条基线，等它哪天变成违规」）；
  * 同键（rule + file + line）不可重复，重复 → 退出 2。

用法
----
    python3 tools/check_import_direction.py                 # 缺省：仓根 + 同目录基线
    python3 tools/check_import_direction.py --json          # 机器可读
    python3 tools/check_import_direction.py --root DIR      # 覆盖仓根（反向自证用）
    python3 tools/check_import_direction.py --self-test     # 反向自证（临时目录）

退出码
------
    0  通过（未登记的违规为空）
    1  存在**未登记**的违规
    2  用法 / 基线 / 解析错误 —— **不静默返回 0**：基线条目缺理由、基线条目
       与违规对不上、显式基线文件缺失、`--root` 不是目录、某文件解析失败，
       都算「门禁没牙齿」或「本该查而查不了」，取不到判定结果就等于没查。
       （与同族 `tools/check_no_machine_paths.py` 的 0/1/2 形状一致。）

只读保证
--------
主路径**不写任何文件**：只 `read_text` + 打印到 stdout/stderr。
唯一例外是 `--self-test`（反向自证）：它在 `tempfile.mkdtemp()` 里造一棵
假仓库树并在 `finally` 里 `shutil.rmtree` 删掉，**绝不在真仓内造脏**，
且若临时目录意外落在真仓内会直接中止（见 `run_self_tests` 的守卫）。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------
RULE_FROZEN_IMPORT = "frozen-import"
RULE_PRIVATE_NAME = "private-name"
RULES = (RULE_FROZEN_IMPORT, RULE_PRIVATE_NAME)

# 冻结区 = 内核（项目级 AGENTS.md §三：core/ rules/ compiler/ assets/ runtime/
# eval/ cli/ 全部 [冻]）。扩展区 = adapters/ packs/ trigger/（[扩]）。
# 「扩展区」这个字面量只出现在这里——判据自指。
FROZEN_LAYERS = ("core", "rules", "compiler", "assets", "runtime", "eval", "cli")
EXT_ROOTS = ("adapters", "packs", "trigger")

BASELINE_FILENAME = "import_direction_baseline.txt"
SELF_RELATIVE = "tools/check_import_direction.py"

# 跳过这些目录（不参与 import 方向判定）
SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist"})

# 测试目录名。**R2 在测试目录内不判**，理由见模块 docstring 的「范围」一节。
# 与 `tools/structure_budget/check.py` 同口径（它也排除 `tests/`）：结构类判据
# 管的是生产代码的形状，测试天生要够得着内部实现去断言它。
TEST_DIRNAMES = ("tests",)

# 基线条目：<rule> <file>:<line>  <理由>
# 位置格式**只接受带行号的 `<file>:<int>`**——没有行号的条目会被静默错配
# （把基线挂到同一文件的另一条违规上），那种错配看不见。
_BASELINE_LINE_RE = re.compile(
    r"^\s*(?P<rule>[a-z][a-z-]*)\s+(?P<file>[A-Za-z0-9_./-]+\.py):(?P<line>\d+)"
    r"(?:[ \t]+(?P<reason>.*))?$"
)

# ---------------------------------------------------------------------------
# 路径与模块名口径
# ---------------------------------------------------------------------------
def repo_relative(path: Path, root: Path) -> str:
    """仓内相对路径（POSIX 风格）；越界返回绝对路径。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def file_package(rel: str) -> list:
    """文件的**包含包**（点分段）。`a/b.py` → `["a"]`；`a/__init__.py` → `["a"]`。"""
    stem = rel[:-3] if rel.endswith(".py") else rel
    return stem.split("/")[:-1]


def in_root(rel: str, root_name: str) -> bool:
    """仓内路径（POSIX，`/` 分隔）是否位于名为 `root_name` 的顶层目录下。"""
    return rel.split("/")[0] == root_name


def dotted_top(dotted: str) -> str:
    """模块名（`.` 分隔）的顶层根名。`adapters.tts_macsay` → `adapters`。

    **不能复用 `in_root`**：它按 `/` 切分，而 import 语句里给的是点分模块名
    ——同一个名字两套分隔符，混用会让扩展区判据静默失效（自测 AC2a 已验证过）。
    """
    return dotted.split(".")[0]


def in_extension(dotted: str) -> bool:
    """模块名是否落在扩展区（`adapters.` / `packs.` / `trigger.` 开头）。"""
    return dotted_top(dotted) in EXT_ROOTS


def in_frozen(rel: str) -> bool:
    return any(in_root(rel, layer) for layer in FROZEN_LAYERS)


def is_test_file(rel: str) -> bool:
    """是否位于测试目录（任一级路径段名为 `tests`）。"""
    return any(part in TEST_DIRNAMES for part in rel.split("/"))


def same_or_child_module(source: list, importer_pkg: list) -> bool:
    """目标模块 `source` 是否与导入方所在包 `importer_pkg` 同包或其子包（=「包内」）。"""
    return bool(source) and source[: len(importer_pkg)] == importer_pkg


def resolve_module(rel: str, node: ast.ImportFrom) -> list:
    """把一个 `ast.ImportFrom` 解析成目标模块的点分段。

    绝对导入：`module` 即点分段。相对导入：`node.level >= 1`，从文件所在包
    向上跳 `level - 1` 级再拼 `module`。越界（点太多）抛 `ValueError`。
    """
    parts = node.module.split(".") if node.module else []
    level = getattr(node, "level", 0) or 0
    if level <= 0:
        return parts
    base_len = len(file_package(rel)) - (level - 1)
    if base_len < 0:
        raise ValueError(
            f"{rel}:{getattr(node, 'lineno', '?')} 相对导入层级 {level} 越界"
            f"（所在包只有 {len(file_package(rel))} 级）"
        )
    return file_package(rel)[:base_len] + parts


def statement_text(source_lines: list, node) -> str:
    """语句原文（跨行合并成一行，便于基线与报告直接引用）。"""
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None) or start
    if not start:
        return ""
    return " ".join(line.strip() for line in source_lines[start - 1: end])


def collect_python_files(root: Path) -> list:
    """仓内全部 `.py`（POSIX 相对路径，升序）。**含 tests/**——门禁扫得越宽越好，
    漏掉一处就是不查。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            out.append(repo_relative(Path(dirpath) / name, root))
    return sorted(out)


def default_baseline_path(root: Path) -> Path:
    """缺省基线位置 = 与本脚本**同目录**（由 `__file__` 推导，不硬编码目录名）。

    脚本位于仓内时返回 `root/<脚本相对目录>/<BASELINE_FILENAME>`；
    脚本不在该仓根下（例如 `--root` 指向临时副本）时回落到 `root/<basename>`。
    """
    here = Path(__file__).resolve()
    try:
        rel_dir = here.parent.relative_to(root)
    except ValueError:
        return root / BASELINE_FILENAME
    return root / rel_dir / BASELINE_FILENAME


# ---------------------------------------------------------------------------
# 基线
# ---------------------------------------------------------------------------
def parse_baseline(text: str, name: str = "baseline") -> list:
    """解析基线文本 → 条目列表。

    条目格式 `<rule> <file>:<line>  <理由>`；`#` 开头的行与空行忽略。
    **条目缺理由 / 判据名未知 / 格式不可解析 → 抛 `ValueError`**
    （不允许「往基线里随便加一条」）。
    """
    entries, problems = [], []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _BASELINE_LINE_RE.match(raw)
        if not m:
            problems.append(f"{name}:{lineno} 格式不可解析：<rule> <file>:<line> <理由>")
            continue
        rule = m.group("rule")
        reason = (m.group("reason") or "").strip()
        if rule not in RULES:
            problems.append(f"{name}:{lineno} 未知判据名 {rule!r}（只认 {'/'.join(RULES)}）")
        if not reason:
            problems.append(f"{name}:{lineno} 缺理由字段：<rule> <file>:<line> <理由>")
        entries.append(
            {
                "rule": rule,
                "file": m.group("file"),
                "line": int(m.group("line")),
                "location": f"{m.group('file')}:{int(m.group('line'))}",
                "reason": reason,
            }
        )
    if problems:
        raise ValueError("基线条目不合格：\n  " + "\n  ".join(problems))
    return entries


def validate_baseline(entries: list, violations: list) -> list:
    """校验基线本身（**基线也在门禁之下**）；返回问题清单（空 = 合格）。

      * 条目缺理由 → 硬失败；
      * 条目与违规不配对（陈旧或凭空写的）→ 硬失败；
      * 同键（rule + file + line）重复 → 硬失败。
    """
    problems = []
    counts: dict = {}
    for v in violations:
        key = f"{v['rule']} {v['file']}:{v['line']}"
        counts[key] = counts.get(key, 0) + 1
    seen: dict = {}
    for e in entries:
        if not e.get("reason"):
            problems.append(f"基线条目 {e.get('location')} 缺理由字段")
            continue
        key = f"{e['rule']} {e['location']}"
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            problems.append(f"基线条目重复：{key}")
        if counts.get(key, 0) < seen[key]:
            problems.append(f"基线条目与违规不配对（陈旧或凭空）：{key}")
    return problems


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def scan_one(rel: str, root: Path, self_path: Path) -> tuple:
    """扫一个文件，返回 (违规列表, 错误列表)。"""
    path = root / rel
    if path.resolve() == self_path:
        return [], []  # 脚本自己：判据的载体不能被自己的判据判红
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return [], [{"file": rel, "line": getattr(exc, "lineno", None),
                     "error": f"SyntaxError: {exc.msg}"}]
    lines = source.splitlines()
    importer_pkg = file_package(rel)
    out, errors = [], []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if not in_frozen(rel):
                continue
            hit = [alias.name for alias in node.names if in_extension(alias.name)]
            if hit:
                out.append(
                    {
                        "rule": RULE_FROZEN_IMPORT,
                        "file": rel,
                        "line": getattr(node, "end_lineno", node.lineno) or node.lineno,
                        "detail": "import " + ", ".join(hit),
                        "text": statement_text(lines, node),
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            priv = [
                alias.name for alias in node.names
                if isinstance(alias, ast.alias) and alias.name.startswith("_")
            ]
            try:
                source_parts = resolve_module(rel, node)
            except ValueError as exc:
                errors.append({"file": rel, "line": node.lineno, "error": str(exc)})
                continue
            dotted = ".".join(p for p in source_parts if p)
            line = getattr(node, "end_lineno", node.lineno) or node.lineno

            # R1：冻结区 → 扩展区
            if in_frozen(rel) and in_extension(dotted):
                out.append(
                    {
                        "rule": RULE_FROZEN_IMPORT,
                        "file": rel,
                        "line": line,
                        "detail": f"from {dotted} import " + ", ".join(
                            a.name for a in node.names if isinstance(a, ast.alias)
                        ),
                        "text": statement_text(lines, node),
                    }
                )
            # R2：跨包私有名（包内不判）
            if priv and not same_or_child_module(source_parts, importer_pkg):
                out.append(
                    {
                        "rule": RULE_PRIVATE_NAME,
                        "file": rel,
                        "line": line,
                        "detail": f"from {dotted} import " + ", ".join(priv),
                        "text": statement_text(lines, node),
                    }
                )
    return out, errors


def scan(root: Path, baseline: str | None = None) -> dict:
    """执行判定，返回机器可读结果字典（**不打印、不写文件**）。

    `baseline` 为基线文件路径；`None` → 用缺省位置；该文件不存在 → 视为空表。
    """
    entries: list = []
    baseline_path: str | None = None
    if baseline is not None:
        bpath = Path(baseline).resolve()
        if not bpath.is_file():
            raise FileNotFoundError(f"基线文件不存在：{baseline}")
        entries = parse_baseline(bpath.read_text(encoding="utf-8", errors="replace"),
                                 name=bpath.as_posix())
        baseline_path = str(bpath)
    else:
        bpath = default_baseline_path(root)
        if bpath.is_file():
            entries = parse_baseline(bpath.read_text(encoding="utf-8", errors="replace"),
                                     name=bpath.as_posix())
            baseline_path = str(bpath)

    self_path = Path(__file__).resolve()
    py_files = collect_python_files(root)
    if not py_files:
        raise RuntimeError(f"在 {root} 下找不到任何 .py 文件——取不到扫描对象就等于没查")

    # 冻结区为空 → R1 自动退化为永不命中，必须硬失败而不是静默不查
    if not any(in_frozen(rel) for rel in py_files):
        raise RuntimeError(
            f"{root} 下没有任何冻结区文件（{'/'.join(FROZEN_LAYERS)}/）——"
            "判据 frozen-import 将永不命中，不得按通过处理"
        )

    violations, test_scoped, parse_errors = [], [], []
    for rel in py_files:
        found, errors = scan_one(rel, root, self_path)
        for v in found:
            # 两条规则在测试目录内一律不判（见模块 docstring「范围」）：收进
            # test_scoped 而不是丢弃——「不判」不等于「不查」，条数必须看得见。
            #
            # 判据的边界是「产品的依赖方向」，不是「文件里出现了 import 这个动作」。
            # 测试要拿具体适配器当被测对象（compiler/tests 造 MacSayTts、
            # eval/tests 造 OmlxAsr），那是**测试夹具**，不是产品代码依赖扩展区。
            # 真实口径按「排除 tests/ 后是否还有违规」判定——本仓非测试产品代码
            # 的 frozen-import 违规数为 0，那才是 D3 真正要守的东西。
            #
            # （R1 早期版本不做这个豁免，抓出 4 处全在 tests/ 内的夹具 import，
            #  是判据没区分产品与夹具，不是代码有错。判据错就改判据。）
            if is_test_file(rel):
                test_scoped.append(v)
            else:
                violations.append(v)
        parse_errors.extend(errors)

    baseline_problems = validate_baseline(entries, violations)
    counts: dict = {}
    for v in violations:
        key = f"{v['rule']} {v['file']}:{v['line']}"
        counts[key] = counts.get(key, 0) + 1

    used: dict = {f"{e['rule']} {e['location']}": 0 for e in entries}
    exempted, unregistered = [], []
    reason_by_key = {f"{e['rule']} {e['location']}": e["reason"] for e in entries}
    for v in violations:
        key = f"{v['rule']} {v['file']}:{v['line']}"
        if key in used and used[key] < counts.get(key, 0):
            used[key] += 1
            exempted.append({**{k: v[k] for k in ("rule", "file", "line", "detail")},
                             "reason": reason_by_key.get(key, "")})
        else:
            unregistered.append(v)

    return {
        "tool": "check_import_direction",
        "root": str(root),
        "passed": not unregistered and not baseline_problems,
        "scanned_files": len(py_files),
        "frozen_zone_layers": list(FROZEN_LAYERS),
        "extension_roots": list(EXT_ROOTS),
        "violations": sorted(violations, key=lambda v: (v["rule"], v["file"], v["line"])),
        "frozen_import_violations": sorted(
            [v for v in violations if v["rule"] == RULE_FROZEN_IMPORT],
            key=lambda v: (v["file"], v["line"])),
        "private_name_violations": sorted(
            [v for v in violations if v["rule"] == RULE_PRIVATE_NAME],
            key=lambda v: (v["file"], v["line"])),
        "unregistered": sorted(unregistered, key=lambda v: (v["rule"], v["file"], v["line"])),
        "exempted": sorted(exempted, key=lambda v: (v["rule"], v["file"], v["line"])),
        "test_scoped": sorted(test_scoped, key=lambda v: (v["file"], v["line"])),
        "test_scoped_count": len(test_scoped),
        "unregistered_count": len(unregistered),
        "exempted_count": len(exempted),
        "baseline": entries,
        "baseline_path": baseline_path,
        "baseline_count": len(entries),
        "baseline_problems": baseline_problems,
        "parse_errors": parse_errors,
    }


# ---------------------------------------------------------------------------
# 反向自证（AC2 / AC3）
# ---------------------------------------------------------------------------
def run_self_tests() -> int:
    """在临时目录里造假仓库做反向自证。

    临时树一律 `tempfile.mkdtemp()` + `finally` 删除，**不在真仓内造脏**。
    若临时目录意外落在真仓内（TMPDIR 被设成仓内），直接中止而不是继续。
    """
    repo_root = Path(__file__).resolve().parents[1]
    tmp = Path(tempfile.mkdtemp(prefix="vox-id-selftest-"))
    failures: list = []
    try:
        if str(tmp).startswith(str(repo_root) + os.sep) or tmp == repo_root:
            print(f"::error::临时目录落在真仓内，拒绝造脏：{tmp}", file=sys.stderr)
            return 2

        def check(name: str, cond: bool, detail: str = "") -> None:
            if cond:
                print(f"PASS  {name}")
            else:
                print(f"FAIL  {name}  {detail}")
                failures.append(name)

        def mk(rel: str, text: str) -> None:
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

        mk("runtime/__init__.py", "")
        mk("core/__init__.py", "")
        mk("adapters/__init__.py", "")
        mk("adapters/tts_macsay/__init__.py", "VOICE = 'A'\n")
        mk("core/base.py", "CONST = 1\n")

        # ── AC2a：冻结区 import 扩展区 → 必须红，且点名 file:line ──
        mk("runtime/boom.py", "import adapters.tts_macsay\n")
        r = scan(tmp)
        check("AC2a 冻结区 import 扩展区 → 未登记违规非空",
              r["unregistered_count"] == 1, f"实际 {r['unregistered']}")
        check("AC2a 判据未被豁免（baseline_problems 为空、exempted 为空）",
              not r["exempted"] and not r["baseline_problems"])
        check("AC2a 报告含 file:line",
              r["unregistered"] and r["unregistered"][0]["file"] == "runtime/boom.py"
              and r["unregistered"][0]["line"] == 1,
              f"实际 {[ (v['file'], v['line']) for v in r['unregistered'] ]}")
        check("AC2a 报告含该 import 语句原文",
              r["unregistered"] and "import adapters.tts_macsay" in r["unregistered"][0]["text"],
              f"实际 {r['unregistered'][0]['text'] if r['unregistered'] else ''}")

        # ── AC2b：跨包私有名 → 必须红（adapters/ 引 core/ 的私有名）──
        mk("core/pub.py", "_priv = 1\n")
        mk("adapters/a.py", "from core.pub import _priv\n")
        r2 = scan(tmp)
        priv = [v for v in r2["violations"] if v["rule"] == RULE_PRIVATE_NAME]
        check("AC2b 跨包私有名 → 违规",
              any(v["file"] == "adapters/a.py" and v["line"] == 1 for v in priv),
              f"实际 {priv}")
        check("AC2b 报告 detail 点出了被引的私有名",
              any("_priv" in v["detail"] for v in priv), f"实际 {priv}")

        # ── AC2c：同包私有名不判（包内互访是包自己的实现细节，不是跨包越界）──
        # 注意夹具选型：导入方与被引方必须**同一顶层包**才构成「同包」。
        mk("core/uses.py", "from core.pub import _priv\n")        # 同在 core → 不判
        mk("core/pkg/__init__.py", "_inner = 1\n")
        mk("core/pkg/mod.py", "from core.pkg import _inner\n")    # 同在 core → 不判
        r3 = scan(tmp)
        check("AC2c 同包私有名不判",
              not any(v["file"] in ("core/uses.py", "core/pkg/mod.py")
                      for v in r3["violations"]),
              f"实际 {[v for v in r3['violations'] if v['file'].startswith('core/')]}")

        # ── AC2d：AST 口径 —— 注释与字符串里的 "import" 不算命中 ──
        mk("core/lure.py", '# import adapters.tts_macsay  ← 注释\nS = "import adapters"\n')
        r4 = scan(tmp)
        check("AC2d 注释/字符串中的 import 不误判",
              not any(v["file"] == "core/lure.py" for v in r4["violations"]),
              f"实际 {[v for v in r4['violations'] if v['file'] == 'core/lure.py']}")

        # ── AC2e：相对导入跨包私有名 → 判 ──
        mk("core/sub/__init__.py", "")
        mk("core/sub/deep.py", "from ..pub import _priv\n")
        r5 = scan(tmp)
        check("AC2e 相对导入跨包私有名 → 违规",
              any(v["file"] == "core/sub/deep.py" for v in r5["violations"]),
              f"实际 {[v for v in r5['violations'] if v['file'] == 'core/sub/deep.py']}")

        # ── AC2f：多行 import 定位到结束行（基线靠它对齐，必须稳定）──
        mk("adapters/multi.py", "from core.pub import (\n    _priv,\n)\n")
        r6 = scan(tmp)
        multi = [v for v in r6["violations"] if v["file"] == "adapters/multi.py"]
        check("AC2f 多行 import 定位到结束行 (line=3)",
              bool(multi) and multi[0]["line"] == 3, f"实际 {multi}")
        check("AC2f 原文含全部导入名",
              bool(multi) and "_priv" in multi[0]["text"], f"实际 {multi}")

        # ── 基线小节：另起一棵只含 1 处违规的干净小树 ──
        # 上面那棵树已经累积了若干违规，用它来验「通过」会把断言搅浑。
        btree = tmp / "basetree"
        mk_bt = lambda rel, text: (btree / rel).parent.mkdir(parents=True, exist_ok=True) \
            or (btree / rel).write_text(text, encoding="utf-8")
        mk_bt("core/__init__.py", "")
        mk_bt("adapters/__init__.py", "")
        mk_bt("core/pub.py", "_priv = 1\n")
        mk_bt("adapters/a.py", "from core.pub import _priv\n")  # 唯一的违规

        # ── AC3a：基线条目缺理由 → 必须失败（不允许「往基线里随便加一条」）──
        bad = btree / "bad_baseline.txt"
        bad.write_text("private-name adapters/a.py:1\n", encoding="utf-8")
        try:
            parse_baseline(bad.read_text(encoding="utf-8"), name=str(bad))
            check("AC3a 缺理由条目 → 失败", False, "parse_baseline 未抛错（门禁没牙齿）")
        except ValueError as exc:
            check("AC3a 缺理由条目 → 失败", "缺理由字段" in str(exc), str(exc))

        # ── AC3a'：判据名写错 → 同样失败 ──
        badrule = btree / "badrule_baseline.txt"
        badrule.write_text("frozon-import adapters/a.py:1  拼错了判据名\n", encoding="utf-8")
        try:
            parse_baseline(badrule.read_text(encoding="utf-8"), name=str(badrule))
            check("AC3a' 未知判据名 → 失败", False, "未抛错")
        except ValueError as exc:
            check("AC3a' 未知判据名 → 失败", "未知判据名" in str(exc), str(exc))

        # ── AC3a''：位置不带行号 → 失败（否则基线会静默错配到同一文件别的行）──
        badloc = btree / "badloc_baseline.txt"
        badloc.write_text("private-name adapters/a.py  没有行号\n", encoding="utf-8")
        try:
            parse_baseline(badloc.read_text(encoding="utf-8"), name=str(badloc))
            check("AC3a'' 位置缺行号 → 失败", False, "未抛错")
        except ValueError as exc:
            check("AC3a'' 位置缺行号 → 失败", "格式不可解析" in str(exc), str(exc))

        # ── AC3b：带理由条目且与违规配对 → 已登记例外并豁免 → 通过 ──
        good = btree / "good_baseline.txt"
        good.write_text(
            "private-name adapters/a.py:1  T63 登记的既存破口（跨包引用下划线私有名）\n",
            encoding="utf-8",
        )
        r7 = scan(btree, baseline=str(good))
        check("AC3b 带理由条目 → 通过、记为已登记例外、且豁免条目带理由",
              r7["passed"] and r7["exempted_count"] == 1
              and r7["unregistered_count"] == 0
              and r7["exempted"][0]["reason"].startswith("T63"),
              f"passed={r7['passed']} exempted={r7['exempted_count']} "
              f"unregistered={r7['unregistered_count']} problems={r7['baseline_problems']}")

        # ── AC3c：基线条目与违规对不上（陈旧 / 凭空写的）→ 失败 ──
        stale = btree / "stale_baseline.txt"
        stale.write_text(
            "private-name adapters/a.py:99  理由写得很充分，但那一行根本没有违规\n",
            encoding="utf-8",
        )
        r8 = scan(btree, baseline=str(stale))
        check("AC3c 陈旧/凭空条目 → 不通过并点名",
              not r8["passed"] and bool(r8["baseline_problems"])
              and any("不配对" in p for p in r8["baseline_problems"]),
              f"problems={r8['baseline_problems']}")

        # ── AC3d：重复条目 → 失败 ──
        dup = btree / "dup_baseline.txt"
        dup.write_text(
            "private-name adapters/a.py:1  理由一\n"
            "private-name adapters/a.py:1  理由二\n",
            encoding="utf-8",
        )
        r9 = scan(btree, baseline=str(dup))
        check("AC3d 重复条目 → 不通过",
              not r9["passed"] and any("重复" in p for p in r9["baseline_problems"]),
              f"problems={r9['baseline_problems']}")

        # ── AC3e：显式基线文件缺失 → 硬失败，不静默按空表 ──
        try:
            scan(btree, baseline=str(btree / "never_existed.txt"))
            check("AC3e 显式基线缺失 → 失败", False, "未抛错")
        except FileNotFoundError:
            check("AC3e 显式基线缺失 → 失败", True)

        # ── AC3f：缺省基线不存在 → 空表（不失败），违规照常红 ──
        r10 = scan(btree)
        check("AC3f 缺省基线不存在 → 空表、不报错、违规照常报",
              r10["baseline_count"] == 0 and not r10["baseline_problems"]
              and r10["unregistered_count"] == 1,
              f"baseline_count={r10['baseline_count']} "
              f"unregistered={r10['unregistered_count']}")

        # ── AC3g：缺省基线存在（与脚本同目录口径）→ 条目生效 ──
        (btree / BASELINE_FILENAME).write_text(
            "private-name adapters/a.py:1  放在缺省位置的基线\n", encoding="utf-8"
        )
        r11 = scan(btree)
        check("AC3g 缺省位置的基线被读到并生效",
              r11["passed"] and r11["baseline_count"] == 1,
              f"passed={r11['passed']} baseline_count={r11['baseline_count']} "
              f"problems={r11['baseline_problems']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("FAILED self-test: " + ", ".join(failures))
        return 1
    print("PASS check_import_direction --self-test（全部通过）")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="check_import_direction.py",
        description="import 方向闸门（只读；判据收在代码里，文档只留调用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "判据：\n"
            "  frozen-import   冻结区（core/ rules/ compiler/ assets/ runtime/ eval/ cli/）\n"
            "                  import 扩展区（adapters/ packs/ trigger/）\n"
            "  private-name    跨包 `from x import _y`（下划线私有名）；包内不判\n"
            "基线：已登记的例外记为「已登记例外」并豁免；未登记的违规即失败。\n"
            "       基线条目必须有非空理由且与真实违规配对，否则退出 2。\n"
            "退出码：0 通过 / 1 有未登记违规 / 2 用法、基线或解析错误。\n"
        ),
    )
    ap.add_argument("--root", default=None,
                    help="仓根（缺省由本文件位置向上推）；反向自证可指向临时副本")
    ap.add_argument("--baseline", default=None,
                    help=f"基线文件（缺省 = 与本脚本同目录的 {BASELINE_FILENAME}；"
                         "缺省时不存在视为空表，显式传入却缺失则退出 2）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 到 stdout")
    ap.add_argument("--self-test", action="store_true",
                    help="在临时目录里做反向自证（AC2/AC3），不在真仓造脏")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return run_self_tests()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    if not root.is_dir():
        print(f"::error::--root 不是目录：{args.root}", file=sys.stderr)
        return 2

    try:
        result = scan(root, baseline=args.baseline)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2

    unregistered = result["unregistered"]
    exempted = result["exempted"]

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"scanned={result['scanned_files']} 违规={len(unregistered)} "
            f"已登记例外={len(exempted)}（基线 {result['baseline_count']} 条）"
        )
        for v in unregistered:
            print(f"{v['file']}:{v['line']}  [{v['rule']}] {v['detail']}")
            print(f"    {v['text']}")
        for v in exempted:
            print(f"已登记例外 {v['file']}:{v['line']}  [{v['rule']}] {v['detail']}")
            print(f"    理由：{v['reason']}")
        # R2 在测试目录内不判，但条数必须看得见（「不判」不等于「不查」）
        print(
            f"测试目录内跨包私有名（不判，仅登记 {result['test_scoped_count']} 处）："
            + ("无" if not result["test_scoped"]
               else ", ".join(f"{v['file']}:{v['line']}" for v in result["test_scoped"]))
        )
        for e in result["baseline_problems"]:
            print(f"::error::基线：{e}")
        for e in result["parse_errors"]:
            print(f"::error::解析失败 {e['file']}:{e.get('line')} {e['error']}")

    if result["baseline_problems"] or result["parse_errors"]:
        return 2
    return 0 if not unregistered else 1


if __name__ == "__main__":
    sys.exit(main())
