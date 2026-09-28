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

「没被检查的内容」不是一个零（2026-09-28 T60 必修①）
---------------------------------------------------
旧判据是「文件里含 NUL 就整文件跳过」——于是**在文件首字节塞一个 NUL**，
就能让整道红线静默失效：文件被记进 `skipped`（信息性字段，不影响
`passed`、不影响退出码），`violation_count` 仍是 0，CI 照样打印「PASS」。
一个字节换掉整道门禁，且没有任何信号。

现在判「真二进制」要**四条同时成立**（见 `classify_binary`），缺一即
**照扫不误**：把每行的 NUL 剥掉后按文本扫，违规照常计入 `violation_count`。

同时新增 JSON 字段 **`unscanned[]`**：凡是没有被逐行扫过的文件都在里面，
**每一项带 reason 与 detail（证据）**。它的长度就是「有多少内容没被检查」
这个数字——以前这个数字不存在（`skipped` 是信息性的），**没有数字的
「不查」就是无声的**。注意 `unscanned` 是**数组**（含证据），计数请取
`len()`；它**不参与判定**（真二进制本就不该按文本扫），但它必须始终存在。

**边界要说清**：魔数可伪造，所以这套判据**不是安全边界**，它把绕过面从
「随手加一个 NUL」缩到「刻意伪造文件头」；让跳过不再无声的是**证据 + 计数**，
不是扫描本身（详见 `_NON_TEXT` 上方的注释）。

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

# 兜底根前缀（root-literal）。**这些不是本机路径**——是本机推导不到时的兜底，
# 按「跨机器恒定」分层登记。本机具体前缀一律靠运行时推导，见 build_prefixes()。
#
# 为什么分组登记，而不是平铺一串字面量（T63 立的判据：判据的载体不能是判据本身）：
#   前一张卡的毛病是把 macOS 的四条平铺在一个元组里，读者看不出哪几条只有某个
#   OS 家族成立，补 Linux 时要么补不上、要么补错 OS。现在拆成三张按**OS 约定**
#   归类的表，每条都写明它属于哪个 OS 家族、出处是什么；新增兜底前缀必须落在
#   某一张表里并说明出处，否则这条前缀的来源无从查证。
#
# 三条都**不是**运行时探测的结果，是各 OS 家族的目录约定（探测不到，只能登记），
# 因此来源必须写在注释里 —— 这是「前缀必须在运行时推导**或有注释说明来源**」中
# 后半句的落点。为什么探测不到：`$HOME` 只给**当前这台机器、当前这个用户**的家目录
# （macOS 上推不出 Linux 的），而门禁要拦的是**别人提交进来**的路径。
#
# OS_HOME_ROOTS —— 各 OS 家族的用户家目录根
#   macOS   /Users/<user>        系统约定（System 与 POSIX 层）
#   Linux   /home/<user>         FHS §3.10：普通用户家目录
#   Linux   /root                FHS §3.10 的例外：root 用户家目录**不**在 /home 下
#
#   已知取舍（不许悄悄改）：`/root/` 短且通用，路径段名恰好叫 `root` 的仓库内
#   相对路径（形如 `/a/b/root/c`）会被误判。实测本仓命中为 0；真出现误判时，
#   那一行用 `# scan-exempt: <理由>` 显式标注，**不要**把这条前缀删掉。
OS_HOME_ROOTS = (
    ("root:user-home-macos", "/Users/"),
    ("root:user-home-linux", "/home/"),
    ("root:user-home-linux-root", "/root/"),
)

# VOLUME_MOUNT_ROOTS —— 外部卷挂载根
#   macOS   /Volumes              HFS+ 外部卷挂载约定
VOLUME_MOUNT_ROOTS = (("root:external-volume", "/Volumes/"),)

# PER_USER_TMP_ROOTS —— 每用户临时目录根
#   macOS   /var/folders          per-user 临时目录约定
#   macOS   /private/var/folders  同一目录的 canonical 路径（/var 是符号链接）
#                                 两个都要：realpath 视角与字面视角各命中一半，
#                                 缺一条就有一半 traceback 漏判。
PER_USER_TMP_ROOTS = (
    ("root:per-user-tmp", "/var/folders/"),
    ("root:per-user-tmp-canonical", "/private/var/folders/"),
)

ROOT_PREFIXES = OS_HOME_ROOTS + VOLUME_MOUNT_ROOTS + PER_USER_TMP_ROOTS

# 本仓自有的机器推导前缀类别（用来在报告里区分 `machine-path`）
MACHINE_SOURCES = ("machine-home", "volume-mount", "boot-tmp")

SELF_EXEMPT_MARKER = "# scan-exempt:"  # 显式豁免标记：行内出现即豁免
MAX_FILE_BYTES = 16 * 1024 * 1024       # 超过就不读，但**在输出里点名**（不静默跳过）
SELF_RELATIVE = "tools/check_no_machine_paths.py"

# --------------------------------------------------------------------------
# 二进制判定：看魔数，不看「有没有 NUL」
# --------------------------------------------------------------------------
# 卡面要求「判二进制改为看魔数」。本实现在魔数之外再加三条，**四条缺一即
# 照扫不误**（文件被剥掉 NUL 后按文本扫，违规照常计入 `violation_count`）：
#
#   ① 文件头**魔数**证据（`detect_magic`）—— 替换掉「有没有 NUL」；
#   ② 确实含 NUL —— 否则那只是个恰好以 `%PDF-` 开头的文本文件；
#   ③ 整份非文本字节占比 ≥ MIN_BINARY_RATIO；
#   ④ **载荷区**（魔数签名之后那一段）非文本占比同样 ≥ MIN_BINARY_RATIO。
#
# ③④ 存在的理由：只判整文件占比是「无标度」的，一个 26 字节的伪造件塞
# 6 个 NUL 就能到 0.23，于是「`RIFF` + 补零 + `WAVE` + NUL + 家目录路径」
# 会混过去（实测 whole=0.14 过、tail=0.04 不过）。载荷区也要密才拦得住。
#
# ⚠️ 这四条**不是安全边界，只是把绕过面从「一个 NUL」缩到「刻意伪造文件头」。
#    不许把它们当边界依赖**：③④ 的比值**由写文件的人直接控制**，实测
#    `RIFF`+补零+`WAVE`+50 个 NUL+路径（tail 0.69）与
#    `PK\x03\x04`+4 个 NUL+路径（tail 0.15）都能过——**魔数可伪造，
#    任何只看字节的判据都拦不住有意伪造**。
#    真正让「跳过」不再无声的是另外两件事：
#      - 每一次跳过都在 `skipped[].reason` 里带**可核对的证据**（魔数名 + 占比）；
#      - 同一批文件另进 `unscanned[]`，**「有多少内容没被检查」是个能看见的数字**。
#    真二进制里藏了路径（本仓 11 个 wav 剥 NUL 后实测无前缀，属幸运而非保证）
#    只能靠这个数字被看见，**不能靠扫描抓到**。
#
# 调阈值的方向性：阈值**调高**只可能让文件「被多扫」，而被多扫最多是噪音、
# 绝不是绕过——所以宁可调高不调低。

# 非文本字节：NUL、C0 控制符（除 \t \n \r \f\v）、DEL
_NON_TEXT = frozenset(set(range(0, 9)) | {11, 12} | set(range(14, 32)) | {127})

# 阈值取 0.10 的理由：本仓 11 个 wav 实测非文本占比 0.2636–0.3801
# （且**尾段**同量级 0.2636–0.3801），两侧都留 2.6 倍余量。
MIN_BINARY_RATIO = 0.10

# 尾段短于此不做判定——判不准就当文本扫（严的一侧）
MIN_BINARY_TAIL_BYTES = 16

# 魔数表：(名字, ((偏移, 签名), ...))。复合签名用来在同族容器里消歧
# （`RIFF` 家族有 WAV / WEBP / AVI …）。
# **只看偏移处的真实字节，从不看扩展名**——按扩展名跳过等于「改名即绕过」，
# 而改名是零成本的（卡面明令禁止的绕过面）。
_MAGIC_SIGNATURES = (
    ("riff-wave", ((0, b"RIFF"), (8, b"WAVE"))),
    ("rf64-wave", ((0, b"RF64"), (8, b"WAVE"))),
    ("riff-webp", ((0, b"RIFF"), (8, b"WEBP"))),
    ("riff-avi", ((0, b"RIFF"), (8, b"AVI "))),
    ("png", ((0, b"\x89PNG\r\n\x1a\n"),)),
    ("jpeg", ((0, b"\xff\xd8\xff"),)),
    ("gif87", ((0, b"GIF87a"),)),
    ("gif89", ((0, b"GIF89a"),)),
    ("pdf", ((0, b"%PDF-"),)),
    ("zip", ((0, b"PK\x03\x04"),)),
    ("zip-empty", ((0, b"PK\x05\x06"),)),
    ("zip-spanned", ((0, b"PK\x07\x08"),)),
    ("gzip", ((0, b"\x1f\x8b"),)),
    ("elf", ((0, b"\x7fELF"),)),
    ("ogg", ((0, b"OggS"),)),
    ("flac", ((0, b"fLaC"),)),
    ("7z", ((0, b"7z\xbc\xaf\x27\x1c"),)),
    ("rar", ((0, b"Rar!\x1a\x07"),)),
    ("mp4", ((4, b"ftyp"),)),
    ("sqlite", ((0, b"SQLite format 3\x00"),)),
    ("wasm", ((0, b"\x00asm"),)),
    ("bmp", ((0, b"BM"),)),
    ("ico", ((0, b"\x00\x00\x01\x00"),)),
    ("mp3", ((0, b"ID3"),)),
    ("psd", ((0, b"8BPS"),)),
    ("truetype-ttcf", ((0, b"ttcf"),)),
    ("truetype-ttf", ((0, b"\x00\x01\x00\x00"),)),
    ("opentype-otf", ((0, b"OTTO"),)),
    ("truetype-ttf-mac", ((0, b"true"),)),
    ("java-class", ((0, b"\xca\xfe\xba\xbe"),)),
    ("wasm-alt", ((0, b"\x00asm"),)),
)


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
    if home and home != "/":
        machine[home.rstrip("/") + "/"] = "machine-home"
    try:
        ph = str(Path.home())
        if ph and ph != "/":
            machine[ph.rstrip("/") + "/"] = "machine-home"
    except (RuntimeError, OSError):
        pass

    mount = _mount_ancestor(root)
    if mount is not None:
        mp = str(mount).rstrip("/") + "/"
        if mp != "/":
            machine[mp] = "volume-mount"

    for tmp in {tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}:
        if tmp and tmp != "/":
            machine[tmp.rstrip("/") + "/"] = "boot-tmp"

    # 兜底：任何等于 "/" 的键一律不得进入 prefixes。
    #
    # WHY（T76）：上面三处守卫（machine-home / volume-mount / boot-tmp）是三份
    # 各自维护的记忆，每一份都能被未来的改动单独碰掉——这次被碰掉的就是第三份：
    # `_mount_ancestor` 在根卷 checkout 上返回 `Path("/")`，
    # 而 `str("/").rstrip("/") + "/"` 恒等于 `"/"`，`mount is not None` 挡不住它。
    # `"/"` 作前缀会命中每一行里的每一个路径分隔符，一次扫描产出上万条噪音，
    # 红到没人再看输出——那道门禁等于静默降级成「看起来在跑」。
    # 守卫照旧各管各的（哪一路退化，测试就点名哪一路）；这道闸只兜住最终产物里
    # 那条不可协商的事实：`"/"` 不是前缀。于是「守卫被碰掉」从一次事故
    # 降级成一个测试。
    #
    # 边界：这道闸**只管推导出来的前缀**，不管下面的 `VOX_SCAN_EXTRA_PREFIXES`。
    # 推导是机器替人做的决定（没有人要求过），逃逸口是运维显式写下的要求——
    # 运维写了 `"/"` 却被设置本身悄悄忽略、脚本照样报绿，那是 D4 换个方向复发，
    # 比噪音更坏。所以逃逸口按原样透传，由写它的那个人负责。
    machine = {k: v for k, v in machine.items() if k != "/"}

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
# 二进制判定
# --------------------------------------------------------------------------
def _non_text_ratio(raw: bytes) -> float:
    """非文本字节占比（NUL / C0 控制符 / DEL）。"""
    if not raw:
        return 0.0
    return sum(1 for c in raw if c in _NON_TEXT) / len(raw)


def detect_magic(raw: bytes):
    """按**文件头真实签名**返回 `(魔数名, 签名结束偏移)`；没命中返回 None。

    只看偏移处的字节，不看扩展名、不看文件名（理由见 `_MAGIC_SIGNATURES`）。
    返回结束偏移是为了让密度判据能作用在**载荷区**而不是整文件上。
    """
    for name, parts in _MAGIC_SIGNATURES:
        if all(raw[off:off + len(sig)] == sig for off, sig in parts):
            return name, max(off + len(sig) for off, sig in parts)
    return None


def classify_binary(raw: bytes):
    """真二进制 → 返回证据串（进 `skipped[].reason` 与 `unscanned[].detail`）；
    **任何一条不成立 → 返回 None，调用方必须照扫不误**。

    四条判据与取值理由见本文件 `_NON_TEXT` 上方的注释（T60 必修①）。
    """
    hit = detect_magic(raw)                  # ① 无魔数证据 → 绝不跳过
    if hit is None:
        return None
    magic, sig_end = hit
    if b"\x00" not in raw:                   # ② 没有 NUL → 那只是文本文件
        return None
    whole = _non_text_ratio(raw)
    if whole < MIN_BINARY_RATIO:             # ③ 文本里撒 NUL，不是二进制
        return None
    tail = raw[sig_end:]                    # ④ 载荷区（魔数签名之后）也要密
    if len(tail) < MIN_BINARY_TAIL_BYTES:
        return None                         #    尾段太短判不准 → 当文本扫
    tail_ratio = _non_text_ratio(tail)
    if tail_ratio < MIN_BINARY_RATIO:
        return None
    return (f"magic={magic} nontext={whole:.2f} tail={tail_ratio:.2f} "
            f"nul={raw.count(0)}")


def decode_lines(raw: bytes):
    """产出「剥掉 NUL 后」的文本行，行号仍对应原文件。

    **先按行切、再逐行剥 NUL**（而不是对整篇做 replace 或整篇 strip）：
    这样剥掉的 NUL 不会把相邻两行粘成一行，违规报告里的 `line` 仍然能
    让人直接翻回原文件那一行。

    剥（而不是替换成 U+FFFD）是有意的：NUL 正是能把机器家目录前缀
    （形如 user-home-macos 那一档）劈成两截藏起来的那个字节，
    **剥掉才拼得回来**——替换成一个正常字符就拼不回来了。
    """
    for line in raw.splitlines():
        yield line.replace(b"\x00", b"").decode("utf-8", "replace")


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------
def scan_file(path: Path, root: Path, prefixes):
    """返回 (violations, skipped_reason)。violations 里的 excerpt 已做前缀脱敏。

    `skipped_reason` 为 `None` 表示**逐行扫过了**；非 `None` 表示该文件
    没有被扫（真二进制 / 超限 / 不可读），理由里必须带证据。
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [], f"unreadable: {exc.__class__.__name__}"
    if len(raw) > MAX_FILE_BYTES:
        return [], f"oversize: {len(raw)}B > {MAX_FILE_BYTES}B"
    evidence = classify_binary(raw)
    if evidence is not None:
        # reason 以 "binary" 开头（沿用旧值的前缀形状），后面跟证据
        return [], f"binary: {evidence}"
    rel = path.relative_to(root).as_posix()

    out = []
    for lineno, line in enumerate(decode_lines(raw), 1):
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
    violations, skipped, unscanned, scanned = [], [], [], 0
    for path in files:
        if not path.is_file() or path.resolve() == self_path:
            continue  # 脚本自己豁免：判据的载体不能被自己的判据判红
        scanned += 1
        rel = path.relative_to(root).as_posix()
        found, reason = scan_file(path, root, prefixes)
        violations.extend(found)
        if reason:
            skipped.append({"file": rel, "reason": reason})
            # 同一批文件另记一份「没被检查」台账：reason 给类别，detail 给证据。
            # len(unscanned) = 有多少内容没进过扫描——以前这个数字不存在。
            unscanned.append(
                {"file": rel, "reason": reason.split(":", 1)[0], "detail": reason}
            )

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
        "unscanned": unscanned,
        "prefix_labels": sorted({label for label, _, _ in prefixes}),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
