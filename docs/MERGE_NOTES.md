# cz-v1 整合与验证记录

## 分支来源

- 从 main@8d6d817 创建 cz-v1。
- 使用真正的双父 merge commit 合并 origin/constraint-gates-v1@762944e。
- 后续在 cz-v1 统一策略、修复门控与评测，保留两个原分支的历史。

## 修复的问题

- 删除 Agent 中将宽泛商品类别 REJECT 直接改为 READY 的规则。
- 删除 ConstraintManager 中将宽泛类别的语义冲突改为 SUPPORTED 的规则。
- Product Gate 不再因类别判断提前跳过属性、价格和选项检查。
- 购买重考虑 API 失败不能回退执行原来的拒绝购买；购买执行前再次验证 READY。
- 统一处理 < prev：从商品返回结果清空当前候选，从详情返回商品保留候选。
- 新搜索清空 active candidate；每个候选的选项、证据和页面状态隔离。
- 精确选项匹配替代子串匹配；替换选项覆盖原值，不能沿用其他商品的历史点击。
- Decimal 价格保留小数与严格/包含边界，规则和环境评分采用相同边界。
- 当前商品的 Price 字段作为价格证据，评论里的金额不充当商品价格。
- ReAct 不读取 instruction_attributes；runner 保留完整任务给环境评分。
- 所有配置用相同模型、预算、scratchpad、记忆、重试和满分成功判定。
- 提取验证器返回 false 或 API 错误不会被规则锚定检查覆盖为 valid。
- 否定短语不直接通过精确匹配；无序 token 覆盖不再作为放行依据。
- 空评分属性不再导致上游 get_attribute_reward 除零。
- 环境选项评分传入 goal_options.values()，避免将键值元组错当选项值。
- 标注缺失的约束指标显示 null；Gate verdict 不充当独立 gold label。
- 记录所有 LLM 调用尝试和错误；resume 校验数据、参数、源码 hash。
- 上游 tests/web-agent-site、tests/transfer 和 transfer 辅助实现恢复保留。
- CI 覆盖当前分支，使用无需 API/数据的核心回归与四组合成演示。

## 验证

核心命令：
~~~bash
python -m unittest discover -s tests -p test_cz_v1.py -v
python -m demo.run_demo --output-dir results/demo-cz-v1-final
python -m evaluation.preflight
~~~

43 项核心回归测试通过，覆盖购买绕过、API 故障、候选导航、选项评分、价格、缓存、独立标签、信息泄露和四组公平性。17 项上游测试通过；旧测试的 spaCy 固定分数改为独立验证模型语义和受控 token 的分数公式，日志测试关闭 FileHandler 以支持 Windows。

首次整合验证只检查了默认 Python 和本目录，因此只运行了离线验证。后续找到本机已有的 webshop Conda 环境，并从另一个项目副本复制真实数据和 1k 索引，已验证真实搜索、商品页和 DeepSeek 调用。

真实小规模测试进一步修复：旧版 SDK 环境的 HTTP 兼容接口、Java 路径、搜索首页/结果页导航、product_type 元数据，以及无对应选择器时固定颜色的证据检查。实际有选项选择器的商品仍须选中正确值，详情页保留已知选项组，不能跳过选择。固定属性缺失或冲突仍不放行。

历史分支结果和脚本化演示不与新的真实实验混合。原始 10 任务 main 结果仍作为历史记录保留。
