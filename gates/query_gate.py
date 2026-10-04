"""Check and repair every explicit query constraint before executing search."""

import re

from gates.constraint_manager import ConstraintManager
from gates.hard_constraints import parse_price_constraint


class QueryGate:
    def __init__(self, model=None, constraint_manager=None):
        self.constraint_manager = constraint_manager or ConstraintManager(model=model)

    @property
    def extraction_calls(self):
        return self.constraint_manager.extraction_calls

    @property
    def extraction_cache_hits(self):
        return self.constraint_manager.schema_cache_hits

    @staticmethod
    def _clean_query(query):
        query = str(query or "").strip()
        match = re.fullmatch(r"search\[(.*?)\]", query, re.I | re.S)
        return " ".join((match.group(1) if match else query).split())

    def get_constraints(self, instruction):
        return self.constraint_manager.get_constraints(instruction)

    def validate_query(self, instruction, proposed_query):
        query = self._clean_query(proposed_query)
        schema = self.get_constraints(instruction)
        result = {"original_query": query, "revised_query": None, "constraint_coverage": [],
                  "missing_constraints": [], "contradicted_constraints": [], "gate_error": None}
        if schema.get("extraction_error"):
            return dict(result, decision="UNAVAILABLE", reason="No validated constraint schema",
                        gate_error=schema["extraction_error"])
        checks = [("product_type", schema["product_type"])]
        checks += [("required", item) for item in schema["required_constraints"]]
        checks += [("post_selection", item) for item in schema["post_selection_constraints"]]
        for group, constraint in checks:
            matcher = self.constraint_manager.match_product_type if group == "product_type" else self.constraint_manager.match_constraint
            # A selectable requirement can use the exact option value in a query.
            if group == "post_selection":
                constraint = dict(constraint, aliases=list(dict.fromkeys(
                    constraint.get("aliases", []) + [constraint.get("value", constraint["canonical"])])))
            checked = matcher(query, constraint, context="search query " + group)
            label = constraint["canonical"]
            result["constraint_coverage"].append(dict(checked, constraint=label, group=group))
            if checked["status"] != "SUPPORTED":
                key = "contradicted_constraints" if checked["status"] == "CONTRADICTED" else "missing_constraints"
                result[key].append(label)
            if checked.get("error"):
                result["gate_error"] = checked["error"]
        if schema["price_constraint"]:
            expected = parse_price_constraint(schema["price_constraint"])
            actual = parse_price_constraint(query)
            status = "SUPPORTED" if actual == expected and actual is not None else "MISSING" if actual is None else "CONTRADICTED"
            result["constraint_coverage"].append({"constraint": "price", "group": "price", "status": status,
                                                   "match_type": "price_bounds", "reason": "Compare exact price bounds"})
            if status != "SUPPORTED":
                result["missing_constraints" if status == "MISSING" else "contradicted_constraints"].append("price")
        if not query:
            result["missing_constraints"].append("nonempty query")
        if not result["missing_constraints"] and not result["contradicted_constraints"]:
            return dict(result, decision="PASS", reason="All query constraints preserved")
        parts = [schema["product_type"]["canonical"]]
        parts += [item["canonical"] for item in schema["required_constraints"]]
        parts += [item.get("value", item["canonical"]) for item in schema["post_selection_constraints"]]
        if schema["price_constraint"]:
            parts.append(schema["price_constraint"])
        return dict(result, decision="REVISE", reason="Restore missing or contradicted constraints",
                    revised_query=" ".join(dict.fromkeys(x for x in parts if x)))

    def evaluate(self, instruction, proposed_query, history=None):
        result = self.validate_query(instruction, proposed_query)
        previous = [self._clean_query(x.get("action", "")) for x in (history or [])
                    if str(x.get("action", "")).lower().startswith("search[")]
        result["repeated_query"] = self.constraint_manager.normalize_text(proposed_query) in {
            self.constraint_manager.normalize_text(x) for x in previous}
        return result
