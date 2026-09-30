import json


def load_tasks(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tasks = []

    # WebShop 原始格式
    if isinstance(data, dict):
        for item_id, item in data.items():

            # 没有 instruction 的 item 跳过
            if not item.get("instruction"):
                continue

            tasks.append({
                "task_id": item_id,
                "instruction": item["instruction"],
                "attributes": item.get("attributes", []),
                "instruction_attributes": item.get(
                    "instruction_attributes", []
                ),
            })

    # 已经是统一 task 格式
    elif isinstance(data, list):
        tasks = data

    else:
        raise ValueError("Unsupported task file format.")

    for task in tasks:
        if "task_id" not in task:
            raise ValueError("Each task must have task_id.")
        if "instruction" not in task:
            raise ValueError("Each task must have instruction.")

    return tasks