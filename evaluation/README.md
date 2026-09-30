# Evaluation

所有 Agent 使用同一个 `tasks.json`。

目前支持的实验配置：

- baseline
- query_gated
- product_gated
- full

最终结果统一保存为 JSONL：

```text
results/
    baseline.jsonl
    query_gated.jsonl
    product_gated.jsonl
    full.jsonl

