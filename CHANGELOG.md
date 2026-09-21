# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号语义化（SemVer）。

## [0.1.0] - 2026-09-19 · 首次开源

### 机制（十一个测试根 1,147 条全绿）
- core：plan 协议、封闭原语集、参数三档合并
- rules：五类话术规则（R-1…R-5）与技能指令层
- compiler：预铸流水线、四属性校验器（无死锁/全 key 可达/无未审核文本/有界）、差量重铸
- assets：包格式（key→音频+文本指纹+变体池+语速档+TTL 位）
- runtime：线性执行器（短路判定/槽位现场合成/事件流三态白名单）
- adapters：TTS（macos say）/ ASR（oMLX）/ 宿主桥（kefu 预铸旁路钩子，默认关）
- trigger：前置包 `state → plan` 决策表 + 轮级留痕（JSONL，敏感分层）
- eval：离线对拍（reference gate）、回读 CER harness（不美化三防线）
- cli：`vox pack check|build` / `run` / `bench` / `verify`（退出码冻结 0/2/3/4/5）
- tools：语料自给管道（fetch / derive / baseline，许可 fail-closed）

### 实测
- 真链路集成（kefu）：钩子命中返回音频与包内资产 sha256 字节级一致；未命中走原路留痕
- 离线对拍：预铸命中首响 P50 0.232 ms vs 实时合成 586.7 ms（≈2530×）
- 回读 CER 真机闭环 0.0；方言域 ASR 边界（Wu-Bench 吴语 n=100，CER mean 0.269）
- 准入边界实测：真实客服语料流程决定性话轮 23%；语义匹配路线判否（top-1 11.8% vs 门槛 98.7%）

### 设计裁定
- key 只能由业务流程产出（准入判据 `docs/14`：A1 流程决定性话轮 / A2 业务可枚举播报）
- 逐字相等是唯一命中判定（归一化四步冻结）；未命中 fail-closed 走原路留痕
