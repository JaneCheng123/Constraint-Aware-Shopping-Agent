"""Outcome metrics and independent, human-labelled constraint/gate evaluation."""

import json
from collections import Counter

from gates.hard_constraints import normalize_option


def full_success(reward, done=True):
    return bool(done and float(reward) > 0.999999)


def _options(options):
    return {normalize_option(key): normalize_option(value) for key, value in options.items()}


class AnnotationStore:
    """Labels must come from a reviewer independent of the gate being evaluated."""
    def __init__(self, records=None):
        self.records = records or []
        self._validate()

    @classmethod
    def load(cls, path):
        if path is None:
            return cls()
        with open(path, encoding="utf-8-sig") as stream:
            return cls(json.load(stream))

    def _validate(self):
        seen = set()
        for record in self.records:
            key = (str(record["task_id"]), record["kind"],
                   normalize_option(record.get("query", record.get("product_id", ""))),
                   json.dumps(_options(record["selected_options"]), sort_keys=True)
                   if "selected_options" in record else None)
            if record["kind"] not in {"query", "product"} or key in seen:
                raise ValueError("Annotation kind must be query/product and keys must be unique")
            if not isinstance(record.get("acceptable"), bool):
                raise ValueError("Every annotation needs an independent boolean acceptable label")
            statuses = record.get("constraint_statuses", {})
            if not isinstance(statuses, dict) or any(value not in {"SUPPORTED", "MISSING", "CONTRADICTED"}
                                                     for value in statuses.values()):
                raise ValueError("constraint_statuses must map names to SUPPORTED/MISSING/CONTRADICTED")
            seen.add(key)

    def find(self, task_id, kind, value, selected_options=None):
        matches = [record for record in self.records if str(record["task_id"]) == str(task_id)
                   and record["kind"] == kind
                   and normalize_option(record.get("query" if kind == "query" else "product_id", "")) == normalize_option(value)]
        # Prefer an explicit variant label. Wildcards are suitable only for facts
        # (e.g. wrong category) that do not depend on selected options.
        for record in matches:
            if "selected_options" in record and _options(record["selected_options"]) == _options(selected_options or {}):
                return record
        return next((record for record in matches if "selected_options" not in record), None)


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def compute_summary(records, annotations=None):
    labels = annotations or AnnotationStore()
    totals = Counter()
    confusion = {gate: Counter() for gate in ("query", "product")}
    usage = Counter()
    failures = Counter()
    for record in records:
        totals["tasks"] += 1
        totals["errors"] += bool(record.get("error"))
        totals["full_success"] += full_success(record.get("reward", 0), record.get("done", False)) and not record.get("error")
        totals["partial_success"] += bool(record.get("done") and 0 < record.get("reward", 0) <= 0.999999)
        totals["reward_sum"] += float(record.get("reward", 0))
        totals["steps_sum"] += record.get("steps", 0)
        totals["seconds_sum"] += record.get("elapsed_seconds", 0)
        for key, value in record.get("usage", {}).get("calls_by_purpose", {}).items():
            usage[key] += value
        totals["llm_calls"] += record.get("usage", {}).get("llm_calls", 0)
        totals["llm_errors"] += record.get("usage", {}).get("llm_errors", 0)
        failures[record.get("stop_reason", "unknown")] += 1
        for step in record.get("trajectory", []):
            if step.get("executed", True) and str(step.get("action", "")).startswith("search["):
                totals["queries"] += 1
                label = labels.find(record["task_id"], "query", step["action"][7:-1])
                if label:
                    totals["labelled_queries"] += 1
                    statuses = label.get("constraint_statuses", {})
                    totals["query_constraints"] += len(statuses)
                    totals["query_constraints_supported"] += sum(x == "SUPPORTED" for x in statuses.values())
        if record.get("purchased_product"):
            totals["purchases"] += 1
            label = labels.find(record["task_id"], "product", record["purchased_product"], record.get("purchased_options", {}))
            if label:
                totals["labelled_purchases"] += 1
                totals["satisfied_purchases"] += label["acceptable"]
                statuses = label.get("constraint_statuses", {})
                totals["product_constraints"] += len(statuses)
                totals["product_constraints_supported"] += sum(x == "SUPPORTED" for x in statuses.values())
        seen_events = set()
        for event in record.get("gate_events", []):
            gate, decision = event["gate"], event["result"]["decision"]
            identity = (gate, event.get("query", event.get("product_id")),
                        json.dumps(_options(event.get("selected_options", {})), sort_keys=True),
                        event.get("visible_evidence", ""), decision)
            if identity in seen_events:
                continue
            seen_events.add(identity)
            counts = confusion[gate]
            counts["events"] += 1
            label = labels.find(record["task_id"], gate, event.get("query", event.get("product_id")), event.get("selected_options", {}))
            if not label:
                counts["unlabelled"] += 1
                continue
            counts["labelled"] += 1
            if decision not in {"PASS", "READY", "REVISE", "REJECT", "EXHAUSTED"}:
                counts["deferred"] += 1
                continue
            accepted = decision in {"PASS", "READY"}
            counts["true_accept" if accepted and label["acceptable"] else
                   "false_accept" if accepted else "false_reject" if label["acceptable"] else "true_reject"] += 1
    gates = {}
    for gate, counts in confusion.items():
        gates[gate] = {key: counts[key] for key in ("events", "labelled", "unlabelled", "deferred",
                                                   "true_accept", "false_accept", "true_reject", "false_reject")}
        gates[gate].update(
            false_acceptance_rate=_ratio(counts["false_accept"], counts["false_accept"] + counts["true_reject"]),
            false_rejection_rate=_ratio(counts["false_reject"], counts["false_reject"] + counts["true_accept"]))
    n = totals["tasks"]
    return {"tasks": n, "errors": totals["errors"], "full_success": totals["full_success"],
            "partial_success": totals["partial_success"],
            "task_success_rate": _ratio(totals["full_success"], n),
            "average_reward": _ratio(totals["reward_sum"], n),
            "average_steps": _ratio(totals["steps_sum"], n),
            "average_llm_calls": _ratio(totals["llm_calls"], n),
            "llm_calls": totals["llm_calls"], "llm_errors": totals["llm_errors"],
            "calls_by_purpose": dict(usage), "average_seconds": _ratio(totals["seconds_sum"], n),
            "query_constraint_coverage": _ratio(totals["query_constraints_supported"], totals["query_constraints"]),
            "query_label_coverage": _ratio(totals["labelled_queries"], totals["queries"]),
            "query_constraint_count": totals["query_constraints"],
            "product_constraint_satisfaction": _ratio(totals["product_constraints_supported"], totals["product_constraints"]),
            "purchase_satisfaction_rate": _ratio(totals["satisfied_purchases"], totals["labelled_purchases"]),
            "purchase_label_coverage": _ratio(totals["labelled_purchases"], totals["purchases"]),
            "product_constraint_count": totals["product_constraints"],
            "gate_metrics": gates, "stop_reasons": dict(failures)}
