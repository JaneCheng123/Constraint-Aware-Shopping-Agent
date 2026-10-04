"""The proposal's baseline: shared ReAct policy with both gates disabled."""

from agent.react_agent import ReActAgent


class BaselineAgent(ReActAgent):
    def __init__(self, num_products=1000, max_steps=20, **kwargs):
        kwargs.pop("use_query_gate", None)
        kwargs.pop("use_product_gate", None)
        super().__init__(num_products=num_products, max_steps=max_steps,
                         use_query_gate=False, use_product_gate=False, **kwargs)
