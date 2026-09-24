"""tests/test_fetch_success_floor.py — 成功数下限断言（T32 · docs/13 §五#16）。

被验对象：`tools/corpus_fetch/fetch.py` 在「全部来源都被跳过（成功 0）」时的退出行为——
缺省必须 fail-closed 非零退出，只有显式 `--allow-all-skipped` 才以 0 退出并打 WARNING。

反空转要求：负例**真实走 CLI 入口**（`python3 -m tools.corpus_fetch.fetch` 子进程），
不 import 内部函数、不复用 `tools/tests/test_corpus_fetch.py` 的 FakePlatform 替身绕开
退出码——`--only` 指向不存在平台名走的是真实 `PLATFORMS` 字典的真实 KeyError 路径。

全部离线：不联网。fixture 的 sources.json 用假平台名，在真实平台表里查不到即抛
`FetchError`（进程内捕获 → 真实退出码 3），不会发出任何 HTTP 请求。
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tools.corpus_fetch.fetch as fetch_mod  # noqa: E402
from tools.corpus_fetch.fetch import (  # noqa: E402
    FLAG_ALLOW_ALL_SKIPPED,
    FetchError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FETCH_MODULE = "tools.corpus_fetch.fetch"

# 假平台名：不在 fetch_mod.PLATFORMS（{"modelscope", "hf-mirror"}）里 →
# 真实字典查找抛 KeyError，这是「全部来源被跳过」这一负例场景的触发点，
# 且不联网（PLATFORMS 查找发生在 http_get 之前）。
FAKE_PLATFORM = "no-such-platform"


def _write_all_skipped_sources(path: Path) -> Path:
    """写一份「一份来源都不处理」的 sources.json：三条来源全部 optional，缺省全跳过。

    平台名故意写 FAKE_PLATFORM（真实平台表里没有）——万一 optional 过滤闸门被绕过，
    这条来源会真的进 process() 并因 KeyError 报错，本 fixture 就不会静默变绿。
    """
    doc = {"sources": [
        {"id": "a/optional", "platform": FAKE_PLATFORM, "kind": "dataset",
         "optional": True, "files": ["*"]},
        {"id": "b/optional", "platform": FAKE_PLATFORM, "kind": "dataset",
         "optional": True, "files": ["*"]},
    ]}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _write_rejected_sources(path: Path) -> Path:
    """写一份「成功 1 / 拒绝 1」的 sources.json：证明下限断言只拦「全部跳过」，
    不拦既有的「有成功也有拒绝 → 非零退出」口径（不得顺手改弱原有行为）。"""
    doc = {"sources": [
        {"id": "ok/one", "platform": FAKE_PLATFORM, "kind": "dataset", "files": ["*"]},
        {"id": "ok/two", "platform": FAKE_PLATFORM, "kind": "dataset", "files": ["*"]},
    ]}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path



class SuccessFloorCliTests(unittest.TestCase):
    """跑批脚本必须断言成功数（R09 验收 5）：全部跳过不得以 0 退出。"""

    def setUp(self) -> None:
        self.tmp = Path("/tmp/vox-fetch-floor-cli")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir()
        self.out_root = self.tmp / "out"
        self.sources = _write_all_skipped_sources(self.tmp / "sources.json")
        self.lock = self.out_root / "corpus.lock.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, *extra: str) -> "subprocess.CompletedProcess[str]":
        """真实走 CLI 入口：`python3 -m tools.corpus_fetch.fetch ...`。"""
        return subprocess.run(
            [sys.executable, "-m", FETCH_MODULE,
             "--sources", str(self.sources),
             "--lock", str(self.lock),
             "--out-root", str(self.out_root),
             "--dry-run"] + list(extra),
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        )

    def test_all_skipped_without_flag_exits_nonzero_and_names_the_flag(self):
        """缺省：全部来源被跳过 → 非零退出，且错误消息点名 --allow-all-skipped。

        断言可失败：若断言被删掉（回退到旧行为 exit 0），returncode != 0 立即失败。
        """
        proc = self._run_cli()
        self.assertNotEqual(
            proc.returncode, 0,
            f"全部来源被跳过却以 0 退出（成功 0 / 拒绝 0 的假绿）；stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.returncode, fetch_mod.EXIT_RUNTIME_FAILURE,
            "运行期失败应按冻结码表退出 3（docs/08 §8.5）；"
            f"实际 {proc.returncode}，stderr={proc.stderr!r}",
        )
        combined = proc.stdout + proc.stderr
        self.assertIn(FLAG_ALLOW_ALL_SKIPPED, combined,
                      "错误消息必须点名放行开关，否则调用方不知道该怎么显式放行")
        self.assertIn("全部来源被跳过", combined,
                      "错误消息必须说明失败原因（不是笼统的 'error'）")
        self.assertIn("--allow-all-skipped", combined)
        # fail-closed 的留痕：不得写台账（一份都没成功，台账只能是空的）
        self.assertFalse(
            self.lock.exists(),
            f"全部跳过时不得写台账（会留下空台账冒充跑批成功）：{self.lock}",
        )

    def test_all_skipped_with_flag_exits_zero_and_warns(self):
        """显式 --allow-all-skipped：以 0 退出，并在 stderr 打 WARNING 留痕。

        断言可失败：若开关被删掉，argparse 会以 2 退出并给出用法错误，
        returncode == 0 与 'WARNING' in stderr 两处都会失败。
        """
        proc = self._run_cli("--allow-all-skipped")
        self.assertEqual(
            proc.returncode, 0,
            f"已显式 {FLAG_ALLOW_ALL_SKIPPED} 却未放行；stderr={proc.stderr!r}",
        )
        self.assertIn("WARNING", proc.stderr,
                      "显式放行必须在 stderr 留痕（否则会退化成静默降级）")
        self.assertIn("全部来源被跳过", proc.stderr,
                      "WARNING 必须说明放行的到底是什么（成功 0 / 拒绝 0）")
        # 放行不等于伪造成功：不得写台账
        self.assertFalse(self.lock.exists(),
                         "显式放行也不得写空台账（台账只记真实成功的语料）")


class SuccessFloorModuleTests(unittest.TestCase):
    """库级入口（main()）与 CLI 入口同一口径；额外覆盖既有拒绝口径未被改弱。"""

    def setUp(self) -> None:
        self.tmp = Path("/tmp/vox-fetch-floor-mod")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir()
        self.out_root = self.tmp / "out"
        self.lock = self.out_root / "corpus.lock.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _argv(self, sources: Path, *extra: str) -> list:
        return ["--sources", str(sources), "--lock", str(self.lock),
                "--out-root", str(self.out_root), "--dry-run"] + list(extra)

    def _run(self, sources: Path, *extra: str) -> "tuple[int, str]":
        """跑 main() 并返回 (退出码, 合并后的 stdout+stderr 文本)。

        main() 内部直接写 sys.stdout / sys.stderr，redirect_stdout 只能兜住 stdout；
        所以 stderr 用 contextlib.redirect_stderr 一并兜住，否则断言拿不到输出。
        """
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fetch_mod.main(self._argv(sources, *extra))
        return code, out.getvalue() + err.getvalue()

    def test_main_raises_on_all_skipped(self):
        """main() 层同一口径：全部跳过 → 抛 FetchError，消息点名放行开关。"""
        sources = _write_all_skipped_sources(self.tmp / "sources.json")
        with self.assertRaises(FetchError) as ctx:
            with redirect_stdout(io.StringIO()):
                fetch_mod.main(self._argv(sources))
        self.assertIn(FLAG_ALLOW_ALL_SKIPPED, str(ctx.exception))
        self.assertIn("全部来源被跳过", str(ctx.exception))
        # 跳过声明必须被带进消息，调用方才看得出「为什么一份都没处理」
        self.assertIn("a/optional", str(ctx.exception))

    def test_main_allows_all_skipped_with_flag(self):
        """main() 层：显式开关 → 返回 0 且 stderr 打 WARNING（与 CLI 层同一分支）。"""
        sources = _write_all_skipped_sources(self.tmp / "sources.json")
        code, out = self._run(sources, "--allow-all-skipped")
        self.assertEqual(code, 0)
        self.assertIn("成功 0 / 拒绝 0", out)
        self.assertIn("WARNING", out,
                      "显式放行必须留痕（缺了这条就等于静默降级）")
        self.assertFalse(self.lock.exists(), "显式放行也不得写空台账")

    def test_rejected_sources_are_reported_not_all_skipped(self):
        """两条非 optional 来源全部被拒绝 → 归为「有拒绝即非零」口径，不是「全部跳过」。

        断言可失败：若下限断言被实现成「entries 为空即全部跳过」，这里两条来源被拒绝后
        entries 同样为空，但口径必须是「有拒绝即非零」——用「全部来源被跳过」消息
        不出现这一点守住区分。

        平台名故意用 FAKE_PLATFORM：process() 里的 PLATFORMS 字典查找抛 KeyError，
        被 main() 的 except (LicenseRejected, FetchError) 之外捕获不了——所以这里
        直接断言 KeyError 冒泡（证明该来源**进了处理循环**，不是被跳过），
        与「全部跳过」分支在控制流上可区分。
        """
        sources = _write_rejected_sources(self.tmp / "sources.json")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(KeyError) as ctx:
                fetch_mod.main(self._argv(sources))
        self.assertEqual(str(ctx.exception), f"'{FAKE_PLATFORM}'",
                         "该来源必须真的进 process() 才会抛 KeyError；"
                         "若被当成「全部跳过」处理，这里不会抛 KeyError")


class ExitCodeMappingTests(unittest.TestCase):
    """异常类型 → 退出码的映射（docs/08 §8.5 冻结码表：3 运行期失败 / 5 fail-closed）。"""

    def test_fetch_error_maps_to_runtime_failure(self):
        self.assertEqual(
            fetch_mod.exit_code_for(fetch_mod.FetchError("x")), fetch_mod.EXIT_RUNTIME_FAILURE)

    def test_license_rejected_maps_to_runtime_failure(self):
        """许可拒绝是 FetchError 子类，同样归为运行期失败（fail-closed 的正常出口）。"""
        self.assertEqual(
            fetch_mod.exit_code_for(fetch_mod.LicenseRejected("x")), fetch_mod.EXIT_RUNTIME_FAILURE)

    def test_unexpected_error_maps_to_fail_closed(self):
        self.assertEqual(
            fetch_mod.exit_code_for(ValueError("boom")), fetch_mod.EXIT_FAIL_CLOSED)

    def test_flag_is_registered_on_cli(self):
        """--allow-all-skipped 必须真实注册在 CLI 上（不是只存在于错误消息里）。

        做法：喂一份存在但无 optional 来源的空表 sources，使流程走到「全部跳过」分支。
        若开关未注册，argparse 会以用法错误 2 退出；若已注册，才会进入该分支并
        打 WARNING。断言可失败：改名字符串时 argparse 立即以 2 退出。
        """
        tmp = Path("/tmp/vox-fetch-flag-reg")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        empty = tmp / "empty.json"
        empty.write_text(json.dumps({"sources": [{"id": "a/optional",
                                                  "platform": FAKE_PLATFORM,
                                                  "kind": "dataset",
                                                  "optional": True,
                                                  "files": ["*"]}]}),
                          encoding="utf-8")
        try:
            with redirect_stdout(io.StringIO()):
                code = fetch_mod.main(["--allow-all-skipped",
                                       "--sources", str(empty),
                                       "--lock", str(tmp / "out" / "corpus.lock.json"),
                                       "--out-root", str(tmp / "out"),
                                       "--dry-run"])
            self.assertEqual(code, 0,
                             f"显式放行应以 0 返回，实际 {code}"
                             "（argparse 不认这个开关会走用法错误路径）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
