#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_no_machine_paths.py —— 发布树绝对路径门禁（只读，零第三方依赖）。

为什么要有这个脚本
------------------
本仓的「不得出现本机绝对路径」这条规矩，此前**只以一段 grep 正则的形式活在
文档里**（写在那张卡的「验收标准」一节）。判据的正则会扫到写判据的那一行，
于是任何扫描都恒非零 —— 这不是泄漏，是**判据自指**；后果是这条门禁事实上
无法自动化，只能靠人记。

本脚本把判据**收进代码**：文档里只留 `python3 tools/check_no_machine_paths.py`
这一句调用，正则与前缀一律不出现在被扫描的文档中。豁免走**显式标注**
（`# scan-exempt: <理由>`），不留「悄悄不查」的口子。

只读保证
--------
本脚本**不写任何文件**（`--json` 也只输出到 stdout）。它是门禁，不是工具；
门禁改被查物就等于自证。

前缀怎么来（三类，见卡面「判据」条）
------------------------------------
1. `machine-home`     —— 本机家目录，取 `$HOME` / `Path.home()`，**不写死**。
2. `volume-mount`     —— 本仓所在的外接卷挂载点，从仓根**向上推导**（第一个
                         `os.path.ismount()` 的祖先），**不写死**。
3. `boot-tmp`         —— 启动卷上的本机临时目录，取 `tempfile.gettempdir()`
                         及其 `realpath` 变体（macOS 上 `/var` → `/private/var`），
                         **不写死**。
4. `root:*`           —— 上列三类**取不到具体值**时的兜底根前缀（写在脚本常量里，
                         是**通用根**，不是本机路径）。用于兜住「换了机器就漏判」
                         的场景；命中它不等于泄漏本机身份，但仍是发布树里不该有的
                         绝对路径，按违规记（分级见下）。

分级：两条都判红，但报告里分开
------------------------------
- `machine-path`：命中上面 1/2/3 类**本机推导前缀** —— 真泄漏，最严重。
- `root-literal`：只命中 `root:*` 通用根 —— 多为「判据自指」的存档段落。

两条都计入 `violations`、都非零退出（`docs/13 §八#22`：假绿比红更坏）；
分级只为了让人知道**先清哪一批**。

环境变量
--------
- `VOX_SCAN_EXTRA_PREFIXES`  —— 追加前缀，`os.pathsep`（`:`）分隔。用于把
  「本仓自己没派生出来、但同样属于本机」的路径补进来（例如第二块卷）。
- `VOX_SCAN_ROOT`           —— 仓根覆盖（缺省由本文件位置向上推）。

用法
----
    python3 tools/check_no_machine_paths.py            # 人读 + 机器读都靠这一条
    python3 tools/check_no_machine_paths.py --json     # 同上（显式声明，输出恒为 JSON）
    python3 tools/check_no_machine_paths.py --root .   # 覆盖仓根

退出码（对齐 `docs/08-CLI口径.md` 的形状）
------------------------------------------
    0  通过（violations 为空）
    1  有违规
    2  用法/环境错误（例如仓里没有 git、路径不存在）——**不静默返回 0**：
       取不到文件清单就等于没查，假绿比红更坏。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# 通用根前缀（兜底）。**这些不是本机路径**——是跨机器恒定的那一层根。
# 本机具体前缀一律靠推导，见 build_prefixes()。
ROOT_PREFIXES = (
    ("root:user-home", "/Users/"),
    ("root:external-volume", "/Volumes/"),
    ("root:per-user-tmp", "/var/folders/"),
    ("root:per-user-tmp-canonical", "/private/var/folders/"),
)

# 本仓自有的机器推导前缀类别（用来在报告里区分 `machine-path`）
MACHINE_SOURCES = ("machine-home", "volume-mount", "boot-tmp")

SELF_EXEMPT_MARKER = "# scan-exempt:"  # 显式豁免标记：行内出现即豁免
MAX_FILE_BYTES = 16 * 1024 * 1024       # 超过就不读，但**在输出里点名**（不静默跳过）
SELF_RELATIVE = "tools/check_no_machine_paths.py"


# --------------------------------------------------------------------------
# 前缀装配
# --------------------------------------------------------------------------
def _mount_ancestor(start: Path):
    """从 `start` 向上找第一个挂载点（即外接卷的挂载根）。

    刻意**不写死任何卷名**：换机器、换卷挂载点都自动跟着变。
    """
    cur = start.resolve()
    while True:
        parent = cur.parent
        if parent == cur:
            return None
        if os.path.ismount(str(parent)):
            return parent
        cur = parent


def build_prefixes(root: Path):
    """返回 [(label, prefix, class)]，class ∈ {machine-path, root-literal}。

    本机前缀优先去重：`/Volumes/macos` 与通用 `/Volumes/` 同时命中时，
    先按最长前缀报（否则「真泄漏」会被「通用根」这条降级掉）。
    """
    machine = {}

    home = os.environ.get("HOME") or ""
    if home:
        machine[home.rstrip("/") + "/"] = "machine-home"
    try:
        ph = str(Path.home())
        if ph and ph != "/":
            machine[ph.rstrip("/") + "/"] = "machine-home"
    except (RuntimeError, OSError):
        pass

    mount = _mount_ancestor(root)
    if mount is not None:
        machine[str(mount).rstrip("/") + "/"] = "volume-mount"

    for tmp in {tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}:
        if tmp and tmp != "/":
            machine[tmp.rstrip("/") + "/"] = "boot-tmp"

    prefixes = []
    for prefix, label in machine.items():
        prefixes.append((label, prefix, "machine-path"))
    for label, prefix in ROOT_PREFIXES:
        prefixes.append((label, prefix, "root-literal"))

    extra = os.environ.get("VOX_SCAN_EXTRA_PREFIXES", "")
    for idx, item in enumerate(p for p in extra.split(os.pathsep) if p.strip()):
        prefixes.append((f"extra:{idx}", item.strip().rstrip("/") + "/", "machine-path"))

    # 长前缀在前：命中多条时取最具体的那条报，避免把真泄漏降级成通用根
    prefixes.sort(key=lambda t: len(t[1]), reverse=True)
    return prefixes


# --------------------------------------------------------------------------
# 文件清单：入库 + 未跟踪（尊重 .gitignore）
# --------------------------------------------------------------------------
def list_files(root: Path):
    """`git ls-files -co --exclude-standard -z`：已跟踪 + 未跟踪且未被 ignore。

    为什么用 git 而不是 os.walk：`.gitignore` 是本仓对「什么算发布树内容」的
    既有裁定（`.zcode/` `*.log` `data/` 等本地产物明确不进仓），os.walk 会把
    那些也扫进来，那是「拿门禁当噪音发生器」。git 是真源，CI 里也一致。

    git 不可用 / 报错 → 抛错（由 main 收成退出码 2）。**绝不静默返回空清单**。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:  # git 根本没装
        raise RuntimeError(f"无法调用 git（{exc}）——取不到文件清单就等于没查") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"git ls-files 失败 rc={proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    names = [n for n in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
    return [root / n for n in names]


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------
def scan_file(path: Path, root: Path, prefixes):
    """返回 (violations, skipped_reason)。violations 里的 excerpt 已做前缀脱敏。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [], f"unreadable: {exc.__class__.__name__}"
    if len(raw) > MAX_FILE_BYTES:
        return [], f"oversize: {len(raw)}B > {MAX_FILE_BYTES}B"
    if b"\x00" in raw:
        return [], "binary"
    text = raw.decode("utf-8", "replace")
    rel = path.relative_to(root).as_posix()

    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if SELF_EXEMPT_MARKER in line:
            continue  # 显式豁免：行内写明理由，审计可追
        for label, prefix, cls in prefixes:
            idx = line.find(prefix)
            if idx < 0:
                continue
            head, tail = line[:idx], line[idx + len(prefix):]
            out.append(
                {
                    "file": rel,
                    "line": lineno,
                    "col": idx + 1,
                    "prefix_label": label,
                    "prefix_class": cls,
                    # 脱敏：只回显命中的那一个前缀标签，不把机器路径再吐一遍
                    "excerpt": head + "<PREFIX:" + label + ">" + tail,
                }
            )
            break  # 一行只报最具体的那条前缀
    return out, None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="check_no_machine_paths.py",
        description="发布树绝对路径门禁（只读；输出恒为 JSON 到 stdout）",
    )
    ap.add_argument("--json", action="store_true",
                    help="显式声明输出格式（恒为 JSON，保留此开关只为命令行自解释）")
    ap.add_argument("--root", default=os.environ.get("VOX_SCAN_ROOT", ""),
                    help="仓根（缺省由本文件位置向上推）")
    args = ap.parse_args(argv)

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = Path(__file__).resolve().parents[1]
    if not root.is_dir():
        print(json.dumps({"tool": "check_no_machine_paths", "passed": False,
                          "error": f"仓根不存在：{args.root or '(推导)'}"},
                         ensure_ascii=False, indent=2))
        return 2

    prefixes = build_prefixes(root)
    try:
        files = list_files(root)
    except RuntimeError as exc:
        print(json.dumps({"tool": "check_no_machine_paths", "passed": False,
                          "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    self_path = Path(__file__).resolve()
    violations, skipped, scanned = [], [], 0
    for path in files:
        if not path.is_file() or path.resolve() == self_path:
            continue  # 脚本自己豁免：判据的载体不能被自己的判据判红
        scanned += 1
        found, reason = scan_file(path, root, prefixes)
        violations.extend(found)
        if reason:
            skipped.append({"file": path.relative_to(root).as_posix(), "reason": reason})

    by_class = {}
    for v in violations:
        by_class[v["prefix_class"]] = by_class.get(v["prefix_class"], 0) + 1

    report = {
        "tool": "check_no_machine_paths",
        "root": str(root),
        "passed": not violations,
        "scanned_files": scanned,
        "listed_files": len(files),
        "violation_count": len(violations),
        "violations_by_class": by_class,
        "violations": violations,
        "skipped": skipped,
        "prefix_labels": sorted({label for label, _, _ in prefixes}),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
