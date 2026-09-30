import importlib.util
from pathlib import Path

from evaluation.runner import run_agent_on_tasks
from evaluation.task_loader import load_tasks


def load_agent_class():
    agent_path = Path(__file__).resolve().parents[1] / "agent" / "No-memory ReAct.py"
    spec = importlib.util.spec_from_file_location("no_memory_react_agent", agent_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.NoMemoryReActAgent


def main():
    tasks = load_tasks("evaluation/webshop_test_100.json")
    agent = load_agent_class()(
        num_products=1000,
        max_steps=20,
    )
    run_agent_on_tasks(
        agent=agent,
        tasks=tasks,
        output_path="results/no_memory_react.jsonl",
        trajectory_dir="results/trajectories/no_memory_react",
    )


if __name__ == "__main__":
    main()
