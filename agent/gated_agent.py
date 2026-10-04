"""Configurable Query/Product gates around the same baseline ReAct policy."""

from agent.react_agent import ReActAgent


class GatedAgent(ReActAgent):
    def __init__(self, num_products=1000, max_steps=20,
                 use_query_gate=True, use_product_gate=True, **kwargs):
        super().__init__(num_products=num_products, max_steps=max_steps,
                         use_query_gate=use_query_gate, use_product_gate=use_product_gate, **kwargs)
