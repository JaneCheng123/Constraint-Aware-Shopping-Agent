# cz-v1 Evaluation

所有配置使用相同任务、模型、交互预算、策略和任务内记忆。任务完整交给 WebShopWrapper 构造评分 goal；ReAct 与 Gate 只接收 instruction 和可见交互证据。

当前机器已通过真实环境测试，四组各 31 任务、20 步、1 次重复的结果见 [真实测试记录](../docs/LIVE_VALIDATION.md)。已有 Windows webshop Conda 环境时可用 .\evaluation\run_live.ps1 启动，必要时加 -DirectApi 绕过系统代理的 TLS 问题。

## 运行入口

从仓库根目录使用模块方式运行：

~~~bash
python -m evaluation.preflight --num-products 1000
python -m evaluation.run_experiment --configs baseline query product full --dataset evaluation/webshop_test_100.json --model deepseek-chat --max-steps 20 --num-products 1000 --repeats 3 --seed 42 --output-dir results/cz-v1-live
~~~

仅运行一种配置可用 --configs product；相同实验续跑加 --resume。错误任务会重新尝试，汇总采用每个 task_id/repeat 的最新记录。所有错误仍计入任务总数，不能通过排除失败来提高成功率。

run_gates_31、run_baseline 与 run_no_memory_react 保留模块入口兼容性，但都调用同一 runner。旧 --overwrite 已移除；需要独立实验时使用新的输出目录。历史分支结果不与新结果合并。

真实实验需要：
- data/items_shuffle_1000.json、data/items_ins_v2_1000.json、data/items_human_ins.json。
- search_engine/indexes_1k（1000 商品），以及上游 Java/Pyserini/spaCy 环境。
- requirements.txt 所需依赖及 DEEPSEEK_API_KEY。
- 数据或依赖缺失时预检失败，不能替换成合成数据后标为真实成绩。

## 指标定义

| 指标 | 定义 |
|---|---|
| Task Success | done=true 且 reward > 0.999999；分母包括错误任务 |
| Average Reward | 所有任务 reward 的平均值，错误任务按 0 分 |
| Interaction Steps | 实际执行的 env.step 次数；重试和被拦截提议不计入 |
| LLM Calls | policy、重试、提取、验证、语义回退、修复、最终审计的调用尝试总数 |
| Query Constraint Coverage | 已执行且有独立标注的搜索中，SUPPORTED 条件数 / 全部标注条件数 |
| Product Constraint Satisfaction | 已购买且有独立标注的商品/选项组合中，SUPPORTED 条件数 / 全部标注条件数 |
| Purchase Satisfaction | 独立标注为 acceptable 的购买 / 全部已标注购买 |
| False Acceptance Rate | 无效样本被放行 / 已明确接受或拒绝的无效标注样本 |
| False Rejection Rate | 有效样本被拒绝 / 已明确接受或拒绝的有效标注样本 |

Query Gate：PASS 为接受，REVISE 为拒绝原始搜索词。Product Gate：READY 为接受，REJECT/EXHAUSTED 为拒绝。INSPECT/UNAVAILABLE 属于暂缓，单列 deferred，不充当明确接受或拒绝。相同任务内同一输入、选项、证据与决策重复发生时去重。

没有独立标注的事件列入 unlabelled；指标分母为零返回 null。暂缓、未标注和条件数量都在 summary.json 中公开。必须同时报告 Query/Purchase Label Coverage，不能把局部已标注成绩当成全体任务成绩。

WebShop reward 是原环境的属性、选项、类别与价格评分；Product Constraint Satisfaction 是额外的独立审查，两者不能互相替代。选项不同的同一 ASIN 是不同的标注状态。

## 独立标注工作流

先运行真实四组实验。然后导出模型实际看到的搜索和商品证据：

~~~bash
python -m evaluation.export_annotations results/cz-v1-live/baseline/results.jsonl results/cz-v1-live/query/results.jsonl results/cz-v1-live/product/results.jsonl results/cz-v1-live/full/results.jsonl --output results/cz-v1-live/review-draft.json
~~~

标注者独立阅读原始 instruction、可见商品证据和当前选项，不参考 Gate 的 verdict。工具不会把 Gate verdict 或抽取 schema 写成标签。每条记录：
- task_id：任务标识。
- kind：query 或 product。
- query / product_id：精确输入；产品另带 selected_options。
- acceptable：由审查者填写 true/false，草稿 null 不能用于评测。
- constraint_statuses：独立列出 instruction 中全部明确要求，并标为 SUPPORTED/MISSING/CONTRADICTED。
- reviewer：审查者标识；建议两人审查并记录分歧处理。

示例格式：

~~~json
[
  {
    "task_id": "task_id_from_dataset",
    "kind": "product",
    "product_id": "visible_ASIN",
    "selected_options": {"color": "blue"},
    "acceptable": false,
    "constraint_statuses": {"product_type": "SUPPORTED", "bpa free": "MISSING", "price": "SUPPORTED"},
    "reviewer": "reviewer_name"
  }
]
~~~

完成后，从保存的轨迹重新计算指标，不再调用 LLM：

~~~bash
python -m evaluation.summarize results/cz-v1-live --annotations results/cz-v1-live/reviewed.json
~~~

也可在首次运行时指定 --annotations reviewed.json。评价标签 SHA256 记录在分析结果中，原始运行 manifest 和代码快照保留。真实标签不得替换为 demo/annotations.json；该文件仅覆盖合成演示。

## 输出及复现

manifest.json 保存任务 ID、数据/标签 hash、模型、随机种子、重复数、预算、Git revision、工作区状态、Python/包版本和执行代码 hash。商品 JSON 记录完整 hash，Lucene 文件记录大小和修改时间指纹；后者不是索引内容的密码学 hash，重建索引后应开新实验目录。code_snapshot 保留运行时源码和演示资产。results.jsonl 保存前后观察、原始提议、最终动作、实际执行标识、选项、Gate 事件、停止原因和调用分项。

固定 seed 用于环境随机行为；不承诺外部 LLM 服务逐次一致。建议报告每个 repeat 的结果和分布，而非仅展示最佳一次。

失败分析应分别检查：
1. 搜索词约束丢失或召回不足。
2. 错误类别、硬约束冲突和缺少可见证据。
3. Gate 误接受、误拒绝或过多暂缓。
4. 选项、导航、步骤耗尽、模型输出格式与 API 错误。

当前本地验证仅为离线回归与合成演示。真实 WebShop reward 与人工审查结论必须在数据/索引准备后取得。
