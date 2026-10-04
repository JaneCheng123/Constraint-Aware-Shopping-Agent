# 四组 WebShop 评测

四组使用相同任务、模型、预算、ReAct 策略和任务内记忆。用户指令与页面证据用于决策，完整任务评分信息仅交给环境。

## 正式入口

从仓库根目录运行；执行会调用付费 API：

```powershell
.\evaluation\run_live.ps1 -MaxSteps 20 -Repeats 1 -OutputDir results/webshop
```

默认参数：四组 `baseline/query/product/full`、20 步、1 次重复、seed=42、deepseek-chat、coverage Query Gate、grounded Product Gate。共享 schema、变体复核和候选去重等研究干预均关闭。不需要先做人工标注。

| 常用参数 | 用途 |
|---|---|
| `-Configs product,full` | 只跑指定配置 |
| `-MaxSteps 20` | 每个任务的环境动作上限 |
| `-Repeats 3` | 每个任务/配置重复三次 |
| `-OutputDir results/webshop-next` | 为新实验选择独立目录 |
| `-Resume` | 按相同参数及源码续跑；失败任务会重试 |
| `-PythonPath C:\path\to\python.exe` | 指定已有环境 |
| `-Model deepseek-chat`、`-Dataset evaluation/webshop_test_100.json` | 选择模型和任务文件 |
| `-DirectApi` | API 域名直连，用于系统代理的 TLS 问题 |

启动器自动执行预检，优先选择用户的 `miniconda3/envs/webshop`，配置该环境的 Java，并在退出时恢复环境变量。API key 来自 `DEEPSEEK_API_KEY`，不自动读取 `.env`。

当前机器的 WebShop 环境已完成真实测试，见 [修复后最新记录](../docs/LIVE_GATE_FIX_VALIDATION.md)。新环境需要上游依赖、Java/Pyserini/spaCy，以及以下数据：

- `data/items_shuffle_1000.json`、`data/items_ins_v2_1000.json`、`data/items_human_ins.json`。
- `search_engine/indexes_1k`（1000 商品的 Lucene 索引）。
- `requirements.txt` 所需依赖和 API key。

缺少依赖或数据时预检失败。保留上游依赖版本；上游安装可在支持 Bash/Conda 的环境中执行 `pip install -r requirements.txt` 和 `bash setup.sh -d small`。合成演示不能替代真实环境成绩。

## 自动指标与输出

| 指标 | 定义 |
|---|---|
| Task Success | done=true 且 reward > 0.999999；错误任务仍在分母中 |
| Average Reward | 所有任务 reward 的平均值，错误任务按 0 分 |
| Interaction Steps | 实际 env.step 次数；拦截提议和模型重试不计入环境步数 |
| LLM Calls / Tokens | 策略、提取、验证、修复和审计等全部调用尝试及 token |

每组输出 `results.jsonl` 和 `summary.json`；根目录输出 `comparison.json`、`report.html`、`manifest.json` 和 `code_snapshot`。轨迹包含观察、原始提议、执行动作、选项、Gate 事件和停止原因。

manifest 保存模型、任务、seed、重复数、预算、干预设置、代码版本与 hash。商品 JSON 保存内容 hash，Lucene 索引保存文件大小/修改时间指纹。修改参数、代码或数据后应使用新目录，不能续写旧实验。汇总采用每个 task_id/repeat 的最新记录，失败任务不会被静默剔除。

默认任务文件有 31 个带指令的任务。Gate 组抽取失败按失败保留在同一分母中，Baseline 正常运行。固定 seed 不保证外部模型每次相同；正式研究建议至少三次重复，并报告各次结果。

## 可选研究与独立评估

日常运行使用上面的正式入口。高级设置保留在 `run_live_advanced.ps1`；底层 `python -m evaluation.run_experiment` 及旧模块入口继续兼容，但不作为入门步骤。

独立约束覆盖、购买满足度、误接受/误拒绝需要独立标签；无标签时为 null，不影响自动成功率和 reward。标注、审计重放与其他 Gate 模式的命令集中在 [可选研究说明](../docs/GATE_EXPERIMENTS.md)。历史结果与新运行分开保存。
