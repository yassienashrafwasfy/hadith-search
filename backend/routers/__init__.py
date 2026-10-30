"""Public router surface: `from routers import auth_router, make_search_router, ...`.

Loaded lazily so importing one router does not pull in the others. In particular
`make_search_router` imports the retrieval stack (NLTK/CAMeL/torch), which deployments with
search disabled (e.g. APP_MODE=annotation) deliberately avoid.
"""

from lazy_exports import install

install(
    globals(),
    {
        "annotation_router": ("routers.annotation", "router"),
        "auth_router": ("routers.auth", "router"),
        "benchmark_router": ("routers.benchmark", "router"),
        "hadiths_router": ("routers.hadiths", "router"),
        "kv_pairs_router": ("routers.kv_pairs", "router"),
        "make_root_router": ("routers.root", "make_root_router"),
        "make_search_router": ("routers.search", "make_search_router"),
        "suggestions_router": ("routers.suggestions", "router"),
    },
)
