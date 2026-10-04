"""Export/load shared extraction outcomes without turning failures into successes."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from gates.constraint_manager import ConstraintManager


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_frozen(path, tasks):
    bundle = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if bundle.get("version") != 1 or not bundle.get("provenance"):
        raise ValueError("Frozen schema bundle needs version=1 and provenance")
    rows = bundle["tasks"]
    for task in tasks:
        row = rows.get(str(task["task_id"]))
        if not row or row["instruction"] != task["instruction"]:
            raise ValueError("Frozen schema task/instruction mismatch: " + task["task_id"])
        schema = row["schema"]
        if not schema.get("extraction_error"):
            if schema.get("validation", {}).get("valid") is not True:
                raise ValueError("A usable frozen schema must have passed validation")
            if ConstraintManager._deterministic_validation_issues(task["instruction"], schema):
                raise ValueError("Frozen schema failed source anchoring")
    return copy.deepcopy(bundle)


def export_frozen(results, destination):
    rows = {}
    preparation_calls = 0
    for line in Path(results).read_text(encoding="utf-8-sig").splitlines():
        record = json.loads(line)
        schema = record.get("constraint_schema")
        if schema is None:
            raise ValueError("Source must be a gated extraction run, not baseline")
        key = str(record["task_id"])
        if key in rows:
            raise ValueError("Select one shared extraction outcome per task; repeats are not interchangeable")
        rows[key] = {"instruction": record["instruction"], "schema": schema}
        preparation_calls += sum(record.get("usage", {}).get("calls_by_purpose", {}).get(k, 0)
                                 for k in ("extract_from_instruction", "validate_extraction"))
    bundle = {"version": 1, "provenance": {"kind": "shared_extraction",
              "source_file": str(Path(results).resolve()), "source_sha256": digest(results),
              "shared_preparation_calls": preparation_calls,
              "cost_note": "Preparation cost is separate from per-episode frozen-schema usage"}, "tasks": rows}
    path = Path(destination)
    if path.exists():
        raise ValueError("Choose a new frozen schema output file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    export_frozen(args.results, args.output)


if __name__ == "__main__":
    main()
