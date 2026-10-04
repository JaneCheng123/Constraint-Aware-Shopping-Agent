"""Export a review draft; acceptable=null must be filled by an independent reviewer."""

import argparse
import json
from pathlib import Path

from gates.hard_constraints import normalize_option


def export_annotations(result_files, output_path):
    records = {}
    for filename in result_files:
        for line in Path(filename).read_text(encoding="utf-8-sig").splitlines():
            record = json.loads(line)
            events = list(record.get("gate_events", []))
            for step in record.get("trajectory", []):
                action = str(step.get("action") or "")
                if step.get("executed", True) and action.startswith("search["):
                    events.append({"gate": "query", "query": action[7:-1]})
            if record.get("purchased_product"):
                product_id = record["purchased_product"]
                evidence = []
                for step in record.get("trajectory", []):
                    if step.get("active_product_after_action") == product_id and not step.get("done"):
                        evidence.append(step.get("observation_after", ""))
                    if step.get("active_product_before_action") == product_id:
                        evidence.append(step.get("observation_before", ""))
                events.append({"gate": "product", "product_id": record["purchased_product"],
                               "selected_options": record.get("purchased_options", {}),
                               "visible_evidence": "\n\n".join(dict.fromkeys(x for x in evidence if x))})
            for event in events:
                kind = event["gate"]
                field = "query" if kind == "query" else "product_id"
                options = event.get("selected_options", {})
                identity = (record["task_id"], kind, normalize_option(event[field]),
                            json.dumps(options, sort_keys=True) if kind == "product" else None)
                item = records.setdefault(identity, {"task_id": record["task_id"], "kind": kind,
                    field: event[field], "instruction": record["instruction"], "acceptable": None,
                    "constraint_statuses": {}, "reviewer": None})
                if kind == "product":
                    item["selected_options"] = options
                    if event.get("visible_evidence"):
                        item["visible_evidence"] = event["visible_evidence"]
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(list(records.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print("Review records:", export_annotations(args.results, args.output))
    print("Fill acceptable, complete constraint_statuses and reviewer before evaluating.")


if __name__ == "__main__":
    main()
