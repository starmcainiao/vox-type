"""
cli/tests — CLI 测试包（共享夹具集中在这里，避免四个测试文件各复制一份）

夹具纪律（本卡反空转条款）：
  - 测试必须调用**产品 API**：`cli.main.main` / 各命令函数 / `compiler.load_source`，
    不在测试里复制判定逻辑、也不自己解析 CLI 参数。
  - 一律 `tempfile`，不往 `packs/**` 或仓库内写任何产物。
  - 极小包夹具只有 2 条资产（2 phrase × 1 variant × 1 rate），一条 `say` 调用一条——
    `packs/repair` 是 54 条，单测里跑它太慢；夹具只钉契约，不钉体量。
    端到端体量回归由 `packs/repair` 的手跑验收覆盖。
"""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional, Tuple

# 仓库根（本文件位于 <root>/cli/tests/__init__.py，往上两层即根）
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

# 真实业务包：验收用的活体回归用例（只读，测试不得修改它）
PACKS_REPAIR: Path = REPO_ROOT / "packs" / "repair"


# ---------------------------------------------------------------------------
# 极小包夹具数据（合法：pack.json 只含必填字段，剧本四属性合规）
# ---------------------------------------------------------------------------
TINY_PACK_JSON = {
    "pack_id": "tiny",
    "pack_version": "1",
    "protocol_version": "0.1",
    "ruleset_version": "v1",
    "voice": "Tingting",
    "model_version": "macos-say",
    "rates": ["normal"],
}

TINY_PHRASES_JSON = {
    "phrases": [
        {"key": "greeting", "variants": ["您好，请问需要什么帮助。"], "rates": ["normal"]},
        {"key": "closing", "variants": ["感谢您的来电。"], "rates": ["normal"]},
    ]
}

TINY_SCRIPT_JSON = {
    "script_version": 1,
    "terminal_keys": ["closing"],
    "live_whitelist": ["asr_low_confidence"],
    "max_retry": 3,
    "units": [
        {"key": "greeting", "rate": "normal"},
        {"key": "closing", "rate": "normal"},
    ],
}

# 违规剧本：末单元不是终态 → C1a no_exit（正好触发 pack check / pack build 的退出码 4）
TINY_SCRIPT_BAD_EXIT_JSON = {
    "script_version": 1,
    "terminal_keys": ["closing"],
    "live_whitelist": [],
    "max_retry": 3,
    "units": [
        {"key": "greeting", "rate": "normal"},
    ],
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def write_json(path: Path, data: Any) -> None:
    """写 JSON 文件（UTF-8，中文不转义，便于错误信息里直接读）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_tiny_pack(
    root: Path,
    *,
    pack: Optional[dict] = None,
    phrases: Optional[dict] = None,
    script: Optional[dict] = None,
) -> Path:
    """在 root 下写出一份合法业务包源（pack.json + phrases.json + script.json）。

    参数任一为 None 时取默认夹具数据；传入自定义数据用于构造负例。
    """
    write_json(root / "pack.json", pack if pack is not None else TINY_PACK_JSON)
    write_json(root / "phrases.json", phrases if phrases is not None else TINY_PHRASES_JSON)
    write_json(root / "script.json", script if script is not None else TINY_SCRIPT_JSON)
    return root


def build_tiny_pack(
    src: Path,
    out: Path,
    *,
    adapter: Optional[str] = None,
    allow_partial: bool = False,
    as_json: bool = True,
) -> Tuple[int, str, str]:
    """用产品 CLI 预铸极小包，返回 (退出码, stdout, stderr)。"""
    argv = ["pack", "build", str(src), "--out", str(out)]
    if adapter:
        argv += ["--adapter", adapter]
    if allow_partial:
        argv.append("--allow-partial")
    if as_json:
        argv.append("--json")
    return run_cli(argv)


# ---------------------------------------------------------------------------
# 产品入口调用（测试的唯一入口：调 CLI，不复制判定逻辑）
# ---------------------------------------------------------------------------
def run_cli(argv: list) -> Tuple[int, str, str]:
    """调用 `cli.main.main` 并捕获 stdout/stderr，返回 (退出码, stdout, stderr)。

    WHY 走 `main()` 而不是 subprocess：同一进程内即可断言 stdout/stderr 分工，
        且能覆盖 argparse 的 error() 路径（main 已把 SystemExit 归一成返回码）。
    """
    from cli.main import main

    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = main(argv)
    return rc, out_buf.getvalue(), err_buf.getvalue()


# ---------------------------------------------------------------------------
# 测试基类与断言辅助
# ---------------------------------------------------------------------------
class CliTestBase(unittest.TestCase):
    """提供 tempfile 目录与清理；测试不得往 packs/** 或仓库内写任何文件。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="cli_test_")
        self.tmp = Path(self._tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def assert_json_stdout(stdout: str, test: unittest.TestCase) -> dict:
    """真跑 `json.loads(stdout)` 并返回解析结果。

    卡的反空转条款：`--json` 的每一条断言必须真解析，不得只断言「含某个字符串」。
    若 stdout 混进了人读摘要，json.loads 会直接抛，测试即失败。
    """
    test.assertNotEqual(stdout.strip(), "", "stdout 为空（--json 时应输出 JSON）")
    return json.loads(stdout)


def assert_not_json(text: str, test: unittest.TestCase) -> None:
    """断言一段文本不是合法 JSON（用于「不带 --json 时 stdout 是人读摘要」）。"""
    with test.assertRaises(json.JSONDecodeError):
        json.loads(text)
