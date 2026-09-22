"""
eval/_io — eval 层共用的文件级 I/O 原语（仅标准库）

职责：把 eval 三个 harness（bench / readback / report）里**逐字重复**的三件
     文件级 helper 收敛到一处：JSONL 写、JSONL 读、文件字节 sha256。
     只做文件级读写与摘要，不做任何指标统计与质检判定——统计口径归 eval/stats.py，
     指纹核对归 eval/report.py。

边界（本文件存在的唯一理由）：
  本文件**只 import 标准库**。不得引入 assets/、runtime/、adapters/ 或
  eval.bench —— eval.bench 为了跑 harness 必须静态 import assets 与 runtime，
  一旦 helper 住在那里，回读路径（readback）会被迫拖进两条不该有的跨层依赖。
  所以收敛点只能落在标准库这层：一份实现、三处调用，且零新增层依赖。

三件 helper 的行为契约（与 T21 前各处「等价实现」逐字一致，去重不改变任何字节）：
  - write_jsonl    ：ensure_ascii=False、default=str、一行一条、父目录自动创建；
  - read_jsonl     ：坏行记为 {"__bad_line__": 行号}、非 dict 记为
                     {"__not_dict__": 行号}，不抛错——报告要如实呈现损坏；
  - sha256_of_file ：按文件**字节流**分块（65536）摘要，不假设编码与行结构。

指纹咬合耦合（bench 写 ↔ report 验，两侧必须共用这一个实现）：
  eval/bench.py 落 raw 时用本文件的 sha256_of_file **写指纹**
  （`{name}_sha256` / `{name}_lines` 追加式字段），
  eval/report.py 的 check_raw_on_disk 用**同一个函数**重算**校验指纹**。
  写侧与验侧若各写一份，两边分块策略或编码假设差一个字，就会「自己写的指纹自己验不过」
  或「该抓的值级篡改抓不到」（docs/08 §8.7 欠账 1）。收敛到此处后，
  写指纹与重算指纹在物理上不可能漂移。

WHY 是文件字节摘要而不是内容语义摘要（bench / readback 三处的 WHY 在此合并留痕）：
  行数与 index 都在、只把 `first_audio_ms` 除以 1000（或把某条 cer 改成 0.0）的
  「值级篡改」不改变任何计数结构，任何按行数 / 结构 / 语义聚合的比对都抓不到它；
  只有逐字节摘要会变。用 hashlib.sha256 而不是内建 hash()：后者字符串种子随进程
  变化，同一文件两次跑会算出不同值。
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

# 摘要分块大小：与原三处实现逐字一致（改这个值会立刻改变指纹，破坏向后兼容）
_CHUNK_SIZE = 65536


def write_jsonl(path: Path, records: Sequence[Dict[str, Any]]) -> None:
    """逐条落 JSONL（一行一条），父目录自动创建。

    契约：ensure_ascii=False（中文原样落盘，不被转义成 \\uXXXX）、default=str
    （datetime 等非标量不炸）、每条一个换行、UTF-8。三个 harness 的 raw 块
    都靠这个格式落盘，report.check_raw_on_disk 也按同一格式回读。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """逐行读 JSONL；坏行不抛错（报告要如实呈现损坏，而不是因为脏数据挂掉）。

    契约：
      - 空行跳过；
      - 解析失败 → {"__bad_line__": 行号}；
      - 解析出来不是 dict → {"__not_dict__": 行号}（JSONL 约定一条一个对象）。
    行号从 1 起，与写侧的「第 N 条」口径一致，报错时能指到具体行。
    """
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except ValueError:
                value = {"__bad_line__": line_no}
            records.append(value if isinstance(value, dict) else {"__not_dict__": line_no})
    return records


def sha256_of_file(path: Path) -> str:
    """按文件字节流计算 sha256（全文分块读，不假设编码 / 行结构）。

    本函数同时是「指纹写侧」（eval/bench.py 的 raw 块）与「指纹验侧」
    （eval/report.py 的 check_raw_on_disk）的唯一实现，见模块 docstring 的
    「指纹咬合耦合」一节。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
