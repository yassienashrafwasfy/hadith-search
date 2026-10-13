"""LLM clients for the Quran RAG package: llama-server over HTTP and a scripted fake."""

import json

import httpx

from .types import LLMError

CHAT_PATH = "/v1/chat/completions"


class LlamaServerClient:
    """Talks to llama-server's OpenAI-compatible chat endpoint. Every failure is an LLMError."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        model: str = "default",
    ) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate_json(self, prompt: str, schema: dict) -> dict:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "strict": True, "schema": schema},
        }
        content = await self._complete(prompt, response_format=response_format)
        try:
            data = json.loads(content)
        except ValueError as error:
            raise LLMError(f"invalid JSON from model: {error}") from error
        _check_schema(data, schema)
        return data

    async def generate_text(self, prompt: str, max_tokens: int = 256) -> str:
        return (await self._complete(prompt, max_tokens=max_tokens)).strip()

    async def _complete(self, prompt: str, **extra: object) -> str:
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            **extra,
        }
        try:
            response = await self._client.post(CHAT_PATH, json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise LLMError(f"llama-server request failed: {error!r}") from error
        return _message_content(payload)


def _message_content(payload: object) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMError("unexpected llama-server response shape") from error
    if not isinstance(content, str):
        raise LLMError("model returned no text content")
    return content


_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def _check_schema(data: object, schema: dict) -> None:
    """Small JSON-schema subset: object root, required, property type, enum, array items."""
    if not isinstance(data, dict):
        raise LLMError("model JSON is not an object")
    for key in schema.get("required", []):
        if key not in data:
            raise LLMError(f"model JSON misses required key {key!r}")
    for key, sub in schema.get("properties", {}).items():
        if key in data:
            _check_value(data[key], sub, key)


def _check_value(value: object, sub: dict, path: str) -> None:
    kind = sub.get("type")
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    if kinds and not any(_is_type(value, k) for k in kinds):
        raise LLMError(f"model JSON key {path!r} has wrong type")
    if "enum" in sub and value not in sub["enum"]:
        raise LLMError(f"model JSON key {path!r} not in enum")
    if isinstance(value, list) and "items" in sub:
        for index, item in enumerate(value):
            _check_value(item, sub["items"], f"{path}[{index}]")
    if isinstance(value, dict) and "properties" in sub:
        _check_schema(value, sub)


def _is_type(value: object, kind: str) -> bool:
    expected = _TYPES.get(kind, object)
    if isinstance(value, bool) and kind in ("integer", "number"):
        return False
    return isinstance(value, expected)


class FakeLLM:
    """Scripted LLMClient for tests. Responses are consumed in order; prompts are recorded.

    A response that is an Exception instance is raised. When the script runs out, the last
    response of that kind repeats if `repeat_last` is set, else LLMError is raised.
    """

    def __init__(
        self,
        json_responses: list[dict | Exception] | None = None,
        text_responses: list[str | Exception] | None = None,
        repeat_last: bool = False,
    ) -> None:
        self._json = list(json_responses or [])
        self._text = list(text_responses or [])
        self._repeat_last = repeat_last
        self.prompts: list[str] = []
        self.schemas: list[dict] = []

    async def generate_json(self, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        result = self._next(self._json)
        if isinstance(result, Exception):
            raise result
        return result

    async def generate_text(self, prompt: str, max_tokens: int = 256) -> str:
        self.prompts.append(prompt)
        result = self._next(self._text)
        if isinstance(result, Exception):
            raise result
        return result

    def _next(self, script: list):
        if not script:
            raise LLMError("FakeLLM script exhausted")
        if len(script) == 1 and self._repeat_last:
            return script[0]
        return script.pop(0)
