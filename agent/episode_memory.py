"""Task-local visible state shared by all four configurations."""

import re
import json

from gates.hard_constraints import normalize_option


def page_type(actions):
    if actions.get("page_type"):
        return actions["page_type"]
    clicks = {str(x).lower() for x in actions.get("clickables", [])}
    if "buy now" in clicks:
        return "product"
    if actions.get("product_ids"):
        return "results"
    if actions.get("has_search_bar"):
        return "search"
    return "detail"


def product_ids(actions):
    if "product_ids" in actions:
        return {str(x).casefold() for x in actions["product_ids"]}
    if page_type(actions) == "product":
        return set()
    return {str(x).casefold() for x in actions.get("clickables", [])
            if re.fullmatch(r"[a-zA-Z0-9]{10}", str(x)) and re.search(r"\d", str(x))}


class EpisodeMemory:
    def __init__(self):
        self.candidates = {}
        self.current = None
        self.queries = []
        self.history = []

    @property
    def candidate(self):
        return self.candidates.get(self.current)

    def context(self):
        return {"current_candidate": self.current, "queries": self.queries,
                "candidates": {key: {field: item[field] for field in
                    ("seen_sections", "selected_options", "status", "assessment")}
                    for key, item in self.candidates.items()}}

    def inspection_state(self):
        return self.candidate or {}

    def evidence(self):
        return "\n\n".join(f"[{source}]\n{text}" for source, text in (self.candidate or {}).get("pages", {}).items())

    def filter_revisits(self, actions):
        """Optional conservative dedup: only exhausted, fully inspected states.

        Never exclude a product with alternative selectors, a transient error, or
        a semantic REJECT. A change in the known state permits a revisit.
        """
        import copy
        blocked = set()
        for pid in actions.get("product_ids", []):
            item = self.candidates.get(str(pid).upper())
            if not item or item["status"] != "exhausted":
                continue
            if set(item["available_sections"]) - set(item["seen_sections"]):
                continue
            if any(len(values) > 1 for values in item["option_groups"].values()):
                continue
            # An exhausted state can be reopened if its known state changes.
            state = json.dumps({"pages": item["pages"], "options": item["selected_options"],
                                "groups": item["option_groups"]}, sort_keys=True)
            if item.get("exhausted_state") == state:
                blocked.add(str(pid).casefold())
        filtered = copy.deepcopy(actions)
        filtered["product_ids"] = [p for p in actions.get("product_ids", []) if str(p).casefold() not in blocked]
        filtered["clickables"] = [p for p in actions.get("clickables", []) if str(p).casefold() not in blocked]
        filtered["blocked_revisits"] = sorted(blocked)
        return filtered

    def update(self, action, assessment, observation, before, after, cleaner):
        candidate_before = self.candidate
        if candidate_before is not None:
            candidate_before["assessment"] = assessment
        if action.startswith("search["):
            self.queries.append(" ".join(action[7:-1].casefold().split()))
            self.current = None
        else:
            value = action[6:-1]
            lowered = value.casefold()
            if lowered == "back to search" or (lowered == "< prev" and page_type(before) == "product"):
                self.current = None
            elif lowered in product_ids(before):
                self.current = value.upper()
                self.candidates.setdefault(self.current, {
                    "pages": {}, "seen_sections": [], "selected_options": {},
                    "available_sections": [], "product_observation": "", "option_groups": {},
                    "status": "active", "assessment": "",
                })
            elif candidate_before is not None and page_type(before) == "product":
                for group, values in before.get("option_groups", {}).items():
                    if normalize_option(value) in {normalize_option(x) for x in values}:
                        candidate_before["selected_options"][group] = value
            if self.candidate is not None:
                candidate = self.candidate
                if lowered in {"features", "description", "reviews"}:
                    if lowered not in candidate["seen_sections"]:
                        candidate["seen_sections"].append(lowered)
                    candidate["pages"][lowered] = cleaner(observation)
                if page_type(after) == "product":
                    candidate["product_observation"] = cleaner(observation)
                    candidate["pages"]["product"] = candidate["product_observation"]
                    candidate["available_sections"] = [x for x in ("features", "description", "reviews")
                                                        if x in {str(c).lower() for c in after.get("clickables", [])}]
                    # Use directly visible selections when the adapter supplies them.
                    if "selected_options" in after:
                        candidate["selected_options"] = dict(after["selected_options"])
                    candidate["option_groups"] = dict(after.get("option_groups", {}))
                if lowered == "buy now":
                    candidate["status"] = "purchased"
        # The policy never receives reward or target identifiers from evaluation.
        self.history.append({"assessment": assessment, "action": action})
