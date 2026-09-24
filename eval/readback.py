"""
eval.readback — 回读 CER harness（manifest → ASR → CER → 可复现报告）

职责：把 manifest JSONL 里的每条 (wav, reference) 喂给注入的 ASR 适配器，逐条算 CER，
      汇总 n / mean / p50 / p99，出 JSON 报告 + raw/readback_samples.jsonl。
不负责：不做 ASR 转写实现（adapters/asr_omlx，经 dotted path 注入）；
      不做编辑距离（cer.py）；不做分位数实现（stats.percentile 是唯一分位实现）。

层边界（eval/AGENTS.md ⑤，T08 纪律）：本文件 import 区**只有**标准库 + core/ +
      eval 自己的兄弟模块。adapters / compiler / rules 一律不静态 import——
      ASR 适配器用 `--asr <模块:类>` 的 dotted path 运行时解析（模式同 eval.bench 的
      `_resolve_adapter`）。

不得美化（T08b 三条教训的落点）：
  1. 任一条 transcribe 抛错 → 该条进 incomplete_reasons（含异常原文 + wav 路径），
     **不中止整轮**、**不缩分母之外的美化**（分母只计成功条数是口径定义，不是缩水：
     失败条数原样留在 incomplete_reasons 与样本量对照里），报告照样写出；
  2. ASR 全挂 → n=0 且 mean/p50/p99 = **null**（不是 0.0）——0.0 会被读成
     "拼接完全没有损失内容"，这是最危险的一次美化；
  3. raw 样本被抽改 → 指纹/行数核对报红（复用 eval.report.check_raw_on_disk）。

替身纪律：ASR 适配器若在替身（synthetic 属性为 True，或未声明）下产出报告，
      报告带 `synthetic` 标记与「不得对外引用」警告（对齐 OfflineTts 的 T08b 标记纪律）。
"""

import argparse
import hashlib
import json
import os
import platform
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import cer as _cer
from . import stats as _stats
# 文件级 I/O 与指纹摘要的统一实现（仅标准库；不引入 bench 的 assets/runtime 依赖）
from . import _io
from .report import (
    CALIBER,
    SCHEMA_VERSION,
    check_raw_on_disk,
    render_summary,
)

# 原始数据相对路径（报告 raw 块指向它；指纹元数据键由 _raw_pointers 追加）
RAW_SAMPLES_REL: str = "raw/readback_samples.jsonl"

# 指纹元数据键后缀：与 eval.report.check_raw_on_disk 的解析规则完全一致
# （bench 的 raw 块用同样两个后缀，这里复用同名规则以保持 `vox verify` 一条路径可核对）
_SHA256_SUFFIX: str = "_sha256"
_LINES_SUFFIX: str = "_lines"

# 指纹元数据键的解析白名单（避免把普通字段误判成指纹）
_FINGERPRINT_SUFFIXES: Tuple[str, ...] = (_SHA256_SUFFIX, _LINES_SUFFIX)

# 报告 schema 版本：回读报告独立于 bench 的 schema（字段不同），单独命名
READBACK_SCHEMA_VERSION: str = "vox-eval-readback-report/1"

# 报告 caliber：口径说明 + 设计依据（每个数字都要能追溯，eval/AGENTS.md §③.2）
CALIBER_READBACK: Dict[str, Any] = {
    _cer.CER: (
        f"{_cer.CER} = 字符级编辑距离 / 归一后 reference 长度（取值下界 0.0，"
        "上界可 > 1.0：转写比源文本多说的字全部计为错误，不夹取）。"
        "设计依据：docs/05 §5.3「内容可懂度（回读 CER）」，Seed-TTS-eval 口径"
    ),
    "normalize": (
        "归一 = 去全部标点与空白后逐字比对（Seed-TTS-eval 式）。"
        "标点属韵律标记而非内容，故句号不影响 CER；"
        "**不做**同义/模糊/语义归一（docs/10 裁定 1 禁止）"
    ),
    "denominator": (
        f"分母 = 归一后 reference 的字符数；{ _cer.CER_N } = transcribe 成功且 "
        "reference 归一后非空的条数。失败条数不缩分母之外的任何处理——"
        "失败原文逐条留在 incomplete_reasons"
    ),
    "quantile_method": _stats.QUANTILE_METHOD,
    "statistics": (
        "厚尾分布禁均值外推（eval/AGENTS.md §①）：一律报 P50/P99 + 样本量；"
        "mean 仅作辅助读数，不得作为结论"
    ),
    "reproducibility": (
        "同 manifest + 同 ASR 引擎/模型 + 同种子 → CER 逐值相等（CER 是确定性算法）；"
        "ASR 换模型版本会改变全部数字，因此 asr.model_version 进报告 env"
    ),
    "raw_fingerprint": (
        "raw 块同时记录 sha256 与行数：值级篡改（把某条 cer 改成 0）不改变行数与 index，"
        "只有逐字节摘要能抓它（docs/08 §8.7 欠账 1）"
    ),
    "synthetic": (
        "synthetic=true 时报告数字来自替身 ASR，不代表真机转写质量，"
        "**不得对外引用**（不得美化，eval/AGENTS.md §③.3）"
    ),
}

# 摘要里出现「不得对外引用」的固定措辞（测试用它做子串断言，避免措辞漂移）
SYNTHETIC_WARNING: str = "不得对外引用"


class ReadbackError(ValueError):
    """harness 配置/manifest/报告组装错误。

    消息必须包含导致失败的具体值（路径、行数、key），不允许吞上下文。
    抛此异常时不得产生报告文件（配置期错误一律在建目录之前拦下）。
    """


# ---------------------------------------------------------------------------
# 1. manifest 读取
# ---------------------------------------------------------------------------
def load_manifest(path: Any) -> List[Dict[str, Any]]:
    """读 manifest JSONL：每行 {"wav": <路径>, "reference": <源文本>}。

    参数：
        path: manifest 文件路径（str 或 Path）

    返回：
        条目列表，每条形如 {"wav": str, "reference": str}

    边界（全部抛 ReadbackError，不产生半成品报告）：
        - 文件不存在 / 空文件 / 无有效行
        - 行不是 JSON 对象、缺 wav 或 reference、类型不对
        坏行**逐行报出**（含行号与原文），不静默跳过——跳过的行等于凭空缩分母。
    """
    p = Path(path)
    if not p.is_file():
        raise ReadbackError(f"manifest 文件不存在或不是文件: {p}")

    entries: List[Dict[str, Any]] = []
    bad: List[str] = []
    with p.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except ValueError as exc:
                bad.append(f"第 {line_no} 行不是合法 JSON: {exc}")
                continue
            if not isinstance(value, dict):
                bad.append(f"第 {line_no} 行不是 JSON 对象（实际 {type(value).__name__}）")
                continue
            wav = value.get("wav")
            ref = value.get("reference")
            if not isinstance(wav, str) or not wav:
                bad.append(f"第 {line_no} 行 wav 字段缺失或不是非空字符串（实际 {wav!r}）")
                continue
            if not isinstance(ref, str) or not ref.strip():
                bad.append(
                    f"第 {line_no} 行 reference 字段缺失或为空（实际 {ref!r}）——"
                    f"reference 是 CER 的真值，空 reference 无法计算"
                )
                continue
            entries.append({"wav": wav, "reference": ref})

    if bad:
        raise ReadbackError(
            f"manifest {p} 有 {len(bad)} 行不合格（逐条报出，不静默跳过——"
            f"跳过的行等于凭空缩分母）: " + " | ".join(bad)
        )
    if not entries:
        raise ReadbackError(f"manifest {p} 无有效条目（至少需要 1 条）")
    return entries


def manifest_sha256(path: Any) -> Optional[str]:
    """manifest 文件字节 sha256（取前 16 位，报告里做指纹对照用）。"""
    p = Path(path)
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 2. ASR 适配器解析（dotted path 注入）
# ---------------------------------------------------------------------------
def resolve_asr(spec: str, **kwargs: Any) -> Any:
    """按 `模块:类名` 动态解析 ASR 适配器（importlib），并校验接口形状。

    WHY 运行时解析而不是静态 import：eval/ 的静态 import 里不得出现 adapters/
      （T08 层边界纪律）。调用方（CLI 侧）才知道要接哪个引擎，eval 只认接口。

    参数：
        spec:    "模块路径:类名"（如 "adapters.asr_omlx:OmlxAsr"）
        **kwargs: 透传给构造函数的参数（base_url / model / timeout_seconds …）

    返回：
        适配器实例（必须有 transcribe 方法）

    异常：
        ReadbackError: 格式非法 / 导入失败 / 类不存在 / 构造失败 / 缺 transcribe
    """
    if not isinstance(spec, str) or ":" not in spec:
        raise ReadbackError(
            f"--asr 必须是 '模块路径:类名' 形式，实际为 {spec!r}"
            f"（例：adapters.asr_omlx:OmlxAsr）"
        )
    module_name, _, class_name = spec.partition(":")
    try:
        import importlib

        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ReadbackError(f"无法导入 ASR 模块 {module_name!r}: {exc}") from exc
    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise ReadbackError(
            f"模块 {module_name!r} 中没有类 {class_name!r}（实际 --asr={spec!r}）"
        ) from exc
    try:
        # 只把构造函数接受的参数传进去：适配器形状不一（真机 OmlxAsr 接 base_url/model，
        # 测试替身可能只接 responses）。多余参数一律丢弃而不是报错——
        # CLI 给的是一组「通用 ASR 参数」，不是每个替身的契约。
        import inspect

        accepted = inspect.signature(cls).parameters
        supported = {k: v for k, v in kwargs.items() if k in accepted}
        asr = cls(**supported)
    except Exception as exc:
        raise ReadbackError(
            f"ASR 适配器 {spec!r} 实例化失败: {type(exc).__name__}: {exc}"
        ) from exc
    if not callable(getattr(asr, "transcribe", None)):
        raise ReadbackError(
            f"ASR 适配器 {spec!r} 缺 transcribe 方法（不符 ASR 接口契约）"
        )
    return asr


def _adapter_env(asr: Any) -> Dict[str, Any]:
    """从适配器实例取报告 env 需要的字段（缺属性给 None，不猜）。"""
    return {
        "name": getattr(asr, "name", type(asr).__name__),
        "model_version": getattr(asr, "model_version", None),
        "synthetic": _is_synthetic(asr),
    }


def _is_synthetic(asr: Any) -> bool:
    """判定适配器是否为替身（不依赖单一属性，T08b 审计 #2 的教训）。

    fail-safe 纪律（T14c 修 1）：synthetic 属性**存在**即按 bool() 归真，
      只有**显式声明为 False**（或等价的 falsy 值）才可能按真机处理。
      修前用 `is True` 比较——适配器把 `synthetic` 写成 truthy 非 True（`1` / `"yes"`
      这类类型笔误）会穿透成 `synthetic: false` + `[COMPLETE]` + rc 0，替身数据被
      当真机背书。判定不依赖「恰好写了对的 True」（T08b 在 bench 上修掉的同类缺陷）。

    四重检查：
      ① asr.synthetic 显式为假值 → 候选真机，再看 ②③④ 是否把它拉回替身；
      ② 模块名以 eval. 开头（unittest discover 前缀场景）；
      ③ 源文件位于 eval 包目录下（discover -s eval 无前缀场景）；
      ④ 不在 adapters/ 下且未显式声明 synthetic（第三方替身场景）。
    真机适配器（adapters/ 下且显式 synthetic=False）→ False。
    属性缺失或任何 truthy 值（含 1 / "yes" / [] 的反例除 falsy 外）→ True。
    """
    declared = hasattr(asr, "synthetic")
    synthetic_value = getattr(asr, "synthetic", True)
    if bool(synthetic_value):
        return True
    mod_name = type(asr).__module__
    mod_obj = sys.modules.get(mod_name)
    file = getattr(mod_obj, "__file__", "") or ""
    directory = os.path.dirname(os.path.abspath(file)) if file else ""
    eval_dir = os.path.dirname(os.path.abspath(_cer.__file__))
    in_eval_pkg = (
        directory == eval_dir or directory.startswith(eval_dir + os.sep)
    )
    in_adapters = mod_name.startswith("adapters.")
    return (
        mod_name.startswith("eval.")
        or in_eval_pkg
        or (not in_adapters and not declared)
    )


# ---------------------------------------------------------------------------
# 3. 原始数据落盘
# ---------------------------------------------------------------------------
# 文件级 I/O（write_jsonl / sha256_of_file）在 eval._io：实现只有一份。
# T21 前这里曾点名为「与 bench 等价实现而不 import 它」——当时收敛失败是因为
#   eval.bench 静态 import 了 assets/ 与 runtime/（harness 需要读包与跑执行器），
#   而 readback 是更窄的 harness，引 bench 会把两条不该有的层依赖带进回读路径。
# T21 的解法：收敛点落在**只含标准库**的 eval/_io.py，既消除逐字拷贝，
#   又没新增任何跨层 import；sha256 的分块策略与 T21 前逐字一致，指纹值不变。
# WHY 是文件字节摘要而不是语义摘要：把某条 cer 改成 0.0 不改变行数与 index，
#   任何按计数聚合的比对都抓不到它；只有逐字节摘要会变（docs/08 §8.7 欠账 1）。


def _raw_pointers(path: Path, work_dir: Path, records: Sequence[Dict[str, Any]]
                  ) -> Dict[str, Any]:
    """构造报告 raw 块：路径指针 + sha256 指纹 + 行数（追加式字段）。

    键名规则与 eval.report.check_raw_on_disk 完全对齐：
      `readback_samples` → 相对路径，`readback_samples_sha256` / `readback_samples_lines`
      是元数据。这样回读报告能被 `vox verify` 用同一条 check_raw_on_disk 路径核对。
    """
    rel = path.relative_to(work_dir).as_posix()
    return {
        _cer.RAW_SAMPLES: rel,
        f"{_cer.RAW_SAMPLES}{_SHA256_SUFFIX}": _io.sha256_of_file(path),
        f"{_cer.RAW_SAMPLES}{_LINES_SUFFIX}": len(records),
    }


# ---------------------------------------------------------------------------
# 4. 统计块
# ---------------------------------------------------------------------------
def _cer_block(values: Sequence[float]) -> Dict[str, Any]:
    """n / mean / p50 / p99（分位数一律走 stats.percentile，不自造）。

    n = 0 时全部给 **None**（不是 0.0）——0.0 会被读成"零内容损失"，
    这是本卡明令禁止的美化路径。
    """
    if not values:
        return {
            _cer.CER_N: 0,
            _cer.CER_MEAN: None,
            _cer.CER_P50: None,
            _cer.CER_P99: None,
            "max": None,
            "unit": "ratio",
        }
    return {
        _cer.CER_N: len(values),
        _cer.CER_MEAN: sum(values) / len(values),
        _cer.CER_P50: _stats.percentile(values, 0.5),
        _cer.CER_P99: _stats.percentile(values, 0.99),
        "max": float(max(values)),
        "unit": "ratio",
    }


# ---------------------------------------------------------------------------
# 5. 主入口
# ---------------------------------------------------------------------------
def run_readback(
    manifest_path: Any,
    asr: Any,
    *,
    out_dir: Optional[Any] = None,
    command: str = "",
    seed: Optional[int] = None,
    manifest_path_for_report: Optional[Any] = None,
) -> Dict[str, Any]:
    """跑回读 CER 全轮并组装报告字典（**不写盘**，写盘见 write_readback）。

    参数：
        manifest_path:      manifest JSONL 路径
        asr:                ASR 适配器实例（必须有 transcribe）
        out_dir:            报告输出目录（None 时用临时目录；工作目录在此之下）
        command:            复现命令（写进报告 command 字段）
        seed:               随机种子（CER 是确定性算法，seed 只作报告可追溯字段）
        manifest_path_for_report: 报告里记录的 manifest 路径（缺省用 manifest_path）

    返回：
        报告字典（结构见 READBACK_SCHEMA_VERSION）

    边界（不得美化）：
        - 任一条 transcribe 抛错 → 进 incomplete_reasons（含异常原文 + wav 路径），
          该条**不计入** CER 样本，但不中止整轮、报告照样写出；
        - 全部失败 → n=0 且 mean/p50/p99 = None；
        - reference 归一后为空（纯标点）→ 同样进 incomplete_reasons（CER 无定义）。
    """
    if not callable(getattr(asr, "transcribe", None)):
        raise ReadbackError(
            f"run_readback: asr 必须提供 transcribe 方法，"
            f"实际为 {type(asr).__name__}"
        )
    seed = _stats.DEFAULT_SEED if seed is None else seed
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ReadbackError(
            f"run_readback.seed: 必须是整数，实际为 {seed!r}"
        )

    entries = load_manifest(manifest_path)
    total = len(entries)

    samples: List[Dict[str, Any]] = []
    cer_values: List[float] = []
    reasons: List[str] = []
    failed = 0

    for index, entry in enumerate(entries):
        wav = Path(entry["wav"])
        reference = entry["reference"]
        try:
            hypothesis = asr.transcribe(wav)
        except Exception as exc:
            # 全类型兜住：ASR 可能抛 AsrError / OSError / socket 相关等任意异常。
            # 关键纪律是**记录原文并继续**，不是分类处理。
            failed += 1
            reasons.append(
                f"[{index}] {wav} transcribe 失败: {type(exc).__name__}: {exc}"
            )
            samples.append({
                "index": index,
                "wav": str(wav),
                "reference": reference,
                "hypothesis": None,
                _cer.CER: None,
                "status": "transcribe_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            continue

        try:
            value = _cer.cer(reference, hypothesis)
        except ValueError as exc:
            # reference 归一后为空 = CER 无定义（0/0），不当作 0 也不当作 1
            failed += 1
            reasons.append(
                f"[{index}] {wav} CER 无定义: {type(exc).__name__}: {exc}"
            )
            samples.append({
                "index": index,
                "wav": str(wav),
                "reference": reference,
                "hypothesis": hypothesis,
                _cer.CER: None,
                "status": "cer_undefined",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            continue

        cer_values.append(value)
        samples.append({
            "index": index,
            "wav": str(wav),
            "reference": reference,
            "hypothesis": hypothesis,
            _cer.CER: value,
            "status": "ok",
        })

    synthetic = _is_synthetic(asr)

    incomplete = bool(reasons)
    block = _cer_block(cer_values)

    report: Dict[str, Any] = {
        "schema_version": READBACK_SCHEMA_VERSION,
        "generated_at": _utc_now_iso(),
        "command": command,
        "seed": seed,
        "incomplete": incomplete,
        "incomplete_reasons": reasons,
        "env": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "asr": _adapter_env(asr),
        },
        # 替身标记单独成字段（不进 incomplete_reasons）：
        # incomplete 表示「数据不完整」（失败/缺失），synthetic 表示「数据不是真机产出的」，
        # 两件事不同——混在一起会让「全成功的替身跑」被读成有数据缺陷。
        # 摘要与 CLI 退出码必须独立检查它（不得美化，eval/AGENTS.md §③.3）。
        "synthetic": synthetic,
        "manifest": {
            "path": str(manifest_path_for_report or manifest_path),
            "sha256": manifest_sha256(manifest_path),
            "entries": total,
        },
        "cer": block,
        "caliber": dict(CALIBER_READBACK),
        "raw": {},
        "samples": samples,
        "reference": "docs/05 §5.3 内容可懂度（回读 CER），Seed-TTS-eval 口径",
    }
    return report


def run_readback_cli(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口：写盘 + 打印人读摘要，返回退出码。

    退出码（对齐 AGENTS.md §四 冻结约定）：
        0 = 报告已写出且 incomplete=False
        2 = 用法或参数错误（**不产生报告文件**）
        3 = 运行期失败（stderr 带异常类型名）
        4 = 质检不通过（raw 指纹/行数核对报红）
        5 = fail-closed 中止（incomplete：任一条失败 / 全部失败 / 替身产出）
    """
    args = _parse_args(argv)

    # 配置期错误在建目录之前拦下：失败时不产生任何文件。
    # T14c 修 2：manifest 读取纳入同一 try——manifest 非 UTF-8 时 read_text
    # 抛的是 UnicodeDecodeError（ValueError 子类），属「输入错误」应归 rc 2；
    # 修前它逃出 try 变成裸 traceback + rc 1（冻结退出码集合 {0,2,3,4,5} 之外的出口）。
    try:
        asr = resolve_asr(
            args.asr,
            base_url=args.base_url,
            model=args.model,
            timeout_seconds=args.timeout,
        )
        report = run_readback(
            args.manifest,
            asr,
            command=args.command,
            seed=_stats.DEFAULT_SEED,
        )
    except _CLI_ERRORS as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="vox-readback-"))

    try:
        samples = _report_samples(report)
        # 原始样本 = 逐条明细（成功与失败都在，不缩分母视图）
        raw_dir = out_dir / Path(RAW_SAMPLES_REL).parent
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / RAW_SAMPLES_REL
        _io.write_jsonl(raw_path, samples)
        report["raw"] = _raw_pointers(raw_path, out_dir, samples)
    except ReadbackError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except _CLI_ERRORS as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    # 落盘前复核磁盘状态（复用 eval.report.check_raw_on_disk 的指纹/行数核对）；
    # write_readback 会再核一次并去重追加（T14c 修 3），这里保留早核对以便定 rc 4。
    disk_issues = check_raw_on_disk(report, out_dir)
    if disk_issues:
        report["incomplete"] = True
        report["incomplete_reasons"] = list(report["incomplete_reasons"]) + disk_issues

    try:
        write_readback(report, out_dir)
    except _CLI_ERRORS as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    print(_render_readback_summary(report))

    if disk_issues:
        return 4
    # 5 = fail-closed 中止：任一条转写失败 / 全部失败 / 替身产出——
    # 报告已写出，但结论不得对外引用（退出码把「写了报告」与「能拿去引用」分开）
    if report["incomplete"] or report.get("synthetic"):
        return 5
    return 0


def _report_samples(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """取逐条样本明细（成功条与失败条全在，不缩分母视图）。

    `samples` 是报告 schema 的公开字段：raw JSONL 与报告 cer 块必须来自同一份
    明细，否则「报告说 n=3、raw 里只有 2 行」这类不一致无法被发现。
    """
    return list(report.get("samples", []))


def write_readback(report: Dict[str, Any], out_dir: Any) -> Path:
    """写 report.json 到 out_dir，落盘前再核一次磁盘上的 raw 指纹与行数。

    为什么落盘前再核一次：raw JSONL 先于 report.json 落盘，两者之间文件被改
      （或写盘半途中断）时，指纹核对能把它变红而不是照写——与 eval.report.write_report
      的纪律一致（不美化，eval/AGENTS.md §③.3）。
      核对基准就是 out_dir：run_readback_cli 写入的 raw 指针是相对它的 `raw/…`。

    异常：
        OSError: 目录创建或写文件失败
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    issues = check_raw_on_disk(report, out)
    if issues:
        # T14c 修 3：调用方（run_readback_cli）在建指针时已核过一次并追加过同样的
        # 问题——重复追加会让 1 条问题在报告里出现 2 条、摘要显示「2 项原因」。
        # 这里按问题原文去重，保证同一条问题在 report.json 里只出现一次。
        report["incomplete"] = True
        existing = list(report.get("incomplete_reasons", []))
        for issue in issues:
            if issue not in existing:
                existing.append(issue)
        report["incomplete_reasons"] = existing
    path = out / "report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _render_readback_summary(report: Dict[str, Any]) -> str:
    """人读摘要：incomplete 时首行必须以 [INCOMPLETE] 开头（eval/report.render_summary 同款规则）。

    替身 ASR 产出时必须有一行「不得对外引用」警告。
    """
    lines: List[str] = []
    if report.get("incomplete"):
        reasons: List[str] = list(report.get("incomplete_reasons", []))
        lines.append(
            f"[INCOMPLETE] 本报告数据不完整（{len(reasons)} 项原因），{SYNTHETIC_WARNING}："
        )
        for i, reason in enumerate(reasons, 1):
            lines.append(f"  {i}. {reason}")
    else:
        lines.append("[COMPLETE] 全部条目转写成功；CER 可逐值复现")

    synthetic = (report.get("env") or {}).get("asr", {}).get("synthetic")
    if synthetic:
        lines.append(
            f"[警告] asr.synthetic=true（替身 ASR）：CER 数字由替身转写产出，"
            f"{SYNTHETIC_WARNING}（不得美化，eval/AGENTS.md §③.3）"
        )

    manifest = report.get("manifest") or {}
    block = report.get("cer") or {}
    asr_env = (report.get("env") or {}).get("asr") or {}
    lines.append("")
    lines.append(
        f"样本量 { _cer.CER_N }={block.get(_cer.CER_N)} / manifest 条目 "
        f"{manifest.get('entries')} seed={report.get('seed')} "
        f"分位口径={report.get('caliber', {}).get('quantile_method')}"
    )
    p50 = block.get(_cer.CER_P50)
    p99 = block.get(_cer.CER_P99)
    mean = block.get(_cer.CER_MEAN)
    lines.append(
        f"{_cer.CER}: { _cer.CER_P50 }={p50} { _cer.CER_P99 }={p99} "
        f"{ _cer.CER_MEAN }={mean} max={block.get('max')} "
        f"（ASR {asr_env.get('name')} / {asr_env.get('model_version')}）"
    )
    pointers = {
        k: v for k, v in (report.get("raw") or {}).items()
        if isinstance(v, str) and not k.endswith(_FINGERPRINT_SUFFIXES)
    }
    if pointers:
        lines.append("原始数据: " + ", ".join(f"{k}={v}" for k, v in sorted(pointers.items())))
    lines.append("可复现: " + str(report.get("caliber", {}).get("reproducibility")))
    if report.get("command"):
        lines.append(f"复现命令: {report['command']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI 解析
# ---------------------------------------------------------------------------
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析 CLI 参数（argparse 自带未知参数/类型错误的非零退出）。"""
    parser = argparse.ArgumentParser(
        prog="python3 -m eval.readback",
        description=(
            "回读 CER harness：manifest(wav+reference) → 注入的 ASR 适配器 → "
            "CER → 可复现报告（Seed-TTS-eval 口径，docs/05 §5.3）"
        ),
    )
    parser.add_argument("--manifest", required=True, help="manifest JSONL（每行 wav+reference）")
    parser.add_argument(
        "--asr", required=True,
        help="ASR 适配器 dotted path（模块:类名），例 adapters.asr_omlx:OmlxAsr",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:10099",
                        help="ASR 服务地址（可覆盖）")
    parser.add_argument("--model", default="Qwen3-ASR-0.6B-8bit",
                        help="请求体 model 字段值（可覆盖）")
    parser.add_argument("--timeout", type=float, default=120.0, help="单次转写超时秒数")
    parser.add_argument("--out", default=None, help="报告输出目录（缺省用临时目录）")
    parser.add_argument("--command", default=None,
                        help="写进报告的复现命令（缺省用当前命令行还原）")
    return parser.parse_args(list(argv) if argv is not None else None)


def _utc_now_iso() -> str:
    """当前 UTC 时间戳（ISO 8601，带 Z）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# 需要在 CLI 顶层拦住的异常类型（其余异常属实现缺陷，直接 traceback）
_CLI_ERRORS = (ReadbackError, OSError, ValueError, TypeError, ImportError)


if __name__ == "__main__":
    sys.exit(run_readback_cli())
