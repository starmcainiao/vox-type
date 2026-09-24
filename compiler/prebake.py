"""
compiler.prebake — 预铸流水线：枚举 → 合成 → 质检 → 差量复用 → 写包 + 预铸账

职责：把 PackSource 编译成 assets/ 格式的资产包，并在编译期拦住一切不合规。
      产物 = 资产包（manifest.json + audio/）+ 预铸账（prebake_ledger.json）。
不负责：不做命中判定与播放（runtime/）、不实现 TTS（adapters/）、不做四属性校验（T06）。

三个关键决定（为什么这样写）：
  1. **先落暂存**：合成产物先写 out_root/.staging/，全部条目过质检后才移进 audio/。
     这样质检不过 / 合成失败的条目绝不污染正式包目录，fail-closed 时一次清理即可，
     不会出现「半包残留在 audio/ 里被下次装载当成合法资产」。
  2. **内容寻址**：音频文件名用指纹（audio/<fingerprint>.wav），而不是 key/variant 序号。
     同文本+同音色+同语速+同模型 → 同指纹 → 同文件，天然去重；文本一变指纹即变，
     旧文件自动成为孤儿（不影响新包校验），这正是差量重铸能「只重铸受影响条目」的基础。
  3. **fail-closed**：任一失败/质检不过 → 默认不写 manifest、清掉暂存、抛 PrebakeError。
     预铸是编译期，编译失败的正确行为是「什么都不产出」，而不是「产出个半成品」——
     半包会让下游以为预铸成功，运行时才炸，且零留痕。
"""

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from assets import fingerprint, load_pack, validate_pack
from assets.pack import AssetPackError

from compiler.quality import QualityError, check_quality, measure
from compiler.source import PackSource


# ---------------------------------------------------------------------------
# 1. 数据结构与异常
# ---------------------------------------------------------------------------
@dataclass
class PrebakeReport:
    """预铸报告（同时是预铸账的内容主体）。

    属性：
        pack_id / pack_version / protocol_version / ruleset_version / voice /
        model_version / created_at: 与 manifest.json 对齐的包身份
        total:               应铸条目数（key × variant × rate）
        synthesized:         本次真正调用 TTS 的条数
        reused:              复用旧资产的条数（不调用 TTS）
        failed:              合成失败明细 [{key, part_index, rate_key, variant, reason}]
        quality_issues:      质检不过明细 [{key, part_index, rate_key, variant, issues}]
        loudness_normalized: 本轮恒 False（LUFS 归一明确推迟，见 docs/06 §6.1.4）
        clean:               failed 与 quality_issues 都为空
        tts_calls:           TTS 调用次数（与 synthesized 同值，供断言用）
        pack_reload_status:  旧包装载状态（复用判定的留痕；不得静默）
    """
    pack_id: str
    pack_version: str
    protocol_version: str
    ruleset_version: str
    voice: str
    model_version: str
    created_at: str
    total: int = 0
    synthesized: int = 0
    reused: int = 0
    failed: List[Dict[str, Any]] = field(default_factory=list)
    quality_issues: List[Dict[str, Any]] = field(default_factory=list)
    loudness_normalized: bool = False
    clean: bool = False
    tts_calls: int = 0
    pack_reload_status: str = "no_manifest"


class PrebakeError(Exception):
    """预铸失败异常。

    异常对象带 report 属性，调用方能直接拿到完整的 failed / quality_issues 明细，
    不需要重跑一遍流水线去复盘——失败明细必须可取，否则「失败不静默」无从谈起。
    """

    def __init__(self, message: str, report: PrebakeReport):
        super().__init__(message)
        self.report = report


# ---------------------------------------------------------------------------
# 2. 内部辅助
# ---------------------------------------------------------------------------
@dataclass
class _Item:
    """一条待铸条目：话术 key × variant × rate 的组合（part_index 恒为 0）。"""
    key: str
    variant: int
    rate_key: str
    text: str
    fingerprint: str
    index: int  # 枚举顺序（用于保持 manifest 条目顺序稳定）
    ttl: Optional[int] = None                      # 源里的相对失效秒数（审计留痕）
    invalid_at: Optional[datetime] = None          # 绝对失效时刻（UTC，判定唯一依据）


def _enumerate_items(source: PackSource, created_at: datetime) -> List[_Item]:
    """枚举全部待铸条目：每个 phrase 的每个 variant × 每个 rate。

    预期指纹**必须**调 assets.fingerprint（唯一实现）；这里不另写指纹算法。
    part_index 一律 0（多分片明确推迟，见 docs/06 §6.1.4）。

    T18：`ttl` 在这里被**折算成 invalid_at**（`created_at + ttl 秒`），落进 manifest
    的只有绝对时刻——资产层只看 `invalid_at`，不在运行期做二次折算。
    """
    items: List[_Item] = []
    idx = 0
    for phrase in source.phrases:
        invalid_at = phrase.invalid_at
        if invalid_at is None and phrase.ttl is not None:
            # 从**预铸时刻**起算：created_at 是本包唯一的"预铸基准时刻"。
            invalid_at = created_at + timedelta(seconds=phrase.ttl)
        # variant 序号 = 该 variant 在列表中的下标，从 0 起
        for vi, text in enumerate(phrase.variants):
            for rate_key in phrase.rates:
                fp = fingerprint(
                    text=text,
                    voice=source.voice,
                    rate_value=rate_key,
                    model_version=source.model_version,
                )
                items.append(
                    _Item(
                        key=phrase.key,
                        variant=vi,
                        rate_key=rate_key,
                        text=text,
                        fingerprint=fp,
                        index=idx,
                        ttl=phrase.ttl,
                        invalid_at=invalid_at,
                    )
                )
                idx += 1
    return items


def _load_old_pack(out_root: Path) -> Tuple[Optional[Any], str]:
    """尝试装载旧包用于差量复用。

    返回 (AssetPack 或 None, 状态字符串)。状态必须写进报告——旧包装载失败时
    **不做复用、全量重铸**，这个「本该复用却换了路径」的情况不允许静默。

    参数：
        out_root: 输出目录（旧包所在位置）

    返回：
        (旧包实例或 None, 状态描述)
    """
    manifest_path = out_root / "manifest.json"
    if not manifest_path.exists():
        return None, "no_manifest"

    try:
        pack = load_pack(out_root)
        return pack, "loaded"
    except AssetPackError as e:
        # WHY：旧包损坏/过期不是错误——按 docs/06 §6.1.5 全量重铸即可，
        #      但必须在报告里留痕，否则事后无法解释「为什么这次全部重铸了」。
        return None, f"load_failed: {e}"
    except (OSError, ValueError) as e:
        return None, f"load_failed: {e}"


def _build_reuse_index(pack: Any) -> Dict[Tuple[str, int, str, int], Any]:
    """把旧包条目索引成 (key, part_index, rate_key, variant) → 条目。"""
    index: Dict[Tuple[str, int, str, int], Any] = {}
    for entry in pack.assets:
        index[(entry.key, entry.part_index, entry.rate_key, entry.variant)] = entry
    return index


def _write_ledger(out_root: Path, report: PrebakeReport, verify_issues: List[str]) -> None:
    """写预铸账（prebake_ledger.json）：report 全量落盘。

    无论成功还是失败都写——「失败不静默」要求失败明细可追溯。
    loudness_normalized 显式写 false（LUFS 归一明确推迟，见 docs/06 §6.1.4），
    避免下游误以为已经做过 LUFS 归一。

    参数：
        out_root:      输出目录
        report:        预铸报告
        verify_issues: 资产层校验问题列表（空 = 通过）
    """
    ledger = asdict(report)
    ledger["generated_at"] = _iso_now()
    ledger["loudness_normalized"] = False
    ledger["pack_verify_issues"] = verify_issues
    with open(out_root / "prebake_ledger.json", "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def _iso_now() -> str:
    """生成 UTC ISO 8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def _expiry_fields(item: _Item) -> Dict[str, Any]:
    """构造 manifest 条目里的失效字段（T18）。

    裁定：manifest 存 `invalid_at`（绝对时刻，判定唯一依据）+ 可选 `ttl` 原值（审计）。
    两个都不给（旧源）→ 不写任何失效字段，manifest 与既有产物**逐字节一致**。
    """
    fields: Dict[str, Any] = {}
    if item.ttl is not None:
        fields["ttl"] = item.ttl
    if item.invalid_at is not None:
        fields["invalid_at"] = item.invalid_at.isoformat()
    return fields


# ---------------------------------------------------------------------------
# 3. 主入口
# ---------------------------------------------------------------------------
def prebake(
    source: PackSource,
    adapter: Any,
    out_root: Union[str, Path],
    *,
    allow_partial: bool = False,
) -> PrebakeReport:
    """预铸流水线：枚举 → 合成 → 质检 → 差量复用 → 写包 + 预铸账。

    参数：
        source:        已校验的 PackSource
        adapter:       TTS 适配器（需有 synthesize(text, path, rate_key=) /
                       voice / model_version）
        out_root:      输出目录（资产包根目录）
        allow_partial: True 时即使有失败/质检不过也写「部分包」；默认 False（fail-closed）

    返回：
        PrebakeReport

    异常：
        PrebakeError: clean is False 且 allow_partial is False（异常对象带 report）
    """
    out_root = Path(out_root)
    created_at_str = _iso_now()
    # 预铸基准时刻（T18）：`ttl` 从这里起算。与 manifest.created_at 同一个值，
    # 所以「invalid_at == created_at + ttl」对验收方是可复算的（容差 ≤ 2 秒）。
    created_at = datetime.fromisoformat(created_at_str)

    # 1. 枚举待铸条目
    items = _enumerate_items(source, created_at)
    total = len(items)

    # 2. 差量复用：尝试装载旧包
    old_pack, reload_status = _load_old_pack(out_root)
    reuse_index = _build_reuse_index(old_pack) if old_pack is not None else {}

    # 复用需要旧包的 voice/model_version 与当前引擎一致
    # WHY：音色/模型版本换了，旧音频是「另一个声音」，复用 = 运行时突然换了个声音，
    #      这是红线里点名的静默降级。
    engine_ok = (
        old_pack is not None
        and old_pack.voice == getattr(adapter, "voice", source.voice)
        and old_pack.model_version == getattr(adapter, "model_version", source.model_version)
    )

    # 3. 准备暂存目录（先落暂存，全部过质检后才移进 audio/）
    staging = out_root / ".staging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    staged: List[Tuple[_Item, str, int]] = []  # (条目, 暂存路径, 时长ms)
    failed: List[Dict[str, Any]] = []
    quality_issues: List[Dict[str, Any]] = []
    synthesized = 0
    reused = 0
    tts_calls = 0

    # 4. 逐条处理：复用 or 合成 + 质检
    for item in items:
        identity = (item.key, 0, item.rate_key, item.variant)

        # 4a. 差量复用（四条全中才复用：同身份 + 指纹相同 + 音频存在 + 引擎一致）
        old = reuse_index.get(identity)
        audio_rel = f"audio/{item.fingerprint}.wav"
        if (
            old is not None
            and engine_ok
            and old.fingerprint == item.fingerprint
            and (out_root / audio_rel).exists()
        ):
            reused += 1
            entries.append({
                "key": item.key,
                "part_index": 0,
                "rate_key": item.rate_key,
                "variant": item.variant,
                "text": item.text,
                "fingerprint": item.fingerprint,
                "path": audio_rel,
                "duration_ms": int(old.duration_ms),
                "_index": item.index,
                **_expiry_fields(item),
            })
            continue

        # 4b. 合成（落到暂存，不直接写进 audio/）
        staged_path = staging / f"{item.fingerprint}.wav"
        try:
            adapter.synthesize(item.text, staged_path, rate_key=item.rate_key)
            synthesized += 1
            tts_calls += 1
        except Exception as e:
            # WHY：失败必须记录并**继续处理其余条目**——一次预铸里可能只有一条话术
            #      触发引擎问题，中断会让整批前功尽弃；静默跳过则下游毫无线索。
            failed.append({
                "key": item.key,
                "part_index": 0,
                "rate_key": item.rate_key,
                "variant": item.variant,
                "reason": f"{type(e).__name__}: {e}",
            })
            continue

        # 4c. 质检：measure + check_quality
        try:
            stats = measure(staged_path)
            issues = check_quality(stats)
        except QualityError as e:
            # 度量失败（非 WAV / 打不开）同样视为质检不过，不得静默
            issues = [f"度量失败: {e}"]
            stats = None

        if issues:
            quality_issues.append({
                "key": item.key,
                "part_index": 0,
                "rate_key": item.rate_key,
                "variant": item.variant,
                "issues": issues,
            })
            continue

        # 4d. 通过质检 → 记住暂存文件，稍后移进 audio/
        staged.append((item, str(staged_path), stats.duration_ms))

    # 5. fail-closed：不干净且不允许多余 → 不写包、清掉暂存、抛错
    clean = not failed and not quality_issues
    report = PrebakeReport(
        pack_id=source.pack_id,
        pack_version=source.pack_version,
        protocol_version=source.protocol_version,
        ruleset_version=source.ruleset_version,
        voice=source.voice,
        model_version=source.model_version,
        created_at=created_at_str,
        total=total,
        synthesized=synthesized,
        reused=reused,
        failed=failed,
        quality_issues=quality_issues,
        loudness_normalized=False,
        clean=clean,
        tts_calls=tts_calls,
        pack_reload_status=reload_status,
    )

    if not clean and not allow_partial:
        # fail-closed：不写 manifest、清掉暂存；预铸账仍要留（失败不静默）。
        # WHY：半包会让下游以为预铸成功、运行时才炸且零留痕；
        #      所以「什么都不产出」，但失败明细必须可取（e.report + 预铸账双通道）。
        shutil.rmtree(staging, ignore_errors=True)
        _write_ledger(out_root, report, [])
        raise PrebakeError(
            f"预铸失败：共 {total} 条，合成失败 {len(failed)} 条，"
            f"质检不过 {len(quality_issues)} 条；"
            f"已 fail-closed（不写 manifest），失败明细见 e.report",
            report,
        )

    # 6. 写包：把通过的音频移进 audio/<fingerprint>.wav（内容寻址）
    audio_dir = out_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for item, staged_path, duration_ms in staged:
        dest = audio_dir / f"{item.fingerprint}.wav"
        if dest.exists():
            # 内容寻址下同指纹 = 同内容（如同文本同语速出现在不同 key 下），
            # 已存在则直接丢弃暂存副本，避免覆盖造成无意义的文件抖动。
            Path(staged_path).unlink(missing_ok=True)
        else:
            shutil.move(staged_path, dest)
        entries.append({
            "key": item.key,
            "part_index": 0,
            "rate_key": item.rate_key,
            "variant": item.variant,
            "text": item.text,
            "fingerprint": item.fingerprint,
            "path": f"audio/{item.fingerprint}.wav",
            "duration_ms": int(duration_ms),
            "_index": item.index,
            **_expiry_fields(item),
        })

    # 清理暂存目录（含未通过质检的残留文件）
    shutil.rmtree(staging, ignore_errors=True)

    # 按枚举顺序排序，保证两次预铸产出的 manifest 条目顺序一致（幂等可测）
    entries.sort(key=lambda e: e["_index"])
    for entry in entries:
        entry.pop("_index", None)

    manifest = {
        "pack_id": source.pack_id,
        "pack_version": source.pack_version,
        "protocol_version": source.protocol_version,
        "ruleset_version": source.ruleset_version,
        "voice": source.voice,
        "model_version": source.model_version,
        "created_at": created_at_str,
        "assets": entries,
    }

    if entries:
        with open(out_root / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    else:
        # 全失败且 allow_partial=True：不产出空包（assets 必须非空才能过资产层校验），
        # 只留预铸账记录失败——这是「不写包」与「写部分包」的边界。
        (out_root / "manifest.json").unlink(missing_ok=True)

    # 7. 写出的包必须能过资产层校验；过不了 = 产物本身有问题，fail-closed
    # WHY：资产层是格式的唯一权威，我们的产物必须自证合规；
    #      若这里能过而 vox verify 过不了，说明两处口径漂移，必须立刻暴露。
    verify_issues: List[str] = []
    if (out_root / "manifest.json").exists():
        try:
            load_pack(out_root)
        except AssetPackError as e:
            verify_issues.append(f"load_pack 失败: {e}")
        verify_issues.extend(validate_pack(out_root))

    # 8. 写预铸账（report 全量落盘；无论成功还是校验失败都写——失败留痕是本层红线）
    _write_ledger(out_root, report, verify_issues)

    if verify_issues:
        # 产物不合格 → 撤掉 manifest（不留半成品包）并抛错
        (out_root / "manifest.json").unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        raise PrebakeError(
            f"预铸产物未通过资产层校验: {verify_issues}",
            report,
        )

    return report
