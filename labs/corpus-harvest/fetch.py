#!/usr/bin/env python3
"""
fetch.py — 首批公开语料的可复现获取器（纯标准库，无第三方依赖）

职责：按 sources.json 的声明，**先验许可、再下载、逐文件校验 sha256**，产出 corpus.lock.json 台账。
不负责：不做语料清洗、不派生话术板（那是 T12 卡的事）、不碰仓库内任何数据文件。

设计红线（docs/11 §11.3）：
  1. 许可判定以 README 正文为证据，**不信任平台 license 标签**——实测数据堂数据集平台标
     Apache-2.0、正文写「版权归数据堂所有，商用数据」；
  2. 判据不通过就**不下载**（fail-closed），不是下载后再补台账；
  3. 台账里的 size/sha256 必须来自**实测**并与平台声明交叉核对，不等即报错；
  4. 语料一律落仓外（~/corpus/），本脚本拒绝往仓库目录里写数据。

纯标准库的原因：本机 pypi 吞吐实测极低（12 s 只拉到 363 KB / 46 MB），装 modelscope/huggingface_hub
的代价远高于直接用平台 HTTP API。两个平台的 API 形态都已实测确认（docs/11 §11.4）。

支持平台：modelscope、hf-mirror（后者必需——huggingface.co 在本机直连超时到不了）。

用法：
  python3 fetch.py --dry-run          # 只做许可审计，不下载（推荐先跑这个）
  python3 fetch.py                    # 下载全部来源
  python3 fetch.py --only <id>        # 只处理某一个来源
  python3 fetch.py --recheck          # 对已下载文件重算 sha256 并与台账比对（巡检用）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
SOURCES_PATH = HERE / "sources.json"
LOCK_PATH = HERE / "corpus.lock.json"

# 语料落盘根目录：**必须在仓库之外**（docs/11 裁定 2）
CORPUS_ROOT = Path(os.environ.get("CORPUS_ROOT", "~/corpus")).expanduser()
# 仓库根自推（T36）：此前是脱敏成占位符 `<仓库根>` 的——占位符让下面「拒绝写入仓库内」
# 那两道守卫**静默失效**（任何路径都不被视为仓内路径）。自推既恢复守卫，也不再需要占位符。
REPO_ROOT = Path(__file__).resolve().parents[2]

# 台账里路径值的锚（T36，与 tools/corpus_fetch/fetch.py 同口径）：台账随仓公开分发，
# **不得记录机器绝对路径**——发布树六类扫描要求机器盘位与家目录路径零命中（类别见 `docs/18 §一`）；
# 且绝对路径对别的机器本来就不成立。
ROOT_PLACEHOLDER = "${CORPUS_ROOT}"

# 可接受的许可（SPDX 小写）。不在这个集合里的许可一律拒绝下载。
ALLOWED_LICENSES = {"mit", "apache-2.0", "cc-by-4.0", "cc0-1.0", "bsd-3-clause"}

# 商业语料警示词：命中即**硬拒绝**，除非 sources.json 里显式写了 license_keyword_ack 并说明理由。
# WHY：平台标签不可信已有实物证据（数据堂案），而「商业授权」与「开源许可」在法务上是两回事，
#      不能靠模型/脚本猜，必须留下一次人工拍板的记录。
COMMERCIAL_WARNING_PATTERNS = [
    r"版权归.{0,12}所有",
    r"商用数据",
    r"商业授权",
    r"需.{0,4}采购",
    r"付费.{0,4}获取",
    r"商业用途.{0,6}(需|须|请)",
]

# 「禁商用 / 禁演绎」声明：命中即**硬拒绝，无 ack 逃生口**。
# WHY（2026-09-18 实测事故）：MagicData 方言集在 HF 上的 front-matter 与平台 cardData 都写
#      `apache-2.0`，而**正文表格写的是 CC BY-NC-ND 4.0、并注明 "for non-commercial use only"**。
#      只扫 front-matter 的审计让这份禁商用数据「假通过」了——本仓是要公开分发的，
#      放进一份 NC-ND 语料等于给整个仓的法务状态埋雷，所以这类命中不接受任何人工放行。
NONCOMMERCIAL_PATTERNS = [
    r"NonCommercial",
    r"Non-Commercial",
    r"non-commercial",
    r"BY-NC",
    r"NC-ND",
    r"ND 4\.0",
    r"禁止商用",
    r"不得用于商业",
    r"非商业",
]

HTTP_TIMEOUT = 180  # 秒；大文件下载用
HTTP_RETRIES = 3


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class FetchError(Exception):
    """获取流程的致命错误——一律向上抛，不吞、不降级。"""


class LicenseRejected(FetchError):
    """许可判据不通过——这是 fail-closed 的正常出口，不是 bug。"""


def portable_path(p: Path, root: Path) -> str:
    """把落盘路径写成 `${CORPUS_ROOT}/<相对>` 的可移植形式（T36）。

    **不在 root 之下 → 抛 FetchError**，不退化成写绝对路径：退化成绝对路径等于把
    刚修掉的发布树红线又放回去（静默降级，本仓禁止）。
    """
    rp = Path(p).expanduser().resolve()
    rr = Path(root).expanduser().resolve()
    try:
        rel = rp.relative_to(rr)
    except ValueError as e:
        raise FetchError(
            f"落盘路径不在语料根之下，无法写成可移植形式: {rp}（root={rr}）——"
            f"台账不得记录机器绝对路径（T36 / docs/11 §11.3）"
        ) from e
    return f"{ROOT_PLACEHOLDER}/{rel.as_posix()}"


def resolve_portable(value: str, root: Path) -> Path:
    """把台账里的路径值还原为本机路径（`--recheck` 用；`portable_path` 的逆）。

    只认 `${CORPUS_ROOT}` 前缀；其余形态（旧台账的裸绝对路径、相对路径）原样按 Path
    处理——**向后兼容**，免得一改就让历史台账读不动。`root` 由调用方给（CLI 侧 =
    `--out-root`）：指错根不会静默通过，会逐条报「缺失」并以 1 退出。
    """
    if value.startswith(ROOT_PLACEHOLDER):
        return Path(root).expanduser() / value[len(ROOT_PLACEHOLDER):].lstrip("/")
    return Path(os.path.expanduser(value))


# ---------------------------------------------------------------------------
# HTTP 辅助
# ---------------------------------------------------------------------------
def http_get(url: str, *, timeout: int = HTTP_TIMEOUT, binary: bool = False) -> Any:
    """GET 一个 URL。binary=False 返回解码后的文本，True 返回原始 bytes。

    带有限次重试（网络抖动是常态，但**绝不允许无限重试**——那会掩盖真实的不可达）。
    非 2xx 一律抛 FetchError，不做「失败返回空」这种静默降级。

    **注意**：binary=True 会把整个body读进内存。大文件（>几十 MB）不要走这里，
    用 `http_download_to_file`（流式）。
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vox-corpus-fetch/1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return raw if binary else raw.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < HTTP_RETRIES:
                time.sleep(2 * attempt)
    raise FetchError(f"GET 失败（重试 {HTTP_RETRIES} 次）: {url} :: {last_err!r}")


def http_download_to_file(url: str, dest: Path, *, chunk: int = 1 << 20) -> Tuple[int, str]:
    """流式下载到 dest，边下边算 sha256，返回 (字节数, sha256)。

    WHY 不能沿用 `http_get(binary=True)`：那是 `resp.read()` 一次性读进内存——
    1 GB 的 parquet 就是 1 GB 内存占用。实测教训（2026-09-18）：`ASLP-lab/WenetSpeech-Wu-Bench`
    的 `understanding/asr.parquet` 用一次性读法直接撞 socket read 超时，
    失败信息只是 `TimeoutError('The read operation timed out')`，看不出是"体量太大"还是"网断了"。
    流式读 + 逐块写盘至少保证：要么拿到完整文件，要么在磁盘上留下可识别的 `.part`。

    先写 `.part` 再改名：中断不会留下半个文件被当成完整语料。
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, HTTP_RETRIES + 1):
        tmp = dest.with_suffix(dest.suffix + ".part")
        h = hashlib.sha256()
        total = 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vox-corpus-fetch/1"})
            dest.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp, tmp.open("wb") as f:
                while True:
                    block = resp.read(chunk)
                    if not block:
                        break
                    f.write(block)
                    h.update(block)
                    total += len(block)
            tmp.replace(dest)
            return total, h.hexdigest()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            # 失败的半个文件必须删掉，否则下次可能被当成完整语料
            tmp.unlink(missing_ok=True)
            if attempt < HTTP_RETRIES:
                time.sleep(2 * attempt)
    raise FetchError(f"下载失败（流式，已重试 {HTTP_RETRIES} 次）: {url} :: {last_err!r}")


class Platform:
    """平台适配器基类。子类只需实现四个取数方法，其余（审计/下载/校验）全部复用。"""

    name = "?"

    def meta(self, sid: str) -> Dict[str, Any]:
        raise NotImplementedError

    def readme_raw_url(self, sid: str, rev: str) -> str:
        raise NotImplementedError

    def evidence_url(self, sid: str, rev: str) -> str:
        """给人点的证据链接（前端页面），不是给脚本读的。"""
        raise NotImplementedError

    def tree(self, sid: str, rev: str) -> List[Dict[str, Any]]:
        """递归列文件，返回 [{path, size, declared_sha256, is_lfs}]。"""
        raise NotImplementedError

    def download_url(self, sid: str, rev: str, path: str) -> str:
        raise NotImplementedError


class ModelScope(Platform):
    """魔搭。API 形态实测要点（docs/11 §11.4）：

    - 文件树的参数名是 `Root=`，**不是 `Path=`**（实测 Path 被忽略并返回根目录）；
    - 文件树返回体**自带 Sha256**，可与下载后实测值交叉核对；
    - `/repo?FilePath=` 下载 LFS 文件时重定向被透明处理，实测 sha256 与声明逐字相符。
    """

    name = "modelscope"
    BASE = "https://modelscope.cn"

    def _api(self, path: str) -> Dict[str, Any]:
        url = f"{self.BASE}/api/v1{path}"
        body = http_get(url)
        try:
            doc = json.loads(body)
        except json.JSONDecodeError as e:
            raise FetchError(f"{self.name} 返回非 JSON: {url} :: {body[:200]!r}") from e
        if doc.get("Code") != 200:
            raise FetchError(
                f"{self.name} API Code={doc.get('Code')} Message={doc.get('Message')!r}: {url}"
            )
        return doc.get("Data") or {}

    def meta(self, sid: str) -> Dict[str, Any]:
        return self._api(f"/datasets/{sid}")

    def readme_raw_url(self, sid: str, rev: str) -> str:
        q = urllib.parse.urlencode({"Revision": rev, "FilePath": "README.md"})
        return f"{self.BASE}/api/v1/datasets/{sid}/repo?{q}"

    def evidence_url(self, sid: str, rev: str) -> str:
        return f"{self.BASE}/datasets/{sid}/blob/{rev}/README.md"

    def tree(self, sid: str, rev: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        queue, seen = [""], set()
        while queue:
            d = queue.pop(0)
            if d in seen:
                continue
            seen.add(d)
            q = urllib.parse.urlencode({"Revision": rev, "Root": d})
            data = self._api(f"/datasets/{sid}/repo/tree?{q}")
            for f in data.get("Files", []):
                if f.get("Type") == "tree":
                    queue.append(f["Path"])
                elif f.get("Type") == "blob":
                    out.append(
                        {
                            "path": f["Path"],
                            "size": f.get("Size", 0),
                            "declared_sha256": f.get("Sha256") or "",
                            "is_lfs": bool(f.get("IsLFS")),
                        }
                    )
        return out

    def download_url(self, sid: str, rev: str, path: str) -> str:
        q = urllib.parse.urlencode({"Revision": rev, "FilePath": path})
        return f"{self.BASE}/api/v1/datasets/{sid}/repo?{q}"


class HFMirror(Platform):
    """hf-mirror.com（HuggingFace 国内镜像）。

    WHY 必需：huggingface.co 在本机**直连不通**（curl 返回 000 / exit 28），任何硬编码
    huggingface.co 的脚本都会超时；镜像实测 200。

    注意：HF 的文件树只对 **LFS 文件**给出 `lfs.oid`（即 sha256），普通小文件没有平台哈希，
    此时 `declared_sha256` 为空——台账里会如实留空，**不拿空格子冒充已核对**。
    路径可能含空格，必须 URL 编码（实测未编码会抛 InvalidURL）。
    """

    name = "hf-mirror"
    BASE = "https://hf-mirror.com"

    def _api(self, path: str) -> Any:
        url = f"{self.BASE}/api{urllib.parse.quote(path, safe='/?=&:')}"
        body = http_get(url)
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise FetchError(f"{self.name} 返回非 JSON: {url} :: {body[:200]!r}") from e

    def meta(self, sid: str) -> Dict[str, Any]:
        return self._api(f"/datasets/{sid}")

    def readme_raw_url(self, sid: str, rev: str) -> str:
        return f"{self.BASE}/datasets/{sid}/raw/{rev}/README.md"

    def evidence_url(self, sid: str, rev: str) -> str:
        return f"{self.BASE}/datasets/{sid}/blob/{rev}/README.md"

    def tree(self, sid: str, rev: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        queue, seen = [""], set()
        while queue:
            d = queue.pop(0)
            if d in seen:
                continue
            seen.add(d)
            entries = self._api(f"/datasets/{sid}/tree/{rev}/{d}".rstrip("/"))
            if not isinstance(entries, list):
                raise FetchError(f"{self.name} 文件树返回非列表: {sid}/{d} :: {entries!r}")
            for f in entries:
                if f.get("type") == "directory":
                    queue.append(f["path"])
                else:
                    lfs = f.get("lfs") or {}
                    out.append(
                        {
                            "path": f["path"],
                            "size": f.get("size", 0),
                            "declared_sha256": lfs.get("oid", ""),
                            "is_lfs": bool(lfs),
                        }
                    )
        return out

    def download_url(self, sid: str, rev: str, path: str) -> str:
        return f"{self.BASE}/datasets/{sid}/resolve/{rev}/{urllib.parse.quote(path, safe='/')}"


PLATFORMS: Dict[str, Platform] = {"modelscope": ModelScope(), "hf-mirror": HFMirror()}


# ---------------------------------------------------------------------------
# 许可审计（下载之前）
# ---------------------------------------------------------------------------
def _normalize_license(raw: Any) -> Optional[str]:
    """把平台/README 里的许可写法归一到 SPDX 小写。识别不了的返回 None（= 判据不通过）。"""
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if not s:
        return None
    # 常见等价写法。注意 "Apache License 2.0" 这种带 License 字样的写法：
    # 第一版正则漏了中间的 license 一词，导致 5 份语料全被判「无法识别许可」——
    # fail-closed 把这个 bug 响亮地抓了出来，而不是悄悄放行。
    if re.fullmatch(r"mit(\s*license)?", s):
        return "mit"
    if re.search(r"apache[\s-]*(license[\s-]*)?2(\.0)?", s):
        return "apache-2.0"
    if re.search(r"cc[\s-]*by[\s-]*4", s):
        return "cc-by-4.0"
    if re.search(r"cc0", s):
        return "cc0-1.0"
    if re.search(r"bsd[\s-]*3", s):
        return "bsd-3-clause"
    return None


def _front_matter_license(readme: str) -> Optional[str]:
    """从 README 的 YAML front-matter 里取 license 字段（`---\\nlicense: x\\n---`）。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", readme, flags=re.DOTALL)
    if not m:
        return None
    lm = re.search(r"^license\s*:\s*(.+)$", m.group(1), flags=re.MULTILINE | re.IGNORECASE)
    return lm.group(1).strip() if lm else None


def _platform_license(platform: Platform, sid: str) -> Any:
    """从平台元信息里取 license 字段。两个平台的字段位置不同，在这里抹平。"""
    meta = platform.meta(sid)
    if platform.name == "modelscope":
        return meta.get("License")
    return (meta.get("cardData") or {}).get("license")


def audit_license(src: Dict[str, Any]) -> Dict[str, Any]:
    """对一份来源做许可审计。**只读，不下载数据**。

    判据（全部通过才算过）：
      ① 平台 license 字段归一后落在 ALLOWED_LICENSES；
      ② README 的 front-matter license 与 ① 一致（缺一不可——平台标签单独不可信）；
      ③ README 正文不含商业语料警示词，或 sources.json 里有显式的 license_keyword_ack；
      ④ 归一后的许可与 sources.json 里声明的 expect_license 相符。

    返回审计记录（供台账使用）；任一不过 → 抛 LicenseRejected（带可读理由）。
    """
    sid = src["id"]
    rev = src.get("revision", "master")
    platform = PLATFORMS[src["platform"]]
    out: Dict[str, Any] = {"id": sid, "audit_checks": []}

    # ① 平台字段
    plat_raw = _platform_license(platform, sid)
    plat_lic = _normalize_license(plat_raw)
    out["license_platform_raw"] = plat_raw
    out["audit_checks"].append(f"平台 license 字段 = {plat_raw!r} → {plat_lic}")

    # ② README 正文（走原文接口，不是前端页面——前端返回 HTML 外壳，解析必然失败）
    readme = http_get(platform.readme_raw_url(sid, rev))
    fm_raw = _front_matter_license(readme)
    fm_lic = _normalize_license(fm_raw)
    out["license_readme_raw"] = fm_raw
    out["license_evidence_url"] = platform.evidence_url(sid, rev)
    out["audit_checks"].append(f"README front-matter license = {fm_raw!r} → {fm_lic}")

    if plat_lic is None and fm_lic is None:
        raise LicenseRejected(f"{sid}: 平台与 README 都未给出可识别的许可（不得下载）")
    if plat_lic is None:
        raise LicenseRejected(f"{sid}: 平台未给许可，仅有 README={fm_lic}（证据不足，不得下载）")
    if fm_lic is None:
        raise LicenseRejected(
            f"{sid}: README 无 license front-matter，只有平台标签 {plat_lic}"
            f"（平台标签不可信，不得下载）"
        )
    if plat_lic != fm_lic:
        raise LicenseRejected(
            f"{sid}: 平台标签({plat_lic}) 与 README({fm_lic}) 冲突，以 README 为准 → 不可用"
        )

    lic = fm_lic
    if lic not in ALLOWED_LICENSES:
        raise LicenseRejected(f"{sid}: 许可 {lic} 不在允许集合 {sorted(ALLOWED_LICENSES)} 内")

    # ③ 商业警示词
    hits = [p for p in COMMERCIAL_WARNING_PATTERNS if re.search(p, readme)]
    out["commercial_warning_hits"] = hits
    if hits:
        ack = src.get("license_keyword_ack")
        if not ack:
            raise LicenseRejected(
                f"{sid}: README 命中商业语料警示词 {hits}，且 sources.json 无 license_keyword_ack"
                f"（硬拒绝，不得下载；确需使用请人工核后在该来源上写明 ack 理由）"
            )
        out["audit_checks"].append(f"商业警示词 {hits} 已由人工 ack：{ack}")
    else:
        out["audit_checks"].append("README 无商业语料警示词")

    # ③b 禁商用/禁演绎声明：**无逃生口**
    # WHY：这一条是 2026-09-18 的实测事故加上的——front-matter 写 apache-2.0、正文写
    #      CC BY-NC-ND 的语料真实存在（MagicData 方言集），只信 front-matter 会假通过。
    nc_hits = [p for p in NONCOMMERCIAL_PATTERNS if re.search(p, readme, re.IGNORECASE)]
    out["noncommercial_hits"] = nc_hits
    if nc_hits:
        raise LicenseRejected(
            f"{sid}: README 正文含禁商用/禁演绎声明 {nc_hits}"
            f"（front-matter 写的是 {lic}，与正文冲突 → 以正文为准 → 不可用；无放行口）"
        )
    out["audit_checks"].append("README 正文无禁商用/禁演绎声明")

    # ④ 与声明核对
    expect = [e.lower() for e in src.get("expect_license", [])]
    if expect and lic not in expect:
        raise LicenseRejected(f"{sid}: 实测许可 {lic} 与 sources.json 声明的 {expect} 不符")

    out["license_spdx"] = lic
    out["audit_checks"].append(f"审计通过：许可 = {lic}")
    return out


# ---------------------------------------------------------------------------
# 文件筛选与下载
# ---------------------------------------------------------------------------
def select_files(src: Dict[str, Any], tree: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 sources.json 的 files 声明筛选要下的文件；`*` = 全部。

    以 `.` 开头的文件（.gitattributes / .DS_Store 等）一律跳过——对语料内容无意义。
    """
    pats = src.get("files", ["*"])
    picked = [f for f in tree if not Path(f["path"]).name.startswith(".") and ("*" in pats or f["path"] in pats)]
    picked.sort(key=lambda f: f["path"])
    if not picked:
        raise FetchError(f"{src['id']}: 文件筛选结果为空（声明 files={pats}）")
    return picked


def download_file(src: Dict[str, Any], rec: Dict[str, Any], dest: Path) -> Tuple[int, str]:
    """下载单个文件到 dest，返回 (实测字节数, 实测 sha256)。

    走流式下载（大文件不进内存），先写 `.part` 再改名，避免中断留下半个文件被当成完整语料。
    """
    url = PLATFORMS[src["platform"]].download_url(src["id"], src.get("revision", "main"), rec["path"])
    return http_download_to_file(url, dest)


def manifest_digest(files: List[Dict[str, Any]]) -> str:
    """多文件语料的整体指纹 = 对 (path, size, sha256) 排序记录求 sha256。

    WHY：语料常由多个文件组成，单一 sha256 字段无法覆盖；用一个确定性摘要把整份语料
         的每个文件都绑定进去，任何文件被替换都会改变这个值。
    """
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x["path"]):
        h.update(f"{f['path']}\t{f['size_bytes']}\t{f['sha256']}\n".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def process(src: Dict[str, Any], *, dry_run: bool, out_root: Path) -> Dict[str, Any]:
    """处理一份来源：审计许可 → （非 dry-run 时）下载并校验 → 返回台账条目。"""
    sid = src["id"]
    rev = src.get("revision", "main")
    platform = PLATFORMS[src["platform"]]
    print(f"\n=== {sid} [{platform.name}] ===")

    audit = audit_license(src)
    for c in audit["audit_checks"]:
        print(f"    {c}")

    if dry_run:
        return {"id": sid, "platform": platform.name, "kind": src["kind"], "dry_run": True, **audit}

    tree = platform.tree(sid, rev)
    picked = select_files(src, tree)
    total = sum(f["size"] for f in picked)
    print(f"    待下 {len(picked)} 个文件，共 {total / 1e6:.1f} MB")

    dest_dir = out_root / sid.replace("/", "__")
    # 双保险：语料绝不落进仓库（docs/11 裁定 2）
    resolved = dest_dir.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise FetchError(f"拒绝写入仓库内路径: {dest_dir}")
    if out_root == REPO_ROOT or REPO_ROOT in out_root.parents:
        raise FetchError(f"--out-root 不得指向仓库内: {out_root}")

    rec_files: List[Dict[str, Any]] = []
    for f in picked:
        path = f["path"]
        dest = dest_dir / path
        size, sha = download_file(src, f, dest)
        # 实测 vs 平台声明必须相符——不符说明传输被截断/内容被改
        if size != f["size"]:
            raise FetchError(f"{sid}::{path} 体量不符：实测 {size} vs 平台 {f['size']}（不得入库）")
        if f["declared_sha256"] and sha != f["declared_sha256"]:
            raise FetchError(
                f"{sid}::{path} sha256 不符：实测 {sha} vs 平台 {f['declared_sha256']}（不得入库）"
            )
        rec_files.append(
            {
                "path": path,
                "size_bytes": size,
                "sha256": sha,
                "declared_sha256": f["declared_sha256"],
                "is_lfs": f["is_lfs"],
                "verified_against_platform": bool(f["declared_sha256"]),
                "local_file": portable_path(dest, out_root),
            }
        )

    n_verified = sum(1 for f in rec_files if f["verified_against_platform"])
    print(f"    ✓ {len(rec_files)} 个文件已下并校验（{n_verified} 个有平台哈希可交叉核对）")

    entry = {
        "id": sid,
        "platform": platform.name,
        "kind": src["kind"],
        "revision": rev,
        "license_spdx": audit["license_spdx"],
        "license_platform_raw": audit.get("license_platform_raw"),
        "license_readme_raw": audit.get("license_readme_raw"),
        "license_evidence_url": audit["license_evidence_url"],
        "size_bytes": sum(f["size_bytes"] for f in rec_files),
        "sha256": manifest_digest(rec_files),
        "files": rec_files,
        "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "local_path": portable_path(dest_dir, out_root),
        "why": src.get("why", ""),
        "notes": src.get("notes_override") or ("; ".join(src.get("skip", {}).values()) if src.get("skip") else ""),
    }
    return entry


def recheck(out_root: Path) -> int:
    """巡检：对台账里每个文件重算 sha256，与台账记录比对。用于事后证明「语料没被动过」。"""
    if not LOCK_PATH.exists():
        print(f"台账不存在: {LOCK_PATH}", file=sys.stderr)
        return 2
    doc = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    bad = 0
    for e in doc.get("entries", []):
        for f in e.get("files", []):
            p = resolve_portable(f["local_file"], out_root)
            if not p.exists():
                print(f"  ✗ 缺失 {p}", file=sys.stderr)
                bad += 1
                continue
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            if sha != f["sha256"]:
                print(f"  ✗ 被改动 {p}: 台账 {f['sha256'][:16]}… 实测 {sha[:16]}…", file=sys.stderr)
                bad += 1
    total = sum(len(e.get("files", [])) for e in doc.get("entries", []))
    print(f"巡检 {total} 个文件，异常 {bad} 个")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="vox 公开语料获取器（先验许可后下载）")
    ap.add_argument("--dry-run", action="store_true", help="只做许可审计，不下载")
    ap.add_argument(
        "--include-optional",
        action="store_true",
        help="含 optional 来源（缺省跳过——大体量的可选项不该在例行跑里被拉下来）",
    )
    ap.add_argument("--only", metavar="ID", help="只处理指定 id")
    ap.add_argument("--recheck", action="store_true", help="对已下载文件重算 sha256 与台账比对")
    ap.add_argument("--out-root", default=str(CORPUS_ROOT), help=f"语料落盘根（默认 {CORPUS_ROOT}）")
    args = ap.parse_args()

    out_root = Path(args.out_root).resolve()

    if args.recheck:
        return recheck(out_root)

    doc = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    sources = doc["sources"]
    # optional 过滤：**缺省跳过**，且 `--only` 指定时必须能显式下（便于人工按需拉大件）。
    # WHY：2026-09-18 实测教训——重写本脚本时漏掉了这个过滤，结果例行跑会去下
    #      标着"本批不下载"的 1 GB parquet，白等一轮超时。
    total_declared = len(sources)
    if args.only:
        sources = [s for s in sources if s["id"] == args.only]
        if not sources:
            print(f"未找到来源 id={args.only}", file=sys.stderr)
            return 2
    elif not args.include_optional:
        skipped = [s["id"] for s in sources if s.get("optional")]
        sources = [s for s in sources if not s.get("optional")]
        if skipped:
            print(f"跳过 optional 来源 {skipped}（要下用 --include-optional 或 --only <id>）")

    out_root.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    rejected: List[Tuple[str, str]] = []
    for src in sources:
        try:
            entries.append(process(src, dry_run=args.dry_run, out_root=out_root))
        except (LicenseRejected, FetchError) as e:
            # 许可不过 = fail-closed 的正常出口：记录、跳过、继续下一份，最后汇总非零退出
            tag = "许可拒绝（不下载）" if isinstance(e, LicenseRejected) else "获取失败"
            print(f"    ✗ {tag}: {e}", file=sys.stderr)
            rejected.append((src["id"], str(e)))

    if not args.dry_run and entries:
        # 台账合并写：保留本次未处理的旧条目，避免「跑 --only 一次就把台账清空」。
        # 关键：旧条目若指向的语料已被删除，必须留着让 recheck 报「缺失」——
        #       静默删掉旧条目等于把「语料没了」这件事藏起来。
        old: List[Dict[str, Any]] = []
        if LOCK_PATH.exists():
            try:
                old = json.loads(LOCK_PATH.read_text(encoding="utf-8")).get("entries", [])
            except json.JSONDecodeError:
                print(f"警告：旧台账解析失败，将被覆盖: {LOCK_PATH}", file=sys.stderr)
        touched = {e["id"] for e in entries}
        merged = [e for e in old if e.get("id") not in touched] + entries
        merged.sort(key=lambda e: e["id"])
        LOCK_PATH.write_text(
            json.dumps(
                {
                    "_schema": "vox-corpus-lock/2",
                    "_note": "由 labs/corpus-harvest/fetch.py 生成；size/sha256 均为实测并已与平台声明交叉核对（verified_against_platform=false 表示平台未提供哈希，仅记录了实测值）。路径值一律写成 ${CORPUS_ROOT}/<相对>：台账随仓公开分发，不得记录机器绝对路径（T36）。",
                    "entries": merged,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\n台账已写: {LOCK_PATH}（{len(merged)} 条）")

    print(f"\n{'[dry-run] ' if args.dry_run else ''}成功 {len(entries)} / 拒绝 {len(rejected)}")
    for sid, reason in rejected:
        print(f"  ✗ {sid}: {reason}", file=sys.stderr)
    # 有拒绝即以非零退出：让调用方（CI/人）无法忽略
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
