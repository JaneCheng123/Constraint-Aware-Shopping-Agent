"""One injectable, counted client shared by the policy and both gates."""

import os
from collections import Counter


class LLMClient:
    def __init__(self, model=None, api_key=None, base_url=None, timeout=60):
        self.model = model or os.getenv("SHOPPING_MODEL", "deepseek-chat")
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.timeout = timeout
        self.calls = Counter()
        self.errors = Counter()
        self.input_tokens = 0
        self.output_tokens = 0
        self._sdk = None

    def complete(self, prompt, purpose="policy", model=None):
        self.calls[purpose] += 1
        try:
            if self._sdk is None:
                if not self.api_key:
                    raise RuntimeError("Set DEEPSEEK_API_KEY before a live run.")
                try:
                    from openai import OpenAI
                except ImportError as exc:
                    raise RuntimeError("Install requirements-agent.txt for live LLM calls.") from exc
                # Count every attempt; the SDK must not hide retries from evaluation.
                self._sdk = OpenAI(api_key=self.api_key, base_url=self.base_url,
                                   timeout=self.timeout, max_retries=0)
            response = self._sdk.chat.completions.create(
                model=model or self.model,
                messages=[{"role": "user", "content": prompt}], temperature=0,
            )
            if response.usage:
                self.input_tokens += response.usage.prompt_tokens or 0
                self.output_tokens += response.usage.completion_tokens or 0
            return response.choices[0].message.content or ""
        except Exception:
            self.errors[purpose] += 1
            raise

    def snapshot(self):
        return {"llm_calls": sum(self.calls.values()), "calls_by_purpose": dict(self.calls),
                "llm_errors": sum(self.errors.values()), "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens}

    def create(self, model, messages, temperature=0, purpose="gate"):
        """Mapping response for the inherited gate code, using the same counted client."""
        text = self.complete("\n".join(item["content"] for item in messages), purpose=purpose, model=model)
        return {"choices": [{"message": {"content": text}}]}


def usage_delta(before, after):
    return {
        "llm_calls": after["llm_calls"] - before["llm_calls"],
        "llm_errors": after["llm_errors"] - before["llm_errors"],
        "input_tokens": after["input_tokens"] - before["input_tokens"],
        "output_tokens": after["output_tokens"] - before["output_tokens"],
        "calls_by_purpose": {
            key: value - before["calls_by_purpose"].get(key, 0)
            for key, value in after["calls_by_purpose"].items()
        },
    }
