import json
import os
import re

import openai

from agent.base_agent import BaseAgent
from evaluation.result_schema import make_result
from webshop_wrapper.env import WebShopWrapper


def is_valid_action(action, available_actions):
    if action is None:
        return False

    match = re.fullmatch(r"(search|click)\[(.*?)\]", action, re.IGNORECASE)
    if match is None:
        return False

    action_type, value = match.groups()
    if action_type.lower() == "search":
        return bool(available_actions.get("has_search_bar")) and bool(value.strip())

    return any(
        value.casefold() == str(clickable).casefold()
        for clickable in available_actions.get("clickables", [])
    )


class NoMemoryReActAgent(BaseAgent):

    def __init__(self, num_products=1000, max_steps=20):
        super().__init__("no_memory_react")
        self.num_products = num_products
        self.max_steps = max_steps

        openai.api_key = os.environ["DEEPSEEK_API_KEY"]
        openai.api_base = "https://api.deepseek.com"

    def ask_deepseek(
        self, instruction, scratchpad, observation, available_actions,
        episode_state,
        previous_invalid_action=None,
        step=None,
    ):

        prompt = f"""
        You are a shopping agent operating WebShop.

        User instruction:
        {instruction}

        Current episode scratchpad:
        {json.dumps(scratchpad, ensure_ascii=False)}

        Current episode state:
        {json.dumps(episode_state, ensure_ascii=False, separators=(",", ":"))}

        Current observation:
        {observation}

        Available actions:
        {json.dumps(available_actions, ensure_ascii=False)}

        Output exactly two lines:

        ASSESSMENT: Page=<page type>; Candidate=<item or none>; Explicit=[...]; Plausible=[...]; Unknown=[...]; Conflict=[...]; Decision=<brief next-step reason>
        ACTION: <exactly one valid WebShop action>
        Write evidence lists as JSON arrays of exact canonical constraint strings.

        Allowed actions:
        search[query]
        click[item]

        Decision procedure:

        1. Separate the requested product type/category from its attributes and options.
           Treat a category phrase as one product intent unless the instruction clearly
           specifies separate requirements.

        2. Evaluate each explicit constraint using current WebShop evidence:
           Use only the canonical constraint strings in Current episode state;
           do not rewrite or reinterpret them in the evidence lists.
           - Explicit: directly stated or clearly supported by an unambiguous close paraphrase.
           - Plausible: reasonably supported, but not directly or clearly established.
           - Unknown: neither supported nor contradicted by enough evidence.
           - Conflict: clearly inconsistent with the available information.
           Plausible is not fully verified; Unknown is not a failure.

        3. For a product candidate:
           Reuse its recorded evidence and inspected pages in Current episode state.
           Inspect Features and Description at most once each.
           - If there is a Conflict, continue with another candidate.
           - If important constraints are Unknown and informative product details
             are available, inspect them.
           - After relevant details have been inspected, judge the candidate from the
             total evidence and its relevance compared with alternatives.
           - Purchase a strong candidate when the product intent is supported,
             no explicit constraint conflicts with it, required options are selected,
             and further inspection is unlikely to add useful evidence.
           - If its status is decision, stop inspecting details and decide from
             existing evidence: buy, leave, or select a required option first.
             Navigate back to the product page if needed.
           Compare its evidence with best_candidate; keep the stronger candidate.
           Do not require every constraint to be Explicit, and never prefer Conflict.
           best_candidate is only relative best, not automatically eligible to buy.
           Buy only after inspection is complete, with no Conflict or Plausible,
           at most one Unknown, and every other constraint Explicit.
           With one Unknown, inspect another candidate before buying the first.

        4. On search results, inspect higher-ranked reasonable candidates first,
           especially those not yet fully checked; skip clear type conflicts.
           Ranking is a prior, not ground truth.
           Use the episode scratchpad to retain verified evidence and prior candidate
           outcomes.

        5. When reformulating a search, preserve the instruction's original meaning
           and polarity. Prefer the original product terms and the constraints most
           useful for retrieval. Do not repeat a normalized query in queries.
           Only after several reasonable candidates yield no stronger best_candidate,
           reformulate once using selected, shortened, or recombined original terms;
           keep all canonical constraints for final candidate evaluation.
           If further search yields no stronger candidate, reconsider best_candidate.

        The ACTION must be valid in the current Available actions.
        """

        if step is not None and self.max_steps - step + 1 <= 5:
            best_asin = episode_state["best_candidate"]
            best = episode_state["candidates"].get(best_asin)
            if best is None or not self.is_candidate_eligible(
                best, episode_state["constraints"]
            ):
                prompt += "\nCommit phase: no eligible best_candidate; do not force a purchase.\n"
            elif len(best["unknown"]) == 1 and self.completed_candidate_count(
                episode_state
            ) < 2:
                prompt += (
                    "\nCommit phase: best_candidate is eligible but still needs "
                    "one other inspected candidate for comparison; do not buy yet.\n"
                )
            elif episode_state["current_candidate"] == best_asin:
                prompt += (
                    "\nCommit phase: the eligible best_candidate is current; "
                    "stop exploring and finish legal navigation and purchase.\n"
                )
            elif any(
                str(item).casefold() == best_asin.casefold()
                for item in available_actions.get("clickables", [])
            ):
                prompt += (
                    "\nCommit phase: the eligible best_candidate is clickable now; "
                    "select it and finish its purchase using legal actions.\n"
                )
            else:
                prompt += (
                    "\nCommit phase: the eligible best_candidate is not currently "
                    "reachable; do not invent a click, recovery search, or purchase "
                    "a weaker candidate.\n"
                )

        if previous_invalid_action is not None:
            prompt += f"""
Previous action was invalid:
{previous_invalid_action}

Choose a valid action from the current Available actions.
"""

        response = openai.ChatCompletion.create(
            model="deepseek-flash",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0,
        )

        return response["choices"][0]["message"]["content"].strip()

    def parse_react_output(self, text):
        assessment_match = re.search(
            r"ASSESSMENT:\s*(.*?)(?=\nACTION:|$)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        action_match = re.search(
            r"ACTION:\s*((?:search|click)\[.*?\])",
            text,
            re.IGNORECASE,
        )
        assessment = (
            assessment_match.group(1).strip()
            if assessment_match else ""
        )
        action = (
            action_match.group(1).strip()
            if action_match else None
        )
        return assessment, action

    @staticmethod
    def update_candidate_evidence(episode_state, assessment):
        asin = episode_state["current_candidate"]
        if asin is None or asin not in episode_state["candidates"]:
            return

        try:
            decoder = json.JSONDecoder()
            evidence = {}
            for label in ("Explicit", "Plausible", "Unknown", "Conflict"):
                match = re.search(
                    r"(?:^|;\s*)" + label + r"=\s*", assessment
                )
                if match is None:
                    return
                value, _ = decoder.raw_decode(
                    assessment[match.end():].lstrip()
                )
                if not isinstance(value, list) or not all(
                    isinstance(item, str) for item in value
                ):
                    return
                evidence[label.lower()] = value

            constraints = episode_state["constraints"]
            listed = sum(evidence.values(), [])
            if any(item not in constraints for item in listed):
                return
            if len(listed) != len(set(listed)):
                return

            candidate = episode_state["candidates"][asin]
            candidate["explicit"] = evidence["explicit"]
            candidate["plausible"] = evidence["plausible"]
            candidate["conflict"] = evidence["conflict"]
            candidate["unknown"] = evidence["unknown"] + [
                item for item in constraints if item not in listed
            ]
            if (
                candidate["status"] == "active"
                and not candidate["unknown"]
                and NoMemoryReActAgent.is_candidate_eligible(candidate, constraints)
                and any(page in candidate["inspected"] for page in ("features", "description"))
            ):
                candidate["status"] = "decision"
            if candidate["status"] == "decision":
                NoMemoryReActAgent.update_best_candidate(episode_state, asin)
        except (TypeError, ValueError, KeyError):
            return

    @staticmethod
    def update_best_candidate(episode_state, asin):
        candidate = episode_state["candidates"][asin]
        best_asin = episode_state["best_candidate"]
        if candidate["conflict"]:
            if best_asin == asin:
                episode_state["best_candidate"] = None
            return
        if best_asin is None:
            episode_state["best_candidate"] = asin
            return
        if best_asin == asin:
            return

        best = episode_state["candidates"][best_asin]
        if best["conflict"]:
            episode_state["best_candidate"] = asin
        elif len(candidate["explicit"]) > len(best["explicit"]):
            episode_state["best_candidate"] = asin
        elif len(candidate["explicit"]) == len(best["explicit"]):
            if len(candidate["plausible"]) > len(best["plausible"]):
                episode_state["best_candidate"] = asin
            elif len(candidate["plausible"]) == len(best["plausible"]):
                if len(candidate["unknown"]) < len(best["unknown"]):
                    episode_state["best_candidate"] = asin

    @staticmethod
    def is_candidate_eligible(candidate, constraints):
        if candidate["conflict"] or candidate["plausible"]:
            return False
        if len(candidate["unknown"]) > 1:
            return False
        covered = candidate["explicit"] + candidate["unknown"]
        return (
            len(covered) == len(constraints)
            and len(covered) == len(set(covered))
            and set(covered) == set(constraints)
        )

    @staticmethod
    def completed_candidate_count(episode_state):
        return sum(
            candidate["status"] in ("decision", "purchased")
            or all(
                page in candidate["inspected"]
                for page in ("features", "description")
            )
            or asin == episode_state["best_candidate"]
            for asin, candidate in episode_state["candidates"].items()
        )

    @staticmethod
    def can_buy_current_candidate(episode_state, commit_phase=False):
        asin = episode_state["current_candidate"]
        if asin is None:
            return False
        candidate = episode_state["candidates"][asin]
        if candidate["status"] != "decision" or not NoMemoryReActAgent.is_candidate_eligible(
            candidate, episode_state["constraints"]
        ):
            return False
        if len(candidate["unknown"]) == 1 and NoMemoryReActAgent.completed_candidate_count(
            episode_state
        ) < 2:
            return False
        if commit_phase:
            best_asin = episode_state["best_candidate"]
            best = episode_state["candidates"].get(best_asin)
            if best is None or not NoMemoryReActAgent.is_candidate_eligible(
                best, episode_state["constraints"]
            ) or asin != best_asin:
                return False
        return True

    @staticmethod
    def update_candidate_action(episode_state, action, available_actions):
        search_match = re.fullmatch(r"search\[(.*?)\]", action, re.IGNORECASE)
        if search_match is not None:
            episode_state["queries"].append(
                " ".join(search_match.group(1).lower().split())
            )
            return

        match = re.fullmatch(r"click\[(.*?)\]", action, re.IGNORECASE)
        if match is None:
            return

        clicked = match.group(1).strip()
        asin = episode_state["current_candidate"]
        if re.fullmatch(r"[A-Z0-9]{10}", clicked, re.IGNORECASE) and re.search(
            r"\d", clicked
        ):
            asin = clicked.upper()
            episode_state["current_candidate"] = asin
            candidate = episode_state["candidates"].setdefault(asin, {
                "explicit": [],
                "plausible": [],
                "unknown": list(episode_state["constraints"]),
                "conflict": [],
                "inspected": ["product"],
                "status": "active",
            })
            candidate["status"] = "active"
            return

        if asin is None:
            return
        candidate = episode_state["candidates"][asin]
        page = clicked.lower()
        if page in ("features", "description") and page not in candidate["inspected"]:
            candidate["inspected"].append(page)
        elif page == "buy now":
            candidate["status"] = "purchased"
        elif page == "back to search" or (
            page == "prev" and any(
                str(item).lower() == "buy now"
                for item in available_actions.get("clickables", [])
            )
        ):
            candidate["status"] = "left"
            episode_state["current_candidate"] = None

    @staticmethod
    def update_candidate_decision(episode_state, available_actions):
        asin = episode_state["current_candidate"]
        if asin is None:
            return
        candidate = episode_state["candidates"][asin]
        if candidate["status"] != "active":
            return

        inspected = candidate["inspected"]
        clickables = {
            str(item).lower() for item in available_actions.get("clickables", [])
        }
        both_inspected = all(
            page in inspected for page in ("features", "description")
        )
        no_remaining_pages = "buy now" in clickables and not any(
            page in clickables and page not in inspected
            for page in ("features", "description")
        )
        if both_inspected or no_remaining_pages:
            candidate["status"] = "decision"
            NoMemoryReActAgent.update_best_candidate(episode_state, asin)

    def run(self, task):

        instruction = task["instruction"]
        task_id = task["task_id"]
        episode_state = {
            "constraints": list(task.get("instruction_attributes", [])),
            "candidates": {},
            "current_candidate": None,
            "best_candidate": None,
            "queries": [],
        }

        env = WebShopWrapper(
            num_products=self.num_products
        )

        observation = env.reset(task)
        scratchpad = []
        trajectory = []

        final_reward = 0.0
        success = False

        try:

            for step in range(1, self.max_steps + 1):

                available_actions = env.get_available_actions()
                recent_scratchpad = scratchpad[-6:]
                self.update_candidate_decision(episode_state, available_actions)
                commit_phase = self.max_steps - step + 1 <= 5

                llm_output = self.ask_deepseek(
                    instruction,
                    recent_scratchpad,
                    observation,
                    available_actions,
                    episode_state,
                    step=step,
                )

                assessment, action = self.parse_react_output(llm_output)

                invalid_action_retry = None
                buy_action = action is not None and re.fullmatch(
                    r"click\[buy now\]", action, re.IGNORECASE
                ) is not None
                if buy_action:
                    self.update_candidate_evidence(episode_state, assessment)
                buy_blocked = buy_action and not self.can_buy_current_candidate(
                    episode_state, commit_phase
                )

                if not is_valid_action(action, available_actions) or buy_blocked:
                    print(
                        f"Step {step}: invalid action {action!r}; "
                        f"LLM output: {llm_output}; retrying once"
                    )
                    invalid_action_retry = {
                        "llm_output": llm_output,
                        "assessment": assessment,
                        "action": action,
                    }
                    if buy_blocked:
                        retry_context = (
                            f"{action} (Buy Now blocked by eligibility or comparison gate)"
                        )
                    else:
                        retry_context = action if action is not None else llm_output
                    retry_output = self.ask_deepseek(
                        instruction,
                        recent_scratchpad,
                        observation,
                        available_actions,
                        episode_state,
                        previous_invalid_action=retry_context,
                        step=step,
                    )
                    assessment, action = self.parse_react_output(retry_output)
                    llm_output = retry_output
                    buy_action = action is not None and re.fullmatch(
                        r"click\[buy now\]", action, re.IGNORECASE
                    ) is not None
                    if buy_action:
                        self.update_candidate_evidence(episode_state, assessment)
                    buy_blocked = buy_action and not self.can_buy_current_candidate(
                        episode_state, commit_phase
                    )

                    if not is_valid_action(action, available_actions) or buy_blocked:
                        print(
                            f"Step {step}: retry invalid action {action!r}; "
                            f"LLM output: {llm_output}; ending episode"
                        )
                        trajectory.append({
                            "step": step,
                            "llm_output": llm_output,
                            "assessment": assessment,
                            "action": action,
                            "reward": None,
                            "done": False,
                            "observation_before": str(observation),
                            "observation_after": str(observation),
                            "available_actions": available_actions,
                            "executed": False,
                            "invalid_action_retry": invalid_action_retry,
                        })
                        break

                self.update_candidate_evidence(episode_state, assessment)

                print(
                    f"Step {step}: {action}"
                )

                observation_before = observation

                observation, reward, done, info = env.step(action)
                self.update_candidate_action(episode_state, action, available_actions)

                trajectory_step = {
                    "step": step,
                    "llm_output": llm_output,
                    "assessment": assessment,
                    "action": action,
                    "reward": reward,
                    "done": done,
                    "observation_before": str(observation_before),
                    "observation_after": str(observation),
                    "available_actions": available_actions,
                }
                if invalid_action_retry is not None:
                    trajectory_step["invalid_action_retry"] = invalid_action_retry
                trajectory.append(trajectory_step)

                scratchpad.append({
                    "step": step,
                    "assessment": assessment,
                    "action": action,
                    "reward": reward,
                    "done": done,
                })

                final_reward = reward

                if done:
                    success = reward > 0.999999
                    break

        finally:
            env.close()

        return make_result(
            task_id=task_id,
            instruction=instruction,
            success=success,
            reward=final_reward,
            trajectory=trajectory,
        )
