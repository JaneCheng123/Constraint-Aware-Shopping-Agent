from agent.gated_agent import GatedAgent
from evaluation.runner import run_agent_on_tasks
from evaluation.task_loader import load_tasks


tasks = load_tasks(
    "evaluation/webshop_test_100.json"
)

agent = GatedAgent(
    num_products=1000,
    max_steps=20,
    use_query_gate=False,
    use_product_gate=True,
)

run_agent_on_tasks(
    agent,
    tasks,
    "results/product_gate.jsonl",
)
