"""tests/test_corpus_fetch.py — fetch 管道：许可审计 / 哈希校验 / 台账原子写。

全部**离线**：不联网。平台取数与下载由测试替身 Platform 子类与
`FakeHttpDownload`（已返回的假响应）驱动——卡内禁止测试联网。
测试只调 `tools/corpus_fetch/fetch.py` 的公开函数，不复制被验逻辑。
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
import urllib.error
import urllib.request
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tools.corpus_fetch.fetch as fetch_mod  # noqa: E402
from tools.corpus_fetch.fetch import (  # noqa: E402
    DEFAULT_REVISION,
    FetchError,
    LicenseRejected,
    ModelScope,
    PLATFORMS,
    audit_license,
    http_get,
    filter_sources,
    manifest_digest,
    merge_lock,
    portable_path,
    process,
    recheck,
    resolve_out_root,
    resolve_portable,
    select_files,
    update_lock,
    write_lock,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 假语料（测试替身响应，不含真实语料原文）
# ---------------------------------------------------------------------------
FAKE_README_CLEAN = (
    "---\n"
    "license: Apache License 2.0\n"
    "---\n"
    "\n"
    "# Fixture dataset\n"
    "\n"
    "A synthetic customer-service dialogue corpus, released under Apache License 2.0.\n"
    "No commercial restrictions. Everything is derived from public templates.\n"
)

# 负例：front-matter 写 apache-2.0，正文写禁商用声明（docs/11 §11.9.2 的事故形态）。
# 五个触发形态都放进一份 README，因为 `audit_license` 抛出的消息列的是**全部命中词**，
# 缺一个形态就不触发对应断言（不是产品漏检，是 fixture 太薄）。
FAKE_README_NC = (
    "---\n"
    "license: apache-2.0\n"
    "---\n"
    "\n"
    "# Fixture dialect TTS corpus\n"
    "\n"
    "| License | CC BY-NC-ND 4.0 |\n"
    "\n"
    "This dataset is released for non-commercial use only.\n"
    "Usage restricted: NonCommercial research. SPDX: BY-NC-ND 4.0. 仅限学术用途，禁止商用。\n"
)

PAYLOAD = b'{"dialogue": [{"speaker": "u", "text": "hi"}]}\n'
EXTRA = b'{"role": "agent", "scene": "billing"}\n'


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakePlatform(ModelScope):
    """不调网络的测试平台：meta / README / tree / 下载全部返回测试固定值。"""

    def __init__(self, *, readme=FAKE_README_CLEAN, platform_license="Apache License 2.0", tree=None,
                 license_body=""):
        super().__init__()
        # name 必须保持 "modelscope"：`_platform_license` 按 name 分派字段位置
        # （ModelScope 取 meta["License"]，HF 取 meta["cardData"]["license"]）。替身返回的是
        # ModelScope 形状的 meta，改成别的值会落到 HF 分支返回 None，表现为「平台未给许可」的
        # 假阴性，而不是替身配置错误。name 只用于身份分派，与 PLATFORMS 的注册键无关。
        self.name = "modelscope"
        self.readme = readme
        self.platform_license = platform_license
        self.license_body = license_body
        self.tree_entries = tree if tree is not None else [
            {"path": "data/CSConv.json", "size": len(PAYLOAD), "declared_sha256": "", "is_lfs": False},
            {"path": ".gitattributes", "size": 12, "declared_sha256": "", "is_lfs": False},
        ]

    def meta(self, sid):
        return {"License": self.platform_license}

    def readme_raw_url(self, sid, rev):
        return f"fake://readme/{sid}"

    def evidence_url(self, sid, rev):
        return f"fake://blob/{sid}/README.md"

    def tree(self, sid, rev):
        return list(self.tree_entries)

    def download_url(self, sid, rev, path):
        return f"fake://download/{sid}/{path}"


def install_platform(platform: FakePlatform):
    """挂假平台 + 挂**离线** http_get 替身（卡内：测试不得联网）。

    http_get 的 README / meta 内容与 `platform.readme` / `platform.platform_license` 保持同源，
    保证 `audit_license` 测到的是产品代码、不是测试副本。
    """
    old_plat = PLATFORMS.get("fake-modelscope")
    PLATFORMS["fake-modelscope"] = platform
    old_get = fetch_mod.http_get
    offline = OfflineHttpGet(readme=platform.readme, platform_license=platform.platform_license,
                             license_body=getattr(platform, "license_body", ""))
    fetch_mod.http_get = offline
    return old_plat, old_get


def uninstall(old):
    if old is None:
        PLATFORMS.pop("fake-modelscope", None)
        fetch_mod.http_get = OfflineHttpGet()
    else:
        PLATFORMS["fake-modelscope"] = old


class OfflineHttpGet:
    """测试级 `http_get` 替身：**绝不联网**，按 URL 形态返回测试替身 README。

    未覆盖的 URL 直接抛 FetchError（fail-closed）——任何意外的联网尝试都会让测试响亮地失败。
    """

    def __init__(self, *, readme=FAKE_README_CLEAN, platform_license="Apache License 2.0",
                 license_body=""):
        self.readme = readme
        self.platform_license = platform_license
        self.license_body = license_body
        self.seen_urls = []  # 记录收到的 URL，便于断言替身确实覆盖了全部请求形态

    def __call__(self, url, *, timeout=180, binary=False):
        self.seen_urls.append(url)
        # 假平台子类复用了 ModelScope.readme_raw_url 的 ...repo?FilePath=README.md 形态
        if "FilePath=README.md" in url or "/readme/" in url:
            return self.readme
        # readme_source 指向的仓内文件（非 README 时走 download_url 的下载形态）
        if self.license_body and "fake://download/" in url and url.rstrip("/").rsplit("/", 1)[-1] != "README.md":
            return self.license_body
        if "api/v1/" in url or url.startswith("fake://"):
            return self.meta_doc(self.platform_license)
        # 假平台子类复用了 ModelScope._api 的 https://modelscope.cn/api/v1/datasets/<id> 调用
        # → 返回与 meta() 同源的许可字段。**这条必须留**：删掉它，FakePlatform 就不必再实现 meta()，
        # 而是真的去请求 —— 那样测试就**联网**了。
        raise FetchError(f"离线测试收到未预期的请求（不应联网）: {url}")

    @staticmethod
    def meta_doc(platform_license: str) -> str:
        """与 meta() 同源的模型平台元信息 JSON（fake:// 与 modelscope /api/v1 两种 URL 形态共用）。"""
        return json.dumps({"Code": 200, "Data": {"License": platform_license}})


class FakeHttpResponse:
    """够假的 urllib 响应：`read()` 返回原始 bytes，可 `with` 使用。"""

    def __init__(self, data: bytes):
        self._data = data

    def read(self, size=-1):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUrnOpen:
    """替换 `urllib.request.urlopen`：**不联网**，返回固定响应（可携 HTTPError）。

    `calls` 记录实际发起的请求次数——用来断言「4xx 不重试」；
    `sleeps` 记录实际等待次数——用来断言「没有白等重试」，两个断言一起才排除
    「改了分支但还在等」这类半个修复。
    """

    def __init__(self, *, http_error: "urllib.error.HTTPError | None" = None, data=b""):
        self.http_error = http_error
        self.data = data
        self.calls = 0
        self.sleeps = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        if self.http_error is not None:
            raise self.http_error
        return FakeHttpResponse(self.data)

    def install(self):
        """挂 FakeUrnOpen + 屏蔽 sleep（测试里不许真的等 2s/4s），返回可还原的快照。"""
        old_open = urllib.request.urlopen
        old_sleep = fetch_mod.time.sleep

        def noop_sleep(secs):
            self.sleeps += 1

        urllib.request.urlopen = self
        fetch_mod.time.sleep = noop_sleep
        return old_open, old_sleep

    def restore(self, snapshot):
        urllib.request.urlopen, fetch_mod.time.sleep = snapshot


class FakeHttpDownload:
    """替换 `http_download_to_file`：不联网，按给定内容**分块**写盘并返回实测值。"""

    def __init__(self, content: bytes, *, chunk: int = 1 << 20):
        self.content = content
        self.chunk = chunk
        self.calls = 0
        self.max_buffer = 0
        self.blocks_written = 0

    def __call__(self, url, dest, *, chunk=None):
        self.calls += 1
        chunk = chunk or self.chunk
        self.max_buffer = max(self.max_buffer, chunk)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out = bytearray()
        for i in range(0, len(self.content), chunk):
            out.extend(self.content[i : i + chunk])
            self.blocks_written += 1
        dest.write_bytes(bytes(out))
        return len(self.content), sha256(self.content)


def src(**overrides) -> dict:
    base = {
        "id": "fixture/ns__dataset",
        "platform": "fake-modelscope",
        "kind": "dataset",
        "revision": "master",
        "files": ["data/CSConv.json"],
        "expect_license": ["apache-2.0"],
        "why": "fixture 来源，测试用",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 许可审计
# ---------------------------------------------------------------------------
class LicenseAuditTests(unittest.TestCase):
    """验收 2（许可负例，消息含命中词）/ 验收 5（审计通过）。"""

    def setUp(self):
        self._old = install_platform(FakePlatform())

    def tearDown(self):
        if self._old is None:
            PLATFORMS.pop("fake-modelscope", None)
        else:
            PLATFORMS["fake-modelscope"] = self._old
        fetch_mod.http_get = OfflineHttpGet()

    def test_clean_license_audits_through(self):
        out = audit_license(src())
        self.assertEqual(out["license_spdx"], "apache-2.0")
        self.assertEqual(out["license_readme_raw"], "Apache License 2.0")
        self.assertEqual(out["noncommercial_hits"], [])
        self.assertEqual(out["noncommercial_hits_body"], [])
        self.assertIn("正文证据与 README 均无禁商用或禁演绎声明", out["audit_checks"])

    def test_frontmatter_apache_body_noncommercial_is_rejected_with_matched_words(self):
        install_platform(FakePlatform(readme=FAKE_README_NC, platform_license="apache-2.0"))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(src(expect_license=["apache-2.0"]))
        msg = str(cm.exception)
        # 卡内要求：错误消息必须包含**命中的具体词**（照 docs/11 §11.9.2）。
        # 断言的是 fetch.py 里 NONCOMMERCIAL_PATTERNS 的真实条目形态，不是 README 里的原句
        # （"CC BY-NC-ND 4.0" 会被拆成 BY-NC / NC-ND / ND 4\.0 三个命中词报出来）。
        for word in ("NonCommercial", "Non-Commercial", "BY-NC", "NC-ND", "禁止商用"):
            self.assertIn(word, msg)
        # front-matter 的宽松许可也不能放行
        self.assertIn("apache-2.0", msg)
        self.assertIn("无放行口", msg)
        self.assertIn("fixture/ns__dataset", msg)

    def test_unrecognised_noncommercial_license_is_rejected_as_nc(self):
        readme = FAKE_README_CLEAN.replace("Apache License 2.0", "cc-by-nc-4.0")
        install_platform(FakePlatform(readme=readme, platform_license="cc-by-nc-4.0"))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(src(expect_license=["cc-by-nc-4.0"]))
        msg = str(cm.exception)
        # `cc-by-nc-4.0` 含 NC，会被 ③b 的禁商用/禁演绎扫描先拦下——这是更强的拒绝
        # （比"不可识别"更硬：不猜是什么许可，直接判定含非商用条款）。
        self.assertIn("禁商用", msg)
        self.assertIn("BY-NC", msg)
        self.assertIn("无放行口", msg)
        # 且必须是这条而不是"许可不在允许集合内"——后者意味着它被识别成了一个具体许可
        self.assertNotIn("不在允许集合", msg)
        # 归一化路径本身：该写法确实归一为 None（集合外，不猜、不近似匹配）
        self.assertIsNone(fetch_mod._normalize_license("cc-by-nc-4.0"))

    def test_unrecognised_license_without_nc_is_rejected_as_unrecognised(self):
        # 与上一例区分：一个**既不在允许集合、又不含 NC 字样**的许可写法，
        # 必须走「都未给出可识别的许可」这一支，而不是被误当成"不在允许集合"（不猜）。
        readme = FAKE_README_CLEAN.replace("Apache License 2.0", "some-house-license-xyz")
        install_platform(FakePlatform(readme=readme, platform_license="some-house-license-xyz"))
        with self.assertRaises(FetchError) as cm:
            audit_license(src(expect_license=["some-house-license-xyz"]))
        self.assertIn("都未给出可识别的许可", str(cm.exception))
        self.assertNotIn("不在允许集合", str(cm.exception))
        self.assertIsNone(fetch_mod._normalize_license("some-house-license-xyz"))

    def test_platform_label_alone_is_not_trusted(self):
        readme = "# Fixture\n\nNo front matter here, only a platform tag.\n"
        install_platform(FakePlatform(readme=readme, platform_license="mit"))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(src(expect_license=["mit"]))
        self.assertIn("平台标签不可信", str(cm.exception))

    def test_commercial_warning_without_ack_is_rejected(self):
        readme = FAKE_README_CLEAN + "\n版权归某数据商所有，商用数据，需采购。\n"
        install_platform(FakePlatform(readme=readme))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(src(expect_license=["apache-2.0"]))
        msg = str(cm.exception)
        for word in ("版权归", "商用数据", "license_keyword_ack"):
            self.assertIn(word, msg)

    def test_commercial_warning_with_explicit_ack_passes(self):
        readme = FAKE_README_CLEAN + "\n版权归某数据商所有，商用数据，需采购。\n"
        install_platform(FakePlatform(readme=readme))
        out = audit_license(src(expect_license=["apache-2.0"], license_keyword_ack="法务已核（2026-09-18）"))
        self.assertEqual(out["license_spdx"], "apache-2.0")
        self.assertEqual(len(out["commercial_warning_hits"]), 3)

    def test_license_conflict_between_platform_and_readme_is_rejected(self):
        install_platform(FakePlatform(readme=FAKE_README_CLEAN, platform_license="MIT"))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(src(expect_license=["mit"]))
        msg = str(cm.exception)
        for word in ("mit", "apache-2.0", "以 README 为准"):
            self.assertIn(word, msg)

    def test_missing_sources_file_fails_closed(self):
        with self.assertRaises(FetchError) as cm:
            fetch_mod.load_sources(Path("/tmp/vox-no-such-sources.json"))
        self.assertIn("/tmp/vox-no-such-sources.json", str(cm.exception))


# ---------------------------------------------------------------------------
# HTTP 取数：4xx 不重试 / 严格 UTF-8 / revision 缺省（T12c 修 3、4、7）
# ---------------------------------------------------------------------------
class HttpGetTests(unittest.TestCase):
    """http_get 的失败形态。全部**离线**：urlopen 由测试替身返回固定响应，不联网。

    负例断言的都是**具体非法值**（状态码 / URL / 字节），不是「抛了个错」——
    否则「改回了默认行为但报错」也会假通过。
    """

    TARGET = "https://modelscope.cn/api/v1/datasets/ns%2Fdataset/repo?FilePath=README.md"

    def test_http_404_fails_fast_without_any_retry_wait(self):
        """T12c 修 3：4xx 是服务端明确拒绝，不是网络抖动——重试只会白等 2s+4s。"""
        fake = FakeUrnOpen(http_error=urllib.error.HTTPError(
            self.TARGET, 404, "Not Found", {}, io.BytesIO(b"")))
        snapshot = fake.install()
        try:
            with self.assertRaises(FetchError) as cm:
                http_get(self.TARGET)
        finally:
            fake.restore(snapshot)

        msg = str(cm.exception)
        self.assertIn("404", msg)                      # 消息保留状态码
        self.assertIn(self.TARGET, msg)                # 以及出错的 URL
        self.assertIn("不重试", msg)
        self.assertEqual(fake.calls, 1)                # 关键：只发了一次请求
        self.assertEqual(fake.sleeps, 0)               # 以及一次等待都没有发生

    def test_network_error_still_retries(self):
        """对照：修 3 只该影响 4xx 这一支——真正的 URLError 仍按原次数重试（未放宽重试纪律）。"""
        fake = FakeUrnOpen(http_error=urllib.error.URLError("DNS failure"))
        snapshot = fake.install()
        try:
            with self.assertRaises(FetchError) as cm:
                http_get("https://modelscope.cn/api/v1/x")
        finally:
            fake.restore(snapshot)

        self.assertEqual(fake.calls, 3)                # 三次全试，没提前放弃
        self.assertEqual(fake.sleeps, 2)               # 2s + 4s 两次等待
        self.assertIn("重试 3 次", str(cm.exception))

    def test_non_utf8_readme_fails_closed_with_url(self):
        """T12c 修 4：README 必须严格 UTF-8——乱码正文会让许可关键词扫描空转「通过」。"""
        url = "https://hf-mirror.com/datasets/ns/raw/main/README.md"
        broken = b"---\nlicense: apache-2.0\n---\n# \xff\xfe\x2e"
        # 前置不变式：这段字节确实不是合法 UTF-8（否则下面的 FetchError 断言就没有意义）
        with self.assertRaises(UnicodeDecodeError):
            broken.decode("utf-8")
        fake = FakeUrnOpen(data=broken)
        snapshot = fake.install()
        try:
            with self.assertRaises(FetchError) as cm:
                http_get(url)
        finally:
            fake.restore(snapshot)

        msg = str(cm.exception)
        self.assertIn("README", msg)
        self.assertIn(url, msg)                       # 必须点名出错的 URL
        self.assertIn("UTF-8", msg)
        self.assertNotIn("\ufffd", msg)                # 不是替换符糊过去了

    def test_binary_mode_is_unaffected_by_strict_decoding(self):
        """binary=True 走 bytes 路径：同一段非 UTF-8 字节在二进制模式下一切正常（不动它）。"""
        broken = b"---\nlicense: apache-2.0\n---\n# \xff\xfe\x2e"
        fake = FakeUrnOpen(data=broken)
        snapshot = fake.install()
        try:
            out = http_get("https://example.invalid/x", binary=True)
        finally:
            fake.restore(snapshot)
        self.assertIsInstance(out, bytes)
        self.assertEqual(out, broken)
        self.assertEqual(fake.calls, 1)


class AuditSkipAndRevisionTests(unittest.TestCase):
    """T12c 修 6（缺 expect_license 留痕）/ 修 7（审计侧 revision 缺省钉在 DEFAULT_REVISION）。"""

    def setUp(self):
        self._old_plat, self._old_get = install_platform(FakePlatform())

    def tearDown(self):
        if self._old_plat is None:
            PLATFORMS.pop("fake-modelscope", None)
        else:
            PLATFORMS["fake-modelscope"] = self._old_plat
        fetch_mod.http_get = self._old_get

    def test_missing_expect_license_is_recorded_in_audit_checks(self):
        """缺声明仍跳过核对，但审计记录里必须**留痕**——否则「通过」与「没核对」无法区分。"""
        out = audit_license(src(expect_license=[]))
        self.assertEqual(out["license_spdx"], "apache-2.0")
        self.assertTrue(any("④" in c and "expect_license 未声明" in c and "跳过核对" in c
                            for c in out["audit_checks"]))
        self.assertIn("审计通过：许可 = apache-2.0", out["audit_checks"])
        # 行为仍是跳过：不声明也不算不过
        self.assertEqual(out["license_spdx"], "apache-2.0")

    def test_audit_skips_check_only_when_expect_license_is_missing(self):
        """对照：声明了 expect_license 时**不**留「跳过」痕迹（不是无脑追加一条）。"""
        out = audit_license(src(expect_license=["apache-2.0"]))
        self.assertFalse(any("跳过核对" in c for c in out["audit_checks"]))

    def test_declared_license_mismatch_still_rejects(self):
        """留痕不等于放行：声明与实测冲突仍按原有 fail-closed 处理。"""
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(src(expect_license=["mit"]))
        self.assertIn("mit", str(cm.exception))
        self.assertIn("apache-2.0", str(cm.exception))

    def test_audit_without_revision_declaration_reads_default_revision(self):
        """T12c 修 7 / 钉住 T12b 修 5：缺省 revision 只认 DEFAULT_REVISION（"main"）。

        master 与 main 的 README 是**两份不同内容**：main 侧合规、master 侧只写 CC BY-NC-ND。
        若审计侧缺省仍取 master，这份禁商用语料会被拒；取 main 才过。所以「审计通过」本身就是
        它读对了侧的证据——这个断言会真失败，不会因为两侧都合规而假通过。
        """
        self.assertEqual(DEFAULT_REVISION, "main")
        readme_main = (
            "---\nlicense: Apache License 2.0\n---\n\n# Fixture dataset (main)\n\n"
            "Released under Apache License 2.0. No commercial restrictions.\n"
        )
        readme_master = (
            "---\nlicense: Apache License 2.0\n---\n\n# Fixture dataset (master)\n\n"
            "This dataset is released for non-commercial use only. SPDX: CC BY-NC-ND 4.0.\n"
        )

        def offline_get(url, *, timeout=180, binary=False):
            assert "Revision=main" in url, f"审计侧请求的不是 DEFAULT_REVISION: {url}"
            if "Revision=main" in url:
                return readme_main
            if "Revision=master" in url:
                return readme_master
            raise FetchError(f"离线测试收到未预期的请求: {url}")

        # FakePlatform 的 URL 是 `fake://readme/{sid}?Revision={rev}`，替身按 Revision 参数分派；
        # evidence_url 同样带上 revision，让「审计取的是哪一侧」能从产物本身直接看出
        fake = FakePlatform()
        fake.readme_raw_url = lambda sid, rev: f"fake://readme/{sid}?Revision={rev}"
        fake.evidence_url = lambda sid, rev: f"fake://blob/{sid}?Revision={rev}"
        PLATFORMS["fake-modelscope"] = fake
        old_get = fetch_mod.http_get
        fetch_mod.http_get = offline_get
        try:
            out = audit_license({k: v for k, v in src(files=[], expect_license=["apache-2.0"]).items()
                                if k != "revision"})
        finally:
            fetch_mod.http_get = old_get
            install_platform(FakePlatform())

        self.assertEqual(out["license_spdx"], "apache-2.0")      # 取的是 main 侧，不是 master 侧
        self.assertIn("main", out["license_evidence_url"])
        self.assertEqual(out["noncommercial_hits"], [])


# ---------------------------------------------------------------------------
# 哈希与体量校验
# ---------------------------------------------------------------------------
class HashVerificationTests(unittest.TestCase):
    """验收 3（平台声明 sha256 ≠ 实测 → 报错且消息含两个哈希前缀）。"""

    def setUp(self):
        self._old_platform = install_platform(FakePlatform())
        self._old_dl = fetch_mod.http_download_to_file

    def tearDown(self):
        if self._old_platform is None:
            PLATFORMS.pop("fake-modelscope", None)
        else:
            PLATFORMS["fake-modelscope"] = self._old_platform
        fetch_mod.http_download_to_file = self._old_dl
        fetch_mod.http_get = OfflineHttpGet()

    def _fresh_out(self, name: str) -> Path:
        out_root = Path(f"/tmp/vox-fake-{name}")
        if out_root.exists():
            shutil.rmtree(out_root)
        return out_root

    def test_declared_hash_mismatch_raises_with_both_prefixes(self):
        declared = "aaaa" + "0" * 60
        tree = [{"path": "data/CSConv.json", "size": len(PAYLOAD), "declared_sha256": declared, "is_lfs": True}]
        install_platform(FakePlatform(tree=tree))
        out_root = self._fresh_out("hashcheck")
        fetch_mod.http_download_to_file = FakeHttpDownload(PAYLOAD)
        try:
            with self.assertRaises(FetchError) as cm:
                process(src(), dry_run=False, out_root=out_root, repo_root=REPO_ROOT)
            msg = str(cm.exception)
            self.assertIn("sha256 不符", msg)
            self.assertIn("data/CSConv.json", msg)
            # 必须同时含**实测**与**平台声明**两个哈希的前缀
            self.assertIn(sha256(PAYLOAD)[:16], msg)
            self.assertIn(declared[:16], msg)
        finally:
            shutil.rmtree(out_root, ignore_errors=True)

    def test_size_mismatch_raises_with_both_sizes(self):
        tree = [{"path": "data/CSConv.json", "size": 999999, "declared_sha256": "", "is_lfs": False}]
        install_platform(FakePlatform(tree=tree))
        out_root = self._fresh_out("sizecheck")
        fetch_mod.http_download_to_file = FakeHttpDownload(PAYLOAD)
        try:
            with self.assertRaises(FetchError) as cm:
                process(src(), dry_run=False, out_root=out_root, repo_root=REPO_ROOT)
            msg = str(cm.exception)
            self.assertIn("体量不符", msg)
            self.assertIn("999999", msg)
            self.assertIn(str(len(PAYLOAD)), msg)
        finally:
            shutil.rmtree(out_root, ignore_errors=True)

    def test_matching_declared_hash_passes(self):
        tree = [{"path": "data/CSConv.json", "size": len(PAYLOAD), "declared_sha256": sha256(PAYLOAD), "is_lfs": True}]
        install_platform(FakePlatform(tree=tree))
        out_root = self._fresh_out("match")
        fetch_mod.http_download_to_file = FakeHttpDownload(PAYLOAD)
        try:
            entry = process(src(), dry_run=False, out_root=out_root, repo_root=REPO_ROOT)
        finally:
            shutil.rmtree(out_root, ignore_errors=True)
        self.assertEqual(len(entry["files"]), 1)
        self.assertTrue(entry["files"][0]["verified_against_platform"])
        self.assertEqual(entry["files"][0]["sha256"], sha256(PAYLOAD))


# ---------------------------------------------------------------------------
# 仓库边界
# ---------------------------------------------------------------------------
class PathGuardTests(unittest.TestCase):
    """验收 4（--out-root 指向仓库内 → 报错，且不得先建目录再报错）。"""

    def test_out_root_inside_repo_raises_before_mkdir(self):
        target = REPO_ROOT / "tools" / "should_never_exist__out_root"
        self.assertFalse(target.exists())
        with self.assertRaises(FetchError) as cm:
            resolve_out_root(target, REPO_ROOT)
        msg = str(cm.exception)
        self.assertIn("--out-root 不得指向仓库内", msg)
        self.assertIn("docs/11 裁定 2", msg)
        # 关键：报错之前**没有**建目录
        self.assertFalse(target.exists())

    def test_out_root_equal_to_repo_root_raises(self):
        with self.assertRaises(FetchError) as cm:
            resolve_out_root(REPO_ROOT, REPO_ROOT)
        self.assertIn("--out-root 不得指向仓库内", str(cm.exception))

    def test_out_root_outside_repo_is_allowed(self):
        out = resolve_out_root(Path("/tmp/vox-corpus-ok"), REPO_ROOT)
        self.assertTrue(out.is_absolute())
        self.assertEqual(out.resolve(), Path("/tmp/vox-corpus-ok").resolve())
        self.assertNotIn("newProject", str(out))

    def test_dest_dir_inside_repo_is_rejected_even_with_valid_out_root(self):
        # 双保险：即使 out_root 合法，拼接后的目标目录也不得落进仓库
        install_platform(FakePlatform())
        old_dl = fetch_mod.http_download_to_file
        old_get = fetch_mod.http_get
        fetch_mod.http_download_to_file = FakeHttpDownload(PAYLOAD)
        fetch_mod.http_get = OfflineHttpGet()
        try:
            with self.assertRaises(FetchError) as cm:
                process(
                    src(),
                    dry_run=False,
                    out_root=REPO_ROOT / "packs",  # 仓库内
                    repo_root=REPO_ROOT,
                )
            self.assertIn("仓库内", str(cm.exception))
        finally:
            fetch_mod.http_download_to_file = old_dl
            fetch_mod.http_get = old_get


# ---------------------------------------------------------------------------
# 端到端离线全链
# ---------------------------------------------------------------------------
class EndToEndOfflineTests(unittest.TestCase):
    """验收 5（fixture 假响应跑完「审计→下载→校验→写台账」全链）。"""

    def setUp(self):
        self.tmpdir = Path("/tmp/vox-fake-e2e")
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)
        self.out_root = self.tmpdir / "out"
        self.lock_path = self.out_root / "corpus.lock.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_chain_offline_with_fixture_responses(self):
        # 两个文件：LFS（平台给了哈希 → verified_against_platform=true）、
        # 普通文件（平台没给哈希 → 必须是 false，不得填 true）
        tree = [
            {"path": "data/CSConv.json", "size": len(PAYLOAD), "declared_sha256": sha256(PAYLOAD), "is_lfs": True},
            {"path": "meta/manifest.json", "size": len(EXTRA), "declared_sha256": "", "is_lfs": False},
        ]
        install_platform(FakePlatform(tree=tree))
        blobs = {"data/CSConv.json": PAYLOAD, "meta/manifest.json": EXTRA}
        old_dl = fetch_mod.http_download_to_file
        chunks: dict = {}

        def download(url, dest, *, chunk=1 << 20):
            # fake://download/<id>/<path> —— id 可能含 "/"（fixture/two__files），
            # 所以按 sources 声明的文件路径去匹配，而不是按 id 段数切。
            rel = next(
                (p for p in blobs if url.endswith("/" + p)),
                None,
            )
            self.assertIn(rel, blobs, f"假下载收到未预期的路径: {rel}（url={url}）")
            dl = FakeHttpDownload(blobs[rel], chunk=chunk)
            chunks[rel] = dl
            return dl(url, dest, chunk=chunk)

        try:
            fetch_mod.http_download_to_file = download
            entry = process(
                src(id="fixture/two__files", files=["data/CSConv.json", "meta/manifest.json"]),
                dry_run=False,
                out_root=self.out_root,
                repo_root=REPO_ROOT,
            )
        finally:
            fetch_mod.http_download_to_file = old_dl
            # install_platform 已装好离线 http_get；这里还原为缺省离线替身
            fetch_mod.http_get = OfflineHttpGet()

        # —— 审计段 ——
        self.assertEqual(entry["id"], "fixture/two__files")
        self.assertEqual(entry["license_spdx"], "apache-2.0")
        self.assertEqual(entry["size_bytes"], len(PAYLOAD) + len(EXTRA))
        # —— 校验段：每文件 size/sha256 与 fixture 一致 ——
        files = {f["path"]: f for f in entry["files"]}
        self.assertEqual(files["data/CSConv.json"]["size_bytes"], len(PAYLOAD))
        self.assertEqual(files["data/CSConv.json"]["sha256"], sha256(PAYLOAD))
        self.assertTrue(files["data/CSConv.json"]["verified_against_platform"])
        # 平台未给哈希 → 必须 false，不得填 true
        self.assertEqual(files["meta/manifest.json"]["declared_sha256"], "")
        self.assertFalse(files["meta/manifest.json"]["verified_against_platform"])
        self.assertEqual(files["meta/manifest.json"]["size_bytes"], len(EXTRA))
        self.assertEqual(files["meta/manifest.json"]["sha256"], sha256(EXTRA))
        # 顶层 sha256 = manifest 摘要（对 (path, size, sha256) 排序记录求 sha256）
        self.assertEqual(entry["sha256"], manifest_digest(entry["files"]))
        # —— 流式段：两次下载都真的走了分块写盘 ——
        self.assertEqual(chunks["data/CSConv.json"].calls, 1)
        self.assertEqual(chunks["meta/manifest.json"].calls, 1)
        # —— 台账段 ——
        write_lock(self.lock_path, [entry])
        doc = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(doc["_schema"], "vox-corpus-lock/2")
        self.assertEqual(len(doc["entries"]), 1)
        self.assertEqual(doc["entries"][0]["size_bytes"], len(PAYLOAD) + len(EXTRA))
        by_path = {f["path"]: f for f in doc["entries"][0]["files"]}
        self.assertEqual(by_path["data/CSConv.json"]["sha256"], sha256(PAYLOAD))
        self.assertFalse(by_path["meta/manifest.json"]["verified_against_platform"])
        # 真实文件在盘上 → recheck 零异常（T36：台账里是 ${CORPUS_ROOT} 锚定值，
        # 必须把同一根传进去才还原得到——指错根会报「缺失」，这是设计而非缺陷）
        self.assertEqual(recheck(self.lock_path, self.out_root), 0)
        # 负例（同一份台账，根指错）：必须报异常并以 1 退出，不得静默通过
        wrong_root = self.tmpdir / "not-the-corpus-root"
        wrong_root.mkdir()
        self.assertEqual(recheck(self.lock_path, wrong_root), 1)

    def test_dry_run_audits_without_downloading_or_writing(self):
        install_platform(FakePlatform())
        old_get = fetch_mod.http_get
        entry = process(src(), dry_run=True, out_root=self.out_root, repo_root=REPO_ROOT)
        fetch_mod.http_get = old_get
        self.assertTrue(entry["dry_run"])
        self.assertFalse(self.out_root.exists())
        self.assertFalse(self.lock_path.exists())


class MainCliTests(unittest.TestCase):
    """验收 7 坑 1（CLI 级：缺省跳过 optional 必须**响亮打印**；--only 可拉单个 optional）。"""

    def _make_sources(self, tmp: Path, with_optional: bool = True) -> Path:
        sources = {"sources": [
            {"id": "a/normal", "platform": "fake-modelscope", "kind": "dataset",
             "files": ["data/CSConv.json"], "expect_license": ["apache-2.0"]},
        ]}
        if with_optional:
            sources["sources"].append(
                {"id": "b/optional-big", "platform": "fake-modelscope", "kind": "dataset",
                 "optional": True, "files": ["data/CSConv.json"], "expect_license": ["apache-2.0"]}
            )
        p = tmp / "sources.json"
        p.write_text(json.dumps(sources), encoding="utf-8")
        return p

    def _run(self, tmp: Path, argv: list) -> tuple:
        install_platform(FakePlatform())
        old_dl = fetch_mod.http_download_to_file
        old_get = fetch_mod.http_get
        out = io.StringIO()
        try:
            fetch_mod.http_download_to_file = FakeHttpDownload(PAYLOAD)
            fetch_mod.http_get = OfflineHttpGet()
            with redirect_stdout(out):
                code = fetch_mod.main(argv)
        finally:
            fetch_mod.http_download_to_file = old_dl
            fetch_mod.http_get = old_get
        return code, out.getvalue()

    def test_default_run_loudly_prints_the_optional_skip(self):
        tmp = Path("/tmp/vox-fake-cli")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            out_root = tmp / "out"
            code, out = self._run(
                tmp,
                [
                    "--sources", str(self._make_sources(tmp)),
                    "--lock", str(out_root / "corpus.lock.json"),
                    "--out-root", str(out_root),
                    "--repo-root", str(REPO_ROOT),
                ],
            )
            self.assertEqual(code, 0)
            # 关键：跳过声明必须打印出来（静默跳过 = 让人以为已经下了）
            self.assertIn("跳过 optional", out)
            self.assertIn("b/optional-big", out)
            # 缺省 dry-run 不写台账（与原脚本一致：dry-run 只审计）；
            # 条目数断言改用在非 dry-run 场景下验证。
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_only_flag_pulls_a_single_optional_source(self):
        tmp = Path("/tmp/vox-fake-cli-only")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        try:
            out_root = tmp / "out"
            code, out = self._run(
                tmp,
                [
                    "--sources", str(self._make_sources(tmp)),
                    "--lock", str(out_root / "corpus.lock.json"),
                    "--out-root", str(out_root),
                    "--repo-root", str(REPO_ROOT),
                    "--only", "b/optional-big",
                ],
            )
            self.assertEqual(code, 0)
            self.assertNotIn("跳过 optional", out)  # 显式点名拉取，不该再打印跳过声明
            doc = json.loads((out_root / "corpus.lock.json").read_text(encoding="utf-8"))
            self.assertEqual([e["id"] for e in doc["entries"]], ["b/optional-big"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_out_root_inside_repo_refused_via_cli(self):
        # CLI 级重验：--out-root 指向仓库内 → 报错且**不建目录**
        tmp = Path("/tmp/vox-fake-cli-guard")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        bad = REPO_ROOT / "tools" / "should_never_exist__cli_out_root"
        self.assertFalse(bad.exists())
        try:
            with self.assertRaises(FetchError) as cm:
                self._run(
                    tmp,
                    [
                        "--sources", str(self._make_sources(tmp, with_optional=False)),
                        "--lock", str(bad / "corpus.lock.json"),
                        "--out-root", str(bad),
                        "--repo-root", str(REPO_ROOT),
                    ],
                )
            self.assertIn("--out-root 不得指向仓库内", str(cm.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(bad.exists())


class OptionalGateTests(unittest.TestCase):
    """验收 7 坑 1（optional 缺省跳过；--only 可显式拉单个 optional 项）。"""

    SOURCES = [
        {"id": "a/normal", "platform": "fake-modelscope", "kind": "dataset"},
        {"id": "b/optional-big", "platform": "fake-modelscope", "kind": "dataset", "optional": True},
    ]

    def test_default_skips_optional(self):
        picked, skipped = filter_sources(self.SOURCES)
        self.assertEqual([s["id"] for s in picked], ["a/normal"])
        # 跳过清单必须非空 → 调用方据此**响亮打印**跳过声明
        self.assertEqual(skipped, ["b/optional-big"])

    def test_only_can_pull_a_single_optional_item(self):
        picked, skipped = filter_sources(self.SOURCES, only="b/optional-big")
        self.assertEqual([s["id"] for s in picked], ["b/optional-big"])
        self.assertEqual(skipped, [])

    def test_include_optional_pulls_everything(self):
        picked, skipped = filter_sources(self.SOURCES, include_optional=True)
        self.assertEqual(len(picked), 2)
        self.assertEqual(skipped, [])

    def test_unknown_only_id_fails_closed(self):
        with self.assertRaises(FetchError) as cm:
            filter_sources(self.SOURCES, only="no/such-id")
        self.assertIn("no/such-id", str(cm.exception))


class StreamingDownloadTests(unittest.TestCase):
    """验收 7 坑 2（流式分块写盘 + 边下边算 sha256；失败不留下 .part 残留）。"""

    def test_streaming_writes_in_chunks_and_hashes_while_downloading(self):
        payload = b"x" * (3 * 1024 + 7)
        dest = Path("/tmp/vox-fake-stream/big.json")
        if dest.parent.exists():
            shutil.rmtree(dest.parent)
        patch = FakeHttpDownload(payload, chunk=1024)
        old = fetch_mod.http_download_to_file
        fetch_mod.http_download_to_file = patch
        try:
            size, digest = fetch_mod.http_download_to_file("fake://download/x/big.json", dest, chunk=1024)
        finally:
            fetch_mod.http_download_to_file = old
        self.assertEqual(size, len(payload))
        self.assertEqual(digest, sha256(payload))
        # 一次只读一个 chunk：不得"一次性读进内存"
        self.assertEqual(patch.max_buffer, 1024)
        self.assertEqual(patch.blocks_written, 4)
        self.assertEqual(dest.read_bytes(), payload)
        self.assertFalse(dest.with_suffix(dest.suffix + ".part").exists())

    def test_failed_download_leaves_no_part_residue(self):
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                raise OSError("simulated socket read timeout")

        old_urlopen = fetch_mod.urllib.request.urlopen
        old_sleep = fetch_mod.time.sleep
        fetch_mod.urllib.request.urlopen = lambda req, timeout=None: R()
        fetch_mod.time.sleep = lambda s: None  # 测试里不等待退避
        dest = Path("/tmp/vox-fake-fail/big.parquet")
        if dest.parent.exists():
            shutil.rmtree(dest.parent)
        try:
            with self.assertRaises(FetchError) as cm:
                fetch_mod.http_download_to_file("fake://x", dest, chunk=64)
            msg = str(cm.exception)
            self.assertIn("下载失败", msg)
            self.assertIn("重试 3 次", msg)
            self.assertIn("fake://x", msg)
        finally:
            fetch_mod.urllib.request.urlopen = old_urlopen
            fetch_mod.time.sleep = old_sleep
        # 关键断言：失败不得留下可被误认为完整语料的 .part 残留
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_suffix(dest.suffix + ".part").exists())
        self.assertEqual(list(dest.parent.glob("*.part")), [])


class SelectFilesDeclarationTests(unittest.TestCase):
    """T12b 修 1：声明了具体路径时逐项核对，缺失就抛错——不静默丢弃。

    旧逻辑只要还有 ≥1 个命中就放行，被改名/删除的声明文件会被静默丢掉：不完整语料
    当完整入库，`manifest_digest` 只覆盖现存文件。
    """

    TREE = [
        {"path": "a.txt", "size": 1, "declared_sha256": "", "is_lfs": False},
        {"path": ".gitattributes", "size": 1, "declared_sha256": "", "is_lfs": False},
    ]

    def test_missing_declared_file_raises_with_its_full_name(self):
        # 验收 2 负例：声明 [a.txt, 缺失.txt] 而树里只有 a.txt
        with self.assertRaises(FetchError) as cm:
            select_files({"id": "d/ns", "files": ["a.txt", "缺失.txt"]}, self.TREE)
        msg = str(cm.exception)
        self.assertIn("缺失.txt", msg)  # 必须点名缺失的文件，不能只说「有缺失」
        self.assertIn("d/ns", msg)      # 以及来源 id
        self.assertIn("不完整语料不得入库", msg)

    def test_all_declared_files_present_passes(self):
        # 验收 2 正例：全部命中 → 正常返回（按 path 排序）
        picked = select_files(
            {"id": "d/ns", "files": ["data/CSConv.json", "data/extra.json"]},
            [
                {"path": "data/extra.json", "size": 1, "declared_sha256": "", "is_lfs": False},
                {"path": "data/CSConv.json", "size": 2, "declared_sha256": "", "is_lfs": False},
            ],
        )
        self.assertEqual([f["path"] for f in picked], ["data/CSConv.json", "data/extra.json"])

    def test_wildcard_declaration_keeps_the_old_behaviour(self):
        # 验收 2：`["*"]` 行为不变——`*` 不存在「漏了哪个」，也不受逐个核对影响
        picked = select_files({"id": "d/ns", "files": ["*"]}, self.TREE)
        self.assertEqual([f["path"] for f in picked], ["a.txt"])  # `.` 开头的照旧被跳过
        # 声明里同时含 `*` 与具体路径也走「不核对」分支（不因为多写一个路径就变严）
        picked2 = select_files({"id": "d/ns", "files": ["*", "not/there.txt"]}, self.TREE)
        self.assertEqual([f["path"] for f in picked2], ["a.txt"])

    def test_declared_dotfile_is_not_reported_missing(self):
        # 缺失核对只看**非 `.` 开头**的声明：声明里全是点文件时，不算「缺失」（否则会逼
        # 人为 .gitattributes 建条目），走的是既有的「筛选结果为空」这一支。
        with self.assertRaises(FetchError) as cm:
            select_files({"id": "d/ns", "files": [".gitattributes"]}, self.TREE)
        self.assertIn("文件筛选结果为空", str(cm.exception))
        self.assertNotIn("不存在", str(cm.exception))


class LockCriticalSectionTests(unittest.TestCase):
    """T12b 修 2 / 修 3：台账的「读旧 → 合并 → 写」必须在同一个锁临界区内。

    旧实现读在锁外、`write_lock` 只盖写：实例 B 在 A 提交前读到陈旧 old，A 释放锁后 B
    才写入 → A 的条目被静默覆盖，B 照常打「台账已写」exit 0。
    """

    def setUp(self):
        self.tmpdir = Path("/tmp/vox-fake-ucrit")
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.tmpdir / "corpus.lock.json"
        self.original = (
            fetch_mod._hold_lease,
            fetch_mod._read_old_entries,
            fetch_mod._write_doc_locked,
        )

    def tearDown(self):
        fetch_mod._hold_lease, fetch_mod._read_old_entries, fetch_mod._write_doc_locked = self.original
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_and_merge_happen_inside_the_held_lease(self):
        """关键断言：读旧台账与写，都发生在持有 lease 期间；持锁外读到的值不会被写回。"""
        observed = {}

        def fake_read(path):
            observed["read"] = observed.get("read", 0) + 1
            # 在「读」这一刻记下 lease 是否仍被持有——这就是「读在锁内」的证据
            observed["held_at_read"] = observed.get("held", None)
            return [{"id": "old/x", "files": []}]

        @contextmanager
        def real_lease(path):
            observed["held"] = True
            try:
                yield
            finally:
                observed["held"] = False

        fetch_mod._read_old_entries = fake_read
        fetch_mod._write_doc_locked = lambda p, e: observed.setdefault("write_entries", e)
        old_lease = fetch_mod._hold_lease
        fetch_mod._hold_lease = real_lease

        try:
            merged = update_lock(self.lock_path, lambda old: merge_lock(old, [{"id": "new/y", "files": []}]))
        finally:
            fetch_mod._hold_lease = old_lease

        self.assertEqual(observed["read"], 1)
        # 读旧台账是在**持有 lease 期间**发生的——而不是像旧实现那样在锁外读
        self.assertEqual(observed["held_at_read"], True)
        # 写也在持锁期间，且写的是**合并后**的条目（不是仅本次条目）
        self.assertIn("write_entries", observed)
        self.assertEqual([e["id"] for e in merged], ["new/y", "old/x"])
        # 临界区结束后锁已释放
        self.assertEqual(observed["held"], False)

    def test_update_lock_sees_a_concurrent_write_it_made_inside_the_same_run(self):
        """并发形态复现：B 在 A 释放锁前读到的不是陈旧值。

        注入方式：在持锁期间（读旧台账之后、写之前）另一个「实例」写入了台账；
        `update_lock` 因为读写同处一个 lease，B 的写入发生在持锁期**之后**，
        于是 A 的读拿到的是**包含 B 条目**的台账，不会用陈旧 old 覆盖掉 B。
        """
        lock = self.lock_path

        def reader_writing_second_instance(path):
            path.write_text(
                json.dumps(
                    # 故意保留 v1 形态：证明**读旧台账仍然可用**（向后兼容，T36 只升写入侧）
                    {"_schema": "vox-corpus-lock/1", "_note": "n",
                     "entries": [{"id": "concurrent/w", "files": []}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [{"id": "concurrent/w", "files": []}]

        # 第一实例（本进程内模拟）：持锁 → 读（此时读函数里已替第二实例写盘）→ 合并 → 写
        fetch_mod._read_old_entries = reader_writing_second_instance
        merged = update_lock(lock, lambda old: merge_lock(old, [{"id": "first/a", "files": []}]))

        doc = json.loads(lock.read_text(encoding="utf-8"))
        ids = [e["id"] for e in doc["entries"]]
        # 关键断言：第二实例的条目**没有被覆盖**——这正是 2026-09-18 那次事故的形态
        self.assertIn("concurrent/w", ids)
        self.assertIn("first/a", ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(ids), sorted([e["id"] for e in merged]))

    def test_malformed_old_ledger_fails_closed_without_touching_the_file(self):
        """T12b 修 3：旧台账是坏 JSON → 抛 FetchError，且原文件字节**未被改动**。"""
        bad = self.tmpdir / "corpus.lock.json"
        payload = b'{bad json"entries": [\n'  # 故意坏
        bad.write_bytes(payload)

        with self.assertRaises(FetchError) as cm:
            update_lock(bad, lambda old: merge_lock(old, [{"id": "n/new", "files": []}]))
        msg = str(cm.exception)
        self.assertIn("corpus.lock.json", msg)  # 消息含台账路径
        self.assertIn("拒绝覆盖", msg)
        self.assertIn("Expecting", msg)          # 以及解析错误摘要
        # 关键断言：原文件字节一个都没动（不是「警告后覆盖」）
        self.assertEqual(bad.read_bytes(), payload)
        # 失败后不留临时文件
        self.assertEqual(list(self.tmpdir.glob("corpus.lock.json.tmp")), [])
        # 台账结构没被改掉后仍可正常更新
        bad.write_text(json.dumps({"entries": [{"id": "recovered", "files": []}]}), encoding="utf-8")
        merged = update_lock(bad, lambda old: merge_lock(old, [{"id": "n/new", "files": []}]))
        self.assertEqual([e["id"] for e in merged], ["n/new", "recovered"])


class LockAtomicityTests(unittest.TestCase):
    """验收 7 坑 3（台账原子写：临时文件 + rename；检测到并发拒绝启动）。"""

    def setUp(self):
        self.tmpdir = Path("/tmp/vox-fake-lock")
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.tmpdir / "corpus.lock.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_lock_is_atomic_rename(self):
        write_lock(self.lock_path, [{"id": "a/b", "files": []}])
        doc = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for e in doc["entries"]], ["a/b"])
        self.assertEqual(doc["_schema"], "vox-corpus-lock/2")
        # 原子：没有留下临时文件
        self.assertEqual(list(self.tmpdir.glob("corpus.lock.json.tmp")), [])
        # 并发门禁的占位文件在同目录（仓外），不写进仓库
        self.assertTrue((self.tmpdir / "corpus.lock.json.lock").exists())

    def test_concurrent_instance_is_refused(self):
        lf = self.lock_path.with_suffix(self.lock_path.suffix + ".lock")
        fd = os.open(str(lf), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(FetchError) as cm:
                write_lock(self.lock_path, [{"id": "a/b", "files": []}])
            msg = str(cm.exception)
            for word in ("检测到并发实例", "拒绝启动", "并发写会丢台账条目"):
                self.assertIn(word, msg)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        # 拒绝之后仍可正常写；且拒绝时不留下半份台账
        self.assertFalse(self.lock_path.exists())
        write_lock(self.lock_path, [{"id": "a/b", "files": []}])
        self.assertTrue(self.lock_path.exists())

    def test_merge_lock_keeps_unprocessed_old_entries(self):
        old = [{"id": "old/untouched", "files": []}]
        new = [{"id": "new/one", "files": []}, {"id": "old/untouched", "files": [{"sha256": "x"}]}]
        merged = merge_lock(old, new)
        self.assertEqual([e["id"] for e in merged], ["new/one", "old/untouched"])
        self.assertEqual(len([e for e in merged if e["id"] == "old/untouched"]), 1)
        self.assertEqual(merged[1]["files"][0]["sha256"], "x")  # 新条目替换旧条目，不是重复保留


class RecheckTests(unittest.TestCase):
    """台账巡检：缺失与改动都要报出来（fail-closed，不能静默放过）。"""

    def setUp(self):
        self.tmpdir = Path("/tmp/vox-fake-recheck")
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)
        self.tmpdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_file(self, rel: str, content: bytes) -> str:
        p = self.tmpdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return str(p)

    def _lock_with(self, files: list) -> Path:
        lock = self.tmpdir / "corpus.lock.json"
        write_lock(lock, [{"id": "d/x", "files": files}])
        return lock

    def test_recheck_clean_returns_zero(self):
        lock = self._lock_with([
            {"path": "f1.json", "sha256": sha256(b"abc"), "local_file": self._write_file("d/f1.json", b"abc")},
            {"path": "f2.json", "sha256": sha256(b"def"), "local_file": self._write_file("d/f2.json", b"def")},
        ])
        self.assertEqual(recheck(lock), 0)

    def test_recheck_detects_missing_and_modified_files(self):
        a = self._write_file("d/f1.json", b"abc")
        gone = self._write_file("d/f2.json", b"def")
        Path(gone).unlink()  # 语料被删：必须留着让 recheck 报「缺失」
        Path(a).write_bytes(b"abc-modified")  # 内容被改：必须报「被改动」
        lock = self._lock_with([
            {"path": "f1.json", "sha256": sha256(b"abc"), "local_file": a},
            {"path": "f2.json", "sha256": sha256(b"def"), "local_file": gone},
        ])
        self.assertEqual(recheck(lock), 1)

    def test_portable_path_anchors_and_refuses_to_escape_root(self):
        """锚定与越界两条都要钉死：**越界必须抛错，不得退化成写绝对路径**。

        WHY 专门补这一条：注入验证实测——把越界分支改成「静默写绝对路径」时，
        其余全部测试仍然全绿。也就是说本卡最核心的那条判据原先**没有任何回归守卫**，
        只有人工注入能发现。
        """
        root = self.tmpdir / "root"
        root.mkdir()
        inside = root / "a" / "b.json"
        inside.parent.mkdir(parents=True)
        inside.write_bytes(b"y")
        self.assertEqual(portable_path(inside, root), "${CORPUS_ROOT}/a/b.json")

        outside = self.tmpdir / "outside" / "x.json"
        outside.parent.mkdir()
        outside.write_bytes(b"x")
        with self.assertRaises(FetchError) as cm:
            portable_path(outside, root)
        # 报错要能定位（带上越界路径与根），且明确指向 T36 的红线
        self.assertIn("不在语料根之下", str(cm.exception))
        self.assertIn("T36", str(cm.exception))

    def test_recheck_warns_on_unanchored_ledger_paths(self):
        """未锚定（v1 旧形态）的路径必须**留痕告警**，但退出码语义不变。"""
        lock = self._lock_with([
            {"path": "f1.json", "sha256": sha256(b"abc"), "local_file": self._write_file("d/f1.json", b"abc")},
        ])
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = recheck(lock, self.tmpdir)
        self.assertEqual(rc, 0)          # 兼容可读 → 仍退出 0（不得改坏退出码语义）
        self.assertIn("未按", err.getvalue())  # 但必须在 stderr 留痕
        self.assertIn("T36", err.getvalue())

    def test_recheck_missing_lock_returns_2(self):
        self.assertEqual(recheck(self.tmpdir / "nope.json"), 2)

    def test_recheck_malformed_lock_returns_3(self):
        bad = self.tmpdir / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(recheck(bad), 3)


# ---------------------------------------------------------------------------
# T22 新增：LICENSE 文件作单边证据 / GitHubArchive / codeload 整包
# ---------------------------------------------------------------------------
class LicenseBodySourceTests(unittest.TestCase):
    """卡面「单边证据」处理：仓库有 LICENSE 文件即视为正文证据（强于 front-matter）。

    判据强度**不降级**：声明 readme_source 时必须同时给出 license_body_ack，
    且商业/禁商用词的硬拒绝扫描对**新证据文件**照常生效。
    """

    def _src(self, **ov):
        d = src(platform_license=None, readme_source="LICENSE",
                license_body_ack="fixture：仓库根 LICENSE 即正文证据")
        d.update(ov)
        return d

    def test_license_file_proves_license_when_platform_and_frontmatter_are_silent(self):
        """平台无标签 + front-matter 无许可 + LICENSE 文件写了 MIT → 通过。

        这是 T22 实测到的真实形态（`ASAPPresearch/ABCD`、`budzianowski/multiwoz`）：
        许可只在 LICENSE 文件里，README 正文一个字都没有。
        """
        install_platform(FakePlatform(readme=FAKE_README_CLEAN.replace(
            "license: Apache License 2.0\n", ""),
            platform_license=None,
            license_body="MIT License\n\nCopyright (c) 2021 ASAPP Research\n"))
        out = audit_license(self._src(expect_license=["mit"]))
        self.assertEqual(out["license_spdx"], "mit")
        self.assertIn("以 readme_source='LICENSE' 为单边证据", " ".join(out["audit_checks"]))

    def test_readme_source_without_ack_is_rejected(self):
        """声明 readme_source 却不写 ack → 拒绝（否则空 ack 就能绕过前两条判据）。"""
        install_platform(FakePlatform(readme=FAKE_README_CLEAN.replace(
            "license: Apache License 2.0\n", ""),
            platform_license=None,
            license_body="MIT License\n"))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(src(platform_license=None, readme_source="LICENSE"))
        self.assertIn("license_body_ack", str(cm.exception))

    def test_noncommercial_license_body_is_rejected_with_matched_words(self):
        """LICENSE 文件本身写了禁商用 → 硬拒收，无放行口。

        这一条是判据强度的守护：换证据文件**不能**绕过禁商用扫描。
        用 hyphen 写法（`Attribution-NonCommercial`）：`NONCOMMERCIAL_PATTERNS` 里
        `BY-NC` 这一项不匹配 hyphen 形态，所以断言 `BY-NC` 会失败——这里断言真正命中的词。
        """
        install_platform(FakePlatform(readme=FAKE_README_CLEAN.replace(
            "license: Apache License 2.0\n", ""),
            platform_license=None,
            license_body="Creative Commons Attribution-NonCommercial 4.0\n"))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(self._src(expect_license=["cc-by-4.0"]))
        msg = str(cm.exception)
        self.assertIn("NonCommercial", msg)
        self.assertIn("无放行口", msg)

    def test_noncommercial_hyphenated_body_hits_by_nc_word(self):
        """hyphen 写法 `BY-NC` 也必须命中并被判无放行口。"""
        install_platform(FakePlatform(readme=FAKE_README_CLEAN.replace(
            "license: Apache License 2.0\n", ""),
            platform_license=None,
            license_body="Creative Commons BY-NC 4.0\n"))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(self._src(expect_license=["cc-by-4.0"]))
        self.assertIn("BY-NC", str(cm.exception))

    def test_readme_source_licence_conflicts_with_frontmatter(self):
        """LICENSE 与 front-matter 不一致 → 以正文（LICENSE）为准拒收。

        这一支只在**平台侧有许可**时可达：平台与 front-matter 冲突时，
        `test_license_conflict_between_platform_and_readme_is_rejected` 已经先拦下了。
        """
        install_platform(FakePlatform(
            readme=FAKE_README_CLEAN,  # front-matter: Apache License 2.0
            platform_license="Apache License 2.0",
            license_body="MIT License\n"))
        with self.assertRaises(LicenseRejected) as cm:
            audit_license(self._src(expect_license=["mit"]))
        msg = str(cm.exception)
        self.assertIn("冲突", msg)
        self.assertIn("mit", msg)


class LicenseNormalizeTests(unittest.TestCase):
    def test_list_license_with_single_value_normalizes(self):
        """HuggingFace 的 cardData.license 是 list（`["apache-2.0"]`），必须能归一。"""
        self.assertEqual(fetch_mod._normalize_license(["apache-2.0"]), "apache-2.0")

    def test_list_license_with_multiple_distinct_values_rejected(self):
        """多许可的义务是并集 → 归一成 None（绝不挑最宽松的那一份）。"""
        self.assertIsNone(fetch_mod._normalize_license(["mit", "apache-2.0"]))

    def test_list_license_with_repeated_same_value_ok(self):
        self.assertEqual(fetch_mod._normalize_license(["cc-by-4.0", "CC-BY-4.0"]), "cc-by-4.0")

    def test_empty_list_and_empty_string_are_none(self):
        self.assertIsNone(fetch_mod._normalize_license([]))
        self.assertIsNone(fetch_mod._normalize_license(""))

    def test_front_matter_survives_leading_whitespace(self):
        """HF 数据卡的 README 实测带前导换行——必须容忍，否则误拒整批采集。"""
        self.assertEqual(
            fetch_mod._front_matter_license("\n---\nlicense: apache-2.0\n---\nbody"),
            "apache-2.0",
        )


class GitHubArchiveTests(unittest.TestCase):
    """codeload 整包通道：审计与解压。"""

    def _mk_zip(self, entries: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for name, content in entries.items():
                z.writestr(name, content)
        return buf.getvalue()

    def test_archive_readme_missing_is_a_hard_error(self):
        """整包里根本没有 README → 硬失败，绝不返回空串给许可扫描。"""
        g = fetch_mod.GitHubArchive()
        zip_b = self._mk_zip({"README.md.txt": b"x"})
        old = fetch_mod.http_get
        try:
            fetch_mod.http_get = lambda url, **kw: zip_b if kw.get("binary") else "{}"
            with self.assertRaises(FetchError) as cm:
                g.fetch_archive_readme("owner/repo", "master")
            self.assertIn("README.md", str(cm.exception))
        finally:
            fetch_mod.http_get = old

    def test_extract_archive_strips_single_top_level_dir(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            blob = self._mk_zip({"owner-repo/README.md": b"hello", "owner-repo/LICENSE": b"MIT"})
            zp = dest / "archive.zip"
            zp.write_bytes(blob)
            out = fetch_mod.extract_archive(zp, dest)
            self.assertEqual(sorted(out), ["LICENSE", "README.md"])
            self.assertEqual((dest / "README.md").read_bytes(), b"hello")

    def test_extract_archive_rejects_zip_slip(self):
        """Zip Slip：包内条目指向目标目录之外 → 拒绝解压。"""
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out"
            blob = self._mk_zip({"owner/../../evil": b"pwn"})
            zp = Path(td) / "archive.zip"
            zp.write_bytes(blob)
            with self.assertRaises(FetchError) as cm:
                fetch_mod.extract_archive(zp, dest)
            self.assertIn("Zip Slip", str(cm.exception))
            self.assertFalse((Path(td) / "evil").exists())

    def test_process_github_archive_writes_lock(self):
        """整包来源能走完 process() 并写出台账条目。"""
        with tempfile.TemporaryDirectory() as td:
            dest_root = Path(td) / "corpus"
            dest_root.mkdir()
            zip_b = self._mk_zip({"owner-repo/README.md": FAKE_README_CLEAN.encode("utf-8"),
                                  "owner-repo/LICENSE": b"MIT License\n"})
            old_get, old_dl = fetch_mod.http_get, fetch_mod.http_download_to_file
            try:
                fetch_mod.http_get = lambda url, **kw: zip_b if kw.get("binary") else "{}"

                def fake_dl(url, dest, **kw):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zip_b)
                    return len(zip_b), hashlib.sha256(zip_b).hexdigest()

                fetch_mod.http_download_to_file = fake_dl

                out = fetch_mod.process(
                    src(id="owner/repo", platform="github-archive",
                        readme_source="LICENSE", license_body_ack="fixture",
                        expect_license=["mit"], files=["*"]),
                    dry_run=False, out_root=dest_root,
                    repo_root=REPO_ROOT,
                )
                self.assertEqual(out["license_spdx"], "mit")
                self.assertEqual(out["files"][0]["path"], "archive.zip")
                # local_file 必须是可移植形式（不含机器绝对路径），且**解析后逐字等于真实落盘位置**——
                # 多拼一层 .zip 会让 recheck 全数报「缺失」（T36 把断言从「字面等于」改为「解析后等于」，
                # 原意图不变：台账里的路径必须能还原到磁盘实物）。
                self.assertEqual(
                    out["files"][0]["local_file"],
                    "${CORPUS_ROOT}/owner__repo/archive.zip",
                )
                self.assertEqual(
                    resolve_portable(out["files"][0]["local_file"], dest_root),
                    dest_root / "owner__repo" / "archive.zip",
                )
                self.assertNotIn(str(dest_root), out["files"][0]["local_file"])
                self.assertNotIn(str(dest_root), out["local_path"])
                self.assertTrue((dest_root / "owner__repo" / "README.md").exists())
                self.assertTrue((dest_root / "owner__repo" / "archive.zip").exists())
            finally:
                fetch_mod.http_get, fetch_mod.http_download_to_file = old_get, old_dl

    def test_process_github_archive_rejects_partial_files_declaration(self):
        """整包来源声明了具体 files → 报错（不完整语料不得入库）。

        必须在**下载之前**拦下：先下载再声明校验的话，不完整语料已经被拉回来了。
        这里用一个「一旦被调用就抛异常」的下载替身证明下载真的没发生。
        """
        with tempfile.TemporaryDirectory() as td:
            dest_root = Path(td) / "corpus"
            dest_root.mkdir()
            old_dl = fetch_mod.http_download_to_file
            try:
                def must_not_download(url, dest, **kw):
                    raise AssertionError(
                        f"files 声明校验必须在下载之前失败，但下载仍发生了: {url}")

                fetch_mod.http_download_to_file = must_not_download
                old_get = fetch_mod.http_get
                # 许可审计会在 process() 开头跑；给 github-archive 一个假的整包内容，
                # 让审计走「单边证据」分支通过，从而真正测到 files 声明校验这一步。
                zip_ok = self._mk_zip({
                    "owner-repo-master/README.md": b"# owner repo\n",
                    "owner-repo-master/LICENSE": b"Apache License 2.0\n",
                })
                def _fake_get(url, **kw):
                    return zip_ok if kw.get("binary") else "{}"

                fetch_mod.http_get = _fake_get
                with self.assertRaises(FetchError) as cm:
                    fetch_mod.process(
                        src(id="owner/repo", platform="github-archive", files=["data/x.json"],
                            readme_source="LICENSE", license_body_ack="fixture"),
                        dry_run=False, out_root=dest_root, repo_root=REPO_ROOT,
                    )
                self.assertIn('files=["*"]', str(cm.exception))
            finally:
                fetch_mod.http_download_to_file = old_dl
                fetch_mod.http_get = old_get


if __name__ == "__main__":
    unittest.main()
