"""Verify accumulated visible evidence; READY is required before a gated purchase."""

import json
import re

from gates.constraint_manager import ConstraintManager
from gates.hard_constraints import check_option, check_price, normalize_option
from gates.audit_prompts import AUDIT_VERSIONS, build_audit_prompt, validate_grounded_audit, prompt_hash


class ProductGate:
    def __init__(self, model=None, constraint_manager=None, audit_version="grounded", variant_review=False):
        if audit_version not in AUDIT_VERSIONS:
            raise ValueError("Unknown audit version")
        if variant_review and audit_version != "grounded":
            raise ValueError("H1 requires the grounded audit's structured constraint issues")
        self.audit_version, self.variant_review = audit_version, variant_review
        self.constraint_manager = constraint_manager or ConstraintManager(model=model)
        self.client = self.constraint_manager.client
        self.model = model or self.constraint_manager.model
        self.rule_checks = self.semantic_fallback_calls = self.semantic_calls = 0
        self.semantic_errors = self.final_audit_calls = self.final_audit_errors = 0
        self._audit_cache = {}
        self._variant_review_cache = {}

    @staticmethod
    def _strip_instruction(instruction, observation):
        parts = str(observation or "").split("[SEP]")
        result, skip_next = [], False
        target = ConstraintManager.normalize_text(instruction)
        for part in parts:
            norm = ConstraintManager.normalize_text(part)
            if norm == "instruction":
                skip_next = True
                continue
            if norm == target or (norm.startswith("instruction ") and norm[12:] == target):
                skip_next = False
                continue
            if skip_next:
                skip_next = False
            if part.strip():
                result.append(part.strip())
        return " [SEP] ".join(result)

    @staticmethod
    def _clickables(actions):
        return [str(x) for x in (actions or {}).get("clickables", [])]

    @staticmethod
    def _preferred_text(constraint):
        return constraint.get("canonical", constraint.get("source_text", ""))

    _check_price = staticmethod(check_price)

    def _resolve(self, evidence, constraint, category=False):
        kind = constraint.get("kind", "attribute")
        # Typed hard fields never fall back to an LLM inventing a value.
        if kind in {"brand", "color", "size"} and not category:
            value = constraint.get("value", self._preferred_text(constraint))
            label = re.search(r"\b" + re.escape(kind) + r"\s*:\s*([^;\n]|\[(?!SEP\]))+", evidence, re.I)
            if label:
                actual = label.group().split(":", 1)[1].strip().split("[SEP]")[0].strip()
                status = "SUPPORTED" if normalize_option(actual) == normalize_option(value) else "CONTRADICTED"
            else:
                matched = ConstraintManager.exact_match(evidence, {"canonical": value})[0]
                negated = re.search(r"\b(?:not|no|without)\s+" + re.escape(str(value)) + r"\b", evidence, re.I)
                status = "SUPPORTED" if matched and not negated else "MISSING"
            return {"status": status, "match_type": "hard_field", "matched_by": value,
                    "reason": "Check explicit visible field/value", "error": None}
        matcher = self.constraint_manager.match_product_type if category else self.constraint_manager.match_constraint
        result = matcher(evidence, constraint, use_semantic=True, context="visible product evidence")
        if result["match_type"].startswith("semantic") or "semantic" in result["match_type"]:
            self.semantic_fallback_calls += 1
            self.semantic_calls += 1
        if result.get("error"):
            self.semantic_errors += 1
        return result

    def _resolve_product_type(self, evidence, constraint):
        return self._resolve(evidence, constraint, category=True)

    def _resolve_constraint(self, evidence, constraint, context="visible evidence"):
        return self._resolve(evidence, constraint)

    def _final_audit(self, instruction, schema, evidence, variant_state=None, use_cache=True):
        variant_state = variant_state or {}
        key = (self.audit_version, instruction, json.dumps(schema, sort_keys=True), evidence,
               json.dumps(variant_state, sort_keys=True))
        if use_cache and key in self._audit_cache:
            return dict(self._audit_cache[key])
        prompt = build_audit_prompt(self.audit_version, instruction, schema, evidence, variant_state)
        raw = None
        try:
            self.final_audit_calls += 1
            self.semantic_calls += 1
            raw = self.client.complete(prompt, purpose="product_final_audit", model=self.model)
            parsed = ConstraintManager._extract_json(raw)
            if not isinstance(parsed, dict) or parsed.get("decision") not in {"ACCEPT", "INSPECT", "REJECT"}:
                raise ValueError("Invalid final audit response")
            if self.audit_version == "grounded":
                validate_grounded_audit(parsed, schema, evidence)
            result = dict(parsed, error=None, raw_output=raw, prompt_sha256=prompt_hash(prompt))
        except Exception as exc:
            self.final_audit_errors += 1
            result = {"decision": "INSPECT", "problematic_constraints": [],
                      "reason": "Final audit unavailable; purchase remains blocked", "error": str(exc),
                      "raw_output": raw, "prompt_sha256": prompt_hash(prompt)}
        result["audit_version"] = self.audit_version
        # A transient transport/format failure must not become a permanent verdict.
        if use_cache and not result.get("error"):
            self._audit_cache[key] = dict(result)
        return result

    def _review_variant_conflict(self, instruction, schema, audit, evidence, state):
        """H1: one targeted review, retaining an explicit UNKNOWN outcome."""
        selected = [x for x in state["checks"] if x.get("selected") and x.get("status") == "SUPPORTED"]
        issues = audit.get("problematic_constraints", [])
        names = {x["constraint"] for x in selected}
        if (audit.get("decision") != "REJECT" or not selected or not issues or
                any(not isinstance(x, dict) or x.get("constraint") not in names for x in issues)):
            return audit
        prompt = f"""Resolve only the reported selected-variant conflict.
Visible evidence: {evidence}
Visible selected state: {json.dumps(state, ensure_ascii=False)}
Reported issues: {json.dumps(issues, ensure_ascii=False)}
Return JSON: status (EXPLICIT_CONFLICT, GENERIC_DEFAULT, UNKNOWN), evidence
(verbatim visible quote), reason. EXPLICIT_CONFLICT requires a quote explicitly
about the selected variant. GENERIC_DEFAULT requires evidence that the conflicting
text describes a default variant. Absence of a conflict is not proof of that.
Use UNKNOWN when provenance cannot be established. Do not invent evidence.
"""
        cache_key = prompt_hash(prompt)
        try:
            if cache_key in self._variant_review_cache:
                review = self._variant_review_cache[cache_key]
            else:
                review = ConstraintManager._extract_json(self.client.complete(
                    prompt, purpose="variant_conflict_review", model=self.model))
            status = review.get("status")
            quote = review.get("evidence", "")
            if status not in {"EXPLICIT_CONFLICT", "GENERIC_DEFAULT", "UNKNOWN"}:
                raise ValueError("Invalid variant review status")
            if status != "UNKNOWN" and (not isinstance(quote, str) or not quote.strip() or quote not in evidence):
                raise ValueError("Variant review needs a visible provenance quote")
            self._variant_review_cache[cache_key] = review
        except Exception as exc:
            review = {"status": "UNKNOWN", "reason": str(exc), "error": str(exc)}
        result = dict(audit, variant_review=review)
        if review["status"] == "EXPLICIT_CONFLICT":
            return result
        if review["status"] == "GENERIC_DEFAULT":
            repeated = self._final_audit(instruction, schema, evidence,
                                        dict(state, provenance_review=review))
            return dict(repeated, variant_review=review)
        # Even GENERIC_DEFAULT does not grant acceptance; a fresh full audit must
        # assess the other constraints. H1 never overrides a rejection to READY.
        result.update(decision="INSPECT", reason="Selected-variant provenance needs resolution")
        return result

    def _recommend_action(self, decision, options, actions, inspection, suggested_pages=None):
        clickables = {str(x).lower(): str(x) for x in self._clickables(actions)}
        on_product = "buy now" in clickables
        if decision in {"REJECT", "EXHAUSTED", "UNAVAILABLE"}:
            name = "back to search" if "back to search" in clickables else "< prev" if "< prev" in clickables else None
            return f"click[{clickables[name]}]" if name else None
        if not on_product and "< prev" in clickables:
            return f"click[{clickables['< prev']}]"
        for option in options:
            if not option["selected"] and option["available"]:
                return f"click[{option['available_action']}]"
        if decision == "READY" and on_product:
            return f"click[{clickables['buy now']}]"
        seen = set((inspection or {}).get("seen_sections", []))
        for section in list(dict.fromkeys((suggested_pages or []) + ["features", "description", "reviews"])):
            if section in clickables and section not in seen:
                return f"click[{clickables[section]}]"
        return None

    def evaluate(self, instruction, observation, history=None, available_actions=None, inspection_state=None):
        self.rule_checks += 1
        actions, inspection = available_actions or {}, inspection_state or {}
        schema = self.constraint_manager.get_constraints(instruction)
        evidence = self._strip_instruction(instruction, observation)
        base = {"ready_to_buy": False, "should_keep_candidate": False, "rule_passed": False,
                "matched_constraints": [], "missing_constraints": [], "contradicted_constraints": [],
                "constraint_evidence": [], "post_selection": [], "price_evidence": {},
                "llm_audit_used": False, "gate_error": None}
        if schema.get("extraction_error"):
            return dict(base, decision="UNAVAILABLE", reason="No validated constraint schema",
                        gate_error=schema["extraction_error"], recommended_action=None)
        # No early category return: check every hard requirement before any decision.
        category = self._resolve_product_type(evidence, schema["product_type"])
        entries = [dict(category, constraint="product_type")]
        groups = actions.get("option_groups") or inspection.get("option_groups", {})
        selected = inspection.get("selected_options", {})
        options = []
        for item in schema["required_constraints"]:
            entries.append(dict(self._resolve_constraint(evidence, item), constraint=self._preferred_text(item)))
            # Extraction may classify a selectable value as an ordinary attribute.
            # Bind only this candidate's selectors; keep the shared schema intact.
            option = check_option(item, groups, selected)
            typed_selector = any(normalize_option(group) == normalize_option(item.get("kind")) for group in groups)
            if option["available"] or typed_selector:
                matches = option["matching_groups"]
                if len(matches) == 1:
                    group = matches[0]
                    option = check_option(item, {group: groups[group]}, selected)
                    option["bound_group"] = group
                elif len(matches) > 1:
                    # An ambiguous group is unresolved, not a guessed click.
                    option.update(status="MISSING", selected=False, available=False, available_action=None)
                option.update(satisfied=option["selected"], source="required")
                options.append(option)
        for item in schema["post_selection_constraints"]:
            option = check_option(item, groups, selected)
            option["satisfied"] = option["selected"]
            # A variant with an actual selector must be selected. A fixed
            # property (e.g. rose-gold tins with only size selectors) is instead
            # verified in visible evidence; don't demand a nonexistent click.
            if not any(normalize_option(group) == normalize_option(item.get("kind")) for group in groups):
                fixed = self._resolve_constraint(evidence, item)
                option["fixed_property_evidence"] = fixed
                option["satisfied"] = fixed["status"] == "SUPPORTED"
                option["status"] = fixed["status"]
            options.append(option)
        price_text = inspection.get("product_observation", evidence)
        price = check_price(schema.get("price_constraint"), price_text)
        if price["status"] != "NOT_REQUIRED":
            entries.append(dict(price, constraint="price", match_type="price_bounds"))
        matched = [x["constraint"] for x in entries if x["status"] == "SUPPORTED"]
        missing = [x["constraint"] for x in entries if x["status"] == "MISSING"]
        missing += [x["constraint"] for x in options if x["status"] == "MISSING"]
        contradicted = [x["constraint"] for x in entries if x["status"] == "CONTRADICTED"]
        contradicted += [x["constraint"] for x in options if x["status"] == "CONTRADICTED"]
        errors = [x.get("error") for x in entries if x.get("error")]
        errors += [x["fixed_property_evidence"]["error"] for x in options
                   if x.get("fixed_property_evidence", {}).get("error")]
        audit = None
        if contradicted:
            decision, reason = "REJECT", "Confirmed conflict: " + ", ".join(contradicted)
        elif missing:
            available_sections = set(inspection.get("available_sections", []))
            seen_sections = set(inspection.get("seen_sections", []))
            can_select = any(x["available"] and not x["selected"] for x in options)
            exhausted = bool(inspection_state is not None) and not (available_sections - seen_sections) and not can_select
            decision = "EXHAUSTED" if exhausted else "INSPECT"
            reason = "Unresolved requirements: " + ", ".join(missing)
        else:
            variant_state = {"option_groups": groups, "selected_options": selected, "checks": options,
                             "constraint_checks": entries,
                             "available_sections": inspection.get("available_sections", []),
                             "seen_sections": inspection.get("seen_sections", [])}
            audit = self._final_audit(instruction, schema, evidence, variant_state)
            if self.variant_review and not audit.get("error"):
                audit = self._review_variant_conflict(instruction, schema, audit, evidence, variant_state)
            decision = {"ACCEPT": "READY", "REJECT": "REJECT", "INSPECT": "INSPECT"}[audit["decision"]]
            reason = audit.get("reason", "")
            if audit.get("error"):
                errors.append(audit["error"])
                decision = "UNAVAILABLE"
            elif decision == "INSPECT" and inspection_state is not None and not (
                set(inspection.get("available_sections", [])) - set(inspection.get("seen_sections", []))
            ):
                decision = "EXHAUSTED"
            if decision == "REJECT":
                contradicted += [x.get("constraint") if isinstance(x, dict) else x
                                 for x in audit.get("problematic_constraints", [])]
        return dict(base, decision=decision, ready_to_buy=decision == "READY",
                    should_keep_candidate=decision in {"READY", "INSPECT"}, rule_passed=not missing and not contradicted,
                    matched_constraints=matched, missing_constraints=missing, contradicted_constraints=contradicted,
                    constraint_evidence=entries, product_type_evidence=category, post_selection=options,
                    price_evidence=price, llm_audit_used=audit is not None, llm_audit=audit,
                    gate_error=errors or None, reason=reason,
                    variant_state={"option_groups": groups, "selected_options": selected, "checks": options,
                                   "constraint_checks": entries,
                                   "available_sections": inspection.get("available_sections", []),
                                   "seen_sections": inspection.get("seen_sections", [])},
                    recommended_action=self._recommend_action(decision, options, actions, inspection,
                                                              (audit or {}).get("suggested_pages", [])))
