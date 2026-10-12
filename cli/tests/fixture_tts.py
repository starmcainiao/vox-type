"""
cli.tests.fixture_tts — cli 测试夹具专用的平台无关 TTS 替身（非产品件）

职责：给 cli 测试的极小包夹具提供**不依赖 macOS `say`、不依赖任何外部命令**的
      合成引擎，使 cli 根测试在 Linux/CI 上也能跑通（docs/13 §五#27 的解耦落点）。
不负责：不是产品代码；不进入任何产品路径；不代表任何真实引擎；不做判定。

身份口径：voice/model_version 动态取自 `cli.tests.TINY_PACK_JSON` 的声明值——
      运行时引擎一致性检查（runtime/executor.py）按这两个字符串逐字比对，
      与包源声明不一致会被判 engine_mismatch（这是被测行为，不是要绕过的）。
实现：子类化 `eval.offline_tts.OfflineTts`（仓库既有的确定性离线替身），
      零参可构造（`cli.commands.resolve_adapter` 以 `cls()` 实例化）。
"""

from cli.tests import TINY_PACK_JSON
from eval.offline_tts import OfflineTts


class FixtureTts(OfflineTts):
    """零参可构造的夹具引擎：身份与 TINY_PACK_JSON 声明一致，产物平台无关。"""

    def __init__(self) -> None:
        super().__init__(
            voice=TINY_PACK_JSON["voice"],
            model_version=TINY_PACK_JSON["model_version"],
        )
