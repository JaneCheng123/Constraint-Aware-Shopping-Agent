import json
import os
import re
import traceback
from statistics import mean, median

from agent.gated_agent import GatedAgent


TASK_FILE = "evaluation/webshop_test_100.json"
OUTPUT_DIR = "results/product_gate_31_linear"

RESULT_JSONL = os.path.join(
    OUTPUT_DIR,
    "results.jsonl",
)

SUMMARY_JSON = os.path.join(
    OUTPUT_DIR,
    "summary.json",
)

SUMMARY_TXT = os.path.join(
    OUTPUT_DIR,
    "summary.txt",
)

MAX_STEPS = 20


# ============================================================
# Helpers
# ============================================================

def load_tasks(path):

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:

        data = json.load(f)

    tasks = []

    for asin, item in data.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        instruction = item.get(
            "instruction"
        )

        if not instruction:
            continue

        task = dict(item)

        task["task_id"] = asin
        task["instruction"] = instruction

        tasks.append(task)

    return tasks


def target_visited(
    task_id,
    trajectory,
):

    target = str(
        task_id
    ).lower()

    for step in trajectory:

        action = str(
            step.get(
                "action",
                "",
            )
        ).strip().lower()

        match = re.fullmatch(
            r"click\[(.*?)\]",
            action,
        )

        if (
            match
            and match.group(1).strip().lower()
            == target
        ):
            return True

    return False


def count_action(
    trajectory,
    target,
):

    target = target.lower()

    return sum(
        1
        for step in trajectory
        if str(
            step.get(
                "action",
                "",
            )
        ).strip().lower()
        == target
    )


def gate_stat(
    record,
    name,
):

    return int(
        record.get(
            "gate_stats",
            {},
        ).get(
            name,
            0,
        )
        or 0
    )


def build_summary(
    records,
):

    full = [
        r for r in records
        if float(
            r["reward"]
        ) == 1.0
    ]

    partial = [
        r for r in records
        if (
            0.0
            < float(
                r["reward"]
            )
            < 1.0
        )
    ]

    zero = [
        r for r in records
        if float(
            r["reward"]
        ) == 0.0
    ]

    visited = [
        r for r in records
        if r.get(
            "target_visited"
        )
    ]

    visited_zero = [
        r for r in records
        if (
            r.get(
                "target_visited"
            )
            and float(
                r["reward"]
            ) == 0.0
        )
    ]

    not_visited_zero = [
        r for r in records
        if (
            not r.get(
                "target_visited"
            )
            and float(
                r["reward"]
            ) == 0.0
        )
    ]

    errors = [
        r for r in records
        if r.get(
            "error"
        )
    ]

    rewards = [
        float(
            r["reward"]
        )
        for r in records
    ]

    steps = [
        int(
            r["steps"]
        )
        for r in records
    ]

    def total_stat(name):

        return sum(
            gate_stat(
                r,
                name,
            )
            for r in records
        )

    return {
        "tasks": len(
            records
        ),

        "full_success": len(
            full
        ),

        "partial_success": len(
            partial
        ),

        "zero": len(
            zero
        ),

        # 和 baseline success=reward>0 保持一致
        "reward_gt_0": (
            len(full)
            + len(partial)
        ),

        "average_reward": (
            mean(rewards)
            if rewards
            else 0.0
        ),

        "average_steps": (
            mean(steps)
            if steps
            else 0.0
        ),

        "median_steps": (
            median(steps)
            if steps
            else 0
        ),

        "target_visited": len(
            visited
        ),

        "target_visited_but_zero": len(
            visited_zero
        ),

        "target_not_visited_and_zero": len(
            not_visited_zero
        ),

        "total_buy_now": sum(
            int(
                r.get(
                    "buy_now_count",
                    0,
                )
            )
            for r in records
        ),

        "total_back_to_search": sum(
            int(
                r.get(
                    "back_to_search_count",
                    0,
                )
            )
            for r in records
        ),

        "errors": len(
            errors
        ),

        "gate_stats": {
            "product_gate_calls": total_stat(
                "product_gate_calls"
            ),

            "product_gate_ready": total_stat(
                "product_gate_ready"
            ),

            "product_gate_inspects": total_stat(
                "product_gate_inspects"
            ),

            "product_gate_rejections": total_stat(
                "product_gate_rejections"
            ),

            "product_feedback_injected_steps": total_stat(
                "product_feedback_injected_steps"
            ),

            "product_reconsider_calls": total_stat(
                "product_reconsider_calls"
            ),

            "product_reconsider_changes": total_stat(
                "product_reconsider_changes"
            ),

            "candidate_abandon_reconsiderations": total_stat(
                "candidate_abandon_reconsiderations"
            ),

            "premature_buy_reconsiderations": total_stat(
                "premature_buy_reconsiderations"
            ),

            "candidate_evidence_pages_added": total_stat(
                "candidate_evidence_pages_added"
            ),

            "constraint_extraction_calls": total_stat(
                "constraint_extraction_calls"
            ),

            "constraint_validation_calls": total_stat(
                "constraint_validation_calls"
            ),

            "semantic_match_calls": total_stat(
                "semantic_match_calls"
            ),

            "product_type_semantic_calls": total_stat(
                "product_type_semantic_calls"
            ),

            "product_final_audit_calls": total_stat(
                "product_final_audit_calls"
            ),
        },
    }


def print_running_summary(
    records,
    total_tasks,
):

    summary = build_summary(
        records
    )

    print()
    print("-" * 80)

    print(
        f"CURRENT PROGRESS: "
        f"{summary['tasks']} / {total_tasks}"
    )

    print(
        f"FULL={summary['full_success']}  "
        f"PARTIAL={summary['partial_success']}  "
        f"ZERO={summary['zero']}  "
        f"AVG_REWARD={summary['average_reward']:.4f}"
    )

    print(
        f"TARGET_VISITED={summary['target_visited']}  "
        f"VISITED_BUT_ZERO="
        f"{summary['target_visited_but_zero']}"
    )

    print("-" * 80)
    print()


# ============================================================
# Main
# ============================================================

os.makedirs(
    OUTPUT_DIR,
    exist_ok=True,
)


tasks = load_tasks(
    TASK_FILE
)


print()
print("=" * 80)
print("PRODUCT GATE ONLY — LINEAR RUN")
print("=" * 80)

print(
    "products file:",
    TASK_FILE,
)

print(
    "instruction tasks:",
    len(tasks),
)

print(
    "max_steps:",
    MAX_STEPS,
)

print(
    "Query Gate:",
    "OFF",
)

print(
    "Product Gate:",
    "ON",
)

print("=" * 80)
print()


if len(tasks) != 31:

    raise RuntimeError(
        f"Expected 31 instruction tasks, "
        f"but found {len(tasks)}"
    )


# 清空旧的 linear run
with open(
    RESULT_JSONL,
    "w",
    encoding="utf-8",
):
    pass


records = []


for index, task in enumerate(
    tasks,
    start=1,
):

    task_id = task[
        "task_id"
    ]

    instruction = task[
        "instruction"
    ]


    print()
    print("#" * 80)

    print(
        f"TASK {index} / {len(tasks)}"
    )

    print(
        f"ASIN: {task_id}"
    )

    print(
        f"Instruction: {instruction}"
    )

    print("#" * 80)
    print()


    try:

        agent = GatedAgent(
            num_products=1000,
            max_steps=MAX_STEPS,
            use_query_gate=False,
            use_product_gate=True,
        )


        result = agent.run(
            task
        )


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


        visited = target_visited(
            task_id,
            trajectory,
        )


        buy_count = count_action(
            trajectory,
            "click[buy now]",
        )


        back_count = count_action(
            trajectory,
            "click[back to search]",
        )


        if reward == 1.0:

            outcome = "FULL"

        elif reward > 0.0:

            outcome = "PARTIAL"

        else:

            outcome = "ZERO"


        record = {
            "task_index": index,

            "task_id": task_id,

            "instruction": instruction,

            "instruction_attributes": (
                task.get(
                    "instruction_attributes",
                    [],
                )
            ),

            "reward": reward,

            # baseline 保持一样
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

            "outcome": outcome,

            "steps": len(
                trajectory
            ),

            "target_visited": visited,

            "buy_now_count": (
                buy_count
            ),

            "back_to_search_count": (
                back_count
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

            "trajectory": (
                trajectory
            ),

            "error": None,
        }


        print()
        print("=" * 80)

        print(
            f"RESULT {index} / {len(tasks)}"
        )

        print("=" * 80)

        print(
            "ASIN:",
            task_id,
        )

        print(
            "OUTCOME:",
            outcome,
        )

        print(
            "REWARD:",
            reward,
        )

        print(
            "STEPS:",
            len(
                trajectory
            ),
        )

        print(
            "TARGET VISITED:",
            visited,
        )

        print(
            "BUY NOW COUNT:",
            buy_count,
        )

        print(
            "BACK TO SEARCH:",
            back_count,
        )

        print(
            "PRODUCT GATE READY:",
            result.get(
                "gate_stats",
                {},
            ).get(
                "product_gate_ready",
                0,
            ),
        )

        print(
            "PRODUCT GATE INSPECT:",
            result.get(
                "gate_stats",
                {},
            ).get(
                "product_gate_inspects",
                0,
            ),
        )

        print(
            "PRODUCT GATE REJECT:",
            result.get(
                "gate_stats",
                {},
            ).get(
                "product_gate_rejections",
                0,
            ),
        )

        print("=" * 80)


    except Exception as e:

        record = {
            "task_index": index,

            "task_id": task_id,

            "instruction": instruction,

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

            "outcome": "ERROR",

            "steps": 0,

            "target_visited": False,

            "buy_now_count": 0,

            "back_to_search_count": 0,

            "gate_stats": {},

            "constraint_schema": None,

            "trajectory": [],

            "error": str(e),

            "traceback": (
                traceback.format_exc()
            ),
        }


        print()
        print("=" * 80)

        print(
            f"ERROR {index} / {len(tasks)}"
        )

        print(
            "ASIN:",
            task_id,
        )

        print(
            "ERROR:",
            str(e),
        )

        print("=" * 80)


    # ========================================================
    # 每跑完一个立刻落盘
    # 即使第 20 个时程序意外断掉，
    # 前 19 个结果也不会丢
    # ========================================================

    records.append(
        record
    )


    with open(
        RESULT_JSONL,
        "a",
        encoding="utf-8",
    ) as f:

        f.write(
            json.dumps(
                record,
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )

        f.flush()

        os.fsync(
            f.fileno()
        )


    # 同时更新中间 summary
    running_summary = (
        build_summary(
            records
        )
    )


    with open(
        SUMMARY_JSON,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            running_summary,
            f,
            ensure_ascii=False,
            indent=2,
        )


    print_running_summary(
        records,
        len(tasks),
    )


# ============================================================
# Final summary
# ============================================================

summary = build_summary(
    records
)


lines = []

lines.append(
    "=" * 80
)

lines.append(
    "PRODUCT GATE ONLY — FINAL 31 INSTRUCTIONS"
)

lines.append(
    "=" * 80
)

lines.append(
    json.dumps(
        summary,
        ensure_ascii=False,
        indent=2,
    )
)


lines.append("")
lines.append(
    "=" * 80
)

lines.append(
    "PER-TASK RESULTS"
)

lines.append(
    "=" * 80
)


for r in records:

    lines.append(
        (
            f"{r['task_index']:02d}/31 "
            f"{r['task_id']} "
            f"{r['outcome']} "
            f"reward={r['reward']} "
            f"steps={r['steps']} "
            f"target={r['target_visited']} "
            f"buy={r['buy_now_count']} "
            f"back={r['back_to_search_count']}"
        )
    )


lines.append("")
lines.append(
    "=" * 80
)

lines.append(
    "TARGET VISITED BUT REWARD=0"
)

lines.append(
    "=" * 80
)


for r in records:

    if (
        r["target_visited"]
        and float(
            r["reward"]
        ) == 0.0
    ):

        lines.append(
            (
                f"{r['task_id']} "
                f"steps={r['steps']} "
                f"back_to_search="
                f"{r['back_to_search_count']}"
            )
        )


lines.append("")
lines.append(
    "=" * 80
)

lines.append(
    "TARGET NEVER VISITED + REWARD=0"
)

lines.append(
    "=" * 80
)


for r in records:

    if (
        not r["target_visited"]
        and float(
            r["reward"]
        ) == 0.0
    ):

        lines.append(
            (
                f"{r['task_id']} "
                f"steps={r['steps']}"
            )
        )


final_text = "\n".join(
    lines
)


print()
print(final_text)


with open(
    SUMMARY_TXT,
    "w",
    encoding="utf-8",
) as f:

    f.write(
        final_text
    )


with open(
    SUMMARY_JSON,
    "w",
    encoding="utf-8",
) as f:

    json.dump(
        summary,
        f,
        ensure_ascii=False,
        indent=2,
    )


print()
print("=" * 80)

print(
    "ALL 31 TASKS FINISHED"
)

print("=" * 80)

print(
    "Detailed results:",
    RESULT_JSONL,
)

print(
    "Summary JSON:",
    SUMMARY_JSON,
)

print(
    "Summary TXT:",
    SUMMARY_TXT,
)
