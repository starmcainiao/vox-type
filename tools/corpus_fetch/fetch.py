#!/usr/bin/env python3
"""
fetch.py — 公开语料的可复现获取器（纯标准库，无第三方依赖）

职责：按 sources.json 的声明，**先验许可、再下载、逐文件校验 sha256**，产出 corpus.lock.json 台账。
不负责：不做语料清洗、不派生话术板、不碰仓库内任何数据文件。

设计红线（docs/11 §11.3）：
  1. 许可判定以 README 正文为证据，**不信任平台 license 标签**——实测数据堂数据集平台标
     Apache-2.0、正文写「版权归数据堂所有，商用数据」；
  2. 判据不通过就**不下载**（fail-closed），不是下载后再补台账；
  3. 台账里的 size/sha256 必须来自**实测**并与平台声明交叉核对，不等即报错；
  4. 语料一律落仓外（缺省 ~/corpus/），本脚本拒绝往仓库目录里写数据。

**由 labs/corpus-harvest-2026-09-18/fetch.py 提升（T12）时去掉的硬编码**：
  - `CORPUS_ROOT`  → `--out-root`（缺省值保留为 ~/corpus/，可覆盖）
  - `REPO_ROOT`    → 缺省从 `__file__` 自推（仓库根 = 本文件往上两级），`--repo-root` 可覆盖
  - sources.json / corpus.lock.json **留在 labs/ 那批**（本批的数据），本工具用 `--sources` / `--lock` 指定

台账写入的三条纪律（labs README §8.1，**提升后不许退化**）：
  - read-modify-write 必须**原子**（临时文件 + rename）；
  - 同一时刻只允许一个实例写同一份台账：检测到并发**拒绝启动**（不是排队等待、不是静默覆盖）；
  - 旧条目若指向的语料已被删除，必须**留着**让 `--recheck` 报「缺失」——静默删掉等于把
    「语料没了」这件事藏起来。

纯标准库的原因：本机 pypi 吞吐实测极低（12 s 只拉到 363 KB / 46 MB），装 modelscope/huggingface_hub
的代价远高于直接用平台 HTTP API。两个平台的 API 形态都已实测确认（docs/11 §11.4）。

用法：
  python3 -m tools.corpus_fetch.fetch --sources <sources.json> --lock <lock.json> --dry-run
  python3 -m tools.corpus_fetch.fetch --sources <sources.json> --lock <lock.json>
  python3 -m tools.corpus_fetch.fetch --sources <sources.json> --only <id>
  python3 -m tools.corpus_fetch.fetch --sources <sources.json> --recheck
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量（路径类一律由参数注入，此处只留缺省值）
# ---------------------------------------------------------------------------
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]  # tools/corpus_fetch/ → 仓库根

# 语料落盘根目录：**必须在仓库之外**（docs/11 裁定 2）
CORPUS_ROOT = Path(os.environ.get("CORPUS_ROOT", "~/corpus")).expanduser()
LOCK_FILENAME = "corpus.lock.json"
SOURCES_FILENAME = "sources.json"

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
LOCK_LOCK_FILENAME = "corpus.lock.json.lock"  # 并发门禁用的占位文件（同目录，仓外）

# 缺省 revision：三处（许可审计 / tree / 下载）必须**同一个**缺省，否则审计证据与实际入库
# 内容会绑在不同版本（T12b 第 5 条：原先缺省在审计侧与下载侧取的不是同一个值）。
DEFAULT_REVISION = "main"

# 台账 _note：说明本批数据由谁生成（T12 提升后写死指向 tools/，不再是 labs/ 的一次性脚本）
LOCK_NOTE = (
    "由 tools/corpus_fetch/fetch.py 生成；size/sha256 均为实测并已与平台声明交叉核对"
    "（verified_against_platform=false 表示平台未提供哈希，仅记录了实测值）。"
)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class FetchError(Exception):
    """获取流程的致命错误——一律向上抛，不吞、不降级。"""


class LicenseRejected(FetchError):
    """许可判据不通过——这是 fail-closed 的正常出口，不是 bug。"""


# ---------------------------------------------------------------------------
# HTTP 辅助
# ---------------------------------------------------------------------------
def http_get(url: str, *, timeout: int = HTTP_TIMEOUT, binary: bool = False) -> Any:
    """GET 一个 URL。binary=False 返回解码后的文本，True 返回原始 bytes。

    带有限次重试（网络抖动是常态，但**绝不允许无限重试**——那会掩盖真实的不可达）。
    非 2xx 一律抛 FetchError，不做「失败返回空」这种静默降级。

    **注意**：binary=True 会把整个 body 读进内存。大文件（>几十 MB）不要走这里，
    用 `http_download_to_file`（流式）。
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vox-corpus-fetch/1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            if binary:
                return raw
            # 文本分支必须**严格解码**（T12c 修 4）：`errors="replace"` 会把非 UTF-8 README
            # 变成带 U+FFFD 的乱码正文，许可关键词扫描在乱码上空转成「通过」——等于给一份
            # 没读过的 README 发了一张许可通行证。宁可硬失败让人去看原文。
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError as e:
                raise FetchError(
                    f"README 不是合法 UTF-8，无法作为许可证据: {url} :: {e}"
                    f"（严格解码；不做替换符降级——乱码正文会让许可关键词扫描空转「通过」）"
                ) from e
        except urllib.error.HTTPError as e:
            # HTTPError 是 URLError 的子类（T12c 修 3）：404/403 这类服务端明确拒绝不是
            # 网络抖动，重试只会白等 2s+4s。单列出来直接抛，不走下面的重试等待。
            raise FetchError(f"GET 失败（HTTP {e.code}，不重试）: {url} :: {e!r}")
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
    **失败时 `.part` 必须删掉**——残留半个文件会被误当成完整语料（labs README §8.1 坑 2）。
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
      ③b README 正文不含禁商用/禁演绎声明（**无逃生口**，docs/11 §11.9.2）；
      ④ 归一后的许可与 sources.json 里声明的 expect_license 相符。

    返回审计记录（供台账使用）；任一不过 → 抛 LicenseRejected（带可读理由）。
    """
    sid = src["id"]
    rev = src.get("revision", DEFAULT_REVISION)
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
    if not expect:
        # T12c 修 6：缺 expect_license 时行为仍是跳过，但必须**留痕**——否则事后无法区分
        # 「④ 核对通过」与「④ 根本没核对」，而后者意味着这份语料的许可只过了前三条。
        out["audit_checks"].append("④ expect_license 未声明，跳过核对")

    out["license_spdx"] = lic
    out["audit_checks"].append(f"审计通过：许可 = {lic}")
    return out


# ---------------------------------------------------------------------------
# 文件筛选与下载
# ---------------------------------------------------------------------------
def select_files(src: Dict[str, Any], tree: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 sources.json 的 files 声明筛选要下的文件；`*` = 全部。

    以 `.` 开头的文件（.gitattributes / .DS_Store 等）一律跳过——对语料内容无意义。

    **声明了具体路径时逐项核对（T12b 修 1）**：只要还有 ≥1 个命中就放行的旧逻辑，会让某个
    被改名/删除的声明文件被**静默丢弃**——不完整语料当完整入库，`manifest_digest` 只覆盖
    现存文件，而 recheck 之后也证明不了「这份语料是完整的」。所以这里对每个非 `.` 开头的
    声明路径逐个比对 tree，有缺失就抛错、一个都不下。
    """
    pats = src.get("files", ["*"])
    picked = [f for f in tree if not Path(f["path"]).name.startswith(".") and ("*" in pats or f["path"] in pats)]
    picked.sort(key=lambda f: f["path"])
    if not picked:
        raise FetchError(f"{src['id']}: 文件筛选结果为空（声明 files={pats}）")
    if "*" not in pats:
        # 全量声明（`*`）不存在「漏了哪个」这个问题——只有具体路径才可能静默缺失
        missing = [p for p in pats if not p.startswith(".") and p not in {f["path"] for f in picked}]
        if missing:
            raise FetchError(
                f"{src['id']}: 声明的文件在平台文件树里不存在 {missing}"
                f"（声明 files={pats}，tree 实有 {len(tree)} 个条目）——不完整语料不得入库，一个都不下。"
                f"请核对 sources.json 的路径声明（改名/删除都会在这里暴露）"
            )
    return picked


def download_file(src: Dict[str, Any], rec: Dict[str, Any], dest: Path) -> Tuple[int, str]:
    """下载单个文件到 dest，返回 (实测字节数, 实测 sha256)。

    走流式下载（大文件不进内存），先写 `.part` 再改名，避免中断留下半个文件被当成完整语料。
    """
    url = PLATFORMS[src["platform"]].download_url(src["id"], src.get("revision", DEFAULT_REVISION), rec["path"])
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
# 台账并发门禁 + 原子写（labs README §8.1 坑 3：并发跑会互相覆盖）
# ---------------------------------------------------------------------------
def _lock_file_path(lock_path: Path) -> Path:
    return lock_path.with_suffix(lock_path.suffix + ".lock")


@contextmanager
def _hold_lease(lock_path: Path) -> Generator[None, None, None]:
    """占住台账写锁；拿不到 = 已有另一个实例在写 → **拒绝启动**。

    实测事故（2026-09-18）：一个早先启动、后被判定为"陈旧"的后台下载在台账提交之后才结束，
    又把台账重写回去，覆盖了已提交的条目。read-modify-write 没有锁，并发必然丢条目。
    所以这里是**硬失败**而不是排队：宁可让人手工处理，也不允许静默覆盖。

    占位文件放在台账同目录（= 仓外 `--out-root` 下），不进仓库。
    """
    lf = _lock_file_path(lock_path)
    lf.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lf), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            raise FetchError(
                f"检测到并发实例正在写同一份台账 {lock_path}（写锁 {lf} 已被持有）——拒绝启动。"
                f"待另一个实例结束后重试；不要同时跑两个 fetch（并发写会丢台账条目）。"
            )
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\t{time.time()}\n".encode("ascii"))
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_doc_locked(lock_path: Path, entries: List[Dict[str, Any]]) -> None:
    """已在锁内时调用：临时文件 + rename 原子落盘，失败不留下半份台账。"""
    doc = {
        "_schema": "vox-corpus-lock/1",
        "_note": LOCK_NOTE,
        "entries": entries,
    }
    tmp = lock_path.with_suffix(lock_path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(lock_path)


def write_lock(lock_path: Path, entries: List[Dict[str, Any]]) -> None:
    """把台账写入 lock_path：**持锁 + 临时文件 + rename**（原子），失败不留下半份台账。

    条目顺序由调用方决定（`merge_lock` 已按 id 排序）——写入端不改语义，只做原子落盘。
    """
    with _hold_lease(lock_path):
        _write_doc_locked(lock_path, entries)


def _read_old_entries(lock_path: Path) -> List[Dict[str, Any]]:
    """读旧台账的 entries；文件不存在 → 空列表；**坏 JSON → 抛 FetchError 中止**。

    修 3（T12b）：原来「警告一行就整体覆盖」会把旧条目全部丢掉、对应语料脱管
    （recheck 不再巡检），exit 0——与本文件自己写的「宁可硬失败也不静默覆盖」相抵触。
    所以这里是硬失败：让人手工裁决（备份/删除该台账）后重跑。
    """
    if not lock_path.exists():
        return []
    try:
        return json.loads(lock_path.read_text(encoding="utf-8")).get("entries", [])
    except json.JSONDecodeError as e:
        raise FetchError(
            f"旧台账解析失败，拒绝覆盖: {lock_path} :: {e}"
            f"（旧条目会被整体丢掉、对应语料从此脱离巡检——不静默覆盖。"
            f"请人工备份或删除该台账后重跑）"
        ) from e


def update_lock(lock_path: Path, merge_fn) -> List[Dict[str, Any]]:
    """在锁临界区内完成「读旧台账 → merge_fn(old, new_entries) → 写」，返回写入的条目。

    修 2（T12b）：旧实现把 `json.loads(read_text)` 放在锁外、`write_lock` 只盖写，
    于是实例 B 在 A 提交前读到陈旧 old、A 释放锁后 B 才写入 → **A 的条目被静默覆盖**，
    B 照常打「台账已写」exit 0。这正是 2026-09-18 那次并发事故的形态：锁没堵住它，
    只是把窗口从「整段」缩到「读→写间隙」。现在读与写同处一个 lease，间隙不存在了。
    """
    with _hold_lease(lock_path):
        old = _read_old_entries(lock_path)
        merged = merge_fn(old)
        _write_doc_locked(lock_path, merged)
        return merged


def merge_lock(old: List[Dict[str, Any]], new_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并台账：保留本次未处理的旧条目，避免「跑 --only 一次就把台账清空」。

    旧条目若指向的语料已被删除，必须留着让 `--recheck` 报「缺失」——静默删掉等于把
    「语料没了」这件事藏起来（fail-closed）。
    """
    touched = {e["id"] for e in new_entries}
    merged = [e for e in old if e.get("id") not in touched] + list(new_entries)
    merged.sort(key=lambda e: e["id"])
    return merged


def load_sources(sources_path: Path) -> List[Dict[str, Any]]:
    """读 sources.json，返回 sources 列表。文件缺失一律报错，不做「空来源」静默降级。"""
    if not sources_path.exists():
        raise FetchError(f"sources 文件不存在: {sources_path}")
    try:
        doc = json.loads(sources_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FetchError(f"sources 不是合法 JSON: {sources_path} :: {e}") from e
    sources = doc.get("sources")
    if not isinstance(sources, list) or not sources:
        raise FetchError(f"sources 缺少非空的 'sources' 列表: {sources_path}")
    return sources


def filter_sources(
    sources: List[Dict[str, Any]], *, only: Optional[str] = None, include_optional: bool = False
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """按 `--only` / optional 闸门筛选来源，返回 (选中, 被跳过的 optional id 列表)。

    **缺省跳过 optional**，且必须**响亮打印**跳过声明——静默跳过等于让人以为"已经下了"
    （labs README §8.1 坑 1：重写脚本时漏掉这个过滤，例行跑去下了一份标着"本批不下载"的
    1030.7 MB parquet）。`--only <id>` 必须能显式拉单个 optional 项。
    """
    if only:
        picked = [s for s in sources if s["id"] == only]
        if not picked:
            raise FetchError(f"未找到来源 id={only}（共 {len(sources)} 个来源）")
        return picked, []
    skipped = [s["id"] for s in sources if s.get("optional")]
    if include_optional:
        return sources, []
    return [s for s in sources if not s.get("optional")], skipped


def resolve_out_root(out_root: Path, repo_root: Path) -> Path:
    """把 `--out-root` 解析为绝对路径并做**仓库边界校验**（docs/11 裁定 2）。

    关键顺序：**先校验、后建目录**。如果先 `mkdir` 再报错，仓库里就留下了一个本不该存在的目录。
    """
    resolved = Path(out_root).resolve()
    if resolved == Path(repo_root).resolve() or Path(repo_root).resolve() in resolved.parents:
        raise FetchError(
            f"--out-root 不得指向仓库内: {resolved}（仓库根 {repo_root}）——"
            f"第三方语料一律落仓外（docs/11 裁定 2）"
        )
    return resolved


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def process(
    src: Dict[str, Any],
    *,
    dry_run: bool,
    out_root: Path,
    repo_root: Path = DEFAULT_REPO_ROOT,
    lock_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """处理一份来源：审计许可 → （非 dry-run 时）下载并校验 → 返回台账条目。"""
    sid = src["id"]
    rev = src.get("revision", DEFAULT_REVISION)
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
    repo_resolved = Path(repo_root).resolve()
    if resolved == repo_resolved or repo_resolved in resolved.parents:
        raise FetchError(f"拒绝写入仓库内路径: {dest_dir}")
    if out_root == repo_resolved or repo_resolved in out_root.parents:
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
                "local_file": str(dest),
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
        "local_path": str(dest_dir),
        "why": src.get("why", ""),
        "notes": src.get("notes_override") or ("; ".join(src.get("skip", {}).values()) if src.get("skip") else ""),
    }
    return entry


def recheck(lock_path: Path) -> int:
    """巡检：对台账里每个文件重算 sha256，与台账记录比对。用于事后证明「语料没被动过」。"""
    if not lock_path.exists():
        print(f"台账不存在: {lock_path}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"台账不是合法 JSON: {lock_path} :: {e}", file=sys.stderr)
        return 3
    bad = 0
    for ent in doc.get("entries", []):
        for f in ent.get("files", []):
            p = Path(f["local_file"])
            if not p.exists():
                print(f"  ✗ 缺失 {p}", file=sys.stderr)
                bad += 1
                continue
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            if sha != f["sha256"]:
                print(f"  ✗ 被改动 {p}: 台账 {f['sha256'][:16]}… 实测 {sha[:16]}…", file=sys.stderr)
                bad += 1
    total = sum(len(ent.get("files", [])) for ent in doc.get("entries", []))
    print(f"巡检 {total} 个文件，异常 {bad} 个")
    return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="vox 公开语料获取器（先验许可后下载）")
    ap.add_argument("--sources", default=None, help=f"来源声明 JSON（缺省 = 本仓库根下的 {SOURCES_FILENAME}）")
    ap.add_argument("--lock", default=None, help=f"台账输出路径（缺省 = --out-root/{LOCK_FILENAME}）")
    ap.add_argument("--dry-run", action="store_true", help="只做许可审计，不下载")
    ap.add_argument(
        "--include-optional",
        action="store_true",
        help="含 optional 来源（缺省跳过——大体量的可选项不该在例行跑里被拉下来）",
    )
    ap.add_argument("--only", metavar="ID", help="只处理指定 id")
    ap.add_argument("--recheck", action="store_true", help="对已下载文件重算 sha256 与台账比对")
    ap.add_argument(
        "--out-root",
        default=str(DEFAULT_CORPUS_ROOT),
        help=f"语料落盘根，必须在仓库之外（默认 {DEFAULT_CORPUS_ROOT}）",
    )
    ap.add_argument(
        "--repo-root",
        default=str(DEFAULT_REPO_ROOT),
        help=f"仓库根，用于拒绝写仓库内路径（默认自推 {DEFAULT_REPO_ROOT}）",
    )
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    out_root = resolve_out_root(Path(args.out_root), repo_root)
    lock_path = Path(args.lock) if args.lock else out_root / LOCK_FILENAME
    # 台账必须在仓外：它记的是语料落盘路径，属本批数据
    resolved_lock = Path(lock_path).resolve()
    if resolved_lock == repo_root or repo_root in resolved_lock.parents:
        raise FetchError(
            f"--lock 不得指向仓库内: {resolved_lock}（仓库根 {repo_root}）——台账随语料落仓外"
        )
    print(f"台账路径: {resolved_lock}（仓外，须在 --out-root 之下）")

    if args.recheck:
        return recheck(resolved_lock)

    sources_path = Path(args.sources) if args.sources else Path(__file__).resolve().parents[2] / SOURCES_FILENAME
    sources = load_sources(sources_path)
    # optional 过滤：**缺省跳过**，且 `--only` 指定时必须能显式下（便于人工按需拉大件）。
    picked_sources, skipped = filter_sources(sources, only=args.only, include_optional=args.include_optional)
    if skipped:
        print(f"跳过 optional 来源 {skipped}（要下用 --include-optional 或 --only <id>）")

    entries: List[Dict[str, Any]] = []
    rejected: List[Tuple[str, str]] = []
    for src in picked_sources:
        try:
            entries.append(process(src, dry_run=args.dry_run, out_root=out_root, repo_root=repo_root))
        except (LicenseRejected, FetchError) as e:
            # 许可不过 = fail-closed 的正常出口：记录、跳过、继续下一份，最后汇总非零退出
            tag = "许可拒绝（不下载）" if isinstance(e, LicenseRejected) else "获取失败"
            print(f"    ✗ {tag}: {e}", file=sys.stderr)
            rejected.append((src["id"], str(e)))

    if not args.dry_run and entries:
        # 台账合并写：保留本次未处理的旧条目，避免「跑 --only 一次就把台账清空」。
        # 「读旧台账 → 合并 → 写」必须在**同一个**锁临界区内完成（update_lock）——
        # 读放在锁外会漏：另一个实例用陈旧 old 合并后写入，把本次条目静默覆盖掉。
        # 锁的创建与 mkdir 都在拿到 entries 之后：审计失败一个都不成 → 不建目录、不写台账
        merged = update_lock(resolved_lock, lambda old: merge_lock(old, entries))
        print(f"台账已写: {resolved_lock}（{len(merged)} 条）")

    print(f"\n{'[dry-run] ' if args.dry_run else ''}成功 {len(entries)} / 拒绝 {len(rejected)}")
    for sid, reason in rejected:
        print(f"  ✗ {sid}: {reason}", file=sys.stderr)
    # 有拒绝即以非零退出：让调用方（CI/人）无法忽略
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
