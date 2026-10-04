"""No-network regressions for controlled interventions and state-level replay."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent.gated_agent import GatedAgent
from agent.episode_memory import EpisodeMemory
from demo.offline import ScriptedClient, DemoEnvironment, FIXTURES
from evaluation.audit_replay import prepare_cases, matching_label, review_draft, replay, wilson
from evaluation.frozen_schemas import export_frozen, load_frozen
from evaluation.matcher_benchmark import benchmark
from gates.audit_prompts import build_audit_prompt
from gates.constraint_manager import ConstraintManager
from gates.product_gate import ProductGate
from gates.query_gate import QueryGate
from test_cz_v1 import QueueClient, TASK


def audit(decision="ACCEPT", name="color: blue", quote="", reason="Specific variant mismatch"):
    return json.dumps({"decision": decision, "problematic_constraints": [] if decision == "ACCEPT" else [
        {"constraint": name, "evidence": quote, "reason": reason}], "suggested_pages": [], "reason": reason})


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.schema = copy.deepcopy(FIXTURES[0]["schema"])
        self.option_name = self.schema["post_selection_constraints"][0]["canonical"]

    def gate(self, *responses):
        client = QueueClient(*responses)
        return ProductGate(constraint_manager=ConstraintManager(client=client)), client

    def test_cache_bypass_gets_two_actual_responses(self):
        gate, client = self.gate(audit(), audit("INSPECT", self.option_name))
        first = gate._final_audit(TASK["instruction"], self.schema, "Visible", use_cache=False)
        second = gate._final_audit(TASK["instruction"], self.schema, "Visible", use_cache=False)
        self.assertEqual((first["decision"], second["decision"]), ("ACCEPT", "INSPECT"))
        self.assertEqual(client.snapshot()["llm_calls"], 2)

    def test_transient_failure_does_not_poison_cache(self):
        gate, client = self.gate(RuntimeError("Temporary outage"), audit())
        self.assertTrue(gate._final_audit(TASK["instruction"], self.schema, "Visible")["error"])
        self.assertEqual(gate._final_audit(TASK["instruction"], self.schema, "Visible")["decision"], "ACCEPT")
        self.assertEqual(client.snapshot()["llm_calls"], 2)

    def test_unknown_condition_cannot_be_used_to_reject(self):
        gate, _ = self.gate(audit("REJECT", "must have certification", "Visible"))
        result = gate._final_audit(TASK["instruction"], self.schema, "Visible")
        self.assertIsNotNone(result["error"])
        self.assertNotEqual(result["decision"], "ACCEPT")

    def test_invented_quote_is_blocked(self):
        gate, _ = self.gate(audit("REJECT", self.option_name, "Not actually visible"))
        self.assertTrue(gate._final_audit(TASK["instruction"], self.schema, "Visible")["error"])

    def test_uncertainty_cannot_be_reject_without_a_quote(self):
        gate, _ = self.gate(audit("REJECT", self.option_name))
        self.assertTrue(gate._final_audit(TASK["instruction"], self.schema, "Visible")["error"])

    def test_historical_templates_keep_distinct_contexts(self):
        state = {"selected_options": {"color": "blue"}}
        old = build_audit_prompt("legacy", TASK["instruction"], self.schema, "Evidence", state)
        benchmark = build_audit_prompt("benchmark", TASK["instruction"], self.schema, "Evidence", state)
        current = build_audit_prompt("variant", TASK["instruction"], self.schema, "Evidence", state)
        self.assertIn("All lower-level constraint checks have already found support", old)
        self.assertNotIn("selected_options", benchmark)
        self.assertIn("selected_options", current)

    def test_user_placeholder_text_is_not_interpreted(self):
        p = build_audit_prompt("benchmark", "literal @@schema@@", self.schema, "Evidence")
        self.assertIn("literal @@schema@@", p)

    def test_h1_unknown_never_grants_acceptance(self):
        gate, _ = self.gate(json.dumps({"status": "UNKNOWN", "reason": "Ambiguous source"}))
        selected = {"checks": [{"constraint": self.option_name, "selected": True, "status": "SUPPORTED"}]}
        rejected = json.loads(audit("REJECT", self.option_name, "Conflict"))
        result = gate._review_variant_conflict(TASK["instruction"], self.schema, rejected, "Conflict", selected)
        self.assertEqual(result["decision"], "INSPECT")

    def test_h1_explicit_selected_conflict_stays_rejected(self):
        gate, _ = self.gate(json.dumps({"status": "EXPLICIT_CONFLICT", "evidence": "Selected blue is red"}))
        state = {"checks": [{"constraint": self.option_name, "selected": True, "status": "SUPPORTED"}]}
        result = gate._review_variant_conflict(TASK["instruction"], self.schema,
            json.loads(audit("REJECT", self.option_name, "Selected blue is red")), "Selected blue is red", state)
        self.assertEqual(result["decision"], "REJECT")

    def test_h1_does_not_relax_a_category_conflict(self):
        gate, client = self.gate()
        state = {"checks": [{"constraint": self.option_name, "selected": True, "status": "SUPPORTED"}]}
        rejected = json.loads(audit("REJECT", "product_type", "Camera"))
        self.assertEqual(gate._review_variant_conflict(TASK["instruction"], self.schema, rejected, "Camera", state), rejected)
        self.assertEqual(client.snapshot()["llm_calls"], 0)

    def test_h1_default_provenance_does_not_override_another_constraint(self):
        name = self.schema["required_constraints"][0]["canonical"]
        evidence = "Default variant red; Contains BPA"
        gate, client = self.gate(json.dumps({"status": "GENERIC_DEFAULT", "evidence": "Default variant red"}),
                                 audit("REJECT", name, "Contains BPA"))
        state = {"checks": [{"constraint": self.option_name, "selected": True, "status": "SUPPORTED"}]}
        result = gate._review_variant_conflict(TASK["instruction"], self.schema,
            json.loads(audit("REJECT", self.option_name, "Default variant red")), evidence, state)
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(client.snapshot()["llm_calls"], 2)


class QueryModeTests(unittest.TestCase):
    def gate(self, mode):
        manager = ConstraintManager(client=ScriptedClient())
        manager.get_constraints = lambda _: copy.deepcopy(FIXTURES[0]["schema"])
        return QueryGate(constraint_manager=manager, mode=mode)

    def test_missing_only_repair_preserves_original_search_strategy(self):
        query = "precision tongue cleaner"
        result = self.gate("coverage").validate_query(TASK["instruction"], query)
        self.assertEqual(result["decision"], "REVISE")
        self.assertTrue(result["revised_query"].startswith(query))
        self.assertEqual(self.gate("coverage").validate_query(TASK["instruction"], result["revised_query"])["decision"], "PASS")

    def test_layered_omissions_are_reported_without_blocking(self):
        result = self.gate("layered").validate_query(TASK["instruction"], "tongue cleaner")
        self.assertEqual(result["decision"], "PASS")
        self.assertIn("price", result["warnings"])
        self.assertTrue(result["missing_constraints"])

    def test_price_substitution_is_blocked_in_every_mode(self):
        for mode in QueryGate.MODES:
            result = self.gate(mode).validate_query(TASK["instruction"], "tongue cleaner under $20")
            self.assertEqual(result["decision"], "REVISE")
            self.assertIn("price", result["contradicted_constraints"])

    def test_empty_query_never_passes_any_mode(self):
        for mode in QueryGate.MODES:
            self.assertEqual(self.gate(mode).validate_query(TASK["instruction"], "")["decision"], "REVISE")

    def test_semantic_transport_error_is_not_nonblocking_omission(self):
        for mode in QueryGate.MODES:
            gate = self.gate(mode)
            gate.constraint_manager.match_constraint = lambda *a, **k: {
                "status": "MISSING", "match_type": "semantic", "error": "Service unavailable"}
            self.assertEqual(gate.validate_query(TASK["instruction"], "tongue cleaner")["decision"], "UNAVAILABLE")


class FrozenAndReplayTests(unittest.TestCase):
    def test_frozen_failure_leaves_baseline_running(self):
        frozen = {TASK["task_id"]: {"instruction": TASK["instruction"], "schema": {"extraction_error": "Failed extraction"}}}
        baseline = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment,
            use_query_gate=False, use_product_gate=False, frozen_schemas=frozen).run(TASK)
        gated = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment,
            use_query_gate=True, use_product_gate=True, frozen_schemas=frozen).run(TASK)
        self.assertTrue(baseline["done"])
        self.assertEqual(gated["stop_reason"], "constraint_schema_unavailable")
        self.assertEqual(gated["steps"], 0)

    def test_frozen_valid_schema_has_no_extraction_calls(self):
        record = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment).run(TASK)
        frozen = {TASK["task_id"]: {"instruction": TASK["instruction"], "schema": record["constraint_schema"]}}
        result = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment, frozen_schemas=frozen).run(TASK)
        self.assertTrue(result["success"])
        self.assertEqual(result["usage"]["calls_by_purpose"].get("extract_from_instruction", 0), 0)

    def test_layered_reminder_outage_does_not_turn_into_a_purchase_or_hidden_retry(self):
        class Client(ScriptedClient):
            def complete(self, prompt, purpose="policy", model=None):
                if purpose == "query_reminder":
                    self.calls[purpose] += 1
                    self.errors[purpose] += 1
                    raise RuntimeError("Reminder service unavailable")
                return super().complete(prompt, purpose, model)
        result = GatedAgent(client=Client(), env_factory=DemoEnvironment, max_steps=1,
                            query_mode="layered", use_product_gate=False).run(TASK)
        self.assertEqual(result["trajectory"][0]["action"], "search[tongue cleaner]")
        self.assertFalse(result["done"])
        self.assertEqual(result["usage"]["calls_by_purpose"]["query_reminder"], 1)
        self.assertEqual(result["usage"]["llm_errors"], 1)

    def test_historical_state_reconstruction_and_blind_labels(self):
        record = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment).run(TASK)
        for e in record["gate_events"]:
            e.pop("variant_state", None)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "results.jsonl"
            source.write_text(json.dumps(record) + "\n", encoding="utf-8")
            bundle = prepare_cases([source])
            export_frozen(source, Path(folder) / "frozen.json")
            self.assertIn(TASK["task_id"], load_frozen(Path(folder) / "frozen.json", [TASK])["tasks"])
        self.assertTrue(bundle["cases"])
        self.assertTrue(all(c["variant_provenance"] == "reconstructed" for c in bundle["cases"]))
        draft = review_draft(bundle)
        self.assertNotIn("decision", draft[0])
        self.assertIsNone(draft[0]["acceptable"])

    def test_label_with_different_evidence_is_not_joined(self):
        case = {"case_id": "state-1", "task_id": "t", "product_id": "p", "evidence": "Features only",
                "variant_state": {"selected_options": {}}}
        label = {"task_id": "t", "product_id": "p", "visible_evidence": "Full description", "acceptable": True,
                 "reviewer": "independent", "selected_options": {}}
        self.assertIsNone(matching_label(case, [label]))

    def test_plans_never_contact_provider(self):
        record = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment).run(TASK)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "r.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            bundle = prepare_cases([path])
            client = QueueClient()
            manifest = replay(bundle, ["legacy", "grounded"], 20, "test", Path(folder) / "plan", client=client)
            self.assertEqual(manifest["planned_api_calls"], len(bundle["cases"]) * 40)
            self.assertEqual(client.snapshot()["llm_calls"], 0)
            benchmark(Path(folder) / "matcher", client=client)
            self.assertEqual(client.snapshot()["llm_calls"], 0)

    def test_replay_counts_attempts_and_preserves_unlabelled_metrics(self):
        case = {"case_id": "one", "instruction": TASK["instruction"], "schema": FIXTURES[0]["schema"],
                "evidence": "Evidence", "variant_state": {"selected_options": {}}, "task_id": "t", "product_id": "p"}
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            client = QueueClient(audit(), RuntimeError("Outage"))
            manifest = replay({"cases": [case]}, ["grounded"], 2, "test", folder, execute=True, client=client)
            summary = json.loads((Path(folder) / "summary.json").read_text())[0]
        self.assertEqual(manifest["usage"]["llm_calls"], 2)
        self.assertEqual(summary["counts"], {"ACCEPT": 1, "ERROR": 1})
        self.assertEqual(summary["labelled_valid_attempts"], 0)
        self.assertLess(wilson(18, 20)[0], 0.9)


class RevisitTests(unittest.TestCase):
    def test_dedup_is_state_scoped_and_does_not_freeze_semantic_rejection(self):
        memory = EpisodeMemory()
        item = {"status": "exhausted", "available_sections": ["features"], "seen_sections": ["features"],
                "option_groups": {}, "selected_options": {}, "pages": {"features": "Evidence"}}
        item["exhausted_state"] = json.dumps({"pages": item["pages"], "options": {}, "groups": {}}, sort_keys=True)
        memory.candidates["B000000001"] = item
        actions = {"product_ids": ["B000000001"], "clickables": ["B000000001", "back to search"]}
        self.assertFalse(memory.filter_revisits(actions)["product_ids"])
        item["pages"]["description"] = "New evidence"
        self.assertTrue(memory.filter_revisits(actions)["product_ids"])
        item["status"] = "rejected"
        self.assertTrue(memory.filter_revisits(actions)["product_ids"])

    def test_dedup_does_not_block_access_to_alternative_variants(self):
        memory = EpisodeMemory()
        memory.candidates["B000000001"] = {"status": "exhausted", "available_sections": [], "seen_sections": [],
                                          "option_groups": {"size": ["small", "large"]}}
        actions = {"product_ids": ["B000000001"], "clickables": ["B000000001"]}
        self.assertEqual(memory.filter_revisits(actions)["product_ids"], actions["product_ids"])


if __name__ == "__main__":
    unittest.main()
