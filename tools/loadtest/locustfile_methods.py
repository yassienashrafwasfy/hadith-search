"""Load test with every search method at equal weight, to compare the methods with each other.

    LOADTEST_FILE=tools/loadtest/locustfile_methods.py tools/loadtest/run.sh LABEL 20 90 CONTAINER

The mixed file (`locustfile.py`) is weighted like a search page and does not call `term-overlap`
or `bm25-tf-idf`. Here the five keyword methods run in English and in Arabic and the three
semantic methods in Arabic, 13 tasks of the same weight, so each row of the report is one method.
"""

import random

from locust import HttpUser, between, task

try:  # run by locust (the folder is on the path) or imported from the repo root
    from locustfile import ARABIC, ENGLISH
except ImportError:  # pragma: no cover
    from tools.loadtest.locustfile import ARABIC, ENGLISH

KEYWORD = ("term-overlap", "tfidf", "bm25", "bm25-tf-idf", "bm25-prf")
SEMANTIC = ("cosine-similarity", "semantic-rerank", "semantic-rrf")


class MethodUser(HttpUser):
    wait_time = between(0.5, 2)

    def _search(self, method: str, lang: str):
        pool = ARABIC if lang == "ar" else ENGLISH
        with self.client.get(
            "/api/v1/searches",
            params={"q": random.choice(pool), "method": method, "lang": lang},
            name=f"{method} {lang}",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")

    @task
    def any_method(self):
        pairs = [(m, lang) for m in KEYWORD for lang in ("en", "ar")]
        pairs += [(m, "ar") for m in SEMANTIC]
        self._search(*random.choice(pairs))
