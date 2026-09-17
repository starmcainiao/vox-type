"""
assets.fingerprint — 文本指纹算法（唯一实现）

职责：根据 text + voice + rate_value + model_version 计算 16 位 hex 指纹。
      整套方案的保险丝：话术改了但资产没重铸 → 指纹不匹配 → 运行时视为未命中。
不负责：不做命中判定（runtime/）、不做资产校验（pack.py）。

指纹算法（冻结，不得改动）：
    sha256(f"{text}\\x00{voice}\\x00{rate_value}\\x00{model_version}".encode("utf-8")).hexdigest()[:16]
"""

import hashlib
from typing import Union


# ---------------------------------------------------------------------------
# 指纹算法（冻结区——唯一实现，其他层只能调它）
# ---------------------------------------------------------------------------
def fingerprint(
    text: str,
    voice: str,
    rate_value: Union[str, int, float],
    model_version: str,
) -> str:
    """计算 16 位 hex 指纹。

    四个输入参数任一变化 → 指纹必变（正交性由测试保证）。
    指纹算法不可更改，改了 = 破坏性变更。

    参数：
        text:          话术文本内容
        voice:         音色标识
        rate_value:    语速值（slow/normal/fast 或数值）
        model_version: 模型版本号

    返回：
        16 位 hex 字符串（SHA-256 截断）
    """
    # WHY：用 \\x00 分隔确保四个参数在字节流中无歧义分界，
    #       避免 "abc" + "de" == "ab" + "cde" 的拼接歧义。
    raw = f"{text}\x00{voice}\x00{rate_value}\x00{model_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
