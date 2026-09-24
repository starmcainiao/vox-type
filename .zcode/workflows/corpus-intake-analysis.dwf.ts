/* zcode-workflow
description: 多源公开语料收集 + 跨行业适用性分析：定源（许可证据位置）→ 收集（语料落仓外、只入台账）→ 分析（复用既有判据）→
  独立验证（只读复算）→ 补充复验；4 道代码门禁卡死产物质量，任一不达标当场停下并返回失败阶段。
whenToUse: 需要从公开渠道收集多个数据集并做可复现分析时用（换行业/换数据集复跑：改脚本顶部 CARD / OUT
  两个常量，换一张任务卡与选源清单即可，门禁与验证阶段不动）。首轮实测抓出 3 处判据缺陷、次轮抓出验证员自己的算术错误——门禁与独立验证员的组合值得保留。
*/
interface SourceRow { name: string; url: string; license: string; evidence: string; kind: string }
interface SourcesOut { sources: SourceRow[]; rejected: { name: string; reason: string; evidence: string }[] }
interface CollectOut { collected: number; ledger_path: string; failures: string[] }
interface AnalysisRow { dataset: string; metric: string; rate: number; n: number }
interface AnalysisOut { rows: AnalysisRow[]; comparison_note: string; replay_candidates: number; boundaries: string[] }
interface VerifyOut { passed: boolean; checked: string[]; discrepancies: string[] }

const CARD = "docs/tasks/T22-多行业语料收集与适用性验证.md"
const OUT = "labs/multi-industry-corpus"

// 门禁 1：选源清单必须齐（≥7 入选 / ≥8 拒收 / 每条含 url+license+evidence）
const GATE1 = `import json
d = json.load(open("${OUT}/sources.json", encoding="utf-8"))
assert len(d["sources"]) >= 7, "入选源不足 7"
assert len(d["rejected"]) >= 8, "拒收集不足 8"
for s in d["sources"]:
    assert s.get("url") and s.get("license") and s.get("evidence"), s
for r in d["rejected"]:
    assert r.get("reason") and r.get("evidence"), r
print("gate1-ok", len(d["sources"]), len(d["rejected"]))`

// 门禁 2：台账必须可解析且条数与选源一致；语料落仓外（仓内不得出现语料本体）
const GATE2 = `import json, os
d = json.load(open("${OUT}/corpus_ledger.json", encoding="utf-8"))
entries = d.get("entries", [])
assert len(entries) >= 7, "台账条目不足 7"
for e in entries:
    assert e.get("name") and e.get("license") and e.get("evidence"), e
    assert e.get("path_out_of_repo") and not e["path_out_of_repo"].startswith("."), e
    assert e.get("sha256") or e.get("status") == "failed", e
bad = [p for p in os.listdir("${OUT}") if p.endswith((".wav", ".zip", ".csv", ".jsonl")) and p != "report.json"]
assert not bad, "仓内出现语料本体: %s" % bad
root_stray = [q for q in os.listdir(".") if q.endswith((".json", ".jsonl", ".zip", ".csv")) and os.path.isfile(q)]
assert not root_stray, "仓库根出现下载残留: %s" % root_stray
print("gate2-ok", len(entries))`

// 门禁 3：分析报告字段与值域
const GATE3 = `import json
d = json.load(open("${OUT}/report.json", encoding="utf-8"))
rows = d.get("rows", [])
assert len(rows) >= 5, "分析行不足 5（四份系统侧对话集 + 用户侧指令集）"
metrics = {r.get("metric") for r in rows}
assert "flow_decided_rate" in metrics, "缺系统侧占比行（flow_decided_rate）"
assert "instruction_template_rate" in metrics, "缺用户侧模板化率行（instruction_template_rate）"
for r in rows:
    assert 0.0 <= float(r["rate"]) <= 1.0, r
    assert int(r["n"]) > 0, r
assert isinstance(d.get("replay_candidates"), int) and d["replay_candidates"] >= 0, "缺回流实验数字"
assert len(d.get("boundaries", [])) >= 3, "缺诚实边界清单"
print("gate3-ok", len(rows), d["replay_candidates"])`

phase("定源与许可核实")
const sources = await agent("选源员", "你是语料选源员：只做选源与许可核实，不下载。许可判定以正文/LICENSE 文件为准，证据必须给到文件与行号。").ask<SourcesOut>(
  `仓库根（当前工作目录）。先完整读任务卡 ${CARD}——它含必收清单（7 份，许可证据位置已列）与拒收集清单（≥8 条）。
产出 ${OUT}/sources.json，形状：{"sources":[{"name","url","license","evidence","kind"}...],"rejected":[{"name","reason","evidence"}...]}。
kind 取 "dialogue"（带系统侧话轮，可测"流程决定话术占比"）或 "instruction"（仅用户侧单句）或 "audio"。
纪律：只写这一个文件；不下载任何语料；许可不明的源放 rejected 并写理由；不要自造 URL。`)
const g1 = await world.run("python3", ["-c", GATE1])
if (g1.exitCode !== 0) { log("门禁1失败：" + g1.stderr.trim()); return { stage: "gate1", failed: true, stderr: g1.stderr.trim() } }
log("门禁1通过：" + g1.stdout.trim())

phase("收集与台账")
const collect = await agent("收集员", "你是语料收集员：按选源清单下载公开数据集，落仓外，写台账；许可与 sha256 必须交叉核对。").ask<CollectOut>(
  `仓库根（当前工作目录）。读 ${OUT}/sources.json 与任务卡 ${CARD}。
用既有管道 tools/corpus_fetch/ 的机制（先读它的 README/AGENTS 与 sources 配置格式，**不得另起一套**）：
1) 把 sources 写进管道配置并执行收集；语料本体落仓外（沿用既有 ~/corpus/ 约定），**仓内只入台账**；
2) 产出 ${OUT}/corpus_ledger.json：{"entries":[{"name","license","evidence","path_out_of_repo","sha256","status","n_rows_or_size"}...]}；
3) 拒收集写进管道台账的 rejected_sources（含理由+证据位置）。
纪律：失败源要如实记 status="failed" 并写原因，不得静默跳过；不新增第三方依赖（需要 pandas/pyarrow 时用 ~/miniforge3/bin/python3 并在报告说明）；不得把语料本体或绝对路径写进仓内产物。`)
const g2 = await world.run("python3", ["-c", GATE2])
if (g2.exitCode !== 0) { log("门禁2失败：" + g2.stderr.trim()); return { stage: "gate2", failed: true, stderr: g2.stderr.trim(), collect } }
log("门禁2通过：" + g2.stdout.trim())

phase("分析与对照")
const analysis = await agent("分析员", "你是适用性分析员：算占比必须复用既有判据，不得自造分类规则；数字要有样本量与口径。").ask<AnalysisOut>(
  `仓库根（当前工作目录）。读任务卡 ${CARD} 与 ${OUT}/corpus_ledger.json。
1) 对带系统侧话轮的对话集（CrossWOZ / ABCD / MultiWOZ / Taskmaster）各算一份「流程决定话轮占比」——
   **分类判据必须与 labs/corpus-harvest/ 的既有脚本同源**（先读它，贴出处），不得自造；
2) 对用户侧指令集（MASSIVE zh-CN / HWU64）算「指令模板化率」，并说明它与主指标的区别；
3) 用 CrossWOZ 的系统侧对话行为标注做一次「从会话记录挖候选话术」小实验（固定种子，给出挖出条数）；
4) 诚实边界：导航播报/机场广播/工业/政务/快递 = 公开数据真空 → 写进 boundaries。
产出 ${OUT}/report.json：{"rows":[{"dataset","metric","rate","n"}...],"comparison_note","replay_candidates","boundaries":[...],"provenance":{...}}。
纪律：固定种子可复现；不得把用户侧指令集当系统侧占比（混用=整卡退回）。`)
const g3 = await world.run("python3", ["-c", GATE3])
if (g3.exitCode !== 0) { log("门禁3失败：" + g3.stderr.trim()); return { stage: "gate3", failed: true, stderr: g3.stderr.trim(), analysis } }
log("门禁3通过：" + g3.stdout.trim())

phase("独立验证与交付")
const verify = await agent("独立验证员", "你是只读验证员：不修改任何产物，只抽验与复算，如实报差异。").ask<VerifyOut>(
  `仓库根（当前工作目录）。**只读**。读 ${OUT}/corpus_ledger.json 与 ${OUT}/report.json，做三件事：
1) 抽 2 份语料：核对 sha256 与文件实际一致（用 shasum -a 256）；
2) 抽 1 个占比：用 report.json 里同一数据集**独立复算**（可自写临时脚本到 /tmp），与报告值比对；
3) 检查"用户侧指令集是否被误当系统侧占比"（若有 → discrepancies 记下）。
产出 ${OUT}/verify.json：{"passed":bool,"checked":[...],"discrepancies":[...]}。不得修改任何既有产物。`)
const readme = await agent("报告撰写员", "你是技术报告撰写员：口径、复现命令、边界齐全，不复述具体数值（活引用 report.json）。").ask(
  `仓库根（当前工作目录）。读 ${OUT}/ 下 sources.json / corpus_ledger.json / report.json / verify.json 与任务卡 ${CARD}，
写 ${OUT}/README.md：口径（含"用户侧 vs 系统侧"的区别）、复现命令、跨行业对照结论、**诚实边界**（公开数据真空的场景）、已知局限。
数值一律写"以落盘 report.json 为准"的活引用，不复述具体数字。`)

// 门禁 4（v2）：补充项必须收口
const GATE4 = `import json, os
led = json.load(open("labs/multi-industry-corpus/corpus_ledger.json", encoding="utf-8"))
blob = json.dumps(led, ensure_ascii=False)
assert "manifest_digest" in blob, "台账未标注 sha256 口径（manifest_digest）"
assert "16521" in blob, "MASSIVE zh-CN 条数未同步为 16521"
rep = json.load(open("labs/multi-industry-corpus/report.json", encoding="utf-8"))
rblob = json.dumps(rep, ensure_ascii=False)
assert "action" in rblob, "report 未细化到 action 埋点口径"
rev = json.load(open("labs/multi-industry-corpus/revision.json", encoding="utf-8"))
items = rev.get("items", [])
assert len(items) >= 6, "修订项不足 6"
assert all(i.get("status") for i in items), "有修订项未写处置"
mc = "labs/multi-industry-corpus/out/mine_candidates.json"
if os.path.exists(mc):
    d = json.load(open(mc, encoding="utf-8"))
    assert d.get("n_candidates") or d.get("_status") == "superseded", "误导产物未处理"
print("gate4-ok", len(items))`

phase("补充与复验（v2）")
const revision = await agent("修订员", "你是修订员：按清单逐项收口，改完自跑校验，如实写每项处置。").ask(
  `仓库根（当前工作目录）。读 labs/multi-industry-corpus/verify.json 的 discrepancies 与任务卡 docs/tasks/T22-多行业语料收集与适用性验证.md，**逐项收口 6 项**，处置写进 labs/multi-industry-corpus/revision.json（{"items":[{"id","status","evidence"}...]}，≥6 条）：
1) corpus_ledger.json：MASSIVE 条目把"取不到"改为"实测可下载（HTTP 200 / 40251390 bytes），但管道三适配器不覆盖 S3 直链 → 未取本体"；
2) corpus_ledger.json：MASSIVE zh-CN 条数 11514 → 16521，注明 train=11514 / dev=2033 / test=2974；
3) corpus_ledger.json：给 sha256 加口径标注（kind="manifest_digest"）+ **可照抄的复算命令**（说明它是对 (path,size,sha256) 再哈希、非文件本体哈希）；
4) report.json：rows 的 side 字段细化（ABCD 注明分母含 36482 个 action 埋点轮、占 27.7%；Taskmaster 记 system(assistant)）——**rate/n 数值一个字不得动**；
5) 修 mine_candidates.py 的静默零结果失败（按实测的 4 元组 dialog_act 结构解析）并重跑，产物与 mine_recompute.py 的 737 对照（一致或说明差异）；
6) 亲跑两项并记录：python3 -m unittest discover -s tools 与 python3 -m tools.structure_budget.check。
另：同步刷新 labs/multi-industry-corpus/README.md 的"已知局限"节（已收口项移除或标注已修，不得留下过期说法）。
纪律：只改 labs/multi-industry-corpus/ 内文件；不得把语料本体或绝对路径写进仓内产物。`)
const g4 = await world.run("python3", ["-c", GATE4])
if (g4.exitCode !== 0) { log("门禁4失败：" + g4.stderr.trim()); return { stage: "gate4", failed: true, stderr: g4.stderr.trim(), revision } }
log("门禁4通过：" + g4.stdout.trim())

const recheck = await agent("复验员", "你是只读复验员：只抽验修订项，不改任何文件，如实报差异。").ask(
  `仓库根（当前工作目录）。**只读**。读 labs/multi-industry-corpus/revision.json，抽验 3 项：
1) 按台账给的复算命令实算 1 份语料的 manifest_digest，与台账逐字比对；
2) mine_candidates.py 修好后与 mine_recompute.py 的 737 是否一致（或差异可解释）；
3) 复核 report.json 的 rate 数值与修订前一致（用 git show HEAD:labs/multi-industry-corpus/report.json 取旧值比对——修订不得动数值）。
产出 labs/multi-industry-corpus/recheck.json：{"passed":bool,"checked":[...],"discrepancies":[...]}。`)

await artifact.file("report", `${OUT}/README.md`, { title: "多行业语料与适用性验证报告", primary: true })
await artifact.file("ledger", `${OUT}/corpus_ledger.json`, { title: "语料台账（含拒收集）" })

report({ stage: "verify", passed: verify.passed, discrepancies: verify.discrepancies })
return {
  sources: sources.sources.length,
  rejected: sources.rejected.length,
  collected: collect.collected,
  failures: collect.failures,
  rows: analysis.rows.length,
  replay_candidates: analysis.replay_candidates,
  verify_passed: verify.passed,
  discrepancies: verify.discrepancies,
  readme: readme,
}