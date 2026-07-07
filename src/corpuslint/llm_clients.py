from __future__ import annotations

import os

from .llm import LLMClient

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_AZURE_API_VERSION = "2024-10-21"

# Generic, task-agnostic system prompt. The actual task (e.g. contradiction
# detection) lives in the caller's prompt, keeping this client reusable.
_DEFAULT_SYSTEM_PROMPT = "You are a precise assistant. Follow the user's instructions exactly."


class LLMClientError(RuntimeError):
    """Raised when an LLM backend cannot be constructed (missing extra, missing env, bad provider)."""


class _OpenAIChatClient:
    """Generic wrapper turning an openai (or Azure) client into the LLMClient Protocol."""

    def __init__(self, client: object, model: str, system_prompt: str = _DEFAULT_SYSTEM_PROMPT) -> None:
        self._client = client
        self._model = model
        self._system_prompt = system_prompt

    def complete(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""


def get_llm_client(provider: str, model: str) -> LLMClient:
    provider = (provider or "openai").lower()
    try:
        import openai
    except ImportError as e:
        raise LLMClientError(
            'The LLM backend needs the optional extra: pip install "corpuslint[llm]"'
        ) from e

    if provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise LLMClientError(
                "OPENAI_API_KEY is not set. Export it to use --llm with the openai provider."
            )
        client = openai.OpenAI()
        return _OpenAIChatClient(client, model or DEFAULT_OPENAI_MODEL)

    if provider == "azure":
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not api_key:
            raise LLMClientError(
                "AZURE_OPENAI_API_KEY is not set. Export it to use --llm with the azure provider."
            )
        if not endpoint:
            raise LLMClientError(
                "AZURE_OPENAI_ENDPOINT is not set. Export it to use --llm with the azure provider."
            )
        if not model:
            raise LLMClientError(
                "--llm-model (the Azure deployment name) is required for the azure provider."
            )
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION)
        client = openai.AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
        )
        return _OpenAIChatClient(client, model)

    raise LLMClientError(f"unknown LLM provider: {provider!r} (expected 'openai' or 'azure')")
