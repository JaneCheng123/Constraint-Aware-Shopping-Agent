# cz-v1 Proposal 对应清单

cz-v1 以 constraint-gates-v1 的约束架构为主体，吸收 main 的 ReAct 推理、动作检查、重试和候选记忆。四组共用同一策略，Gate 仅由两个开关控制。

## Gates 模块列表

1. gates/constraint_manager.py
   - 对应 Phase 2 的 Constraint Extraction 和结构化约束模块。
   - 提取 product_type、required_constraints、post_selection_constraints、price_constraint。
   - source_text 锚定原指令；验证 canonical/aliases 的意义、极性和价格。
   - 共用任务内缓存，规则匹配后按需做语义验证。
2. gates/query_gate.py
   - 对应 Phase 2 的 Query Gate。
   - 检查类别、属性、品牌、颜色/尺寸等选项及价格条件。
   - PASS/REVISE/UNAVAILABLE；修复后的搜索必须再次验证。
3. gates/product_gate.py
   - 对应 Phase 3 的 Product Gate。
   - 汇总可见页面证据，检查类别、属性、硬约束、当前选项和价格。
   - READY/INSPECT/REJECT/EXHAUSTED/UNAVAILABLE；最终语义审计不能覆盖硬约束冲突。
4. gates/hard_constraints.py
   - 对应 rule-based hard constraint checks。
   - 精确 Decimal 金额、包含/严格上下界与区间；只读取商品 Price 字段。
   - 选项按当前候选、选项组和值精确匹配，不做子串匹配。

## Agent 模块列表

1. agent/react_agent.py
   - 对应 Phase 1 的 LLM baseline 和 Phase 3 的完整执行集成。
   - _policy 负责 ReAct 推理、query/action 生成与 Gate 反馈后的重新生成。
   - run 负责环境交互、查询修复、动作重试、购买前最终检查和统一结果。
   - 不读取 instruction_attributes 作为推理输入。
2. agent/episode_memory.py
   - 对应任务内候选证据积累与产品选择。
   - 保存候选摘要、已看页面、当前选项与近期 scratchpad。
   - 搜索/离开当前商品时清理 active candidate；从详情返回商品时保留状态。
3. agent/baseline_agent.py
   - 对应无 constraint gates 的正式 Baseline。
   - 与其他组共享策略和记忆，两个 Gate 都关闭。
4. agent/gated_agent.py
   - 对应 Query-Gated、Product-Gated、Full 三种配置。
   - 使用开关组合，而不是分别维护三套策略。
5. agent/llm_client.py
   - 对应统一模型接口、效率指标和可复现调用记录。
   - Policy/Gates 共用客户端，记录全部调用尝试、错误和 token。

## 其余交付物

| Proposal 交付物 | 新分支对应部分 | 状态 |
|---|---|---|
| WebShop wrapper | webshop_wrapper/env.py | 保留完整评分元数据，暴露可见选项状态 |
| 结构化约束模块 | gates/constraint_manager.py | 已实现 |
| Query Generator + Query Gate | agent/react_agent.py 的 _policy / gates/query_gate.py | 已实现生成、修复与验证 |
| Product Gate | gates/product_gate.py / hard_constraints.py | 已实现 |
| 四种 Agent 配置 | evaluation/run_experiment.py 的 CONFIGURATIONS | 已实现 |
| 统一 reward/success/效率评测 | evaluation/metrics.py / run_experiment.py | 已实现 |
| 独立约束和误接受/误拒绝评测 | evaluation/export_annotations.py / metrics.py / summarize.py | 已实现工具，真实标签需要人工完成 |
| 可复现项目包 | manifest、代码快照、CI、README、requirements | 已实现 |
| End-to-end Demo | demo/run_demo.py / offline.py / render_report.py | 离线四组演示可运行 |
| 真实实验结果及失败分析 | results/<live-run> | 本机缺数据、索引和环境依赖，尚未生成 |

## 实验边界

Baseline 不使用 main 原先基于标注属性的购买资格判断。结构化约束抽取与购买资格只在对应 Gate 开启时生效；所有配置都有相同的通用动作校验和任务内记忆。

价格、选项和品牌等显式值先用规则检查。语义不明确时保留 MISSING；已确认硬冲突必须拒绝。提取/验证错误不会被当成约束全部满足。

真实实验至少使用相同 31 任务做四组比较，建议重复 3 次，再增加任务量和步数预算敏感性实验。不能将历史分支结果、脚本化演示与新真实实验混合汇总。
