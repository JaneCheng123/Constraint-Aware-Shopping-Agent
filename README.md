# Constraint-Aware Shopping Agent

保留共享 ReAct 策略，用两个 Gate 检查搜索和购买。四组使用同一策略、任务内记忆、模型和交互预算，只切换 Gate。

## 核心流程

1. Gate 开启时，Constraint Manager 从用户指令提取并验证约束。
2. ReAct 根据当前页面、记忆和 Gate 反馈提出动作。
3. 搜索经过 Query Gate；导航和选项选择交给 WebShop。
4. Product Gate 检查当前候选的可见证据，并把结果反馈给 ReAct。
5. ReAct 提出购买时，再检查当前商品是否 READY，然后执行购买。重复以上交互直到完成或预算耗尽。

Baseline 不提取结构化约束。Query-only 不检查购买资格；Product-only 不检查搜索。模型只看到用户指令与可见交互内容，不读取评分标签或目标 ASIN。

| 配置 | Query Gate | Product Gate |
|---|---|---|
| baseline | 关 | 关 |
| query | 开 | 关 |
| product | 关 | 开 |
| full | 开 | 开 |

Query Gate 默认 **coverage**：检查查询的约束覆盖，普通遗漏时保留原查询补充；冲突或明确品牌未得到支持时重建，避免把错误品牌与要求品牌拼在一起。Product Gate 默认 **grounded** 审计：复核已有匹配，拒绝须指出具体约束和可见证据，不得新增验收条件。价格与选项的硬检查不能被审计覆盖。

普通属性如果明确对应当前商品的真实选项，也要确认已经选中，不能仅凭页面列出该值就购买。固定属性继续检查页面文本；选择器绑定不改写共享 schema。

Product Gate 的 READY 表示允许购买，由 ReAct 决定是否购买；INSPECT 表示继续查证或选择选项；REJECT 表示候选存在冲突；EXHAUSTED 表示当前候选的可用证据耗尽。约束提取或验证失败会记录错误并停止。

## 正式运行：一个入口

在仓库根目录运行，使用已有的 Windows webshop Conda 环境。API key 从 `DEEPSEEK_API_KEY` 环境变量读取，不会自动读取 `.env`。以下命令会调用付费 API：

```powershell
.\evaluation\run_live.ps1 -MaxSteps 20 -Repeats 1 -OutputDir results/webshop
```

默认运行四组，使用 coverage、grounded、seed=42；可选研究干预均关闭。数据文件含 100 个商品记录，其中 31 个有 instruction，因此默认每组运行 31 个任务。人工标注不是启动条件。

常用调整：`-Configs baseline` 只跑基线；`-Repeats 3` 做三次重复；同一实验续跑用 `-Resume`。输出目录应为新目录，已有实验只有参数和源码一致时才能续跑。需要指定环境用 `-PythonPath`；若系统代理导致 API TLS 连接问题，可加 `-DirectApi`。完整运行条件与输出说明见 [evaluation/README.md](evaluation/README.md)。

自动报告满分成功率、平均 reward、交互步数、模型调用与 token。每组保存轨迹和汇总，根目录生成 `report.html`、`comparison.json`、`manifest.json` 和运行代码快照。满分成功要求任务终止且 reward > 0.999999；部分成功单独统计。

## 读代码：Agent 与 Gates

| 部分 | 文件 | 职责 |
|---|---|---|
| Agent | [agent/react_agent.py](agent/react_agent.py) | 统一策略、动作执行、Gate 反馈与购买检查 |
| Agent | [agent/episode_memory.py](agent/episode_memory.py) | 候选证据、已看页面、当前选项与近期记忆 |
| Agent | [agent/baseline_agent.py](agent/baseline_agent.py)、[agent/gated_agent.py](agent/gated_agent.py) | 四组配置，共用 ReAct 实现 |
| Agent | [agent/llm_client.py](agent/llm_client.py) | 共享模型客户端与调用计数 |
| Gates | [gates/constraint_manager.py](gates/constraint_manager.py) | 约束提取、验证、匹配与缓存 |
| Gates | [gates/query_gate.py](gates/query_gate.py) | 搜索检查和修复 |
| Gates | [gates/product_gate.py](gates/product_gate.py) | 候选检查、审计与购买资格 |
| Gates | [gates/hard_constraints.py](gates/hard_constraints.py)、[gates/audit_prompts.py](gates/audit_prompts.py) | 价格/选项规则与审计标准 |

## 不调用 API 的验证

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
python -m demo.run_demo --output-dir results/demo-simple
```

演示的 `report.html` 展示四组轨迹，使用合成商品和脚本化模型，只验证执行链路。

## 实验记录与可选工具

最新真实实验在每组 31 任务、20 步、单次重复下，满分成功数为 baseline 4、query 6、product 17、full 17。无香选项修复在两个 Product Gate 组均得到实际选择和满分验证，品牌修复另有固定查询付费诊断。条件、成本和局限见 [修复后测试记录](docs/LIVE_GATE_FIX_VALIDATION.md)；上一轮 6、4、15、16 保留在 [历史记录](docs/LIVE_GATE_DIAGNOSTICS.md)。跨轮差值不能直接归因于修复，整体稳定性仍需重复实验。

正式流程只需要四组运行与自动指标。审计重放、历史提示词、其他 Query 模式、共享 schema 和独立人工评估保留在 [可选研究说明](docs/GATE_EXPERIMENTS.md)，使用 `evaluation/run_live_advanced.ps1` 或对应诊断工具。未提供独立标签时，语义覆盖、误接受/误拒绝等指标保持 null；Gate 自己的判断不能充当标准答案。

[Proposal 对应清单](docs/PROPOSAL_ALIGNMENT.md) · [合并记录](docs/MERGE_NOTES.md) · [早期实验记录](docs/LIVE_VALIDATION.md)

项目基于 Princeton WebShop，上游许可保存在 [THIRD_PARTY_LICENSE_WebShop.md](THIRD_PARTY_LICENSE_WebShop.md)。上游依赖与回归目录保留。
