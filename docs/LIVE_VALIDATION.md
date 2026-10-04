# cz-v1 真实 WebShop 测试记录

此前只检查了默认 Python 和当前目录，漏掉已有的 webshop Conda 环境。后续从本机另一项目副本复制真实商品数据和 1k Lucene 索引，并使用已有的 DEEPSEEK_API_KEY 完成真实测试。没有打印、写入或提交密钥，也没有升级原 Conda 环境。

## 运行条件

- 执行代码提交：b8d48c5a99e835116514e10e3fb609022e9f4897。
- 数据集：evaluation/webshop_test_100.json 中 31 个包含 instruction 的任务。
- 商品库：1000 个真实 WebShop 商品；索引：search_engine/indexes_1k。
- 模型：DeepSeek API 的 deepseek-chat，temperature=0。
- 配置：baseline、query、product、full；每组各 31 任务、1 次重复、seed=42。
- 每任务最多 20 个环境动作；Gate、修复和动作重试的 API 调用另行计数。
- Windows / Python 3.8.20 / OpenJDK 11.0.30 / openai 0.28.1。
- 旧 SDK 环境使用独立 Requests HTTP 客户端，保留 TLS 验证，关闭隐式重试。
- 四组在独立进程中运行。比较前验证参数、任务、源码、商品数据与索引指纹一致；组件 manifest 和源码快照保留。

## 结果

| 配置 | 满分成功 | 成功率 | 平均 reward | 平均环境步数 | 平均 LLM 调用 | 抽取校验失败 |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 6/31 | 19.35% | 0.2258 | 17.58 | 21.45 | 0 |
| Query only | 5/31 | 16.13% | 0.1720 | 16.29 | 24.06 | 2 |
| Product only | 12/31 | 38.71% | 0.3978 | 13.32 | 31.16 | 2 |
| Full | 9/31 | 29.03% | 0.3011 | 14.84 | 35.81 | 2 |

成功定义为任务终止且 reward > 0.999999。部分得分购买分别为 3、1、1、1 次，单独统计，未计入满分成功。抽取校验失败仍包含在 31 个任务的分母内。

本轮 Product-only 的满分成功数和平均 reward 最高，Full 次之。Query Gate 在这一轮未提高基线成绩，同时增加调用量。不同配置各自调用模型抽取与决策，外部服务存在输出波动；单次 31 任务不能证明稳定的因果效果。重复实验、更多任务及 20/50 步敏感性比较应作为后续研究。

## 验证与调用统计

- 截至补充价格格式和选项审计修复，44 项核心回归测试通过，17 项上游环境/transfer 测试通过。
- 主实验完成 124 个任务回合，API 调用失败为 0。
- 主实验共 3487 次 API 调用，输入 4,079,316 tokens、输出 304,384 tokens；不含连通性探测、小规模开发测试及单独的补充检查。
- 所有记录的步数均不超过 20，调用分项之和与总数一致。
- Product-only 和 Full 共 23 次购买，全部存在购买执行前的 READY 检查。
- 主实验源码 hash 已核对，运行时工作区干净；代码快照固定为 b8d48c5。后续补充价格格式修复独立验证，不改写该主实验的源码或成绩。
- 真实选项点击回归：选中 size=0.5 0unce 后，可见 checked 状态与记录一致。
- 真实价格评分回归：$7.99 商品满足 under $10，reward=1；不满足 under $7.99，reward=0.8。该手动环境检查单独保存，不混入 Agent 成绩。

补充的明确尺寸和价格任务首次在抽取阶段失败：模型返回价格对象，旧规范化逻辑将对象转成了字符串形式的字典，验证器又要求价格对象，形成格式不一致。后续代码从对象的 source_text 取原价格短语，并明确标量格式；回归测试确认严格 under 边界不被 canonical 的 at most 覆盖。主实验 31 条原始指令均无显式价格条件，其结果保留在原提交快照下。

价格修复后的补充任务已能提取约束并点击 size=0.5 0unce，但最终语义审计没收到选中状态，只看通用标题/描述中的 1 oz，拒绝了该候选并耗尽步数。后续向审计传入页面可见的选项组、已选值和规则检查结果，说明通用默认规格不能代替当前选中变体，并把选项状态纳入审计缓存键。硬约束失败仍在审计前拦截；未选中的可用选项不会被视为已选。

## 已观察到的失败案例

1. B07DJJXGB5：原指令为 scrubs & body treatments with tea tree, natural ingredients。真实目标商品是 acne body spray；模型对类别和自然成分证据持疑，四组均耗尽 20 步。类别解释与 WebShop 原始分类并不总一致，不能只根据环境满分标签判断语义 Gate 对错。
2. B07K6TCDR9：原指令包含 animal testing。验证器倾向按常见购物意图解释为避免动物测试，拒绝确认字面抽取。三个 Gate 配置均停止，避免在未通过验证时继续执行；该指令需要独立审查极性，不能悄悄改为 cruelty-free 后重跑并混入本轮。
3. B09BW4S48B：makeup remover for sensitive skin, nail polish 被验证器解释为请求两个产品类别，抽取未通过。三个 Gate 配置均计为失败；这暴露了原指令语法与用途约束解析的歧义。
4. 其余大量任务在步数预算内未找到并确认可购买商品。停止原因为 step_budget 的数量依次为 22、23、16、19；这与 API 连接失败不同。

正向案例 B08X2PKKB2：四组均取得 reward=1。rose gold 为商品固定颜色、页面只有 size 选择器；修复后 Product Gate 检查可见颜色证据，不要求点击不存在的颜色选项。有真实颜色选择器时仍必须选中正确值。

## 结果文件与独立标注

- results/cz-v1-live/report.html：四组表格、原始动作与 Gate 轨迹回放。
- results/cz-v1-live/comparison.json：机器可读汇总。
- results/cz-v1-live/<configuration>/results.jsonl：完整 31 个任务记录。
- results/cz-v1-live/manifest.json、code_snapshot、runs：执行参数、组件来源与源码快照。
- results/cz-v1-live/validation.json：完整性和计数检查。
- results/cz-v1-live/review-draft.json：249 条独立审查草稿，acceptable=null，未把 Gate verdict 充当答案。

Query Coverage、Product Satisfaction、False Acceptance/False Rejection 等独立语义指标尚无人工标签，报告中返回 null。本轮真实 reward/success 和调用统计已完成，不能把这两种评估混为一谈。完成草稿审查后可用 evaluation.summarize 重新汇总，不再调用 API。

结果和本地数据受 .gitignore 排除，保存在当前机器；本记录随代码提交。历史分支、合成演示与本轮真实结果均单独存放。

## 再次运行

~~~powershell
.\evaluation\run_live.ps1 -MaxSteps 20 -Repeats 1 -Seed 42 -OutputDir results/cz-v1-live-next -DirectApi
~~~

启动器选择已有 webshop 环境，并为进程设置正确的 Java 路径，结束后恢复环境变量。DirectApi 仅绕过 API 域名的系统代理，不关闭 TLS 验证。新实验使用新目录；相同参数续跑加 -Resume。
