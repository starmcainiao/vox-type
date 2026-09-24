#!/usr/bin/env python3
"""guided decoding 支持度勘察（`docs/17 §二` 近期项的「T 未来·key 约束解码」前置勘察）

问题：kefu brain 的 LLM 运行时（商汤 OpenAI 兼容端点，`OPENAI_BASE_URL` / `OPENAI_MODEL` 来自
kefu 仓 `.env`）能不能把输出**物理约束**到合法 key 集合？——即 `docs/17 §一 坐标 2` 说的
「Trie/FSM 约束解码」在这条运行时上是否可行。

方法（三变体 + 一条判决测试）：
  ① response_format={"type":"json_object"}   —— 只保证"是 JSON"，不约束 schema/enum
  ② response_format={"type":"json_schema", …} —— 真正的 schema 级约束（strict）
  ③ guided_json={…enum…}                     —— vLLM/Outlines 风格的额外参数（OpenAI 兼容端点上常见"收下但忽略"）
  判决：把 enum 设成一个**模型绝不会自然输出**的值（`zzz_impossible_key_9x7`）——
        输出该值 = 约束生效；输出自然倾向值（opening）= **参数被静默忽略**。

凭据：只从环境变量读（`SENSENOVA_API_KEY`），**不进仓、不进产物**。
退出码：0 全部探测完成（无论结论好坏）/ 2 缺凭据 / 3 运行期失败。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("GUIDED_BASE_URL", "https://token.sensenova.cn/v1").rstrip("/")
MODEL = os.environ.get("GUIDED_MODEL", "sensenova-6.8-flash-lite")
KEY = os.environ.get("SENSENOVA_API_KEY", "")
IMPOSSIBLE = "zzz_impossible_key_9x7"

SYSTEM = ('你只输出 JSON，形如 {"key":"<key>"}，key 从 opening/transfer_ready/farewell 中选一个。')
USER = "用户说：你好。"


def call(label, extra=None, tries=3, max_tokens=400):
    """发一次请求；网络抖动重试。返回 dict（含 raw/err），不抛。"""
    if not KEY:
        return {"label": label, "err": "缺少 SENSENOVA_API_KEY"}
    body = {"model": MODEL, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": USER}]}
    if extra:
        body.update(extra)
    for i in range(tries):
        try:
            req = urllib.request.Request(
                BASE + "/chat/completions", data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=120).read())
            ch = r["choices"][0]
            return {"label": label, "http": 200, "finish": ch.get("finish_reason"),
                    "content": ch["message"].get("content"),
                    "completion_tokens": (r.get("usage") or {}).get("completion_tokens")}
        except urllib.error.HTTPError as e:
            return {"label": label, "http": e.code, "err_body": e.read().decode()[:200]}
        except Exception as e:
            if i == tries - 1:
                return {"label": label, "err": f"{type(e).__name__}: {str(e)[:120]}"}
            time.sleep(2)
    return {"label": label, "err": "unreachable"}


def main() -> int:
    if not KEY:
        print("[guided-probe] 需要 SENSENOVA_API_KEY（kefu 仓 .env 的 OPENAI_API_KEY 同源）", file=sys.stderr)
        return 2

    schema = {"type": "object",
              "properties": {"key": {"type": "string", "enum": ["opening", "transfer_ready", "farewell"]}},
              "required": ["key"]}
    impossible_schema = {"type": "object",
                         "properties": {"key": {"type": "string", "enum": [IMPOSSIBLE]}},
                         "required": ["key"]}

    rows = [
        call("基线（无任何约束）"),
        call("① json_object", {"response_format": {"type": "json_object"}}),
        call("② json_schema（enum=三个合法 key，strict）",
             {"response_format": {"type": "json_schema",
                                  "json_schema": {"name": "key_out", "schema": schema, "strict": True}}}),
        call("③ guided_json（enum=三个合法 key）", {"guided_json": schema}),
        call("★判决：guided_json（enum=不可能值）", {"guided_json": impossible_schema}),
        call("★判决：json_schema（enum=不可能值）",
             {"response_format": {"type": "json_schema",
                                  "json_schema": {"name": "k", "schema": impossible_schema, "strict": True}}}),
    ]

    verdict = {}
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))

    def content_of(label):
        for r in rows:
            if r["label"].startswith(label):
                return r.get("content")
        return None

    # 判决：guided_json 下是否吐出了"不可能值"
    c = content_of("★判决：guided_json")
    verdict["guided_json_constrains"] = bool(c and IMPOSSIBLE in c)
    verdict["json_schema_supported"] = not any(
        r.get("http") == 400 and r["label"].startswith("② json_schema") for r in rows)
    verdict["json_object_available"] = bool(content_of("① json_object"))

    print("\n=== 判定 ===")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    print("\n结论（写进 README / docs/17）：")
    if verdict["guided_json_constrains"]:
        print("  guided_json 生效 → 可做 key 集合的物理约束")
    else:
        print("  guided_json **被静默忽略**（输出自然倾向值）→ 该运行时上无法做物理约束；")
        print("  任何「传了 guided_json 就有约束」的实现都是**静默降级**（本仓红线）——必须显式校验输出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
