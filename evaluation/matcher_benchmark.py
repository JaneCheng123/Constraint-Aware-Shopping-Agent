"""Synthetic query substitutions/omissions; plan by default, never run WebShop."""

import argparse
import copy
import json
from pathlib import Path

from agent.llm_client import LLMClient
from gates.constraint_manager import ConstraintManager
from gates.query_gate import QueryGate
from evaluation.audit_replay import state_hash


def fixtures():
    def constraint(text, kind="attribute"):
        return {"source_text": text, "canonical": text, "aliases": [], "kind": kind, "value": text}
    base = {"product_type": constraint("headphones", "product_type"),
            "required_constraints": [constraint("Acme", "brand")],
            "post_selection_constraints": [constraint("blue", "color")], "price_constraint": "under $10",
            "validation": {"valid": True}, "extraction_error": False}
    instruction = "Find me Acme headphones in blue under $10"
    rows = []
    for name, query, expected in [
        ("preserved", "Acme headphones blue under $10", {"Acme": "SUPPORTED", "blue": "SUPPORTED", "price": "SUPPORTED"}),
        ("brand_substitution", "Beta headphones blue under $10", {"Acme": "CONTRADICTED"}),
        ("brand_omission", "headphones blue under $10", {"Acme": "MISSING"}),
        ("variant_substitution", "Acme headphones red under $10", {"blue": "CONTRADICTED"}),
        ("variant_omission", "Acme headphones under $10", {"blue": "MISSING"}),
        ("price_relaxation", "Acme headphones blue under $20", {"price": "CONTRADICTED"}),
        ("price_omission", "Acme headphones blue", {"price": "MISSING"}),
        ("category_substitution", "Acme camera blue under $10", {"headphones": "CONTRADICTED"})]:
        rows.append({"name": name, "instruction": instruction, "schema": copy.deepcopy(base),
                     "query": query, "expected": expected})
    polarity = copy.deepcopy(base)
    polarity.update(required_constraints=[constraint("cruelty free")], post_selection_constraints=[], price_constraint=None)
    rows.append({"name": "polarity_reversal", "instruction": "Find me cruelty free headphones", "schema": polarity,
                 "query": "headphones not cruelty free", "expected": {"cruelty free": "CONTRADICTED"}})
    return rows


def benchmark(output, execute=False, client=None, repeats=1):
    if repeats < 1:
        raise ValueError("repeats must be positive")
    path = Path(output)
    if path.exists() and any(path.iterdir()):
        raise ValueError("Choose a new matcher output directory")
    path.mkdir(parents=True, exist_ok=True)
    cases = fixtures()
    manifest = {"run_kind": "synthetic_matcher_api" if execute else "synthetic_matcher_plan",
                "fixture_sha256": state_hash(cases), "cases": len(cases), "repeats": repeats,
                "schema_source": "known synthetic requirements; extraction is bypassed",
                "query_mode": "coverage", "cache": "fresh manager per case/repeat",
                "scope": "diagnostic examples, not a representative accuracy estimate"}
    (path / "fixtures.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    if execute:
        client = client or LLMClient()
        rows = []
        for repeat in range(repeats):
            for case in cases:
                manager = ConstraintManager(client=client)
                manager._schema_cache[case["instruction"]] = copy.deepcopy(case["schema"])
                result = QueryGate(constraint_manager=manager).validate_query(case["instruction"], case["query"])
                observed = {x["constraint"]: x["status"] for x in result["constraint_coverage"]}
                row = {"case": case["name"], "repeat": repeat, "result": result,
                       "expected": case["expected"], "observed": observed,
                       "correct": all(observed.get(k) == v for k, v in case["expected"].items()) and not result.get("gate_error")}
                rows.append(row)
                with (path / "results.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row) + "\n")
        manifest["model"] = client.model
        manifest["usage"] = client.snapshot()
        manifest["correct_cases"] = sum(bool(r["correct"]) for r in rows)
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = benchmark(args.output_dir, args.execute, repeats=args.repeats)
    print("Synthetic cases:", result["cases"], "; execution:", args.execute)


if __name__ == "__main__":
    main()
