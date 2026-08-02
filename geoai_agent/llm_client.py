"""
LLM client for the GeoAI Agent core.

Targets a local Ollama server exposing an OpenAI-compatible API
(see local_llm_example.py). Any OpenAI-compatible endpoint can be used by
changing base_url / api_key / model.
"""

import json

from openai import OpenAI

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5:7b-instruct"


class LLMClient:
    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL, api_key: str = "ollama"):
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Plain-text completion."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content

    def chat_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Completion constrained to a single JSON object.

        Ollama's OpenAI-compatible endpoint supports response_format
        {"type": "json_object"}. We also strip markdown code fences as a
        defensive fallback in case the model wraps the JSON anyway.
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[len("json"):]
            raw = raw.strip()
        return json.loads(raw)
