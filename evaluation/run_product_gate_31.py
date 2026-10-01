import argparse
import json
import os
import traceback

from agent.gated_agent import GatedAgent


def load_instruction_tasks(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tasks = []

    for asin, item in data.items():

        if not isinstance(item, dict):
            continue

        instruction = item.get("instruction")

        if not instruction:
            continue

        task = dict(item)

        task["task_id"] = asin
        task["instruction"] = instruction

        tasks.append(task)

    return tasks


def target_visited(task_id, trajectory):
    target = str(task_id).lower()

    for step in trajectory:
        action = str(
            step.get("action", "")
        ).lower()

        if action == f"click[{target}]":
            return True

    return False


def count_action(trajectory, target_action):
    target_action = target_action.lower()

    return sum(
        1
        for step in trajectory
        if str(
            step.get("action", "")
        ).lower()
        == target_action
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--shard-id",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--num-shards",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--task-file",
        default="evaluation/webshop_test_100.json",
    )

    parser.add_argument(
        "--output-dir",
        default="results/product_gate_31",
    )

    args = parser.parse_args()

    os.makedirs(
        args.output_dir,
        exist_ok=True,
    )

    tasks = load_instruction_tasks(
        args.task_file
    )

    print(
        f"Total instruction tasks: {len(tasks)}",
        flush=True,
    )

    assert len(tasks) == 31, (
        f"Expected 31 instruction tasks, got {len(tasks)}"
    )

    shard_tasks = [
        task
        for i, task in enumerate(tasks)
        if i % args.num_shards == args.shard_id
    ]

    output_path = os.path.join(
        args.output_dir,
        f"shard_{args.shard_id}.jsonl",
    )

    print(
        f"[Shard {args.shard_id}] "
        f"tasks={len(shard_tasks)}",
        flush=True,
    )

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as fout:

        for index, task in enumerate(
            shard_tasks,
            start=1,
        ):

            task_id = task["task_id"]

            print()
            print("=" * 80)
            print(
                f"[Shard {args.shard_id}] "
                f"{index}/{len(shard_tasks)} "
                f"{task_id}",
                flush=True,
            )

            print(
                task["instruction"],
                flush=True,
            )

            try:
                agent = GatedAgent(
                    num_products=1000,
                    max_steps=args.max_steps,
                    use_query_gate=False,
                    use_product_gate=True,
                )

                result = agent.run(task)

                trajectory = result.get(
                    "trajectory",
                    [],
                )

                reward = float(
                    result.get(
                        "reward",
                        0.0,
                    )
                )

                record = {
                    "task_id": task_id,
                    "instruction": task["instruction"],

                    "instruction_attributes": (
                        task.get(
                            "instruction_attributes",
                            [],
                        )
                    ),

                    "reward": reward,

                    # 保持 baseline 的 success 定义
                    "success": reward > 0,

                    "full_success": (
                        reward == 1.0
                    ),

                    "partial_success": (
                        0.0 < reward < 1.0
                    ),

                    "zero": (
                        reward == 0.0
                    ),

                    "steps": len(
                        trajectory
                    ),

                    "target_visited": (
                        target_visited(
                            task_id,
                            trajectory,
                        )
                    ),

                    "buy_now_count": (
                        count_action(
                            trajectory,
                            "click[buy now]",
                        )
                    ),

                    "back_to_search_count": (
                        count_action(
                            trajectory,
                            "click[back to search]",
                        )
                    ),

                    "gate_stats": (
                        result.get(
                            "gate_stats",
                            {},
                        )
                    ),

                    "constraint_schema": (
                        result.get(
                            "constraint_schema"
                        )
                    ),

                    "trajectory": trajectory,

                    "error": None,
                }

                print(
                    "[RESULT]",
                    f"reward={reward}",
                    f"steps={len(trajectory)}",
                    f"target={record['target_visited']}",
                    f"buy={record['buy_now_count']}",
                    flush=True,
                )

            except Exception as e:

                record = {
                    "task_id": task_id,
                    "instruction": task.get(
                        "instruction"
                    ),
                    "instruction_attributes": (
                        task.get(
                            "instruction_attributes",
                            [],
                        )
                    ),
                    "reward": 0.0,
                    "success": False,
                    "full_success": False,
                    "partial_success": False,
                    "zero": True,
                    "steps": 0,
                    "target_visited": False,
                    "buy_now_count": 0,
                    "back_to_search_count": 0,
                    "gate_stats": {},
                    "constraint_schema": None,
                    "trajectory": [],
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }

                print(
                    "[ERROR]",
                    task_id,
                    str(e),
                    flush=True,
                )

            fout.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

            fout.flush()


if __name__ == "__main__":
    main()
