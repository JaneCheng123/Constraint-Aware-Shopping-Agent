# 可选研究实验与独立评估

正式流程使用 `evaluation/run_live.ps1`，只运行四组 ReAct + Gate 配置并自动统计结果。本文件的高级设置和人工评估用于深入研究，不是运行项目的前置步骤。高级完整任务入口为 `evaluation/run_live_advanced.ps1`；保留这些工具以便复查历史实验。

本轮保留 cz-v1 的共享 ReAct、价格/选项硬检查、购买阻断和满分评分。默认最终审计改用 grounded：复核已有匹配、禁止新增验收条件、区分宽泛类别与显式组合购买。拒绝必须给出已存在的约束名及可见原文引用；不确定使用 INSPECT。格式不合格仍阻断购买，临时 API/格式错误不缓存。

这些是实现后的行为，机制效果需要独立验证。原 31 任务主实验仍属于 b8d48c5，不能把历史成绩标成新版本成绩。用户随后授权的付费诊断和新一轮 20 步实验已完成，见 [最新真实实验记录](LIVE_GATE_DIAGNOSTICS.md)；旧结果保持原样。

## 默认行为与可选干预

| 设置 | 默认 | 含义 |
|---|---|---|
| audit-version | grounded | 新职责与结构化错配；legacy/benchmark/variant 为历史提示词臂 |
| query-mode | coverage | 继续检查全部约束；普通遗漏追加，明确矛盾或明确品牌未支持时重建 |
| query-mode=layered | 可选 | 拦截冲突；遗漏保留诊断，对类别/品牌/规格/价格等发一次非阻断提醒，策略可保留原查询 |
| query-mode=direction | 可选 | 拦截检测到的冲突，遗漏仅记录；空查询始终阻断。属于缩小覆盖目标的新实验臂 |
| variant-review | 关闭 | H1：仅对已选中选项的审计冲突进行一次来源复核，允许 UNKNOWN；明确冲突不降级 |
| deduplicate-candidates | 关闭 | 仅过滤无可变选项、已看完页面且证据状态不变的 EXHAUSTED 候选；不永久过滤语义 REJECT |
| frozen-schemas | 不使用 | 给 Gate 组注入共享抽取结果；Baseline 正常运行，抽取失败仍计入同分母 |

H1 仅适用于 grounded 的结构化问题输出；不影响类别、价格或其他属性冲突。确认 GENERIC_DEFAULT 后再做一次完整审计，不直接把 REJECT 改成 READY。UNKNOWN 保持 INSPECT；没有剩余页面时仍可 EXHAUSTED。候选去重只是保守的状态过滤，没有恢复跨变体永久封禁。

每个结果的 interventions 和 manifest identity 保存这些设置及 frozen 文件 hash；改变任一设置不能续写旧实验。共享抽取准备的成本与冻结实验的回合成本分开报告。

## 先做诊断，再做完整任务

1. 审计重放比较同一证据状态的判定分布；结合独立人工标签区分不稳定与稳定误判。
2. matcher 合成诊断检查品牌替换、选项替换、价格边界改写、极性反向及遗漏。仅是已知例子的诊断，不能外推总体准确率。
3. H1 在其他设置固定时开/关比较；H2 固定 Product Gate 和 schema，分别比较 coverage/layered/direction。候选去重另开一组，不能混入 H1/H2。
4. 完整系统实验正常抽取，多次重复并比较 20/50 动作预算。受控 schema 实验不能代替端到端结果。

H1 同时报告满分成功、明确冲突误放行、拒绝/延期、额外动作/API 调用、是否获得新证据。不能仅看 REJECT 减少。H2 的提醒也使用 API，必须计成本。

## 审计提示词与证据状态

历史提示词保存为提取后的文本模板，附源提交与源码 hash，不执行旧源码：

- legacy：762944e 的聚焦职责提示词。
- benchmark：b8d48c5 主实验提示词，无选项状态输入。
- variant：464cfe5 的变体上下文提示词。
- grounded：本次改进。

三历史臂用于版本比较，grounded 是第四个候选臂。它们消费的上下文不同，因此版本臂之间的差异包含提示词和上下文设计，不等于单独证明某一句措辞的效果。新 API 服务也不能复现历史后端分布。

原主实验事件保存 instruction/schema/evidence，可渲染原 benchmark 提示词。旧日志的变体状态从动作、available_actions 和检查结果重建，并标记 reconstructed。新事件直接记录 variant_state。重放使用 use_cache=False，每次确实调用模型，按区块打乱臂顺序，记录原始响应、错误、提示词 hash、服务返回的模型信息及所有调用数。

原 review-draft 按任务/商品/选项合并过不同证据阶段，不能直接作为每个重放状态的标签。新草稿按 case_id 区分证据状态，隐藏 Gate verdict 和原任务成绩；acceptable/reviewer 保留空值。旧标签仅在证据和选项完全匹配时使用。无法判断的状态保留 null，不强制写成 false；未标注正确性指标也为 null。

重放报告 ACCEPT/INSPECT/REJECT/ERROR 计数及 Wilson 95% 区间，错误单列，另报所有调用的接受比例。重复调用不是新增独立任务；区间是条件调用分布的描述，可能受服务相关性影响。没有人工标签时不能报告审计正确率，更不能分解完整任务的“必然/运气”。

## 命令

在已配置的 webshop Python 环境执行。以下命令默认只读旧结果并生成新文件，不调用 API：

```powershell
python -m evaluation.audit_replay prepare results/cz-v1-live/product/results.jsonl results/cz-v1-live/full/results.jsonl --task-ids B07QXZL1K5 B07CXTNVRJ B096MM7XM4 B094JTNPWK B07N864Q64 --output results/gate-diagnostics/cases.json
python -m evaluation.audit_replay replay --cases results/gate-diagnostics/cases.json --arms legacy benchmark variant grounded --repeats 20 --output-dir results/gate-diagnostics/replay-plan-final
python -m evaluation.matcher_benchmark --output-dir results/gate-diagnostics/matcher-plan
```

最初生成了 15 个冻结证据状态、cases.review.json 盲审草稿、四臂 1200 次调用计划，以及 9 个 matcher 合成例子。2026-10-04 已在独立目录完成真实重放、matcher 诊断和四组 20 步完整任务，见 [真实实验记录](LIVE_GATE_DIAGNOSTICS.md)。重新运行正式重放须选择新的 output-dir 并加 --execute；可加 --labels 已填写的状态级人工标签文件。matcher 同样需 --execute 才调用 API。按模型实际计费，增加提示词臂不是零成本。

共享抽取与完整任务示例：

```powershell
python -m evaluation.frozen_schemas --results results/cz-v1-live/product/results.jsonl --output results/gate-diagnostics/frozen-schemas-final.json
.\evaluation\run_live_advanced.ps1 -Configs product,full -FrozenSchemas results/gate-diagnostics/frozen-schemas-final.json -QueryMode layered -OutputDir results/gate-layered-controlled -DirectApi
.\evaluation\run_live.ps1 -Repeats 3 -MaxSteps 20 -OutputDir results/gate-e2e-next -DirectApi
```

两个 run_live 启动器都会真实运行 WebShop 并调用 API；上面的示例未执行。为每个干预使用不同目录。人工 schema 也可使用相同 bundle 格式，provenance 必须注明独立标注来源，任务与指令必须完全对应，usable schema 必须有 validation.valid=true 并通过源文锚定检查。

## 可选独立标注

自动成功率、reward、步数和调用成本不需要人工标签。只有需要评价 Gate 判断是否正确、具体约束是否满足时才使用本节。先导出已保存的轨迹，再独立阅读 instruction、可见证据和选项：

```powershell
python -m evaluation.export_annotations results/webshop/baseline/results.jsonl results/webshop/query/results.jsonl results/webshop/product/results.jsonl results/webshop/full/results.jsonl --output results/webshop/review-draft.json
python -m evaluation.summarize results/webshop --annotations results/webshop/reviewed.json
```

两条命令之间需要填写并保存 `reviewed.json`；导出和重新汇总不调用 LLM。每条记录保留 task_id、kind、query 或 product_id、selected_options。由独立审查者填写 acceptable=true/false、reviewer，以及 instruction 全部明确要求的 constraint_statuses（SUPPORTED/MISSING/CONTRADICTED）。无法判断的草稿值保留 null，不能作为标签；不要参考 Gate verdict 或用抽取 schema 充当标准答案。合成演示标签不能替代真实标签。

| 独立指标 | 定义 |
|---|---|
| Query Constraint Coverage | 已执行且已标注搜索中的 SUPPORTED 条件 / 全部标注条件 |
| Product Constraint Satisfaction | 已购买且已标注商品/选项中的 SUPPORTED 条件 / 全部标注条件 |
| Purchase Satisfaction | acceptable 购买 / 全部已标注购买 |
| False Acceptance Rate | 无效标注样本被接受 / 已明确接受或拒绝的无效标注样本 |
| False Rejection Rate | 有效标注样本被拒绝 / 已明确接受或拒绝的有效标注样本 |

Query PASS 为接受、REVISE 为拒绝原查询；Product READY 为接受、REJECT/EXHAUSTED 为拒绝。INSPECT/UNAVAILABLE 单列 deferred。相同任务的相同输入、选项、证据与决策重复发生时去重。未标注事件单列 unlabelled，分母为零返回 null，必须同时报告标签覆盖率。不同选项是不同标注状态，WebShop reward 不能替代独立语义审查。标签 hash 保存到分析结果，原运行 manifest 和代码快照保留。

## Gates / Agent 对应

- gates/audit_prompts.py、audit_prompt_versions.json：审计职责、历史臂、结构化错配验证。
- gates/product_gate.py：购买资格、变体上下文、可选 H1、按状态缓存。
- gates/query_gate.py：三个查询实验模式及缺失/矛盾修复分流。
- agent/react_agent.py：开关、提醒调用、共享 schema、完整 Gate 事件、购买阻断。
- agent/episode_memory.py：候选状态、可选 EXHAUSTED 重访过滤。
- evaluation/audit_replay.py：状态提取、盲审草稿、计划/真实重放及统计。
- evaluation/matcher_benchmark.py：合成冲突/遗漏诊断。
- evaluation/run_live.ps1：固定 coverage + grounded 的正式四组入口。
- evaluation/frozen_schemas.py、run_experiment.py、run_live_advanced.ps1：共享抽取与高级实验设置追踪。

69 项无网络回归与 17 项上游测试已通过；其中 25 项覆盖新干预的缓存绕过、语义约束引用、UNKNOWN 不放行、H1 不改类别冲突、共享抽取失败不压低 Baseline、精确证据标签连接及去重的状态边界。随后已完成一轮真实四组成功率评测；H1/H2 和候选去重的独立干预效果仍未验证。
