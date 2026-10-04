"""Versioned audit prompts. Historical templates are extracted from Git ASTs."""

import hashlib
import json
import re
from pathlib import Path


HISTORICAL = json.loads(Path(__file__).with_name("audit_prompt_versions.json").read_text(encoding="utf-8"))
AUDIT_VERSIONS = tuple(HISTORICAL) + ("grounded",)
GROUNDED = """You are the final semantic auditor for a Product Gate.
All lower-level checks have found support. Your only task is to verify that those
matches are semantically correct and preserve the user's actual requirements.
User instruction: @@instruction@@
Validated constraint schema: @@schema@@
Visible product evidence: @@evidence@@
Current visible variant state and lower-level checks: @@variant_state@@
Use only visible evidence. Do not introduce new acceptance conditions, narrow
the requested category, or demand outside certification, testing, ratings or
reviews unless the instruction explicitly requires them. Explicit listing claims
are evidence of the advertised properties; do not demand independent proof of
performance. Check that each claim concerns this candidate and is not negated.
Broad category lists joined with '&' or 'and' normally name alternative members
of one shopping category, not a demand to buy every listed type together. Require
a bundle only when the instruction explicitly asks for multiple items together.
An accessory can belong to an explicitly requested accessories category when
its visible intended use establishes membership. Do not accept merely related
products or invent category membership.
Checked selectors identify the selected variant, not merely available values.
Generic default-variant titles must not alone invalidate a confirmed selection.
Report explicit conflicts about the selected variant. If the source of a size
or color conflict is unclear, use INSPECT and identify that uncertainty.
Return JSON with decision (ACCEPT, INSPECT, REJECT), problematic_constraints,
suggested_pages (features/description/reviews, or []), and reason.
For ACCEPT, problematic_constraints must be []. For INSPECT or REJECT it must be
a nonempty list of objects: {"constraint": "exact canonical constraint name or
product_type or price", "evidence": "verbatim visible quote, or empty for missing
evidence", "reason": "specific mismatch or missing evidence"}.
REJECT requires an explicit contradictory quote. Uncertainty is INSPECT, not
REJECT. INSPECT must identify the existing constraint and where available
evidence could resolve it; do not recommend revisiting already inspected pages.
"""


def build_audit_prompt(version, instruction, schema, evidence, variant_state=None):
    if version not in AUDIT_VERSIONS:
        raise ValueError("Unknown audit prompt version: " + version)
    template = GROUNDED if version == "grounded" else HISTORICAL[version]["template"]
    values = {"instruction": instruction, "evidence": evidence,
              "schema": json.dumps(schema, ensure_ascii=False),
              "variant_state": json.dumps(variant_state or {}, ensure_ascii=False)}
    # One substitution pass: user text containing a placeholder is never expanded.
    return re.sub(r"@@(instruction|schema|evidence|variant_state)@@", lambda m: values[m[1]], template)


def prompt_hash(prompt):
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def validate_grounded_audit(result, schema, evidence):
    """Validate grounding structure; this is not a substitute for semantic labels."""
    issues = result.get("problematic_constraints", [])
    pages = result.get("suggested_pages", [])
    if not isinstance(issues, list) or not isinstance(pages, list) or any(
            p not in {"features", "description", "reviews"} for p in pages):
        raise ValueError("Invalid audit issues/pages")
    if result["decision"] == "ACCEPT":
        if issues:
            raise ValueError("ACCEPT cannot contain unresolved constraints")
        return
    names = {"product_type", schema["product_type"]["canonical"]}
    if schema.get("price_constraint"):
        names.add("price")
    names.update(c["canonical"] for group in ("required_constraints", "post_selection_constraints")
                 for c in schema[group])
    if not issues:
        raise ValueError("Non-acceptance must identify an existing constraint")
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("constraint") not in names or not issue.get("reason"):
            raise ValueError("Audit introduced an unknown constraint or omitted its reason")
        quote = issue.get("evidence", "")
        if not isinstance(quote, str) or (quote and quote not in evidence):
            raise ValueError("Audit quote is not present in visible evidence")
        if result["decision"] == "REJECT" and not quote.strip():
            raise ValueError("REJECT needs explicit contradictory evidence")
