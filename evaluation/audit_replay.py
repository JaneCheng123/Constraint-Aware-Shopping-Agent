"""Prepare frozen audit states; plan by default, explicitly opt into API replay."""

import argparse
import copy
import hashlib
import json
import math
import random
import sys
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

from agent.episode_memory import EpisodeMemory
from agent.llm_client import LLMClient, usage_delta
from gates.audit_prompts import AUDIT_VERSIONS, build_audit_prompt, prompt_hash
from gates.constraint_manager import ConstraintManager
from gates.product_gate import ProductGate


def state_hash(value):
    return prompt_hash(json.dumps(value, ensure_ascii=False, sort_keys=True))


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p, zz = successes / total, z * z
    center = (p + zz / (2 * total)) / (1 + zz / total)
    width = z * math.sqrt(p * (1 - p) / total + zz / (4 * total * total)) / (1 + zz / total)
    return [max(0, center - width), min(1, center + width)]


def reconstruct_variant(record, event):
    if event.get("variant_state"):
        return copy.deepcopy(event["variant_state"]), "recorded"
    memory = EpisodeMemory()
    trajectory = record["trajectory"]
    for index, step in enumerate(trajectory):
        if step["step"] >= event["step"]:
            break
        if not step.get("executed", True):
            continue
        after = trajectory[index + 1]["available_actions"] if index + 1 < len(trajectory) else {}
        memory.update(step["action"], step.get("assessment", ""), step.get("observation_after", ""),
                      step["available_actions"], after,
                      lambda text: ProductGate._strip_instruction(record["instruction"], text))
    if memory.current != event["product_id"]:
        raise ValueError("Cannot reconstruct the event's active candidate")
    item = memory.candidate
    result = event["result"]
    return {"option_groups": copy.deepcopy(item["option_groups"]),
            "selected_options": copy.deepcopy(event.get("selected_options", {})),
            "checks": copy.deepcopy(result["post_selection"]),
            "constraint_checks": copy.deepcopy(result["constraint_evidence"]),
            "available_sections": list(item["available_sections"]),
            "seen_sections": list(item["seen_sections"])}, "reconstructed"


def prepare_cases(files, task_ids=None):
    cases = {}
    sources = {str(Path(p).resolve()): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}
    for filename in files:
        for line in Path(filename).read_text(encoding="utf-8-sig").splitlines():
            record = json.loads(line)
            if task_ids and record["task_id"] not in task_ids:
                continue
            for event in record.get("gate_events", []):
                if event["gate"] != "product" or not event["result"].get("llm_audit_used"):
                    continue
                variant, provenance = reconstruct_variant(record, event)
                state = {"instruction": record["instruction"], "schema": record["constraint_schema"],
                         "evidence": event["visible_evidence"], "variant_state": variant}
                case_id = state_hash(dict(state, task_id=record["task_id"], product_id=event["product_id"]))
                case = cases.setdefault(case_id, dict(state, case_id=case_id, task_id=record["task_id"],
                    product_id=event["product_id"], variant_provenance=provenance, sources=[]))
                case["sources"].append({"file": str(Path(filename).resolve()),
                    "configuration": record["configuration"], "step": event["step"], "phase": event["phase"]})
    return {"version": 1, "source_sha256": sources, "cases": list(cases.values())}


def review_draft(bundle):
    # State-specific and blinded: no Gate verdicts, original outcome or arm name.
    return [{"case_id": c["case_id"], "task_id": c["task_id"], "product_id": c["product_id"],
             "instruction": c["instruction"], "visible_evidence": c["evidence"],
             "selected_options": c["variant_state"]["selected_options"],
             "option_groups": c["variant_state"]["option_groups"],
             "acceptable": None, "reviewer": None, "reason": None} for c in bundle["cases"]]


def matching_label(case, labels):
    for label in labels:
        same = label.get("case_id") == case["case_id"]
        # Historical drafts merge evidence states. Join only an exact evidence match.
        if "case_id" not in label:
            same = (str(label.get("task_id")) == case["task_id"] and
                    str(label.get("product_id", "")).casefold() == case["product_id"].casefold() and
                    label.get("visible_evidence") == case["evidence"] and
                    label.get("selected_options", {}) == case["variant_state"]["selected_options"])
        if same and isinstance(label.get("acceptable"), bool) and label.get("reviewer"):
            return label["acceptable"]
    return None


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row["case_id"], row["arm"]), []).append(row)
    report = []
    for (case_id, arm), samples in groups.items():
        counts = Counter(r["decision"] if not r.get("error") else "ERROR" for r in samples)
        successful = len(samples) - counts["ERROR"]
        accepted = counts["ACCEPT"]
        labelled = [r for r in samples if r["acceptable"] is not None and not r.get("error")]
        report.append({"case_id": case_id, "arm": arm, "attempts": len(samples), "counts": dict(counts),
            "acceptance_fraction_valid": accepted / successful if successful else None,
            "acceptance_wilson_95_valid": wilson(accepted, successful),
            "acceptance_fraction_all_attempts": accepted / len(samples),
            "labelled_valid_attempts": len(labelled),
            "false_acceptance_count": sum(r["decision"] == "ACCEPT" and not r["acceptable"] for r in labelled) if labelled else None,
            "false_rejection_count": sum(r["decision"] == "REJECT" and r["acceptable"] for r in labelled) if labelled else None,
            "defer_count": sum(r["decision"] == "INSPECT" for r in labelled) if labelled else None})
    return report


def replay(bundle, arms, repeats, model, output, execute=False, labels=None, client=None, seed=42):
    if repeats < 1 or not arms or len(set(arms)) != len(arms):
        raise ValueError("Positive repeats and distinct arms required")
    if any(a not in AUDIT_VERSIONS for a in arms):
        raise ValueError("Unknown arm")
    if not bundle.get("cases"):
        raise ValueError("Replay requires at least one evidence state")
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new replay directory")
    output.mkdir(parents=True, exist_ok=True)
    prompts = []
    for case in bundle["cases"]:
        for arm in arms:
            prompt = build_audit_prompt(arm, case["instruction"], case["schema"], case["evidence"], case["variant_state"])
            prompts.append({"case_id": case["case_id"], "arm": arm, "prompt": prompt,
                            "sha256": prompt_hash(prompt)})
    manifest = {"run_kind": "audit_api_replay" if execute else "audit_replay_plan",
                "model": model, "seed": seed, "repeats": repeats, "arms": arms,
                "cases_sha256": state_hash(bundle), "labels_sha256": state_hash(labels or []),
                "planned_api_calls": len(prompts) * repeats, "cache_bypassed": True,
                "sampling_unit": "case; repeated calls are not independent shopping tasks",
                "historical_distribution_reproduced": False}
    manifest["python_version"] = sys.version.split()[0]
    manifest["created_utc"] = datetime.now(timezone.utc).isoformat()
    root = Path(__file__).resolve().parents[1]
    manifest["source_sha256"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("evaluation/audit_replay.py", "gates/product_gate.py", "gates/audit_prompts.py",
                     "gates/audit_prompt_versions.json", "agent/llm_client.py")}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output / "prompts.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in prompts), encoding="utf-8")
    if not execute:
        return manifest
    client = client or LLMClient(model=model)
    before = client.snapshot()
    cases = {c["case_id"]: c for c in bundle["cases"]}
    rng, rows = random.Random(seed), []
    for repeat in range(repeats):
        block = list(prompts)
        rng.shuffle(block)
        for p in block:
            c = cases[p["case_id"]]
            gate = ProductGate(constraint_manager=ConstraintManager(client=client), audit_version=p["arm"])
            result = gate._final_audit(c["instruction"], c["schema"], c["evidence"], c["variant_state"], use_cache=False)
            row = dict(result, case_id=c["case_id"], arm=p["arm"], repeat=repeat,
                       acceptable=matching_label(c, labels or []), provider_metadata=client.last_response_metadata)
            rows.append(row)
            with (output / "replay.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("Replay block", repeat + 1, "completed; attempts", len(rows))
    (output / "summary.json").write_text(json.dumps(summarize(rows), indent=2), encoding="utf-8")
    manifest["usage"] = usage_delta(before, client.snapshot())
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("results", nargs="+")
    prepare.add_argument("--task-ids", nargs="+")
    prepare.add_argument("--output", required=True)
    run = sub.add_parser("replay")
    run.add_argument("--cases", required=True)
    run.add_argument("--arms", nargs="+", choices=AUDIT_VERSIONS, default=list(AUDIT_VERSIONS))
    run.add_argument("--repeats", type=int, default=20)
    run.add_argument("--model", default="deepseek-chat")
    run.add_argument("--labels")
    run.add_argument("--output-dir", required=True)
    run.add_argument("--execute", action="store_true", help="Make paid API calls; default only writes a plan")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        bundle = prepare_cases(args.results, args.task_ids)
        path = Path(args.output)
        draft = path.with_suffix(".review.json")
        if path.exists() or draft.exists():
            raise ValueError("Choose new case/draft paths")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        draft.write_text(json.dumps(review_draft(bundle), ensure_ascii=False, indent=2), encoding="utf-8")
        print("Frozen evidence states:", len(bundle["cases"]))
    else:
        bundle = json.loads(Path(args.cases).read_text(encoding="utf-8-sig"))
        labels = json.loads(Path(args.labels).read_text(encoding="utf-8-sig")) if args.labels else []
        manifest = replay(bundle, args.arms, args.repeats, args.model, args.output_dir, args.execute, labels)
        print("Planned API calls:", manifest["planned_api_calls"], "; execution:", args.execute)


if __name__ == "__main__":
    main()
