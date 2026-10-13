import json

import httpx
import pytest

from quran.llm import FakeLLM, LlamaServerClient
from quran.types import LLMError

SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": ["quran", "tafsir"]},
        "n": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["source"],
}


def _client(handler):
    return LlamaServerClient("http://llm", timeout=1, transport=httpx.MockTransport(handler))


def _reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_generate_json_sends_schema_and_parses():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _reply('{"source": "quran", "n": 2, "tags": ["a"]}')

    result = await _client(handler).generate_json("q?", SCHEMA)
    assert result == {"source": "quran", "n": 2, "tags": ["a"]}
    assert seen["url"] == "http://llm/v1/chat/completions"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert seen["body"]["messages"][0]["content"] == "q?"


async def test_generate_text_strips_and_limits_tokens():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _reply("  hello \n")

    assert await _client(handler).generate_text("hi", max_tokens=7) == "hello"
    assert seen["body"]["max_tokens"] == 7
    assert "response_format" not in seen["body"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1]",
        '{"n": 1}',
        '{"source": "web"}',
        '{"source": "quran", "n": "x"}',
        '{"source": "quran", "n": true}',
        '{"source": "quran", "tags": [1]}',
    ],
)
async def test_schema_violations_raise(content):
    with pytest.raises(LLMError):
        await _client(lambda r: _reply(content)).generate_json("q", SCHEMA)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
async def test_http_and_shape_failures_raise(response):
    with pytest.raises(LLMError):
        await _client(lambda r: response).generate_text("q")


async def test_timeout_and_connection_errors_raise():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("down", request=request)

    for handler in (timeout, refused):
        with pytest.raises(LLMError):
            await _client(handler).generate_json("q", SCHEMA)


async def test_fake_llm_script_records_and_errors():
    fake = FakeLLM(json_responses=[{"a": 1}, LLMError("bad")], text_responses=["t"])
    assert await fake.generate_json("p1", SCHEMA) == {"a": 1}
    with pytest.raises(LLMError):
        await fake.generate_json("p2", SCHEMA)
    assert await fake.generate_text("p3") == "t"
    with pytest.raises(LLMError):
        await fake.generate_text("p4")
    assert fake.prompts == ["p1", "p2", "p3", "p4"]
    assert fake.schemas == [SCHEMA, SCHEMA]


async def test_fake_llm_repeat_last():
    fake = FakeLLM(text_responses=["x"], repeat_last=True)
    assert [await fake.generate_text("a"), await fake.generate_text("b")] == ["x", "x"]
