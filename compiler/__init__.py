# compiler/__init__.py
# 预铸流水线公共接口导出

from compiler.checks import CheckResult, Violation, check_properties
from compiler.prebake import PrebakeError, PrebakeReport, prebake
from compiler.quality import AudioStats, QualityError, check_quality, measure
from compiler.script import Script, ScriptError, load_script
from compiler.source import PackSource, PhraseSource, SourceError, load_source

__all__ = [
    "load_source",
    "PackSource",
    "PhraseSource",
    "SourceError",
    "measure",
    "AudioStats",
    "check_quality",
    "QualityError",
    "prebake",
    "PrebakeReport",
    "PrebakeError",
    # T06 追加：剧本源格式装载与四项属性 + 降级留痕判据
    "load_script",
    "Script",
    "ScriptError",
    "check_properties",
    "Violation",
    "CheckResult",
]
