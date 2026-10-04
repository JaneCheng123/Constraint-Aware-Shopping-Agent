# Constraint-Aware Shopping Agent cz-v1

cz-v1 将 main 的 ReAct 执行能力与 constraint-gates-v1 的约束模块整合为一套可消融的购物 Agent。四组实验共享基础策略、任务内记忆、模型、动作校验与交互预算；仅切换 Query Gate 和 Product Gate。

当前已完成核心实现、离线回归测试、独立标注评测工具和轨迹演示。真实 WebShop 成绩需要安装上游环境、下载商品数据、建立 Lucene 索引并运行实验；仓库不把合成演示结果作为真实基准结果。

## 执行流程

用户指令 → 共享约束提取与验证（Gate 开启时）→ ReAct 生成动作 → Query Gate 检查并修复搜索 → WebShop 交互 → 累积当前商品的可见证据 → Product Gate → 最终购买检查。

- Baseline 不提取结构化约束，也不启用约束购买门控。
- Query-Gated 只检查搜索；Product-Gated 只检查商品；Full 同时检查两者。
- 任务标注、目标 ASIN 和评分属性只用于环境与离线评测，不进入模型提示词。
- 每个任务重置记忆与约束缓存，所有配置保留相同的任务内候选比较能力。
- 开启 Product Gate 时，只有当前商品 READY 才能购买。类别、属性、价格和选项都要检查；硬约束冲突不能被最终语义审计覆盖。
- 缺少约束证据继续检查，证据已用尽则离开。提取/验证失败会报告错误并停止，不静默关闭 Gate。
- 统一用终止任务且 reward > 0.999999 表示满分成功；满分 reward 不等于必须购买某个唯一 ASIN。

## Gates 部分

- [gates/constraint_manager.py](gates/constraint_manager.py)：自然语言约束提取、验证、规范化、别名匹配、语义回退、任务内缓存。两个 Gate 共用一个实例。
- [gates/query_gate.py](gates/query_gate.py)：搜索词中的类别、属性、品牌、选项和价格条件检查；返回 PASS、REVISE 或 UNAVAILABLE。
- [gates/product_gate.py](gates/product_gate.py)：当前候选的完整证据检查、规则与语义判断、最终审计；返回 READY、INSPECT、REJECT、EXHAUSTED 或 UNAVAILABLE。
- [gates/hard_constraints.py](gates/hard_constraints.py)：Decimal 价格边界（严格/包含上限、下限、区间）及选项值精确匹配；规则结果不由 LLM 改写。

## Agent 部分

- [agent/react_agent.py](agent/react_agent.py)：统一 ReAct 动作/搜索生成、Gate 反馈、搜索修复、动作重试、购买前检查及轨迹记录。
- [agent/episode_memory.py](agent/episode_memory.py)：候选、页面证据、已检查页面、当前选项及近期推理；处理搜索、返回和候选切换。
- [agent/baseline_agent.py](agent/baseline_agent.py)：两个 Gate 都关闭的正式 baseline。
- [agent/gated_agent.py](agent/gated_agent.py)：用两个开关组合 Query、Product、Full，继承同一 ReAct 实现。
- [agent/llm_client.py](agent/llm_client.py)：共享可注入客户端、统一模型、超时、所有调用/错误与 token 计数；SDK 隐式重试关闭。
- agent/No-memory ReAct.py：旧文件名的兼容入口，现指向统一 baseline；历史 main 成绩必须单独处理。

## 离线验证与演示

核心回归与合成演示仅依赖 Python 标准库，从仓库根目录运行：

~~~bash
python -m unittest discover -s tests -p test_cz_v1.py -v
python -m demo.run_demo --output-dir results/demo-cz-v1
~~~

打开生成的 results/demo-cz-v1/report.html，可筛选四组配置并展开步骤查看提议、执行、拦截与 Gate 证据。演示使用固定合成商品、脚本化模型和独立编写的预期标签，页面明确标记为合成演示。它验证执行链路，不能证明真实模型性能。

## 真实 WebShop 实验

保留上游 WebShop 依赖版本。建议在独立的、可安装这些旧版本的 Python/Java 环境中准备依赖与数据。setup.sh 使用 Bash、conda、OpenJDK 11 和 gdown；Windows 可在 WSL 中运行上游安装流程。

~~~bash
pip install -r requirements.txt
bash setup.sh -d small
~~~

设置环境变量（不会自动读取 .env）：

~~~powershell
$env:DEEPSEEK_API_KEY = 'your_key'
$env:SHOPPING_MODEL = 'deepseek-chat'
~~~

预检及统一实验：

~~~bash
python -m evaluation.preflight
python -m evaluation.run_experiment --configs baseline query product full --dataset evaluation/webshop_test_100.json --model deepseek-chat --max-steps 20 --num-products 1000 --repeats 3 --seed 42 --output-dir results/cz-v1-live
~~~

evaluation/webshop_test_100.json 有 100 个商品记录，其中 31 个有 instruction。evaluation/tasks.json 是其中 5 个真实任务的样例，不再使用与 WebShop ASIN 不匹配的占位任务。完整参数、独立标注与指标定义见 [evaluation/README.md](evaluation/README.md)。

每组生成 results.jsonl 和 summary.json；根目录生成 comparison.json、report.html、manifest.json 与代码快照。相同参数可加 --resume；任务、模型、预算、代码或标注变化时拒绝混用已有结果。LLM 即使 temperature=0 仍可能产生变化，应报告重复实验结果。

## Proposal 对应关系

详细的需求与实现清单见 [docs/PROPOSAL_ALIGNMENT.md](docs/PROPOSAL_ALIGNMENT.md)。分支来源、修复与验证记录见 [docs/MERGE_NOTES.md](docs/MERGE_NOTES.md)。

Query Coverage、Product Satisfaction 与误接受/误拒绝使用独立标注，不使用 Gate 自己的判断作为答案。无标注或相应分母为零时返回 null，并同时报告标注覆盖率。真实任务的人工标注与实验分析属于仍需执行的研究步骤。

## 上游许可

项目基于 Princeton WebShop。上游许可保存在 THIRD_PARTY_LICENSE_WebShop.md。保留 tests/web-agent-site、tests/transfer 及相应 transfer 辅助模块用于上游回归；这些需要完整环境依赖，与当前离线核心测试分开运行。
