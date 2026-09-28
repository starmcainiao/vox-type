#!/usr/bin/env python3
"""
tools.feedback_mining.make_demo_inputs — 造回流挖掘的演示输入（全部自造话术）

存在理由：T23 的验收 5 要求「用本仓可造的场景跑通一次」。本脚本造两份输入——
  ① 轮级留痕 JSONL   T17 格式（字段与 trigger/ledger.py 的产物一致）
  ② 事件流 JSONL     T07 格式（字段名一律取 core.metrics_spec 的常量）+ `live_text`

**数据分级 = 公开**：所有话术、状态值、用户句都是本脚本自造的占位内容，
不含任何真实会话、用户录音、个人信息或内网地址。留痕的 state 只放结构化层
（与 trigger/ledger.py 的分层红线一致），敏感字段一律不写。

用法：
    python3 tools/feedback_mining/make_demo_inputs.py --out-dir /tmp/ds

确定性：不读时钟、不随机；同一 --out-dir 跑两次产物逐字节一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.metrics_spec import FALLBACK, HIT, MISS, PART, PLAN_ID, REASON, TS, TURN_ID  # noqa: E402

LIVE_TEXT_FIELD = "live_text"
LEDGER_VERSION = 1

# 一份自造的结构化层 state 快照（与 heat 业务的登记/排队流程同形，但值全是占位）
BASE_STATE: Dict[str, Any] = {
    "ticket_status": "待受理",
    "queue_position": 0,
    "is_overdue": False,
    "region": "示例片区",
}

# 场景：一轮会话里 1 个命中 + 3 个未命中（含重复话术、占位符话术、超长话术、跨句话术）
# 全部为自造文本，不来自任何真实会话。
SCENARIOS: List[Dict[str, Any]] = [
    # (state 覆盖, 单元列表: (kind, key, live_text, reason, state))
    {
        "state": {"queue_position": 3, "ticket_status": "排队中"},
        "turn": "t-001",
        "units": [
            {"kind": "hit", "key": "transfer_queued"},
            {"kind": "miss", "key": None, "text": "您好，为您查询到当前排队位置是第 3 位。"},
            {"kind": "miss", "key": None, "text": "请保持通话，坐席接通后我会为您转接。"},
        ],
    },
    {
        "state": {"queue_position": 7, "ticket_status": "排队中"},
        "turn": "t-002",
        "units": [
            # 同一条话术重复出现（聚合的判据：count > 1）
            {"kind": "miss", "key": None, "text": "您好，为您查询到当前排队位置是第 3 位。"},
            # 归一化后应与上一条等价（全角标点 / 空白 / 大小写不同）→ 同键聚合
            {"kind": "miss", "key": None, "text": "您好，为您查询到当前排队位置是第 3 位。 "},
            {"kind": "fallback", "key": "transfer_queued", "reason": "engine_mismatch"},
        ],
    },
    {
        "state": {"queue_position": 1, "ticket_status": "处理中"},
        "turn": "t-003",
        "units": [
            {"kind": "miss", "key": None, "text": "请保持通话，坐席接通后我会为您转接。"},
            # 含槽位占位符 → 必须被拒收（B2 负例）
            {"kind": "miss", "key": None, "text": "已为您预约 {date} 下午上门服务，请保持手机畅通。"},
            # 含方括号占位符 → 同样被拒收（B2 负例，docs/14 的原文写法）
            {"kind": "miss", "key": None, "text": "您的地址在 [小区名] 附近，维修师傅 30 分钟内到达。"},
        ],
    },
    {
        "state": {"queue_position": 1, "ticket_status": "已受理"},
        "turn": "t-004",
        "units": [
            # 超长（> 60 字）→ 被拒收（B4 负例）
            {
                "kind": "miss",
                "key": None,
                "text": (
                    "您好，已为您登记供暖报修工单，当前状态为待受理，"
                    "我们会在工作日内派单，派单后维修师傅会在约定时间上门，"
                    "如需变更上门时间请回复修改时间，如需人工服务请回复转人工。"
                ),
            },
            # 跨句（2 个句末标点）→ 被拒收（B1 负例）
            {
                "kind": "miss",
                "key": None,
                "text": "工单已受理！请保持电话畅通！师傅会尽快联系您。",
            },
            {"kind": "miss", "key": None, "text": "感谢您的来电，祝您生活愉快，再见。"},
        ],
    },
    {
        "state": {"queue_position": 2, "ticket_status": "排队中"},
        "turn": "t-005",
        "units": [
            # 全角标点 → 归一化后与前面的「转接」话术同键
            {"kind": "fallback", "key": None, "reason": "fingerprint_mismatch",
             "text": "请保持通话，坐席接通后我会为您转接。"},
            {"kind": "miss", "key": None, "text": "转人工"},
        ],
    },
]

# plan 里预铸单元的话术文本（命中路径，来自本仓 packs/heat_kefu 的同形自造文本）
PREBAKED_TEXT: Dict[str, str] = {
    "transfer_queued": "当前人工坐席繁忙，您已进入排队，稍后可回复「转人工」重试。",
}


def _iso(i: int, minute: int) -> str:
    """确定性时间戳：不读时钟（2026-09-22 的固定演示时刻）。"""
    return f"2026-09-22T09:{minute:02d}:{i:02d}Z"


def build_ledger() -> List[Dict[str, Any]]:
    """造轮级留痕（T17 格式）：每轮一条，state 只放结构化层。"""
    records: List[Dict[str, Any]] = []
    for i, sc in enumerate(SCENARIOS, start=1):
        state = dict(BASE_STATE)
        state.update(sc["state"])
        units = sc["units"]
        keys = [u.get("key") for u in units]
        records.append(
            {
                "ledger_version": LEDGER_VERSION,
                TURN_ID: sc["turn"],
                PLAN_ID: f"plan-{i:03d}",
                TS: _iso(0, 8 * i),
                "trigger_id": "trigger-heat-demo",
                "rule_id": f"R{i}",
                "state": state,
                "plan": [{"key": u.get("key"), "rate": "normal", "variant": 0, "slots": {}} for u in units],
                "keys": keys,
            }
        )
    return records


def build_events() -> List[Dict[str, Any]]:
    """造事件流（T07 格式 + live_text）：每单元一条，三态用 metrics_spec 的常量。"""
    events: List[Dict[str, Any]] = []
    for i, sc in enumerate(SCENARIOS, start=1):
        plan_id = f"plan-{i:03d}"
        for part, u in enumerate(sc["units"], start=1):
            kind = u["kind"]
            text = u.get("text", PREBAKED_TEXT.get(u.get("key") or "", ""))
            if kind == "hit":
                state_key, reason = HIT, ""
            else:
                state_key = MISS if kind == "miss" else FALLBACK
                reason = u.get("reason") or ("say_live_text" if kind == "miss" else "")
            events.append(
                {
                    TS: _iso(part, 8 * i),
                    TURN_ID: sc["turn"],
                    PLAN_ID: plan_id,
                    "key": u.get("key"),
                    PART: part,
                    "rate": "normal",
                    "variant": 0 if u.get("key") else None,
                    REASON: reason,
                    "pack_version": "heat-kefu-1",
                    "first_audio_ms": 0.232 if kind == "hit" else 515.3,
                    state_key: True,
                    LIVE_TEXT_FIELD: text,
                }
            )
    return events


def _dump_jsonl(records: List[Dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)


def write(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "turns.jsonl").write_text(_dump_jsonl(build_ledger()), encoding="utf-8")
    (out_dir / "events.jsonl").write_text(_dump_jsonl(build_events()), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="造回流挖掘的演示输入（自造话术）")
    ap.add_argument("--out-dir", default="/tmp/vox-feedback-demo", help="输出目录（默认 /tmp/vox-feedback-demo）")
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    write(out)
    print(f"演示输入已写出：{out}/turns.jsonl、{out}/events.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
