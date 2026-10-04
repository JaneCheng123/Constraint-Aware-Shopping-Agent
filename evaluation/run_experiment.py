"""Reproducible four-configuration evaluation with explicit annotation coverage."""

import argparse
import hashlib
import json
import random
import subprocess
import sys
from importlib import metadata
from datetime import datetime, timezone
from pathlib import Path

from agent.gated_agent import GatedAgent
from agent.llm_client import LLMClient
from evaluation.metrics import AnnotationStore, compute_summary
from evaluation.task_loader import load_tasks


CONFIGURATIONS = {"baseline": (False, False), "query": (True, False),
                  "product": (False, True), "full": (True, True)}
ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_files():
    paths = []
    for folder in ("agent", "gates", "evaluation", "webshop_wrapper", "demo"):
        paths += list((ROOT / folder).glob("*.py"))
    paths += list((ROOT / "demo").glob("*.json"))
    paths += list((ROOT / "evaluation").glob("*.ps1"))
    paths += list((ROOT / "web_agent_site").rglob("*.py"))
    paths += list((ROOT / "web_agent_site/templates").glob("*.html"))
    paths += [ROOT / "requirements.txt", ROOT / "requirements-agent.txt"]
    return sorted(set(paths))


def manifest_for(args, tasks):
    sources = {str(path.relative_to(ROOT)): file_hash(path) for path in source_files()}
    product_data = {str(path.relative_to(ROOT)): file_hash(path) for path in (ROOT / "data").glob("*.json")}
    index_name = {100: "indexes_100", 1000: "indexes_1k", 100000: "indexes_100k"}.get(args.num_products, "indexes")
    index_dir = ROOT / "search_engine" / index_name
    index_fingerprint = {str(path.relative_to(ROOT)): {"bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
                         for path in index_dir.rglob("*") if path.is_file()}
    versions = {}
    for package in ("openai", "requests", "gym", "beautifulsoup4", "Flask", "numpy", "torch", "pyserini", "spacy", "thefuzz"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    identity = {"dataset_sha256": file_hash(args.dataset),
                "task_ids": [task["task_id"] for task in tasks], "configs": args.configs,
                "model": args.model, "max_steps": args.max_steps, "num_products": args.num_products,
                "repeats": args.repeats, "seed": args.seed, "run_kind": args.run_kind,
                "annotations_sha256": file_hash(args.annotations) if args.annotations else None,
                "source_sha256": sources, "product_data_sha256": product_data,
                "index_fingerprint": index_fingerprint, "python_version": sys.version.split()[0],
                "package_versions": versions}
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip())
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    return {"identity": identity, "git_revision": revision, "working_tree_dirty": dirty,
            "created_utc": datetime.now(timezone.utc).isoformat()}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(ROOT / "evaluation/webshop_test_100.json"))
    parser.add_argument("--configs", nargs="+", choices=CONFIGURATIONS, default=list(CONFIGURATIONS))
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--num-products", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--annotations", help="Independent human labels; missing metrics remain null")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv=None, client_factory=None, env_factory=None, run_kind="live_webshop"):
    args = build_parser().parse_args(argv)
    args.model = args.model or LLMClient().model
    args.run_kind = run_kind
    if args.max_steps < 1 or args.repeats < 1:
        raise ValueError("max-steps and repeats must be positive")
    if len(set(args.configs)) != len(args.configs):
        raise ValueError("Configurations must be unique")
    if client_factory is None and env_factory is None:
        from evaluation.preflight import missing_prerequisites
        missing = missing_prerequisites(args.num_products)
        if missing:
            raise RuntimeError("Live prerequisites missing: " + "; ".join(missing)
                               + ". Run python -m evaluation.preflight; see evaluation/README.md.")
    tasks = load_tasks(args.dataset)
    if not tasks:
        raise ValueError("Dataset contains no instruction-bearing tasks")
    annotations = AnnotationStore.load(args.annotations)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = manifest_for(args, tasks)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not args.resume:
            raise ValueError("Output already contains a run; choose a new directory or --resume")
        if previous["identity"] != manifest["identity"]:
            raise ValueError("Cannot resume with changed tasks, model, budget, annotations or source")
    else:
        if any(output.glob("*/results.jsonl")):
            raise ValueError("Existing results have no manifest; choose a new output directory")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for src in source_files():
            dest = output / "code_snapshot" / src.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
    summaries = {}
    for config in args.configs:
        folder = output / config
        folder.mkdir(exist_ok=True)
        result_path = folder / "results.jsonl"
        completed = {}
        if result_path.exists():
            for line in result_path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                completed[(record["repeat"], str(record["task_id"]))] = record
        query, product = CONFIGURATIONS[config]
        for repeat in range(args.repeats):
            for task in tasks:
                key = (repeat, task["task_id"])
                if key in completed and not completed[key].get("error"):
                    continue
                random.seed(args.seed + repeat)
                client = client_factory() if client_factory else LLMClient(model=args.model)
                agent = GatedAgent(num_products=args.num_products, max_steps=args.max_steps,
                                   model=args.model, client=client, env_factory=env_factory,
                                   use_query_gate=query, use_product_gate=product, verbose=args.verbose)
                record = agent.run(task)
                record.update(repeat=repeat, seed=args.seed + repeat, run_kind=run_kind)
                # Scoring metadata is stored only after the run, never passed to the policy.
                record["evaluation_metadata"] = {key: task[key] for key in
                    ("instruction_attributes", "instruction_options", "goal_options", "price_upper") if key in task}
                with result_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                completed[key] = record
                print(f"{config} repeat={repeat} task={task['task_id']} reward={record['reward']:.3f} "
                      f"success={record['success']} calls={record['usage']['llm_calls']} stop={record['stop_reason']}")
        records = [completed[(repeat, task["task_id"])] for repeat in range(args.repeats) for task in tasks]
        summary = compute_summary(records, annotations)
        summary["per_repeat"] = {str(repeat): compute_summary([r for r in records if r["repeat"] == repeat], annotations)
                                 for repeat in range(args.repeats)}
        summary["run_kind"] = run_kind
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries[config] = summary
    (output / "comparison.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    from demo.render_report import render_report
    render_report(output)
    print("Comparison:", output / "comparison.json")
    print("Replay:", output / "report.html")
    return summaries


if __name__ == "__main__":
    main()
