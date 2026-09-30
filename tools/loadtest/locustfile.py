"""Load test for the search API.

    .venv/bin/locust -f tools/loadtest/locustfile.py --headless --host http://CONTAINER_IP:8000 \
        -u 20 -r 5 -t 90s --csv $OUT/run

Point --host at the app container, not at nginx, so the nginx rate limit is not measured.
Each request picks a random query, so the answer cache in the browser (ETag) plays no part.
The mix is weighted like a search page: mostly keyword search, some Arabic semantic search.
"""

import random

from locust import HttpUser, between, task

ENGLISH = [
    "prayer in the mosque",
    "fasting in ramadan",
    "charity to the poor",
    "patience in hardship",
    "honesty in trade",
    "rights of neighbours",
    "mercy to animals",
    "intention of deeds",
    "backbiting",
    "purification before prayer",
    "pilgrimage rituals",
    "kindness to parents",
    "forgiveness",
    "knowledge seeking",
    "inheritance shares",
    "oaths and vows",
]
ARABIC = [
    "حكم الصلاة في المسجد",
    "الصيام جنة",
    "فضل الصدقة",
    "الصبر على البلاء",
    "الصدق في البيع",
    "حق الجار",
    "الرحمة بالحيوان",
    "إنما الأعمال بالنيات",
    "الغيبة",
    "الوضوء قبل الصلاة",
    "مناسك الحج",
    "بر الوالدين",
    "التوبة والاستغفار",
    "طلب العلم",
    "الميراث",
    "الأيمان والنذور",
]


class SearchUser(HttpUser):
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

    @task(5)
    def bm25_prf_en(self):
        self._search("bm25-prf", "en")

    @task(3)
    def bm25_en(self):
        self._search("bm25", "en")

    @task(1)
    def tfidf_en(self):
        self._search("tfidf", "en")

    @task(3)
    def bm25_prf_ar(self):
        self._search("bm25-prf", "ar")

    @task(2)
    def bm25_ar(self):
        self._search("bm25", "ar")

    @task(2)
    def semantic_rrf_ar(self):
        self._search("semantic-rrf", "ar")

    @task(1)
    def cosine_ar(self):
        self._search("cosine-similarity", "ar")

    @task(1)
    def semantic_rerank_ar(self):
        self._search("semantic-rerank", "ar")

    @task(1)
    def methods(self):
        self.client.get("/api/v1/search-methods", name="search-methods")
