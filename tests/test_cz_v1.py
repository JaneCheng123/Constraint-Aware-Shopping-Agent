"""Offline regressions for gate bypasses, ablation fairness and independent metrics."""

import copy
import ast
import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent.episode_memory import EpisodeMemory
from agent.gated_agent import GatedAgent
from agent.llm_client import LLMClient
from demo.offline import DemoEnvironment, FIXTURES, ScriptedClient
from evaluation.metrics import AnnotationStore, compute_summary
from evaluation.run_experiment import main as run_experiment
from evaluation.task_loader import load_tasks
from gates.constraint_manager import ConstraintManager
from gates.hard_constraints import check_option, check_price, parse_price_constraint, price_satisfies
from gates.product_gate import ProductGate
from gates.query_gate import QueryGate
from webshop_wrapper.env import WebShopWrapper


TASK = {key: copy.deepcopy(FIXTURES[0][key]) for key in
        ("task_id", "instruction", "instruction_attributes", "instruction_options")}


class ClientCompatibilityTests(unittest.TestCase):
    def test_legacy_environment_uses_counted_http_without_global_sdk_changes(self):
        from unittest.mock import Mock
        response = Mock()
        response.json.return_value = {"model": "reported-model", "choices": [{"message": {"content": "OK"}}],
                                      "usage": {"prompt_tokens": 4, "completion_tokens": 1}}
        session = Mock()
        session.post.return_value = response
        adapter = Mock()
        requests = types.SimpleNamespace(Session=Mock(return_value=session),
                                         adapters=types.SimpleNamespace(HTTPAdapter=adapter))
        old_sdk = types.SimpleNamespace(api_key="untouched")
        with patch.dict("sys.modules", {"openai": old_sdk, "requests": requests}):
            client = LLMClient(api_key="test-key", base_url="https://example.invalid/", timeout=12)
            self.assertEqual(client.complete("hello", purpose="policy"), "OK")
            self.assertEqual(client.complete("again", purpose="gate"), "OK")
        adapter.assert_called_once_with(max_retries=0)
        self.assertEqual(old_sdk.api_key, "untouched")
        self.assertEqual(session.post.call_args.args[0], "https://example.invalid/chat/completions")
        self.assertEqual(session.post.call_args.kwargs["timeout"], 12)
        self.assertEqual(client.snapshot()["llm_calls"], 2)
        self.assertEqual(client.snapshot()["input_tokens"], 8)
        self.assertEqual(client.snapshot()["provider_model_counts"], {"reported-model": 2})

    def test_http_error_is_counted_once_without_retry(self):
        from unittest.mock import Mock
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("Service unavailable")
        client = LLMClient(api_key="test-key")
        client._http = Mock()
        client._http.post.return_value = response
        with self.assertRaises(RuntimeError):
            client.complete("hello", purpose="query_repair")
        client._http.post.assert_called_once()
        self.assertEqual(client.snapshot()["llm_calls"], 1)
        self.assertEqual(client.snapshot()["llm_errors"], 1)


class QueueClient(LLMClient):
    def __init__(self, *responses):
        super().__init__(model="test-model")
        self.responses = list(responses)
        self.prompts = []

    def complete(self, prompt, purpose="policy", model=None):
        self.calls[purpose] += 1
        self.prompts.append((purpose, model, prompt))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            self.errors[purpose] += 1
            raise response
        return response


class HardConstraintTests(unittest.TestCase):
    def test_decimal_price(self):
        bounds = parse_price_constraint("under $50.25")
        self.assertTrue(price_satisfies("50.24", bounds))
        self.assertFalse(price_satisfies("50.25", bounds))
        self.assertFalse(price_satisfies("50.26", bounds))

    def test_inclusive_and_negative_phrases(self):
        for phrase in ("at most $50", "no more than $50", "up to $50"):
            self.assertTrue(price_satisfies("50", parse_price_constraint(phrase)), phrase)
            self.assertFalse(price_satisfies("50.01", parse_price_constraint(phrase)), phrase)
        for phrase in ("at least $50", "no less than $50"):
            self.assertTrue(price_satisfies("50", parse_price_constraint(phrase)), phrase)
            self.assertFalse(price_satisfies("49", parse_price_constraint(phrase)), phrase)

    def test_interval_and_exact(self):
        bounds = parse_price_constraint("between $10.50 and $20.25")
        self.assertTrue(price_satisfies("15", bounds))
        self.assertFalse(price_satisfies("10.49", bounds))
        self.assertTrue(price_satisfies("12", parse_price_constraint("exactly $12")))
        self.assertFalse(price_satisfies("12.01", parse_price_constraint("exactly $12")))

    def test_reviews_are_not_price_evidence(self):
        self.assertEqual(check_price("under $20", "Saved $10 using a coupon")["status"], "MISSING")

    def test_exact_option_values(self):
        constraint = {"canonical": "size: 1", "value": "1", "kind": "size", "aliases": []}
        result = check_option(constraint, {"size": ["10", "1"]}, {"size": "10"})
        self.assertFalse(result["selected"])
        self.assertEqual(result["available_action"], "1")
        blue = {"canonical": "color: blue", "value": "blue", "kind": "color", "aliases": []}
        self.assertFalse(check_option(blue, {"color": ["light blue"]}, {"color": "light blue"})["selected"])

    def test_decimal_bounds_equivalence(self):
        self.assertEqual(parse_price_constraint("under $50.00"), parse_price_constraint("under $50"))


class ConstraintTests(unittest.TestCase):
    def test_validator_false_is_not_overridden(self):
        schema = copy.deepcopy(FIXTURES[0]["schema"])
        client = QueueClient(json.dumps({"valid": False, "issues": ["semantic mistake"], "corrected_schema": schema}))
        manager = ConstraintManager(client=client)
        self.assertFalse(manager.validate_extraction(TASK["instruction"], schema)["valid"])

    def test_validator_error_fails_closed(self):
        manager = ConstraintManager(client=QueueClient(RuntimeError("offline")))
        self.assertFalse(manager.validate_extraction(TASK["instruction"], FIXTURES[0]["schema"])["valid"])

    def test_broad_category_conflict_is_not_supported(self):
        client = QueueClient(json.dumps({"status": "CONTRADICTED", "evidence": "Phone case", "reason": "Outside skin care"}))
        manager = ConstraintManager(client=client)
        result = manager.semantic_match_product_type("Phone case", {"canonical": "skin care", "source_text": "skin care", "aliases": []})
        self.assertEqual(result["status"], "CONTRADICTED")

    def test_negation_does_not_get_exact_acceptance(self):
        client = QueueClient(json.dumps({"status": "CONTRADICTED", "evidence": "not cruelty free", "reason": "Negated"}))
        manager = ConstraintManager(client=client)
        result = manager.match_constraint("This product is not cruelty free", {"canonical": "cruelty free", "source_text": "cruelty free", "aliases": []})
        self.assertEqual(result["status"], "CONTRADICTED")
        self.assertEqual(sum(client.calls.values()), 1)

    def test_extraction_is_shared_and_cached(self):
        client = ScriptedClient()
        manager = ConstraintManager(client=client)
        first = manager.get_constraints(TASK["instruction"])
        first["product_type"]["canonical"] = "mutated"
        second = manager.get_constraints(TASK["instruction"])
        self.assertEqual(manager.extraction_calls, 1)
        self.assertEqual(manager.validation_calls, 1)
        self.assertEqual(second["product_type"]["canonical"], "tongue cleaner")

    def test_product_type_metadata_is_consistent_for_validation(self):
        schema = ConstraintManager._normalize_schema(FIXTURES[0]["schema"])
        self.assertEqual(schema["product_type"]["kind"], "product_type")

    def test_structured_price_response_is_normalized_to_anchored_phrase(self):
        schema = copy.deepcopy(FIXTURES[0]["schema"])
        schema["price_constraint"] = {"source_text": "under $10", "canonical": "at most $10", "kind": "price"}
        normalized = ConstraintManager._normalize_schema(schema)
        self.assertEqual(normalized["price_constraint"], "under $10")
        self.assertFalse(ConstraintManager._deterministic_validation_issues(TASK["instruction"], normalized))
        self.assertFalse(price_satisfies("10", parse_price_constraint(normalized["price_constraint"])))

    def test_query_includes_options_and_price(self):
        gate = QueryGate(constraint_manager=ConstraintManager(client=ScriptedClient()))
        result = gate.validate_query(TASK["instruction"], "tongue cleaner")
        self.assertEqual(result["decision"], "REVISE")
        self.assertIn("price", result["missing_constraints"])
        self.assertIn("color: blue", result["missing_constraints"])
        self.assertEqual(gate.validate_query(TASK["instruction"], result["revised_query"])["decision"], "PASS")


class ProductGateTests(unittest.TestCase):
    def gate(self):
        manager = ConstraintManager(client=ScriptedClient())
        manager.get_constraints = lambda instruction: copy.deepcopy(FIXTURES[0]["schema"])
        return ProductGate(constraint_manager=manager)

    def test_all_requirements_checked_even_on_category_conflict(self):
        gate = self.gate()
        gate._resolve_product_type = lambda *args, **kwargs: {"status": "CONTRADICTED", "match_type": "semantic", "reason": "Wrong type"}
        checked = []
        def resolve(evidence, constraint, context=""):
            checked.append(constraint)
            return {"status": "MISSING", "match_type": "none", "reason": "Missing"}
        gate._resolve_constraint = resolve
        result = gate.evaluate(TASK["instruction"], "Phone case; Price: $99", inspection_state={"product_observation": "Price: $99"})
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(len(checked), 2)
        self.assertEqual(result["price_evidence"]["status"], "CONTRADICTED")
        self.assertEqual(len(result["post_selection"]), 1)
        self.assertEqual(gate.final_audit_calls, 0)

    def test_required_option_cannot_be_inherited_from_other_candidate(self):
        gate = self.gate()
        result = gate.evaluate(TASK["instruction"], "BPA-free tongue cleaner; Price: $9",
            available_actions={"clickables": ["buy now", "blue"], "option_groups": {"color": ["blue", "red"]}},
            inspection_state={"selected_options": {}, "available_sections": [], "seen_sections": []})
        self.assertFalse(result["ready_to_buy"])
        self.assertEqual(result["recommended_action"], "click[blue]")

    def test_price_failure_cannot_be_overridden_by_audit(self):
        gate = self.gate()
        result = gate.evaluate(TASK["instruction"], "BPA-free tongue cleaner; Price: $10",
            available_actions={"option_groups": {"color": ["blue"]}},
            inspection_state={"selected_options": {"color": "blue"}})
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(gate.final_audit_calls, 0)

    def test_fixed_color_needs_evidence_but_not_a_nonexistent_selector(self):
        gate = self.gate()
        result = gate.evaluate(TASK["instruction"], "Blue BPA-free tongue cleaner; Price: $9",
            available_actions={"clickables": ["buy now"], "option_groups": {"size": ["M", "L"]}},
            inspection_state={"selected_options": {}, "available_sections": [], "seen_sections": []})
        self.assertTrue(result["ready_to_buy"])
        self.assertTrue(result["post_selection"][0]["satisfied"])
        self.assertFalse(result["post_selection"][0]["selected"])

    def test_fixed_color_missing_is_not_inferred(self):
        result = self.gate().evaluate(TASK["instruction"], "BPA-free tongue cleaner; Price: $9",
            available_actions={"clickables": ["buy now"], "option_groups": {}},
            inspection_state={"available_sections": [], "seen_sections": []})
        self.assertFalse(result["ready_to_buy"])
        self.assertEqual(result["post_selection"][0]["status"], "MISSING")

    def test_detail_page_does_not_discard_known_unselected_color_selector(self):
        result = self.gate().evaluate(TASK["instruction"], "Blue BPA-free tongue cleaner; Price: $9",
            available_actions={"clickables": ["< prev"], "option_groups": {}},
            inspection_state={"option_groups": {"color": ["blue", "red"]},
                              "selected_options": {}, "available_sections": ["features"], "seen_sections": []})
        self.assertFalse(result["ready_to_buy"])
        self.assertEqual(result["recommended_action"], "click[< prev]")

    def test_final_audit_receives_visible_selection_and_does_not_reuse_other_variant(self):
        client = QueueClient(json.dumps({"decision": "ACCEPT"}), json.dumps({"decision": "REJECT"}))
        gate = ProductGate(constraint_manager=ConstraintManager(client=client), audit_version="variant")
        schema = copy.deepcopy(FIXTURES[0]["schema"])
        blue = {"selected_options": {"color": "blue"}, "option_groups": {"color": ["blue", "red"]}}
        red = {"selected_options": {"color": "red"}, "option_groups": {"color": ["blue", "red"]}}
        evidence = "BPA-free tongue cleaner; Price: $9"
        self.assertEqual(gate._final_audit(TASK["instruction"], schema, evidence, blue)["decision"], "ACCEPT")
        self.assertEqual(gate._final_audit(TASK["instruction"], schema, evidence, red)["decision"], "REJECT")
        self.assertEqual(gate._final_audit(TASK["instruction"], schema, evidence, blue)["decision"], "ACCEPT")
        self.assertEqual(gate.final_audit_calls, 2)
        self.assertIn('"selected_options": {"color": "blue"}', client.prompts[0][2])


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.memory = EpisodeMemory()
        self.results = {"page_type": "results", "product_ids": ["B000000001"], "clickables": ["B000000001"]}
        self.product = {"page_type": "product", "clickables": ["buy now", "< prev"], "option_groups": {"color": ["blue", "red"]}}
        self.memory.update("click[B000000001]", "", "Product", self.results, self.product, str)

    def test_product_prev_clears_current(self):
        self.memory.update("click[< prev]", "", "Results", self.product, self.results, str)
        self.assertIsNone(self.memory.current)

    def test_detail_prev_preserves_candidate(self):
        self.memory.update("click[< prev]", "", "Product", {"page_type": "detail"}, self.product, str)
        self.assertEqual(self.memory.current, "B000000001")

    def test_search_clears_current(self):
        self.memory.update("search[new]", "", "Results", {"has_search_bar": True}, self.results, str)
        self.assertIsNone(self.memory.current)

    def test_option_selection_overwrites_old_value(self):
        self.memory.update("click[blue]", "", "Product", self.product, self.product, str)
        self.memory.update("click[red]", "", "Product", self.product, self.product, str)
        self.assertEqual(self.memory.candidate["selected_options"], {"color": "red"})


class AgentTests(unittest.TestCase):
    def test_illegal_search_on_results_recovers_via_search_home_without_buying(self):
        client = QueueClient("ACTION: search[tongue cleaner]", "ACTION: search[new query]", "ACTION: search[new query]")
        result = GatedAgent(client=client, max_steps=2, env_factory=DemoEnvironment,
                            use_query_gate=False, use_product_gate=False).run(TASK)
        self.assertNotIn("error", result)
        self.assertEqual(result["trajectory"][-1]["action"], "click[back to search]")
        self.assertEqual(result["steps"], 2)
        self.assertFalse(result["done"])

    def test_all_four_configurations_execute(self):
        for query, product in ((False, False), (True, False), (False, True), (True, True)):
            agent = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment, use_query_gate=query, use_product_gate=product)
            result = agent.run(TASK)
            self.assertNotIn("error", result)
            self.assertTrue(result["done"])
            self.assertLessEqual(result["steps"], 20)
            self.assertEqual(result["usage"]["llm_calls"], sum(result["usage"]["calls_by_purpose"].values()))
            if not query and not product:
                self.assertIsNone(result["constraint_schema"])
                self.assertEqual(result["gate_events"], [])

    def test_reconsideration_error_never_executes_rejected_buy(self):
        class Client(ScriptedClient):
            def complete(self, prompt, purpose="policy", model=None):
                if purpose == "product_reconsider":
                    self.calls[purpose] += 1
                    self.errors[purpose] += 1
                    raise RuntimeError("Simulated API outage")
                if purpose == "policy" and '"current_candidate": "B000000001"' in prompt:
                    self.calls[purpose] += 1
                    return "ACTION: click[buy now]"
                return super().complete(prompt, purpose, model)
        agent = GatedAgent(client=Client(), env_factory=DemoEnvironment,
                           use_query_gate=False, use_product_gate=True, max_steps=3)
        result = agent.run(TASK)
        self.assertFalse(any(step.get("action") == "click[buy now]" and step.get("executed") for step in result["trajectory"]))
        self.assertEqual(result["usage"]["llm_errors"], 1)
        self.assertEqual(result["trajectory"][-1]["action"], "click[back to search]")

    def test_final_check_blocks_a_changed_purchase_decision(self):
        ready = {"decision": "READY", "ready_to_buy": True, "recommended_action": "click[buy now]"}
        rejected = {"decision": "REJECT", "ready_to_buy": False, "recommended_action": "click[back to search]"}
        with patch("gates.product_gate.ProductGate.evaluate", side_effect=[ready, rejected]):
            result = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment,
                                use_query_gate=False, use_product_gate=True).run(TASK)
        self.assertEqual(result["stop_reason"], "product_gate_blocked")
        self.assertFalse(result["done"])

    def test_query_repair_outage_uses_verified_fallback(self):
        class Client(ScriptedClient):
            def complete(self, prompt, purpose="policy", model=None):
                if purpose == "query_repair":
                    self.calls[purpose] += 1
                    self.errors[purpose] += 1
                    raise RuntimeError("Repair API unavailable")
                return super().complete(prompt, purpose, model)
        result = GatedAgent(client=Client(), env_factory=DemoEnvironment,
                            use_query_gate=True, use_product_gate=False).run(TASK)
        self.assertTrue(result["success"])
        self.assertIn("under $10", result["trajectory"][0]["action"])

    def test_no_gold_metadata_in_prompts_and_same_model_everywhere(self):
        class Client(ScriptedClient):
            def __init__(self):
                super().__init__()
                self.prompts = []
            def complete(self, prompt, purpose="policy", model=None):
                self.prompts.append((purpose, model, prompt))
                return super().complete(prompt, purpose, model)
        task = dict(TASK, task_id="SECRET_TARGET_42", instruction_attributes=["HIDDEN_GOLD_33"])
        client = Client()
        result = GatedAgent(client=client, model="shared-model", env_factory=DemoEnvironment).run(task)
        self.assertNotIn("error", result)
        for _, model, prompt in client.prompts:
            self.assertEqual(model, "shared-model")
            self.assertNotIn("SECRET_TARGET_42", prompt)
            self.assertNotIn("HIDDEN_GOLD_33", prompt)

    def test_episode_state_resets(self):
        agent = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment)
        first, second = agent.run(TASK), agent.run(TASK)
        self.assertEqual(first["usage"]["llm_calls"], second["usage"]["llm_calls"])
        self.assertEqual(first["steps"], second["steps"])
        self.assertEqual(agent.constraint_manager.extraction_calls, 1)

    def test_schema_failure_closes_environment_without_purchase(self):
        instances = []
        def factory(**kwargs):
            env = DemoEnvironment(**kwargs)
            instances.append(env)
            return env
        result = GatedAgent(client=QueueClient(RuntimeError("Extractor offline")), env_factory=factory).run(TASK)
        self.assertEqual(result["stop_reason"], "constraint_schema_unavailable")
        self.assertTrue(instances[0].closed)
        self.assertEqual(instances[0].actions, [])


class EvaluationTests(unittest.TestCase):
    def test_unlabelled_draft_is_not_accepted_as_ground_truth(self):
        with self.assertRaises(ValueError):
            AnnotationStore([{"task_id": "x", "kind": "query", "query": "x", "acceptable": None}])

    def test_empty_attribute_goal_does_not_divide_by_zero(self):
        source = Path("web_agent_site/engine/goal.py").read_text(encoding="utf-8")
        function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                        and node.name == "get_attribute_reward")
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "goal.py", "exec"), namespace)
        self.assertEqual(namespace["get_attribute_reward"]({"Attributes": []}, {"attributes": []}), (None, 0))

    def test_reward_uses_goal_option_values_not_name_value_tuples(self):
        source = ast.parse(Path("web_agent_site/engine/goal.py").read_text(encoding="utf-8"))
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"get_reward", "get_option_reward"}]
        def normalize(value):
            self.assertIsInstance(value, str)
            return value.lower()
        namespace = {"normalize_color": normalize,
                     "fuzz": types.SimpleNamespace(token_set_ratio=lambda a, b: 100 if a == b else 0),
                     "get_type_reward": lambda *args: {"r_type": 1},
                     "get_attribute_reward": lambda *args: (None, 0)}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "goal.py", "exec"), namespace)
        goal = {"attributes": [], "goal_options": {"color": "blue", "size": "M"}, "price_upper": 10}
        self.assertEqual(namespace["get_reward"]({}, goal, 9, {"color": "blue", "size": "M"}), 1)
        self.assertAlmostEqual(namespace["get_reward"]({}, goal, 9, {"color": "blue", "size": "L"}), 2 / 3)

    def test_missing_annotations_are_null(self):
        record = GatedAgent(client=ScriptedClient(), env_factory=DemoEnvironment).run(TASK)
        summary = compute_summary([record])
        self.assertIsNone(summary["query_constraint_coverage"])
        self.assertIsNone(summary["product_constraint_satisfaction"])
        self.assertIsNone(summary["gate_metrics"]["product"]["false_acceptance_rate"])

    def test_false_acceptance_uses_independent_label(self):
        record = {"task_id": "x", "reward": 0, "done": False, "gate_events": [
            {"gate": "product", "product_id": "wrong", "result": {"decision": "READY"}}]}
        labels = AnnotationStore([{"task_id": "x", "kind": "product", "product_id": "wrong", "acceptable": False}])
        summary = compute_summary([record], labels)
        self.assertEqual(summary["gate_metrics"]["product"]["false_acceptance_rate"], 1)

    def test_errors_remain_in_success_denominator(self):
        summary = compute_summary([{"reward": 1, "done": True, "task_id": "good"},
                                   {"reward": 0, "done": False, "error": "outage", "task_id": "bad"}])
        self.assertEqual(summary["task_success_rate"], 0.5)

    def test_task_loader_preserves_scoring_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "tasks.json"
            file.write_text(json.dumps([dict(TASK, price_upper=10)]), encoding="utf-8")
            loaded = load_tasks(file)[0]
        self.assertEqual(loaded["instruction_options"], {"color": "blue"})
        self.assertEqual(loaded["instruction_attributes"], ["bpa free"])
        self.assertEqual(loaded["price_upper"], 10)

    def test_runner_manifest_resume_and_four_group_equality(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            argv = ["--dataset", "demo/tasks.json", "--model", "scripted-demo", "--output-dir", folder,
                    "--annotations", "demo/annotations.json"]
            result = run_experiment(argv, ScriptedClient, DemoEnvironment, "synthetic_offline_demo")
            self.assertEqual(set(result), {"baseline", "query", "product", "full"})
            self.assertTrue(all(summary["tasks"] == 2 for summary in result.values()))
            resumed = run_experiment(argv + ["--resume"], ScriptedClient, DemoEnvironment, "synthetic_offline_demo")
            self.assertEqual(resumed["full"]["llm_calls"], result["full"]["llm_calls"])
            with self.assertRaises(ValueError):
                run_experiment(argv + ["--resume", "--max-steps", "30"], ScriptedClient, DemoEnvironment, "synthetic_offline_demo")
            with self.assertRaises(ValueError):
                run_experiment(argv + ["--resume", "--query-mode", "layered"], ScriptedClient, DemoEnvironment, "synthetic_offline_demo")
            self.assertTrue((Path(folder) / "report.html").exists())

    def test_wrapper_does_not_inherit_hidden_attributes(self):
        class Env:
            def __init__(self, **kwargs):
                self.server = types.SimpleNamespace(
                    goals=[{"asin": "B000000001", "instruction_text": TASK["instruction"],
                            "attributes": ["bpa free", "HIDDEN_EXTRA"], "goal_options": {}}],
                    user_sessions={})
                self.browser = types.SimpleNamespace(current_url="search")
                self.server.receive = lambda *args: ("html", "search", {})
            def reset(self, session):
                self.session = session
                self.server.user_sessions[session] = {"goal": copy.deepcopy(self.server.goals[session])}
                return self.observation, {}
            @property
            def observation(self):
                return self.get_instruction_text()
            def get_instruction_text(self):
                return self.server.user_sessions[self.session]["goal"]["instruction_text"]
        with redirect_stdout(io.StringIO()):
            wrapper = WebShopWrapper(env_factory=Env)
            wrapper.reset(dict(TASK, task_id="B000000001", price_upper=10))
        goal = wrapper.env.server.user_sessions[wrapper.env.session]["goal"]
        self.assertEqual(goal["attributes"], ["bpa free"])
        self.assertFalse(price_satisfies("10", goal["price_bounds"]))


if __name__ == "__main__":
    unittest.main()
