import json
import copy


def load_tasks(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    tasks = []

    # WebShop 原始格式
    if isinstance(data, dict):
        for item_id, item in data.items():

            # 没有 instruction 的 item 跳过
            if not isinstance(item, dict) or not item.get("instruction"):
                continue

            task = copy.deepcopy(item)
            task["task_id"] = str(item_id)
            task.setdefault("instruction_attributes", item.get("attributes", []))
            tasks.append(task)

    # 已经是统一 task 格式
    elif isinstance(data, list):
        tasks = [copy.deepcopy(item) for item in data
                 if isinstance(item, dict) and str(item.get("instruction", "")).strip()]

    else:
        raise ValueError("Unsupported task file format.")

    for task in tasks:
        if "task_id" not in task:
            raise ValueError("Each task must have task_id.")
        if "instruction" not in task:
            raise ValueError("Each task must have instruction.")
        if not isinstance(task["instruction"], str) or not task["instruction"].strip():
            raise ValueError("Each instruction must be nonempty text.")
        task["task_id"] = str(task["task_id"])
    identifiers = [task["task_id"] for task in tasks]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Task IDs must be unique.")

    return tasks
