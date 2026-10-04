"""Run all four configurations offline and write a synthetic trajectory demo."""

import argparse
from pathlib import Path

from demo.offline import DemoEnvironment, ScriptedClient
from evaluation.run_experiment import main as run_experiment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="results/demo-cz-v1")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = ["--dataset", str(root / "demo/tasks.json"), "--model", "scripted-demo",
               "--annotations", str(root / "demo/annotations.json"), "--output-dir", args.output_dir]
    if args.resume:
        command.append("--resume")
    return run_experiment(command, client_factory=ScriptedClient, env_factory=DemoEnvironment,
                          run_kind="synthetic_offline_demo")


if __name__ == "__main__":
    main()
