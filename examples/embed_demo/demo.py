#!/usr/bin/env python3
"""embed_demo/demo.py — 从 vox-type 仓**外** import 它，执行一轮播报。

形态 3（进程内 / embed）的最小示范。判定、拼接、写盘全部由公开 API 完成，
本文件**不定义任何判定 / 拼接 / 播包逻辑**，只调：

    assets.load_pack / core.protocol.parse_plan / runtime.Executor / adapters.tts_omlx.OmlxTts

用法（本仓没有 pyproject.toml / setup.py / setup.cfg / requirements.txt，
      **不能 pip install**，PYTHONPATH 是唯一安装方式）：

    export VOX_ROOT="<vox-type 仓库路径>"          # 指向 core/ assets/ runtime/ 所在目录
    export PYTHONPATH="${VOX_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
    python3 examples/embed_demo/demo.py            # 输出写到 /tmp/embed_demo_out.wav

退出码：0 成功；2 包没解析对或执行失败（见下方两道防呆）。

两道防呆为什么必须写：
  ① 仓根不在 PYTHONPATH 上就退出——**不自动补环境、不往 sys.path 塞东西**。
     静默修补会让宿主环境的真实问题（PYTHONPATH 指错、同名目录劫持）无法暴露。
  ② cwd 优先于 PYTHONPATH。宿主项目里若也有 `adapters/` `core/` `assets/` `tools/`
     `cli/` 同名目录，会**静默**劫持 vox-type 的同名顶层包，而且失败通常不以
     本来面目暴露，而是变成不指向真因的错，例如
     `ModuleNotFoundError: No module named 'adapters.textmatch'`。
     所以必须在 import 任何产品模块**之前**先判定一次落点。

另外两点值得宿主记住（都是既有设计，不是本 demo 引入的）：
  - `out_path` 是 `execute()` 的**必填**关键字，语义是「写盘」——内核里没有
    「返回音频」这个概念，宿主拿到 `output_path` 后自己去读文件播放。
  - 结果对象的字段叫 `output_path`，而 `vox run --json` 的键叫 `out_path`。
    写 `result.out_path` 会 AttributeError。
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOPLEVELS = ("adapters", "core", "assets", "tools", "cli")


def _fail(message: str) -> int:
    sys.stderr.write(message)
    return 2


# ---------------------------------------------------------------------------
# 防呆 ①：仓根必须在 PYTHONPATH 上（不自动补）。
# ---------------------------------------------------------------------------
_PYTHONPATH = os.environ.get("PYTHONPATH", "")
if str(REPO_ROOT) not in _PYTHONPATH:
    raise SystemExit(_fail(
        "embed_demo: vox-type 仓根不在 PYTHONPATH 上，无法 import。\n"
        "本仓没有 pyproject.toml / setup.py / setup.cfg / requirements.txt，\n"
        "不能 pip install，PYTHONPATH 是唯一安装方式：\n"
        "  export VOX_ROOT=\"<vox-type 仓库路径>\"\n"
        "  export PYTHONPATH=\"${{VOX_ROOT}}{{PYTHONPATH:+:$PYTHONPATH}}\"\n"
        "  python3 examples/embed_demo/demo.py\n"
        f"（当前 PYTHONPATH={_PYTHONPATH or '<空>'}；仓根={REPO_ROOT}）\n"
        "退出码 2。\n"
    ))

# ---------------------------------------------------------------------------
# 防呆 ②：cwd 不能出现会抢在仓根前面的同名顶层包。
# 顺序与解释器一致：cwd → 脚本所在目录 → PYTHONPATH 各段 → sys.path 其余段。
# 用 isdir() 探测而不是 import，避免执行宿主目录里的 __init__.py。
# ---------------------------------------------------------------------------
_CWD = Path.cwd()
_SEGMENTS = [_CWD, Path(sys.path[0])]
_SEGMENTS += [Path(seg) for seg in _PYTHONPATH.split(os.pathsep) if seg]
_SEGMENTS += [Path(seg) for seg in sys.path[1:] if seg and Path(seg).exists()]


def _first_candidate(name: str) -> Path | None:
    for segment in _SEGMENTS:
        candidate = segment / name
        if candidate.is_dir():
            return candidate
    return None


_CANDIDATES = {name: _first_candidate(name) for name in TOPLEVELS}
_OFFENDERS = {
    name: origin
    for name, origin in _CANDIDATES.items()
    if origin is not None and REPO_ROOT not in origin.parents
}

if _OFFENDERS:
    raise SystemExit(_fail(
        "embed_demo: 拒绝执行 —— vox-type 的顶层包被同名目录劫持了。\n"
        "  会解析到（cwd 优先于 PYTHONPATH）：\n"
        + "".join(f"    {name} -> {origin}\n" for name, origin in _OFFENDERS.items())
        + f"  期望落在 {REPO_ROOT} 之下\n"
        f"  cwd = {_CWD}\n"
        "这类撞名是**静默**的，失败通常不以本来面目暴露，而是变成不指向真因的错，例如\n"
        "  ModuleNotFoundError: No module named 'adapters.textmatch'\n"
        "修法：重命名你项目里的同名目录，或 cd 到不含这些目录的目录再跑。\n"
        "退出码 2。\n"
    ))

# ---- 通过两道防呆，才 import 产品模块 ----

from assets import load_pack
from adapters.tts_omlx import OmlxTts
from core.protocol import parse_plan
from runtime import Executor

OUT_PATH = Path("/tmp/embed_demo_out.wav")

__all__ = ["REPO_ROOT", "run_turn"]


def run_turn(pack_dir, *, plan_path=None, allow_fallback=True):
    """执行一轮播报。判定 / 拼接 / 写盘全部由公开 API 完成，本函数不含任何逻辑。

    plan_path 为空时读 examples/plan.json（真实文件，不复制内容）。
    """
    pack = load_pack(Path(pack_dir))                                    # assets.load_pack
    if plan_path is None:
        plan_path = REPO_ROOT / "examples" / "plan.json"
    plan = parse_plan(json.loads(Path(plan_path).read_text(encoding="utf-8")))
    executor = Executor(pack, OmlxTts(), allow_fallback=allow_fallback)
    return executor.execute(                                            # out_path 必填，语义是「写盘」
        plan, plan_id="embed-demo", turn_id="1", out_path=OUT_PATH)


def main() -> int:
    pack_dir = REPO_ROOT / "examples" / "prebuilt-pack"
    if not (pack_dir / "manifest.json").exists():
        return _fail(f"embed_demo: {pack_dir} 缺 manifest.json\n退出码 2。\n")

    try:
        result = run_turn(pack_dir)
    except Exception as exc:                            # noqa: BLE001 — 只报真因，不静默降级
        return _fail(f"embed_demo: 执行失败（{type(exc).__name__}）: {exc}\n退出码 2。\n")

    import adapters as _adapters_pkg

    print("== vox-type embed_demo：一轮播报执行完成 ==")
    print(f"adapters 来源  = {Path(_adapters_pkg.__path__[0]).resolve().parent}")
    print(f"hit             = {result.hit_count}   （plan 共 {len(result.events)} 个单元）")
    print(f"miss            = {result.miss_count}   （未命中单元数）")
    print(f"tts_calls       = {result.tts_calls}   （真实 TTS 调用次数；全命中且无槽位时应为 0）")
    print(f"first_audio_ms  = {result.first_audio_ms:.3f}  （命中即播，走磁盘读；采样型引擎两次跑会漂）")
    print(f"output_path     = {result.output_path}   （写盘路径，不是返回值——宿主自己读这个文件）")
    print(f"total_duration_ms = {result.total_duration_ms}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
