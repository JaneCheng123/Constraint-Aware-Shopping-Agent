"""Recompute independent annotation metrics from a saved run, without LLM calls."""

import argparse
import json
from pathlib import Path

from evaluation.metrics import AnnotationStore, compute_summary
from evaluation.run_experiment import file_hash
from demo.render_report import render_report


def summarize(output_dir, annotations_path=None):
    output = Path(output_dir)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    labels = AnnotationStore.load(annotations_path)
    comparison = {}
    for config in manifest["identity"]["configs"]:
        latest = {}
        for line in (output / config / "results.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            latest[(record["repeat"], record["task_id"])] = record
        records = list(latest.values())
        summary = compute_summary(records, labels)
        summary["expected_tasks"] = len(manifest["identity"]["task_ids"]) * manifest["identity"]["repeats"]
        summary["incomplete_tasks"] = summary["expected_tasks"] - len(records)
        summary["per_repeat"] = {str(repeat): compute_summary([r for r in records if r["repeat"] == repeat], labels)
                                 for repeat in range(manifest["identity"]["repeats"])}
        summary["run_kind"] = manifest["identity"]["run_kind"]
        summary["annotations_sha256"] = file_hash(annotations_path) if annotations_path else None
        (output / config / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        comparison[config] = summary
    (output / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    render_report(output)
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir")
    parser.add_argument("--annotations")
    args = parser.parse_args()
    print(json.dumps(summarize(args.output_dir, args.annotations), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
