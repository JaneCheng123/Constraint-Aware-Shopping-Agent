# 2026-10-05 Gate 修复后的付费 API 验证

本轮完成真实 WebShop 四组各 31 个任务、每任务最多 20 步、每组一次重复：Baseline 4/31、Query 6/31、Product 17/31、Full 17/31 满分成功。无香选择遗漏在 Product/Full 两组均得到真实模型轨迹验证；品牌修复通过固定查询诊断验证。单次实验不能证明整体增益稳定。

## 固定条件

- 运行提交：`eb1f5096d8f8ec7ee0b5246a6e4e0daa498600de`，启动时工作区干净。修复已推送至 cz-v1。
- 正式入口：`evaluation/run_live.ps1`，coverage Query Gate、grounded Product Gate、seed=42、temperature=0；H1、候选去重和共享冻结 schema 均关闭。
- 同一 31 任务集、1000 商品、1k Lucene 索引。各 Gate 组独立正常抽取 schema，未使用人工理想约束。
- 请求模型 deepseek-chat；服务响应实际模型为 deepseek-flash。全部 2891 次调用均记录该模型身份。
- 检查 124 条轨迹的任务身份、模型/预算参数、动作计数、调用分项和运行源码 hash；38 次门控购买全部通过最终 READY，新增绑定要求均确认选中。
- 81 项离线回归此前已通过。本轮运行期间未修改源码；本报告在实验结束后生成。

## 四组结果

满分成功定义为任务终止且 reward > 0.999999。部分得分不计满分；抽取失败等系统失败保留在 31 个任务的分母中。

| 配置 | 上一轮满分 | 本轮满分 | 本轮成功率 | 部分得分 | 平均 reward | 平均环境步数 | API 调用 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 6/31 | 4/31 | 12.90% | 2 | 0.1559 | 18.03 | 668 |
| query | 4/31 | 6/31 | 19.35% | 2 | 0.2204 | 15.97 | 726 |
| product | 15/31 | 17/31 | 54.84% | 2 | 0.5806 | 9.94 | 732 |
| full | 16/31 | 17/31 | 54.84% | 2 | 0.5806 | 9.81 | 759 |

上一轮为 3e9d3b9 的 20 步单次实验，见 [历史记录](LIVE_GATE_DIAGNOSTICS.md)。Baseline 策略未改，满分数仍从 6 变为 4，成功任务仅 2 个重合，说明跨运行波动不能忽略。Query 从 4 变为 6，Product 从 15 变为 17，Full 从 16 变为 17；这些差值不是两项修复各自的因果效果。

Product/Full 本轮共同满分成功 16 个任务；仅 Product 成功的是 B07CXTNVRJ，仅 Full 成功的是 B08PFRP4RN。两组总分相同不代表逐任务行为相同，也不证明 Query Gate 没有价值或有稳定收益。

三个 Gate 组均在 B07K6TCDR9、B09BW4S48B 抽取验证失败，各计 2 个失败。Baseline 正常执行。API 错误、Gate 事件中的调用/格式错误均为 0。

## 无香选项修复

B08WG7VLQF 原指令为 `Find me fragrance free styling products for dry hair`。两组本轮仍把 fragrance free 抽成 required_constraints 的 attribute，post_selection_constraints 仍为空。因此改善不是靠抽取恰好改成正确类型。

Product Gate 从真实 scent 选择器找到 fragrance free，纳入现有选项检查和动作建议。Product/Full 均购买 B08WG7VLQF，轨迹相同：

1. 搜索 fragrance free styling products for dry hair。
2. 进入 B08WG7VLQF。
3. click[fragrance free]，实际选中 scent=fragrance free。
4. 检查 Features。
5. 返回商品页。
6. 最终 READY 后 click[buy now]，reward=1.0。

上一轮两组在该商品的选中状态为空，reward=0.75。本轮验证了补选和购买阻断机制，但不能外推所有属性/选项语义都已解决。Baseline/Query 两组本轮在该任务均耗尽 20 步、reward=0。

## 品牌查询修复

既有 9 个固定合成查询案例使用了 6 次真实 API 调用，分类 8/9 符合预期。品牌替换案例要求 Acme，原查询为 `Beta headphones blue under $10`；模型仍返回 MISSING，未可靠识别 CONTRADICTED。

新修复输出 `headphones Acme blue under $10`，移除了 Beta；品牌遗漏案例也采用重建。这里验证的是修复兜底行为，不能声称 matcher 的品牌识别准确率提高。离线回归同时验证了修复查询可再次通过 Gate，普通属性遗漏仍保留原查询追加。

本轮 31 个真实任务的三个 Gate 组均没有明确品牌约束，真实轨迹也没有触发品牌重建。因此 Query 组成功数变化不能归因于该品牌规则；需要另有品牌任务才能评价其真实检索效果。

## 调用用量

| 阶段 | API 调用 | 输入 tokens | 输出 tokens | API 错误 |
|---|---:|---:|---:|---:|
| 四组 WebShop | 2,885 | 3,530,006 | 246,740 | 0 |
| 固定查询诊断 | 6 | 2,084 | 234 | 0 |
| 合计 | 2,891 | 3,532,090 | 246,974 | 0 |

token 数来自服务响应 usage，未估算账单金额。20 步限制环境动作，不限制 Gate、策略和重试的总调用次数。本轮没有重跑审计重放、H1/H2 或 50 步实验。

## 剩余问题与结果文件

- Product/Full 各有两个部分得分购买：B09MW563KN（0.3333）、B09MCF64RM（0.6667）。原生字符串评分与语义判断的关系仍需独立复核。
- B08W568RSB 的 travel size 被抽为 size，但候选的 size 选择器是 5 packs/15 packs，无法精确对应旅行规格；该任务新旧两轮都未成功。规格语义和选项组命名的歧义仍未全面解决。
- 无独立人工标签，约束覆盖、满足度、误接受和误拒绝保持 null。稳定的 API 调用或 READY 不能替代正确性标注。
- 只有一次重复，数据集较小且缺品牌任务；新增选择会占环境步数，品牌重建可能损失有用搜索词。未来结果需继续独立记录。

本机原始结果目录：`results/gate-fixes-eb1f509-20261005/`。`e2e/report.html` 展示四组轨迹；comparison.json、各组 results.jsonl、manifest.json、code_snapshot 和 validation.json 保存成绩与运行身份。matcher/results.jsonl 与 manifest.json 保存查询诊断，analysis.json 保存配对任务及绑定事件。原始结果按现有 .gitignore 保留本机，本报告随代码提交。
