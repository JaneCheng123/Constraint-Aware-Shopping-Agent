import glob
import json
import statistics


paths = sorted(
    glob.glob(
        "results/product_gate_31/shard_*.jsonl"
    )
)

records = []

for path in paths:
    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:

        for line in f:
            line = line.strip()

            if line:
                records.append(
                    json.loads(line)
                )


# 去重
records = {
    str(r["task_id"]): r
    for r in records
}

records = list(
    records.values()
)


def avg(xs):
    return (
        sum(xs) / len(xs)
        if xs
        else 0.0
    )


def gate_sum(key):
    return sum(
        int(
            r.get(
                "gate_stats",
                {},
            ).get(
                key,
                0,
            )
            or 0
        )
        for r in records
    )


n = len(records)

full = [
    r
    for r in records
    if float(r["reward"]) == 1.0
]

partial = [
    r
    for r in records
    if 0.0 < float(r["reward"]) < 1.0
]

zero = [
    r
    for r in records
    if float(r["reward"]) == 0.0
]

visited = [
    r
    for r in records
    if r.get("target_visited")
]

visited_zero = [
    r
    for r in records
    if (
        r.get("target_visited")
        and float(r["reward"]) == 0.0
    )
]

not_visited_zero = [
    r
    for r in records
    if (
        not r.get("target_visited")
        and float(r["reward"]) == 0.0
    )
]

errors = [
    r
    for r in records
    if r.get("error")
]


summary = {
    "tasks": n,

    "full_success": len(full),
    "partial_success": len(partial),
    "zero": len(zero),

    "reward_gt_0": (
        len(full)
        + len(partial)
    ),

    "average_reward": avg([
        float(r["reward"])
        for r in records
    ]),

    "average_steps": avg([
        int(r["steps"])
        for r in records
    ]),

    "median_steps": (
        statistics.median([
            int(r["steps"])
            for r in records
        ])
        if records
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
        int(r["buy_now_count"])
        for r in records
    ),

    "total_back_to_search": sum(
        int(
            r["back_to_search_count"]
        )
        for r in records
    ),

    "errors": len(errors),

    "gate_stats": {
        "product_gate_calls": gate_sum(
            "product_gate_calls"
        ),

        "product_gate_ready": gate_sum(
            "product_gate_ready"
        ),

        "product_gate_inspects": gate_sum(
            "product_gate_inspects"
        ),

        "product_gate_rejections": gate_sum(
            "product_gate_rejections"
        ),

        "product_feedback_injected_steps": gate_sum(
            "product_feedback_injected_steps"
        ),

        "product_reconsider_calls": gate_sum(
            "product_reconsider_calls"
        ),

        "product_reconsider_changes": gate_sum(
            "product_reconsider_changes"
        ),

        "candidate_evidence_pages_added": gate_sum(
            "candidate_evidence_pages_added"
        ),

        "constraint_extraction_calls": gate_sum(
            "constraint_extraction_calls"
        ),

        "constraint_validation_calls": gate_sum(
            "constraint_validation_calls"
        ),

        "semantic_match_calls": gate_sum(
            "semantic_match_calls"
        ),

        "product_final_audit_calls": gate_sum(
            "product_final_audit_calls"
        ),
    },
}


print()
print("=" * 80)
print("PRODUCT GATE ONLY — 31 INSTRUCTIONS")
print("=" * 80)

print(
    json.dumps(
        summary,
        ensure_ascii=False,
        indent=2,
    )
)


print()
print("=" * 80)
print("FULL SUCCESS")
print("=" * 80)

for r in full:
    print(
        r["task_id"],
        "reward=",
        r["reward"],
        "steps=",
        r["steps"],
    )


print()
print("=" * 80)
print("PARTIAL")
print("=" * 80)

for r in partial:
    print(
        r["task_id"],
        "reward=",
        r["reward"],
        "steps=",
        r["steps"],
    )


print()
print("=" * 80)
print("TARGET VISITED BUT REWARD = 0")
print("=" * 80)

for r in visited_zero:
    print(
        r["task_id"],
        "steps=",
        r["steps"],
        "back_to_search=",
        r["back_to_search_count"],
    )


print()
print("=" * 80)
print("TARGET NEVER VISITED + REWARD = 0")
print("=" * 80)

for r in not_visited_zero:
    print(
        r["task_id"],
        "steps=",
        r["steps"],
    )


if errors:
    print()
    print("=" * 80)
    print("ERRORS")
    print("=" * 80)

    for r in errors:
        print(
            r["task_id"],
            r["error"],
        )


with open(
    "results/product_gate_31/summary.json",
    "w",
    encoding="utf-8",
) as f:

    json.dump(
        summary,
        f,
        ensure_ascii=False,
        indent=2,
    )
