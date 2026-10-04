"""One ReAct policy, memory and interaction budget for the four ablations."""

import json
import re
import time

from agent.base_agent import BaseAgent
from agent.episode_memory import EpisodeMemory, page_type
from agent.llm_client import LLMClient, usage_delta
from evaluation.result_schema import make_result


def parse_react(text):
    match = re.search(r"(?:ACTION:\s*)?(search|click)\[([^\]\n]*)\]", str(text), re.I)
    action = f"{match.group(1).lower()}[{match.group(2).strip()}]" if match else None
    assessment = re.search(r"ASSESSMENT:\s*(.*?)(?:\nACTION:|$)", str(text), re.I | re.S)
    return assessment.group(1).strip() if assessment else "", action


def canonical_action(action, available):
    if not action:
        return None
    match = re.fullmatch(r"(search|click)\[([^\]\n]*)\]", action, re.I)
    if not match:
        return None
    kind, value = match.group(1).lower(), match.group(2).strip()
    if kind == "search":
        return f"search[{value}]" if value and available.get("has_search_bar") else None
    for clickable in available.get("clickables", []):
        if value.casefold() == str(clickable).casefold():
            return f"click[{clickable}]"
    return None


class ReActAgent(BaseAgent):
    def __init__(self, num_products=1000, max_steps=20, model=None, client=None,
                 use_query_gate=False, use_product_gate=False, env_factory=None, verbose=False):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.client = client or LLMClient(model=model)
        self.model = model or self.client.model
        self.num_products, self.max_steps = num_products, max_steps
        self.use_query_gate, self.use_product_gate = use_query_gate, use_product_gate
        self.env_factory, self.verbose = env_factory, verbose
        self.name = ("full" if use_query_gate and use_product_gate else "query" if use_query_gate
                     else "product" if use_product_gate else "baseline")

    def parse_action(self, text):
        return parse_react(text)[1]

    def _policy(self, instruction, memory, observation, actions, feedback=None, retry=None, step=1, purpose="policy"):
        prompt = f"""You are a ReAct shopping agent operating WebShop.
User instruction: {instruction}
Current observation: {observation}
Available actions: {json.dumps(actions, ensure_ascii=False)}
Current page: {page_type(actions)}; search bar available: {bool(actions.get('has_search_bar'))}
Task-local candidate memory: {json.dumps(memory.context(), ensure_ascii=False)}
Recent reasoning and actions: {json.dumps(memory.history[-6:], ensure_ascii=False)}
Remaining interaction steps: {self.max_steps - step + 1}
Output two lines: ASSESSMENT: <brief evidence-based assessment>
ACTION: <exactly one search[query] or click[item]>
Use visible evidence, preserving all user requirements and their polarity.
Inspect useful detail pages at most once per candidate. Select required options.
Do not repeat normalized searches or revisit rejected/exhausted candidates.
Keep stronger candidates in mind when comparing alternatives. During the last five
steps, finish legal navigation and purchase only if supported; do not invent clicks.
Search is legal ONLY when has_search_bar=true. Back to Search opens the search
HOME, not the results. On a product page < Prev returns to results; on a detail
page < Prev returns to the product. On results, inspect visible product IDs or
click Back to Search before issuing a new query. On the search home, issue a
query; old result IDs in memory are not clickable there. On detail pages return
with < Prev before visiting another detail or buying. Do not require reviews,
ratings, or comparison with alternatives unless the user's instruction asks.
"""
        if feedback:
            prompt += "\nConstraint feedback: " + json.dumps(feedback, ensure_ascii=False)
        if retry:
            prompt += "\nPrevious proposal needs correction: " + str(retry)
        return self.client.complete(prompt, purpose=purpose, model=self.model)

    def ask_deepseek(self, instruction, history, observation, available_actions):
        # Compatibility API; the actual episode uses the shared ReAct policy above.
        memory = EpisodeMemory()
        memory.history = history
        return self._policy(instruction, memory, observation, available_actions)

    def run(self, task):
        from gates.constraint_manager import ConstraintManager
        from gates.product_gate import ProductGate
        from gates.query_gate import QueryGate

        started, usage_before = time.perf_counter(), self.client.snapshot()
        instruction = str(task["instruction"])
        memory, trajectory, events = EpisodeMemory(), [], []
        self.constraint_manager = (ConstraintManager(model=self.model, client=self.client)
                                   if self.use_query_gate or self.use_product_gate else None)
        self.query_gate = QueryGate(constraint_manager=self.constraint_manager) if self.use_query_gate else None
        self.product_gate = ProductGate(constraint_manager=self.constraint_manager) if self.use_product_gate else None
        self.memory = memory
        schema, env, reward, done, error = None, None, 0.0, False, None
        stop_reason = "step_budget"
        cleaner = ProductGate._strip_instruction
        clean = lambda obs: cleaner(instruction, obs)

        def product_check(actions, step, phase):
            if not self.product_gate or not memory.current:
                return None
            feedback = self.product_gate.evaluate(
                instruction, memory.evidence(), available_actions=actions,
                inspection_state=memory.inspection_state(),
            )
            memory.candidate["status"] = feedback["decision"].lower()
            events.append({"gate": "product", "step": step, "phase": phase,
                           "product_id": memory.current,
                           "selected_options": dict(memory.candidate["selected_options"]),
                           "visible_evidence": memory.evidence(), "result": feedback})
            return feedback

        try:
            if self.env_factory:
                env = self.env_factory(num_products=self.num_products)
            else:
                from webshop_wrapper.env import WebShopWrapper
                env = WebShopWrapper(num_products=self.num_products)
            # Only the adapter receives scoring metadata. Prompts receive instruction
            # and observable state; never task_id, gold attributes or goal options.
            observation = env.reset(task)
            if self.constraint_manager:
                schema = self.constraint_manager.get_constraints(instruction)
                if schema.get("extraction_error"):
                    stop_reason = "constraint_schema_unavailable"
                    error = schema["extraction_error"]
                    return self._result(task, reward, done, trajectory, events, memory, schema,
                                        stop_reason, error, started, usage_before)
            for step in range(1, self.max_steps + 1):
                actions = env.get_available_actions()
                feedback = product_check(actions, step, "before_policy")
                raw = self._policy(instruction, memory, observation, actions, feedback=feedback, step=step)
                assessment, proposal = parse_react(raw)
                action = canonical_action(proposal, actions)
                retry_output, retry_error = None, None
                repeated_detail = (memory.candidate is not None and action is not None
                                   and action[6:-1].casefold() in memory.candidate["seen_sections"])
                if action is None or repeated_detail:
                    try:
                        retry_output = self._policy(instruction, memory, observation, actions,
                                                    feedback=feedback, retry=proposal or raw, step=step, purpose="action_retry")
                        assessment, proposal = parse_react(retry_output)
                        action = canonical_action(proposal, actions)
                    except Exception as exc:
                        retry_error = str(exc)
                    if action and memory.candidate and action[6:-1].casefold() in memory.candidate["seen_sections"]:
                        action = None
                if action is None:
                    # Recover from an illegal proposal using navigation only.
                    # This is shared by all groups and never authorizes a buy.
                    if page_type(actions) == "detail":
                        action = canonical_action("click[< prev]", actions)
                    elif actions.get("has_search_bar"):
                        action = canonical_action(f"search[{instruction}]", actions)
                    else:
                        action = canonical_action("click[back to search]", actions)
                if action is None:
                    stop_reason = "invalid_action"
                    trajectory.append({"step": step, "proposed_action": proposal, "action": None, "executed": False,
                                       "llm_output": raw, "retry_output": retry_output, "retry_error": retry_error})
                    break
                query_feedback = None
                if self.query_gate and action.startswith("search["):
                    query_feedback = self.query_gate.evaluate(instruction, action[7:-1], memory.history)
                    events.append({"gate": "query", "step": step, "phase": "proposal", "query": action[7:-1],
                                   "result": query_feedback})
                    if query_feedback["decision"] != "PASS":
                        try:
                            recovery = self._policy(instruction, memory, observation, actions,
                                feedback=query_feedback, retry=action, step=step, purpose="query_repair")
                            candidate = canonical_action(parse_react(recovery)[1], actions)
                        except Exception as exc:
                            candidate, retry_error = None, str(exc)
                        if candidate and candidate.startswith("search["):
                            checked = self.query_gate.validate_query(instruction, candidate[7:-1])
                            events.append({"gate": "query", "step": step, "phase": "repair", "query": candidate[7:-1], "result": checked})
                            if checked["decision"] == "PASS":
                                action = candidate
                            else:
                                candidate = None
                        else:
                            candidate = None
                        if candidate is None:
                            revised = query_feedback.get("revised_query")
                            checked = self.query_gate.validate_query(instruction, revised) if revised else None
                            if checked and checked["decision"] == "PASS":
                                action = canonical_action(f"search[{revised}]", actions)
                                events.append({"gate": "query", "step": step, "phase": "fallback", "query": revised, "result": checked})
                            else:
                                stop_reason = "query_gate_unresolved"
                                trajectory.append({"step": step, "proposed_action": proposal, "action": action,
                                                   "executed": False, "query_gate": query_feedback, "retry_error": retry_error})
                                break
                # Reconsider a premature buy once. On failure, use a legal non-buy
                # recommendation; never recover by executing the refused purchase.
                if self.product_gate and action.casefold() == "click[buy now]":
                    if not feedback or feedback["decision"] != "READY":
                        try:
                            recovery = self._policy(instruction, memory, observation, actions, feedback=feedback,
                                                    retry=action, step=step, purpose="product_reconsider")
                            action = canonical_action(parse_react(recovery)[1], actions)
                        except Exception as exc:
                            action, retry_error = None, str(exc)
                        if action is None or action.casefold() == "click[buy now]":
                            action = canonical_action((feedback or {}).get("recommended_action"), actions)
                        if action is None or action.casefold() == "click[buy now]":
                            stop_reason = "product_gate_blocked"
                            trajectory.append({"step": step, "proposed_action": proposal, "action": None,
                                               "executed": False, "product_gate": feedback, "retry_error": retry_error})
                            break
                        # A recovery search goes through the query gate too.
                        if self.query_gate and action.startswith("search["):
                            checked = self.query_gate.validate_query(instruction, action[7:-1])
                            events.append({"gate": "query", "step": step, "phase": "product_recovery", "query": action[7:-1], "result": checked})
                            if checked["decision"] != "PASS":
                                stop_reason = "query_gate_unresolved"
                                break
                    if action.casefold() == "click[buy now]":
                        final = product_check(actions, step, "before_purchase")
                        if not final or not final["ready_to_buy"]:
                            stop_reason = "product_gate_blocked"
                            break
                before_observation, candidate_before = observation, memory.current
                observation, reward, done, _ = env.step(action)
                after = env.get_available_actions() if not done else {"page_type": "done", "clickables": []}
                memory.update(action, assessment, observation, actions, after, clean)
                trajectory.append({"step": step, "assessment": assessment, "llm_output": raw,
                    "proposed_action": proposal, "action": action, "executed": True,
                    "retry_output": retry_output, "retry_error": retry_error,
                    "reward": float(reward), "done": bool(done),
                    "observation_before": str(before_observation), "observation_after": str(observation),
                    "available_actions": actions, "active_product_before_action": candidate_before,
                    "active_product_after_action": memory.current,
                    "selected_options": dict((memory.candidate or {}).get("selected_options", {})),
                    "query_gate": query_feedback, "product_gate": feedback})
                if self.verbose:
                    print(f"[{self.name}] {step}: {action}; reward={reward}")
                if done:
                    stop_reason = "completed"
                    break
        except Exception as exc:
            error, stop_reason = f"{type(exc).__name__}: {exc}", "error"
        finally:
            if env is not None:
                env.close()
        return self._result(task, reward, done, trajectory, events, memory, schema,
                            stop_reason, error, started, usage_before)

    def _result(self, task, reward, done, trajectory, events, memory, schema, reason, error, started, before):
        result = make_result(task["task_id"], task["instruction"],
                             success=bool(done and float(reward) > 0.999999),
                             reward=float(reward), trajectory=trajectory)
        purchased = memory.current if done and memory.candidate and memory.candidate["status"] == "purchased" else None
        usage = usage_delta(before, self.client.snapshot())
        result.update(configuration=self.name, model=self.model, max_steps=self.max_steps,
                      steps=sum(x.get("executed", True) for x in trajectory), done=bool(done),
                      stop_reason=reason, constraint_schema=schema, gate_events=events,
                      purchased_product=purchased,
                      purchased_options=dict((memory.candidate or {}).get("selected_options", {})) if purchased else {},
                      usage=usage, elapsed_seconds=time.perf_counter() - started,
                      full_success=result["success"], partial_success=bool(done and 0 < reward <= 0.999999),
                      gate_stats={"query_gate_calls": sum(x["gate"] == "query" for x in events),
                                  "product_gate_calls": sum(x["gate"] == "product" for x in events)})
        if error:
            result["error"] = error
        return result
