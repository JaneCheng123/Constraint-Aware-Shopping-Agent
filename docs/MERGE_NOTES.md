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

35 项离线回归测试通过，覆盖购买绕过、API 故障、候选导航、选项评分、价格、缓存、独立标签、信息泄露和四组公平性。演示生成执行轨迹、调用分项、比较表和可展开回放。

本机没有真实 WebShop 商品数据和搜索索引，也缺完整上游依赖。因此不报告真实成功率，不将历史 README 中的 31 任务结果作为 cz-v1 的实验结论。原始 10 任务 main 结果仍作为历史记录保留。
