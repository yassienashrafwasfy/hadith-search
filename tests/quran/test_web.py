import httpx
import pytest

from quran.web import ALLOWED_HOSTS, DISABLED_HOSTS, AllowListedWeb, is_allowed_url

JSON = {"content-type": "application/json"}


def _web(handler) -> AllowListedWeb:
    return AllowListedWeb(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize(
    "url",
    [
        "https://api.quran.com/api/v4/search?q=x",
    ],
)
def test_allowed_urls(url):
    assert is_allowed_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.quran.com/x",
        "https://evil-api.quran.com/",
        "https://api.quran.com.evil.io/",
        "https://evil.io/api.quran.com",
        "https://api.quran.com@evil.io/",
        "https://user:pw@api.quran.com/",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://169.254.169.254/",
        "https://localhost/",
        "https://quran.ksu.edu.sa/",
        "https://islamweb.net/",
        "https://quran.com/2",
        "ftp://api.quran.com/",
        "https://api.quran.com:bad/",
        "",
    ],
)
def test_rejected_urls(url):
    assert not is_allowed_url(url)


def test_unverified_hosts_are_separate():
    assert not set(ALLOWED_HOSTS) & set(DISABLED_HOSTS)


def _api(request):
    if request.url.path.endswith("/search"):
        q = request.url.params["q"]
        rows = {"صبر": ["2:153", "2:155"], "فضل": ["2:153"]}.get(q, [])
        results = [{"verse_key": k, "text": f"نص {k}"} for k in rows]
        return httpx.Response(200, headers=JSON, json={"search": {"results": results}})
    return httpx.Response(200, headers=JSON, json={"tafsir": {"text": "<p>تفسير <b>الآية</b></p>"}})


async def test_search_returns_verses_and_their_tafsir_best_overlap_first():
    out = await _web(_api).search("ما فضل الصبر؟")
    assert [p.text for p in out] == [
        "(2:153) نص 2:153",
        "تفسير الآية",
        "(2:155) نص 2:155",
        "تفسير الآية",
    ]
    assert out[0].url == "https://quran.com/2:153"
    assert out[1].url == "https://quran.com/2:153/tafsirs/ar-tafsir-ibn-kathir"


async def test_no_matching_word_means_no_passages():
    assert await _web(_api).search("ما حكم الموسيقى؟") == []


async def test_only_the_allowed_api_host_is_requested():
    seen = set()

    def handler(request):
        seen.add(request.url.host)
        return _api(request)

    await _web(handler).search("الصبر")
    assert seen == {"api.quran.com"}


async def test_redirect_to_off_list_is_rejected():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "evil.io":
            return httpx.Response(200, headers=JSON, text="{}")
        return httpx.Response(302, headers={"location": "https://evil.io/x"})

    assert await _web(handler).search("الصبر") == []
    assert "evil.io" not in seen


async def test_redirect_to_internal_ip_is_rejected():
    def handler(request):
        if request.url.host == "169.254.169.254":
            raise AssertionError("must not be requested")
        return httpx.Response(302, headers={"location": "https://169.254.169.254/"})

    assert await _web(handler).search("الصبر") == []


async def test_redirect_downgrade_to_http_is_rejected():
    def handler(request):
        if request.url.scheme == "http":
            raise AssertionError("must not be requested")
        return httpx.Response(302, headers={"location": "http://api.quran.com/x"})

    assert await _web(handler).search("الصبر") == []


async def test_on_list_redirect_is_followed_and_rechecked():
    def handler(request):
        if request.url.path == "/final":
            return httpx.Response(
                200,
                headers=JSON,
                json={"search": {"results": [{"verse_key": "1:1", "text": "ok"}]}},
            )
        return httpx.Response(301, headers={"location": "/final"})

    out = await _web(handler).search("الصبر")
    assert out and out[0].text == "(1:1) ok"


async def test_redirect_loop_is_capped():
    def handler(request):
        return httpx.Response(302, headers={"location": request.url.path + "x"})

    assert await _web(handler).search("الصبر") == []


async def test_non_json_is_dropped():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text="{}")

    assert await _web(handler).search("الصبر") == []


async def test_oversized_body_is_dropped():
    def handler(request):
        return httpx.Response(200, headers=JSON, content=b"a" * (600 * 1024))

    assert await _web(handler).search("الصبر") == []


async def test_timeout_and_http_errors_give_empty():
    def timeout(request):
        raise httpx.ConnectTimeout("slow")

    def server_error(request):
        return httpx.Response(500, headers=JSON, text="{}")

    assert await _web(timeout).search("الصبر") == []
    assert await _web(server_error).search("الصبر") == []


async def test_blank_query_makes_no_request():
    def handler(request):
        raise AssertionError("no request expected")

    assert await _web(handler).search("   ") == []
