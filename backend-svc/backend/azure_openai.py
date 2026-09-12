"""Azure OpenAI client: embeddings (text-embedding-3-large) + chat (gpt-4o).

Auth precedence:
  1. If AZURE_OPENAI_KEY is set  -> API-key auth (works with only Contributor,
     which can read the resource keys; no RBAC role assignment needed).
  2. Otherwise                   -> DefaultAzureCredential (Entra ID), which
     requires the 'Cognitive Services OpenAI User' data-plane role.

Key auth is the pragmatic choice for local dev; migrate to Entra/Managed
Identity for production once role assignments are available.
"""
import os
from openai import AzureOpenAI

from backend import resilience, tracing

_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")
_EMBED_DEPLOY = os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")
_CHAT_DEPLOY = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")
_KEY = os.getenv("AZURE_OPENAI_KEY")
# Explicit per-call timeout so a hung request can't freeze the UI; our own retry
# layer (backend/resilience.py) handles backoff, so disable the SDK's built-in retries.
_TIMEOUT = float(os.getenv("AZURE_OPENAI_TIMEOUT", "60"))

_client = None


def _aoai() -> AzureOpenAI:
    global _client
    if _client is None:
        if _KEY:
            _client = AzureOpenAI(
                azure_endpoint=_ENDPOINT,
                api_version=_API_VERSION,
                api_key=_KEY,
                timeout=_TIMEOUT,
                max_retries=0,
            )
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
            _client = AzureOpenAI(
                azure_endpoint=_ENDPOINT,
                api_version=_API_VERSION,
                azure_ad_token_provider=token_provider,
                timeout=_TIMEOUT,
                max_retries=0,
            )
    return _client


def embed(text: str) -> list[float]:
    with tracing.model_call("embed", model=_EMBED_DEPLOY, input=text) as gen:
        resp = resilience.call(
            "openai-embed",
            lambda: _aoai().embeddings.create(model=_EMBED_DEPLOY, input=text))
        vec = resp.data[0].embedding
        gen.update(output=f"[{len(vec)}-dim vector]",
                   usage_details=tracing.usage_from_openai(resp))
        return vec


def embed_batch(texts: list[str]) -> list[list[float]]:
    """text-embedding-3 accepts a list; chunk if you exceed token/array limits."""
    if not texts:
        return []
    with tracing.model_call("embed_batch", model=_EMBED_DEPLOY,
                            input=f"{len(texts)} text chunks") as gen:
        resp = resilience.call(
            "openai-embed",
            lambda: _aoai().embeddings.create(model=_EMBED_DEPLOY, input=texts))
        vecs = [d.embedding for d in resp.data]
        gen.update(output=f"{len(vecs)} vectors",
                   usage_details=tracing.usage_from_openai(resp))
        return vecs


def chat(prompt: str, max_tokens: int = 250, deployment: str | None = None) -> str:
    client = _aoai()
    model = deployment or _CHAT_DEPLOY  # allow a caller (e.g. the eval judge) to override
    args = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    with tracing.model_call("chat", model=model, input=prompt,
                            model_parameters={"max_tokens": max_tokens}) as gen:
        def _create():
            try:
                return client.chat.completions.create(max_tokens=max_tokens, **args)
            except Exception as e:  # newer models require max_completion_tokens
                if "max_tokens" in str(e) or "max_completion_tokens" in str(e):
                    return client.chat.completions.create(max_completion_tokens=max_tokens, **args)
                raise
        resp = resilience.call("openai-chat", _create)
        text = resp.choices[0].message.content.strip()
        gen.update(output=text, usage_details=tracing.usage_from_openai(resp))
        return text


def chat_tools(messages: list, tools: list, tool_choice="auto", max_tokens: int = 600):
    """Tool-calling (function-calling) chat used by the agentic routing loop.

    Returns the raw response so the caller can read `.choices[0].message.tool_calls`.
    Requires a chat deployment that supports tool calling.
    """
    client = _aoai()
    with tracing.model_call("chat_tools", model=_CHAT_DEPLOY,
                            input=f"{len(messages)} messages",
                            model_parameters={"max_tokens": max_tokens}) as gen:
        def _create():
            args = {"model": _CHAT_DEPLOY, "messages": messages, "tools": tools,
                    "tool_choice": tool_choice}
            try:
                return client.chat.completions.create(max_tokens=max_tokens, **args)
            except Exception as e:  # newer models require max_completion_tokens
                if "max_tokens" in str(e) or "max_completion_tokens" in str(e):
                    return client.chat.completions.create(max_completion_tokens=max_tokens, **args)
                raise
        resp = resilience.call("openai-agent", _create)
        gen.update(output="(tool call)", usage_details=tracing.usage_from_openai(resp))
        return resp
