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
        previous_invalid_action=None,
    ):

        prompt = f"""
You are a shopping agent operating WebShop.

User instruction:
{instruction}

Current episode scratchpad:
{json.dumps(scratchpad, ensure_ascii=False)}

Current observation:
{observation}

Available actions:
{json.dumps(available_actions, ensure_ascii=False)}

Output exactly two lines:
ASSESSMENT: <brief current-state assessment>
ACTION: <exactly one WebShop action>

The ACTION must be valid in the current Available actions.

Allowed formats:
search[query]
click[item]


Decision policy:

1. Identify the current page type:
   - search page
   - search results
   - product page
   - product details/features/reviews

2. Compare the current information against EVERY explicit user constraint.

3. On a product page:
   - If the available product information cannot verify a soft descriptive constraint after 
     reasonable inspection, do not automatically reject an otherwise strong candidate.
   - If a required constraint clearly fails, go back.
   - If all explicit constraints are satisfied and required options are selected, buy the product.
   - Do NOT leave a promising product page merely because some information is uncertain.

4. On search results:
   - Prefer inspecting relevant products over blindly paging.
   - Do not alternate next/prev without gaining new information.

5. Search again only when the current search strategy is exhausted or clearly poor.


Constraint interpretation rules:

- Do not invent stricter requirements than the user explicitly stated.
- Treat category phrases as WebShop category labels rather than requiring
  every word in the category name independently.
- Distinguish unknown/unverified from explicitly contradicted.
- Missing evidence does not mean failure.
- Reject only with clear evidence of violation.
- Do not use unstated real-world assumptions to reject a product.
- For example, the presence of active ingredients does not by itself
  contradict a "natural ingredients" requirement.


"""

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

    def run(self, task):

        instruction = task["instruction"]
        task_id = task["task_id"]

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

                llm_output = self.ask_deepseek(
                    instruction,
                    recent_scratchpad,
                    observation,
                    available_actions,
                )

                assessment, action = self.parse_react_output(llm_output)

                invalid_action_retry = None

                if not is_valid_action(action, available_actions):
                    print(
                        f"Step {step}: invalid action {action!r}; "
                        f"LLM output: {llm_output}; retrying once"
                    )
                    invalid_action_retry = {
                        "llm_output": llm_output,
                        "assessment": assessment,
                        "action": action,
                    }
                    retry_output = self.ask_deepseek(
                        instruction,
                        recent_scratchpad,
                        observation,
                        available_actions,
                        previous_invalid_action=(
                            action if action is not None else llm_output
                        ),
                    )
                    assessment, action = self.parse_react_output(retry_output)
                    llm_output = retry_output

                    if not is_valid_action(action, available_actions):
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

                print(
                    f"Step {step}: {action}"
                )

                observation_before = observation

                observation, reward, done, info = env.step(action)

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
                    success = reward > 0
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
