from agent.baseline_agent import BaselineAgent
from evaluation.task_loader import load_tasks
from evaluation.runner import run_agent_on_tasks


tasks = load_tasks("evaluation/webshop_test_100.json")

agent = BaselineAgent(
    num_products=1000,
    max_steps=50,
)

run_agent_on_tasks(
    agent=agent,
    tasks=tasks,
    output_path="results/baseline.jsonl",
)
