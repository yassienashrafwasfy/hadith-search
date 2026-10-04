"""Behaviour tests: the Gherkin files in docs/behaviours run on the same fixtures as the other tests.

pytest-bdd has no async steps, so the steps are plain functions and `_api` runs each request on a
private event loop. Scenarios tagged @nginx or @manual need something pytest cannot start, so
they are skipped here with the reason below instead of being faked.
"""

import asyncio

import pytest
from pytest_bdd import parsers, then

_SKIPPED_TAGS = {
    "nginx": "needs the nginx image; tools/test-nginx.sh checks these against a stub app",
    "manual": "needs a browser; the web page has no automated test runner",
}


def pytest_collection_modifyitems(items):
    for item in items:
        for tag, reason in _SKIPPED_TAGS.items():
            if item.get_closest_marker(tag):
                item.add_marker(pytest.mark.skip(reason=f"@{tag}: {reason}"))


@pytest.fixture
def _ctx() -> dict:
    """What the steps of one scenario hand to each other."""
    return {}


async def _await(awaitable):
    return await awaitable


@pytest.fixture
def _run():
    """Run a coroutine, task or future to its end on one event loop that lasts for the scenario."""
    with asyncio.Runner() as runner:
        yield lambda awaitable: runner.run(
            awaitable if asyncio.iscoroutine(awaitable) else _await(awaitable)
        )


class _Api:
    """A synchronous front for an async test client; the last answers go to `ctx["responses"]`."""

    def __init__(self, client, run, ctx):
        self._client, self._run, self._ctx = client, run, ctx

    def __call__(self, method: str, url: str, **kwargs):
        headers = {**self._ctx.get("headers", {}), **kwargs.pop("headers", {})}
        response = self._run(self._client.request(method, url, headers=headers, **kwargs))
        self._ctx["responses"] = [response]
        return response

    def many(self, calls):
        """Make several calls (method, url, kwargs); all their answers are kept."""
        responses = [self(method, url, **kwargs) for method, url, kwargs in calls]
        self._ctx["responses"] = responses
        return responses


@pytest.fixture
def _api(_client, _run, _ctx) -> _Api:
    return _Api(_client, _run, _ctx)


@pytest.fixture
def _search_api(_search_client, _run, _ctx) -> _Api:
    return _Api(_search_client, _run, _ctx)


@then(parsers.parse("the status is {code:d}"))
def _status_is(_ctx, code):
    assert [r.status_code for r in _ctx["responses"]] == [code] * len(_ctx["responses"])


@then(parsers.parse("the content type is {content_type}"))
def _content_type_is(_ctx, content_type):
    assert all(r.headers["content-type"].startswith(content_type) for r in _ctx["responses"]), [
        r.headers["content-type"] for r in _ctx["responses"]
    ]
