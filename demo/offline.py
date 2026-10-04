"""Scripted synthetic environment/provider; no network and no benchmark claims."""

import copy
import json
import re
from pathlib import Path

from agent.llm_client import LLMClient
from gates.hard_constraints import normalize_option

FIXTURES = json.loads((Path(__file__).parent / "fixtures.json").read_text(encoding="utf-8"))


class ScriptedClient(LLMClient):
    def __init__(self):
        super().__init__(model="scripted-demo")

    def complete(self, prompt, purpose="policy", model=None):
        self.calls[purpose] += 1
        fixture = next((item for item in FIXTURES if item["instruction"] in prompt), FIXTURES[0])
        schema = fixture["schema"]
        if purpose in {"extract_from_instruction", "constraint_extraction"}:
            return json.dumps(schema)
        if purpose in {"validate_extraction", "constraint_validation"}:
            return json.dumps({"valid": True, "issues": [], "corrected_schema": schema})
        if purpose == "product_final_audit":
            return json.dumps({"decision": "ACCEPT", "problematic_constraints": [],
                               "reason": "Scripted audit of synthetic visible evidence"})
        if purpose in {"semantic_match", "constraint_semantic", "semantic_match_product_type", "product_type_semantic"}:
            visible = prompt.split("Candidate text:", 1)[-1] if "Candidate text:" in prompt else prompt
            status = "CONTRADICTED" if "Contains BPA" in visible else "MISSING"
            return json.dumps({"status": status, "evidence": "Contains BPA" if status == "CONTRADICTED" else "",
                               "reason": "Scripted synthetic semantic judgment"})
        actions = json.loads(re.search(r"Available actions: ([^\n]+)", prompt).group(1))
        memory = json.loads(re.search(r"Task-local candidate memory: ([^\n]+)", prompt).group(1))
        feedback = None
        if "Constraint feedback: " in prompt:
            feedback = json.JSONDecoder().raw_decode(prompt.split("Constraint feedback: ", 1)[1])[0]
        if feedback and feedback.get("revised_query"):
            action = f"search[{feedback['revised_query']}]"
        elif feedback and feedback.get("recommended_action"):
            action = feedback["recommended_action"]
        elif actions["page_type"] == "search":
            action = f"search[{schema['product_type']['canonical']}]"
        elif actions["page_type"] == "results":
            candidates = [pid for pid in actions["product_ids"] if pid.upper() not in memory["candidates"]]
            action = f"click[{candidates[0]}]" if candidates else f"search[{schema['product_type']['canonical']}]"
        elif actions["page_type"] == "product":
            option = None
            for group, values in actions["option_groups"].items():
                for value in values:
                    if re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", fixture["instruction"], re.I):
                        if normalize_option(actions["selected_options"].get(group, "")) != normalize_option(value):
                            option = value
                            break
                if option:
                    break
            action = f"click[{option}]" if option else "click[buy now]"
        else:
            action = "click[< prev]"
        return "ASSESSMENT: Scripted policy using visible state.\nACTION: " + action


class DemoEnvironment:
    def __init__(self, num_products=1000):
        self.actions = []
        self.closed = False

    def reset(self, task):
        self.fixture = copy.deepcopy(next(x for x in FIXTURES if x["instruction"] == task["instruction"]))
        self.page, self.current, self.selected, self.results = "search", None, {}, []
        return self.observation()

    def product(self):
        return next(x for x in self.fixture["products"] if x["id"] == self.current)

    def observation(self):
        prefix = "Instruction: [SEP] " + self.fixture["instruction"] + " [SEP] "
        if self.page == "search":
            return prefix + "Search"
        if self.page == "results":
            return prefix + " [SEP] ".join(x["id"] + " " + x["title"] for x in self.results)
        if self.page == "done":
            return prefix + "Purchase complete"
        product = self.product()
        if self.page in {"features", "description", "reviews"}:
            return prefix + product.get(self.page, "No reviews available")
        return prefix + f"{product['title']} [SEP] Brand: {product['brand']} [SEP] Price: " + "$" + str(product["price"])

    def get_available_actions(self):
        base = {"page_type": "detail" if self.page in {"features", "description", "reviews"} else self.page,
                "has_search_bar": self.page in {"search", "results"}, "product_ids": [],
                "option_groups": {}, "selected_options": {}, "clickables": []}
        if self.page == "results":
            base["product_ids"] = [x["id"] for x in self.results]
            base["clickables"] = list(base["product_ids"]) + ["back to search"]
        elif self.page == "product":
            base["option_groups"], base["selected_options"] = self.product()["options"], dict(self.selected)
            base["clickables"] = ["buy now", "features", "description", "reviews", "< prev", "back to search"]
            base["clickables"] += [value for values in self.product()["options"].values() for value in values]
        elif self.page in {"features", "description", "reviews"}:
            base["clickables"] = ["< prev", "back to search"]
        return base

    def step(self, action):
        self.actions.append(action)
        reward, done = 0.0, False
        if action.startswith("search["):
            query = normalize_option(action[7:-1]).replace("-", " ")
            def score(product):
                words = set(normalize_option(product["title"]).replace("-", " ").split())
                return len(words & set(query.split()))
            self.results = sorted(self.fixture["products"], key=score, reverse=True)
            self.page, self.current, self.selected = "results", None, {}
        else:
            value = action[6:-1]
            if value in {x["id"] for x in self.fixture["products"]}:
                self.current, self.page, self.selected = value, "product", {}
            elif value == "back to search":
                self.page, self.current = "search", None
            elif value == "< prev":
                self.page = "results" if self.page == "product" else "product"
                if self.page == "results":
                    self.current = None
            elif value in {"features", "description", "reviews"}:
                self.page = value
            elif value == "buy now":
                required = self.fixture["instruction_options"]
                option_score = sum(self.selected.get(key) == expected for key, expected in required.items()) / len(required)
                reward = self.product()["base_reward"] * option_score
                self.page, done = "done", True
            else:
                for group, values in self.product()["options"].items():
                    if value in values:
                        self.selected[group] = value
        return self.observation(), reward, done, {}

    def close(self):
        self.closed = True
