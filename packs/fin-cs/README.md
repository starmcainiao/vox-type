# `packs/fin-cs/` —— 金融账户服务包（第二个业务包）

## 这是什么

一个**流程驱动**的金融客服话术包：17 key / 102 条预铸资产（17 key × 2 变体 × 3 语速档）。
用途：验证「**新增业务 = 新增目录，零内核改动**」（`packs/AGENTS.md:47`），并作为第八批语料实测的载体。

## 来源与许可（派生自第三方语料，**不是虚构的**）

- 话术**改写**自 `tongyi_dianjin/DianJin-CSC-Data`（ModelScope，**MIT**）——1855 通真实客户-坐席对话；
- key 体系映射该语料的 **COPC 十二策略**标注（问候/确认身份/重述转述/细化问题/情感管理/提供建议/
  信息传达/解决实施/反馈请求/关系延续/感谢告别/其它）+ 5 个保障类 key；
- **话术是改写版，不逐字复制语料原文**（`docs/11 §11.7`）；改写依据与全部实测数字见
  `labs/corpus-harvest/README.md`；
- `golden/` 里是逐字原文，**不进公开分发**，见 `golden/README.md`。

## 这个包**不代表**什么（读数字前必看）

`labs/corpus-harvest/` 的实测表明（详见该目录 `README.md`）：

- **它旁边的真实业务里，只有 1.8% 的客服话轮句式可复用**，23.0% 是流程决定性话轮；
- 所以本包的 17 个 key 覆盖的是**流程决定的那部分**（问候/确认身份/告别/合规提示/请稍等/未听清重试/转人工），
  **不是**"整通电话都能预铸"；
- `vox bench` 在本包上跑出的 `hit_rate = 1.0` 是**脚本驱动档的回归闸门**（key 由流程给定、不经检索），
  **不是真实覆盖率**。真实覆盖率见上条。

## 自检

```bash
vox pack check packs/fin-cs                                   # 五类规则 + 四属性
vox pack build packs/fin-cs --out ~/vox-build/fin-cs   # 102 条资产（产物落仓外）
vox bench ~/vox-build/fin-cs \
          --corpus eval/corpus/fin_cs_keydrive.json --out /tmp/fin-cs-bench --repeats 20
```

`eval/corpus/fin_cs_keydrive.json` 的 `notes` 里已写明「仅作回归闸门，不代表真实覆盖率」。
