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
        self.provider_models = Counter()
        self.input_tokens = 0
        self.output_tokens = 0
        self._sdk = None
        self._http = None
        self.last_response_metadata = None

    def complete(self, prompt, purpose="policy", model=None):
        self.calls[purpose] += 1
        self.last_response_metadata = None
        try:
            if self._sdk is None and self._http is None:
                if not self.api_key:
                    raise RuntimeError("Set DEEPSEEK_API_KEY before a live run.")
                try:
                    import openai
                except ImportError as exc:
                    raise RuntimeError("Install requirements-agent.txt for live LLM calls.") from exc
                if hasattr(openai, "OpenAI"):
                    # Count every attempt; the SDK must not hide retries.
                    self._sdk = openai.OpenAI(api_key=self.api_key, base_url=self.base_url,
                                              timeout=self.timeout, max_retries=0)
                else:
                    # Existing WebShop environments commonly use openai 0.28.
                    # Keep that environment intact and use a per-client HTTP
                    # transport, avoiding its global configuration and retries.
                    import requests
                    self._http = requests.Session()
                    self._http.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
            arguments = {"model": model or self.model,
                         "messages": [{"role": "user", "content": prompt}], "temperature": 0}
            if self._http is not None:
                response = self._http.post(self.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.api_key},
                    json=arguments, timeout=self.timeout)
                response.raise_for_status()
                response = response.json()
                self.last_response_metadata = {key: response.get(key) for key in ("id", "model", "system_fingerprint")}
                self.provider_models[self.last_response_metadata["model"] or "unreported"] += 1
                usage = response.get("usage") or {}
                self.input_tokens += usage.get("prompt_tokens") or 0
                self.output_tokens += usage.get("completion_tokens") or 0
                return response["choices"][0]["message"].get("content") or ""
            response = self._sdk.chat.completions.create(**arguments)
            self.last_response_metadata = {key: getattr(response, key, None) for key in ("id", "model", "system_fingerprint")}
            self.provider_models[self.last_response_metadata["model"] or "unreported"] += 1
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
                "output_tokens": self.output_tokens, "provider_model_counts": dict(self.provider_models)}

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
        "provider_model_counts": {
            key: value - before.get("provider_model_counts", {}).get(key, 0)
            for key, value in after.get("provider_model_counts", {}).items()
        },
    }
