#!/usr/bin/env python3
"""
analyze_flow_occupancy.py — 跨行业「系统/助手侧流程决定话轮占比」

卡面 T22 必做 2 的主指标。四份带系统侧话轮的对话集各算一份，与客服 23.0% 对照。

口径（判据全部与既有脚本同源，见 common.py 与各 loader 注释）：
  ① 有对话行为标注的集（CrossWOZ）→ 按标注 intent 判；
  ② 有业务动作埋点的集（ABCD 的 `action` 角色）→ action 话轮直接算流程决定，
     agent 话轮另按文本形态判；
  ③ 无任何行为标注的集（MultiWOZ 2.2 / Taskmaster TM-1）→ 只能走**文本形态**启发式，
     报告中如实标注该口径弱于 ①②。
「句式可复用率」（骨架 ≥5 次）作为同一批数据的第二个数字，与客服 1.8% 对照。

用法：
  python3 analyze_flow_occupancy.py --out report.json [--inject-all-flow]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import common  # noqa: E402

CORPUS_ROOT = Path(os.environ.get("CORPUS_ROOT", "~/corpus")).expanduser()

# 客服基线（labs/corpus-harvest/README.md §六，25810 个客服话轮）
KEFU_BASELINE = {
    "n_turns": 25810,
    "flow_decided_rate": 0.230,
    "reusable_rate": 0.018,
    "compress_ratio": 1.03,
    "zero_hit_dialog_rate": 0.757,
}


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------
def _load_crosswoz_split(split: str) -> Dict[str, Any]:
    """CrossWOZ 一个 split：系统侧话轮 + 标注 intent 分解。"""
    p = CORPUS_ROOT / "thu-coai__CrossWOZ" / "data" / "crosswoz" / f"{split}.json.zip"
    if not p.exists():
        raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {p}")
    with zipfile.ZipFile(p) as z:
        name = next(n for n in z.namelist() if n.endswith(".json"))
        doc = json.loads(z.read(name).decode("utf-8"))
    sys_turns: List[str] = []
    flow_turns = 0
    da_hist: Dict[str, int] = {}
    per_dialog_hit: Dict[str, int] = {}
    for cid, conv in doc.items():
        hits = 0
        for msg in conv["messages"]:
            if msg.get("role") != "sys":
                continue
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            sys_turns.append(text)
            flow_here = False
            for da in msg.get("dialog_act") or []:
                # da = [类别, intent, 域, 槽]
                kind, intent = da[0], da[1]
                da_hist[f"{kind}/{intent}"] = da_hist.get(f"{kind}/{intent}", 0) + 1
                if intent in common.CN_FLOW_INTENTS:
                    flow_here = True
            if flow_here:
                flow_turns += 1
                hits += 1
        per_dialog_hit[str(cid)] = hits
    return {
        "sys_turns": sys_turns,
        "flow_turns": flow_turns,
        "n_dialogues": len(doc),
        "dialogues_with_zero_flow": sum(1 for v in per_dialog_hit.values() if v == 0),
        "flow_da_histogram": dict(sorted(da_hist.items(), key=lambda kv: -kv[1])[:15]),
    }


def load_crosswoz() -> Dict[str, Any]:
    splits = ["train", "val", "test"]
    out: Dict[str, Any] = {}
    for s in splits:
        out[s] = _load_crosswoz_split(s)
    return out


def _load_abcd_split(split: str) -> Dict[str, Any]:
    """ABCD 一个 split：agent 话轮（文本形态判流程）+ action 话轮（业务动作埋点）。"""
    p = CORPUS_ROOT / "ASAPPresearch__ABCD" / "data" / "abcd_v1.1.json.gz"
    if not p.exists():
        raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {p}")
    doc = json.load(gzip.open(p, "rt", encoding="utf-8"))
    agent_texts: List[str] = []
    action_texts: List[str] = []
    agent_flow = 0
    for conv in doc[split]:
        for role, text in conv["original"]:
            t = text.strip()
            if not t:
                continue
            if role == "agent":
                agent_texts.append(t)
                if common.classify_by_flow_patterns(t):
                    agent_flow += 1
            elif role == "action":
                action_texts.append(t)
    return {
        "agent_texts": agent_texts,
        "agent_flow_turns": agent_flow,
        "action_texts": action_texts,
        "n_dialogues": len(doc[split]),
    }


def load_abcd() -> Dict[str, Any]:
    return {s: _load_abcd_split(s) for s in ("train", "dev", "test")}


def load_abcd_delexed() -> List[str]:
    """ABCD delexed agent 话轮：去实体化后的规范写法。

    为什么要这一份：ABCD 的 `original` 里嵌了订单号、地址、用户名这些**实体值**，
    走共用 skeleton 判据时全是「每轮不同」；但 ABCD 自己就提供了 delexed 版本
    （把实体替换成 `[slot : value]` 占位符），这才是与客服语料的转写**同档**的文本。
    客服 23.0% 用的是原始转写，ABCD 的 original 里实体值更密（订单/邮箱/电话），
    直接比会系统性低估 ABCD。两个口径都算，报告里如实标注。
    """
    p = CORPUS_ROOT / "ASAPPresearch__ABCD" / "data" / "abcd_v1.1.json.gz"
    if not p.exists():
        raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {p}")
    doc = json.load(gzip.open(p, "rt", encoding="utf-8"))
    texts: List[str] = []
    for split in ("train", "dev", "test"):
        for conv in doc[split]:
            for t in conv.get("delexed") or []:
                if t.get("speaker") != "agent":
                    continue
                a = (t.get("text") or "").strip()
                if a:
                    texts.append(a)
    return texts


def _load_multiwoz22(split: str) -> Dict[str, Any]:
    """MultiWOZ 2.2 一个 split：SYSTEM 话轮 + 同轮 frame 里是否有 slots/actions。"""
    d = CORPUS_ROOT / "budzianowski__multiwoz" / "data" / "MultiWOZ_2.2" / split
    if not d.exists():
        raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {d}")
    sys_texts: List[str] = []
    sys_with_slots = 0
    sys_with_actions = 0
    sys_flow = 0
    n_dialogues = 0
    for p in sorted(d.glob("dialogues_*.json")):
        for conv in json.loads(p.read_text(encoding="utf-8")):
            n_dialogues += 1
            for t in conv["turns"]:
                if t.get("speaker") != "SYSTEM":
                    continue
                u = (t.get("utterance") or "").strip()
                if not u:
                    continue
                sys_texts.append(u)
                frames = t.get("frames") or []
                slots = [f.get("slots") for f in frames if f.get("slots")]
                actions = [f.get("actions") for f in frames if f.get("actions")]
                if any(slots):
                    sys_with_slots += 1
                if any(actions):
                    sys_with_actions += 1
                if common.classify_by_flow_patterns(u):
                    sys_flow += 1
    return {
        "sys_texts": sys_texts,
        "sys_flow_turns": sys_flow,
        "sys_turns_with_slots": sys_with_slots,
        "sys_turns_with_actions": sys_with_actions,
        "n_dialogues": n_dialogues,
    }


def load_multiwoz22() -> Dict[str, Any]:
    return {s: _load_multiwoz22(s) for s in ("train", "dev", "test")}


def _load_multiwoz21() -> Dict[str, Any]:
    """MultiWOZ 2.1：log 里奇数位是 system，带 dialog_act（SYSTEM-*/HOTEL-INFORM 等）。"""
    p = CORPUS_ROOT / "budzianowski__multiwoz" / "data" / "MultiWOZ_2.1.zip"
    if not p.exists():
        raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {p}")
    with zipfile.ZipFile(p) as z:
        doc = json.loads(z.read("MultiWOZ_2.1/data.json"))
    sys_texts: List[str] = []
    sys_flow = 0
    sys_with_state = 0
    acts_hist: Dict[str, int] = {}
    for cid, conv in doc.items():
        for i, t in enumerate(conv.get("log") or []):
            if i % 2 != 1:  # 偶数位 = user
                continue
            u = (t.get("text") or "").strip()
            if not u:
                continue
            sys_texts.append(u)
            if t.get("metadata"):
                sys_with_state += 1
            for k in (t.get("dialog_act") or {}):
                acts_hist[k] = acts_hist.get(k, 0) + 1
    return {
        "sys_texts": sys_texts,
        "n_dialogues": len(doc),
        "sys_turns_with_state": sys_with_state,
        "act_histogram": dict(sorted(acts_hist.items(), key=lambda kv: -kv[1])[:15]),
        "_raw": doc,
    }


def _load_taskmaster(kind: str) -> Dict[str, Any]:
    """Taskmaster TM-1：ASSISTANT 话轮。"""
    p = CORPUS_ROOT / "google-research-datasets__Taskmaster" / "TM-1-2019" / f"{kind}.json"
    if not p.exists():
        raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    texts: List[str] = []
    flow = 0
    instr_hist: Dict[str, int] = {}
    for conv in doc:
        instr = conv.get("instruction_id") or "unknown"
        instr_hist[instr] = instr_hist.get(instr, 0) + 1
        for u in conv.get("utterances") or []:
            if u.get("speaker") != "ASSISTANT":
                continue
            t = (u.get("text") or "").strip()
            if not t:
                continue
            texts.append(t)
            if common.classify_by_flow_patterns(t):
                flow += 1
    return {
        "texts": texts,
        "flow_turns": flow,
        "n_dialogues": len(doc),
        "instruction_histogram": dict(sorted(instr_hist.items(), key=lambda kv: -kv[1])[:10]),
    }


def load_taskmaster() -> Dict[str, Any]:
    return {k: _load_taskmaster(k) for k in ("self-dialogs", "woz-dialogs")}


def load_hwu64() -> Dict[str, Any]:
    """HWU64 标注 CSV：用户侧指令，`answer` 列（去实体化的规范写法）。"""
    p = CORPUS_ROOT / "xliuhw__NLU-Evaluation-Data" / "AnnotatedData" / \
        "NLU-Data-Home-Domain-Annotated-All.csv"
    if not p.exists():
        raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {p}")
    rows = 0
    intents: Dict[str, int] = {}
    answers: List[str] = []
    with p.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f, delimiter=";")
        for row in r:
            rows += 1
            a = (row.get("answer") or "").strip()
            if a:
                answers.append(a)
            it = row.get("intent") or "?"
            intents[it] = intents.get(it, 0) + 1
    return {
        "answers": answers,
        "n_rows": rows,
        "intent_histogram": dict(sorted(intents.items(), key=lambda kv: -kv[1])[:12]),
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def build(*, inject_all_flow: bool = False) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "_schema": "vox-multi-industry-flow-occupancy/1",
        "run_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "repeat_min": common.REPEAT_MIN,
        "caliber": {
            "flow_decided_rate": "系统/助手侧话轮里，由流程（而非当场内容）决定说什么话的占比",
            "reusable_rate": "同一份语料内，去实体值后的句式骨架出现 ≥5 次的话轮占比（与客服 1.8% 同一判据）",
            "criteria_source": "判据与 labs/corpus-harvest/cross_check_template_rate.py 的 skeleton()/REPEAT_MIN "
                              "同源（见 common.py）；流程决定词表对齐 labs/corpus-harvest/README.md §六 的"
                              "「问候/确认身份/告别/合规提示/请稍等/未听清重试/转人工」七类",
            "flow_decided_rate_note": "各集口径强弱不同，不可直接横比数值，只能比「量级」——见每集的 method 字段",
        },
        "kefu_baseline": {**KEFU_BASELINE, "note": "labs/corpus-harvest/README.md §六，1855 通真实电话"},
        "inject_all_flow": inject_all_flow,
        "corpora": {},
    }

    # ---- CrossWOZ（中文主证据，标注驱动）----
    cw = load_crosswoz()
    cw_all_text: List[str] = []
    cw_flow = 0
    cw_n = 0
    cw_zero = 0
    cw_hist: Dict[str, int] = {}
    for s in ("train", "val", "test"):
        d = cw[s]
        cw_all_text.extend(d["sys_turns"])
        cw_flow += d["flow_turns"]
        cw_n += d["n_dialogues"]
        cw_zero += d["dialogues_with_zero_flow"]
        for k, v in d["flow_da_histogram"].items():
            cw_hist[k] = cw_hist.get(k, 0) + v
    t = common.template_stats(cw_all_text)
    report["corpora"]["thu-coai/CrossWOZ（系统侧，中文五域）"] = {
        "side": "system",
        "method": "标注驱动（sys dialog_act 的 intent ∈ {welcome,greet,thank,bye,reqmore}）",
        "method_strength": "强",
        "n_turns": t["n_turns"],
        "n_dialogues": cw_n,
        "flow_decided_turns": t["n_turns"] if inject_all_flow else cw_flow,
        "flow_decided_rate": 1.0 if inject_all_flow else common.occupancy(t["n_turns"], cw_flow),
        "reusable_rate": t["reusable_rate"],
        "compress_ratio": t["compress_ratio"],
        "n_skeletons": t["n_skeletons"],
        "dialogues_with_zero_flow_turns": cw_zero,
        "flow_da_histogram": cw_hist,
        "top_skeletons": t["top_skeletons"],
        "kefu_delta_pp": round(
            (1.0 if inject_all_flow else common.occupancy(t["n_turns"], cw_flow)) - KEFU_BASELINE["flow_decided_rate"], 4),
    }

    # ---- ABCD（英文客服，业务动作埋点）----
    ab = load_abcd()
    agent_all: List[str] = []
    act_all: List[str] = []
    ab_flow = 0
    ab_n = 0
    for s in ("train", "dev", "test"):
        d = ab[s]
        agent_all.extend(d["agent_texts"])
        act_all.extend(d["action_texts"])
        ab_flow += d["agent_flow_turns"]
        ab_n += d["n_dialogues"]
    t = common.template_stats(agent_all)
    # ABCD 的流程决定 = agent 的话里命中流程形态 + action 埋点话轮（系统自动执行的确定性动作）
    ab_total = t["n_turns"] + len(act_all)
    ab_flow_total = ab_flow + len(act_all)
    report["corpora"]["ASAPPresearch/ABCD（agent 侧，英文电商客服）"] = {
        "side": "system(agent) + action",
        "method": "agent 话轮走文本形态启发式 + action 话轮（业务动作埋点）直接计流程决定",
        "method_strength": "中（action 埋点是强证据；agent 部分仍是启发式）",
        "n_turns": ab_total,
        "n_dialogues": ab_n,
        "agent_turns": t["n_turns"],
        "action_turns": len(act_all),
        "flow_decided_turns": ab_total if inject_all_flow else ab_flow_total,
        "flow_decided_rate": 1.0 if inject_all_flow else common.occupancy(ab_total, ab_flow_total),
        "agent_flow_rate_text_only": common.occupancy(t["n_turns"], ab_flow),
        "reusable_rate": t["reusable_rate"],
        "compress_ratio": t["compress_ratio"],
        "n_skeletons": t["n_skeletons"],
        "top_skeletons": t["top_skeletons"],
        "kefu_delta_pp": round(
            (1.0 if inject_all_flow else common.occupancy(ab_total, ab_flow_total)) - KEFU_BASELINE["flow_decided_rate"], 4),
    }

    # ABCD delexed 口径：同一批 agent 话轮，换成去实体化的规范写法再算骨架复用
    ab_dx = load_abcd_delexed()
    tdx = common.template_stats(ab_dx)
    report["corpora"]["ASAPPresearch/ABCD（agent 侧 delexed 口径，同数据的去实体化版本）"] = {
        "side": "system(agent)",
        "method": "ABCD 自带的 delexed 字段（实体替换成 [slot : value] 占位符）—— 与客服原始转写同档",
        "method_strength": "口径可比性修正项，不是独立结论",
        "n_turns": tdx["n_turns"],
        "flow_decided_rate": None,
        "flow_decided_turns": None,
        "kefu_delta_pp": None,
        "reusable_rate": tdx["reusable_rate"],
        "compress_ratio": tdx["compress_ratio"],
        "n_skeletons": tdx["n_skeletons"],
        "top_skeletons": tdx["top_skeletons"],
    }

    # ---- MultiWOZ 2.2（英文七域任务对话）----
    mw = load_multiwoz22()
    mw_text: List[str] = []
    mw_flow = 0
    mw_slots = 0
    mw_actions = 0
    mw_n = 0
    for s in ("train", "dev", "test"):
        d = mw[s]
        mw_text.extend(d["sys_texts"])
        mw_flow += d["sys_flow_turns"]
        mw_slots += d["sys_turns_with_slots"]
        mw_actions += d["sys_turns_with_actions"]
        mw_n += d["n_dialogues"]
    t = common.template_stats(mw_text)
    rate = 1.0 if inject_all_flow else common.occupancy(t["n_turns"], mw_flow)
    report["corpora"]["budzianowski/multiwoz 2.2（SYSTEM 侧，英文七域）"] = {
        "side": "system",
        "method": "纯文本形态启发式（2.2 无对话行为标注；frame 里的 slots/actions 只作旁证）",
        "method_strength": "弱（无标注，只能下界估计）",
        "n_turns": t["n_turns"],
        "n_dialogues": mw_n,
        "sys_turns_with_slots": mw_slots,
        "sys_turns_with_actions": mw_actions,
        "flow_decided_turns": t["n_turns"] if inject_all_flow else mw_flow,
        "flow_decided_rate": rate,
        "reusable_rate": t["reusable_rate"],
        "compress_ratio": t["compress_ratio"],
        "n_skeletons": t["n_skeletons"],
        "top_skeletons": t["top_skeletons"],
        "kefu_delta_pp": round(rate - KEFU_BASELINE["flow_decided_rate"], 4),
    }

    # ---- MultiWOZ 2.1（带 SYSTEM-* 对话行为）----
    mw21 = _load_multiwoz21()
    # 2.1 的系统侧 dialog_act 用的是 MultiWOZ 通用标签体系（`general-reqmore` /
    # `general-bye` / `hotel-request` / `train-offerbook` …）。其中属于「流程决定」的：
    #   general-reqmore（未听清重试）、general-bye（告别）、general-welcome（问候）、
    #   general-greet（问候）、hotel-request / train-request / restaurant-request /
    #   booking-request（系统反问，与客服「索要身份信息/细化问题」同族）、
    #   * -offerbook / -offerbooked（系统提出方案并询问确认）。
    # Hotel-Inform / Restaurant-Inform 等 `-inform` 是信息传达 = 内容决定。
    MW21_FLOW_SUFFIX = ("-request", "-offerbook", "-offerbooked")
    MW21_FLOW_EXACT = {"general-reqmore", "general-bye", "general-welcome",
                       "general-greet", "general-unknown"}
    mw21_flow = 0
    for cid, conv in mw21["_raw"].items():
        for i, t in enumerate(conv.get("log") or []):
            if i % 2 != 1:
                continue
            for k in (t.get("dialog_act") or {}):
                klow = k.lower()
                if klow in MW21_FLOW_EXACT or any(klow.endswith(s) for s in MW21_FLOW_SUFFIX):
                    mw21_flow += 1
                    break
    t21 = common.template_stats(mw21["sys_texts"])
    rate21 = 1.0 if inject_all_flow else common.occupancy(t21["n_turns"], mw21_flow)
    report["corpora"]["budzianowski/multiwoz 2.1（SYSTEM 侧，含 SYSTEM-* 对话行为）"] = {
        "side": "system",
        "method": "标注驱动（2.1 系统侧 dialog_act：general-reqmore/bye/welcome + *-request + *-offerbook → 流程）",
        "method_strength": "中（act 名是系统行为标签，但语义粒度粗于 CrossWOZ 的 intent）",
        "n_turns": t21["n_turns"],
        "n_dialogues": mw21["n_dialogues"],
        "sys_turns_with_state": mw21["sys_turns_with_state"],
        "act_histogram": mw21["act_histogram"],
        "flow_decided_turns": t21["n_turns"] if inject_all_flow else mw21_flow,
        "flow_decided_rate": rate21,
        "reusable_rate": t21["reusable_rate"],
        "compress_ratio": t21["compress_ratio"],
        "n_skeletons": t21["n_skeletons"],
        "top_skeletons": t21["top_skeletons"],
        "kefu_delta_pp": round(rate21 - KEFU_BASELINE["flow_decided_rate"], 4),
    }

    # ---- Taskmaster TM-1（英文六域）----
    tm = load_taskmaster()
    tm_text: List[str] = []
    tm_flow = 0
    tm_n = 0
    for k in ("self-dialogs", "woz-dialogs"):
        d = tm[k]
        tm_text.extend(d["texts"])
        tm_flow += d["flow_turns"]
        tm_n += d["n_dialogues"]
    t = common.template_stats(tm_text)
    rate = 1.0 if inject_all_flow else common.occupancy(t["n_turns"], tm_flow)
    report["corpora"]["google-research-datasets/Taskmaster TM-1（ASSISTANT 侧，英文六域）"] = {
        "side": "system(assistant)",
        "method": "纯文本形态启发式（TM-1 只有实体标注，无对话行为）",
        "method_strength": "弱（无标注，只能下界估计）",
        "n_turns": t["n_turns"],
        "n_dialogues": tm_n,
        "flow_decided_turns": t["n_turns"] if inject_all_flow else tm_flow,
        "flow_decided_rate": rate,
        "reusable_rate": t["reusable_rate"],
        "compress_ratio": t["compress_ratio"],
        "n_skeletons": t["n_skeletons"],
        "instruction_histogram": tm["woz-dialogs"]["instruction_histogram"],
        "top_skeletons": t["top_skeletons"],
        "kefu_delta_pp": round(rate - KEFU_BASELINE["flow_decided_rate"], 4),
    }

    # ---- 辅指标：用户侧指令模板化率 ----
    hw = load_hwu64()
    th = common.template_stats(hw["answers"])
    report["user_side_instruction_sets"] = {
        "note": "**这些不是主指标**——它们测的是「用户怎么说话」（指令模板化率），"
                "不能用来估「系统说什么由流程决定」。混用会让整卡退回。",
        "xliuhw/NLU-Evaluation-Data (HWU64)": {
            "side": "user",
            "n_turns": th["n_turns"],
            "n_rows": hw["n_rows"],
            "reusable_rate": th["reusable_rate"],
            "compress_ratio": th["compress_ratio"],
            "n_skeletons": th["n_skeletons"],
            "intent_histogram": hw["intent_histogram"],
            "top_skeletons": th["top_skeletons"],
        },
    }
    return report


def comparison_table(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """与客服 23.0% 的对照表。"""
    rows = []
    for cid, b in report["corpora"].items():
        rows.append({
            "corpus": cid,
            "side": b["side"],
            "method_strength": b["method_strength"],
            "n_turns": b["n_turns"],
            "flow_decided_rate": b["flow_decided_rate"],
            "kefu_delta_pp": b["kefu_delta_pp"],
            "reusable_rate": b["reusable_rate"],
        })
    return rows


def verification_gate(report: Dict[str, Any]) -> List[str]:
    """跑完就把反空转校验也写进报告，失败时非零退出。

    两个方向都要判（缺一不可，否则注入测试是空转）：
      · 未注入：任何一份集占比 ≈100% → 分类判据退化成「全判流程决定」→ 失败；
      · 已注入：任何一份集占比 **没**到 1.0 → 注入没生效（判据层没被真跑）→ 同样失败。
    """
    problems = common.verify_occupancy(
        report, upper=1.0,
        expect_not_all=not report.get("inject_all_flow"),
        skip=tuple(c for c in report["corpora"] if report["corpora"][c].get("flow_decided_rate") is None),
    )
    if report.get("inject_all_flow"):
        for cid, block in report["corpora"].items():
            v = block.get("flow_decided_rate")
            if v is None:
                continue
            if v != 1.0:
                problems.append(
                    f"{cid}: 已注入「全判为流程决定」但占比只有 {v} —— 注入未生效，"
                    f"说明这一条判据路径根本没被跑到（这是比判据失效更严重的空转）")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="跨行业系统侧「流程决定话轮占比」")
    ap.add_argument("--out", required=True)
    ap.add_argument("--inject-all-flow", action="store_true",
                    help="反空转：把分类判据改成「全判为流程决定」，占比应约等于 1.0")
    args = ap.parse_args()

    report = build(inject_all_flow=args.inject_all_flow)
    report["comparison_table"] = comparison_table(report)
    problems = verification_gate(report)
    report["verification_gate"] = {
        "expect_not_all": not args.inject_all_flow,
        "problems": problems,
        "status": "fail" if problems else "pass",
    }

    print(f"{'语料':52s} {'话轮':>8s} {'流程占比':>9s} {'vs客服23.0%':>12s} {'可复用率':>9s}")
    for r in report["comparison_table"]:
        rate = r["flow_decided_rate"]
        delta = r["kefu_delta_pp"]
        rate_s = f"{rate*100:>8.1f}%" if rate is not None else f"{'—':>8s}"
        delta_s = f"{delta*100:>+11.1f}pp" if delta is not None else f"{'—':>12s}"
        print(
            f"{r['corpus'][:52]:52s} {r['n_turns']:>8d} "
            f"{rate_s:>9s} {delta_s:>12s} "
            f"{r['reusable_rate']*100:>8.1f}%"
        )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n原始数据 → {args.out}")

    if problems:
        print(f"\n[反空转校验] {len(problems)} 个问题：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
