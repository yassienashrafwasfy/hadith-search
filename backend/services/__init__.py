"""Service layer: business logic that routers delegate to (lazy re-exports)."""

from lazy_exports import install

install(
    globals(),
    {
        "SearchContext": ("services.retrieval", "SearchContext"),
        "default_search_context": ("services.retrieval", "default_search_context"),
        "enabled_systems": ("services.retrieval", "enabled_systems"),
        "run_search": ("services.retrieval", "run_search"),
        "build_results": ("services.results", "build_results"),
        "summarize_query": ("services.agreement", "summarize_query"),
        "overall_summary": ("services.agreement", "overall_summary"),
    },
)
