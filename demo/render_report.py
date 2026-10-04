"""Build a standalone trajectory viewer from saved experiment results."""

import html
import json
from pathlib import Path


def render_report(output_dir):
    output = Path(output_dir)
    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    escape = lambda value: html.escape(str(value))
    rows, details = [], []
    for config, summary in comparison.items():
        fmt = lambda value: "未标注" if value is None else f"{value:.3f}"
        rows.append("<tr>" + "".join(f"<td>{escape(x)}</td>" for x in (
            config, summary["tasks"], fmt(summary["task_success_rate"]), fmt(summary["average_reward"]),
            fmt(summary["average_steps"]), fmt(summary["average_llm_calls"]),
            fmt(summary["query_constraint_coverage"]), fmt(summary["product_constraint_satisfaction"]))) + "</tr>")
        # Resume may append a retry. Show only the latest record per task/repeat.
        latest = {}
        for line in (output / config / "results.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            latest[(record["repeat"], record["task_id"])] = record
        for record in latest.values():
            steps = []
            for step in record["trajectory"]:
                steps.append(f"<details><summary>步骤 {step['step']} · {escape(step.get('action'))}"
                             f" · {'执行' if step.get('executed', True) else '拦截'}</summary>"
                             f"<pre>{escape(json.dumps(step, ensure_ascii=False, indent=2))}</pre></details>")
            events = escape(json.dumps(record.get("gate_events", []), ensure_ascii=False, indent=2))
            details.append(f"<article data-config='{escape(config)}'><h3>{escape(config)} · "
                           f"{escape(record['task_id'])} · repeat {record['repeat']}</h3>"
                           f"<p>{escape(record['instruction'])}</p><p>Reward {record['reward']:.3f} · "
                           f"{escape(record['stop_reason'])} · LLM calls {record['usage']['llm_calls']}</p>"
                           + "".join(steps) + f"<details><summary>Gate 决策与证据</summary><pre>{events}</pre></details></article>")
    kind = manifest["identity"]["run_kind"]
    options = "".join(f"<option>{escape(config)}</option>" for config in comparison)
    title = "离线合成演示（非 WebShop 基准成绩）" if kind == "synthetic_offline_demo" else "WebShop 四组实验"
    document = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>cz-v1 实验与轨迹回放</title><style>
body{{font:16px/1.6 system-ui;background:#f4f6f8;color:#16243b;margin:32px auto;max-width:1200px;padding:0 24px}}
h1{{font-size:28px}} table{{border-collapse:collapse;width:100%;background:white}}
th,td{{text-align:left;padding:12px;border-bottom:1px solid #dde4eb}}
article{{background:white;padding:20px;margin:20px 0;border-radius:12px}}
summary{{cursor:pointer;padding:8px;background:#edf2f7}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}
.notice{{padding:16px;background:#e6effb;border-radius:8px}}
</style><h1>cz-v1 实验与轨迹回放</h1><p class="notice">{escape(title)}。
约束指标使用独立标注；无标注值显示“未标注”。展开步骤查看观察、提议、执行和拦截。</p>
<table><thead><tr><th>配置</th><th>任务</th><th>满分成功率</th><th>平均 reward</th>
<th>步骤</th><th>LLM calls</th><th>Query coverage</th><th>Product satisfaction</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table><p>筛选配置 <select id="config"><option value="all">全部</option>{options}</select></p>
{''.join(details)}<script>document.querySelector('#config').addEventListener('change',function(){{
document.querySelectorAll('article').forEach(x=>x.hidden=this.value!=='all'&&x.dataset.config!==this.value);
}});</script></html>"""
    path = output / "report.html"
    path.write_text(document, encoding="utf-8")
    return path
