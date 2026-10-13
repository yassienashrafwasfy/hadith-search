"""LangGraph agent: route -> retrieve -> draft -> critique -> {revise | web_search | finalize | fallback}.

Nodes are thin. The model only names evidence ids; `grounding` copies the text. Every failure
ends in `Answer(FALLBACK_SENTENCE, fallback=True)` with no generated content.
"""

import inspect
import operator
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from quran.grounding import (
    NO_INFO,
    Evidence,
    check_draft,
    compose_text,
    evidence_from_nodes,
    evidence_from_web,
    extractive_draft,
    parse_written,
    prompt_evidence,
    render,
)
from quran.types import FALLBACK_SENTENCE, Answer, Filters, LLMClient, LLMError, SourceInfo

MAX_EVIDENCE_CHARS = 500
ANSWER_TOKENS = 350


@dataclass
class Deps:
    """Everything the graph touches. Tests pass fakes.

    retrieve(source, question, filters) -> list[NodeRef]; web(query) -> list[WebPassage].
    Both may be sync or async. `choose_source` / `extract_filters` default to the selfquery
    and router modules.
    """

    controller: LLMClient
    answerer: LLMClient
    retrieve: Callable[..., Any]
    sources: list[SourceInfo]
    web: Callable[..., Any] | None = None
    max_loops: int = 2
    min_score: float | None = None
    choose_source: Callable[..., Any] | None = None
    extract_filters: Callable[..., Any] | None = None
    rerank: Callable[..., Any] | None = None  # (question, texts) -> scores in [0, 1]; None = off
    rerank_candidates: int = 12
    rerank_min_score: float = 0.0
    on_step: Callable[[str], None] | None = None  # called with a step name as it starts


class State(TypedDict, total=False):
    question: str
    source: str | None
    filters: Filters | None
    evidence: dict[str, Evidence]
    draft: dict | None
    feedback: str
    loops: int
    web_used: bool
    failed: bool
    no_info: bool
    verdict: str
    answer: Answer
    trace: Annotated[list[str], operator.add]


async def _call(fn: Callable[..., Any], *args: Any) -> Any:
    out = fn(*args)
    return await out if inspect.isawaitable(out) else out


def _fail(reason: str) -> State:
    return {"failed": True, "trace": [f"fail: {reason}"]}


def _lookup_fns(deps: Deps) -> tuple[Callable[..., Any], Callable[..., Any]]:
    if deps.choose_source and deps.extract_filters:
        return deps.choose_source, deps.extract_filters
    from quran.router import choose_source
    from quran.selfquery import extract_filters

    return deps.choose_source or choose_source, deps.extract_filters or extract_filters


async def route_node(state: State, deps: Deps) -> State:
    choose, extract = _lookup_fns(deps)
    try:
        name = await _call(choose, deps.controller, state["question"], deps.sources)
        info = next((s for s in deps.sources if s.name == name), None)
        filters = await _call(extract, deps.controller, state["question"], info) if info else None
    except LLMError as exc:
        return _fail(f"route {exc}")
    return {"source": name if info else None, "filters": filters, "trace": [f"route: {name}"]}


def _usable(deps: Deps, nodes: list) -> list:
    if deps.min_score is None:
        return nodes
    return [n for n in nodes if n.score >= deps.min_score]


async def retrieve_node(state: State, deps: Deps) -> State:
    nodes: list = []
    if state.get("source"):
        try:
            found = await _call(deps.retrieve, state["source"], state["question"], state["filters"])
            nodes = _usable(deps, found)
        except Exception as exc:  # noqa: BLE001 - any retrieval failure means no evidence
            return {"evidence": {}, "trace": [f"retrieve error: {type(exc).__name__}"]}
    return {"evidence": evidence_from_nodes(nodes), "trace": [f"retrieve: {len(nodes)} hits"]}


def _rank_evidence(
    evidence: dict[str, Evidence], scores: list[float], min_score: float
) -> dict[str, Evidence]:
    """Best score first, below `min_score` dropped, ids renumbered e1, e2 ... in the new order."""
    ranked = sorted(zip(scores, evidence.values()), key=lambda pair: pair[0], reverse=True)
    kept = [e for score, e in ranked if score >= min_score]
    return {f"e{i}": replace(e, id=f"e{i}") for i, e in enumerate(kept, start=1)}


async def rerank_node(state: State, deps: Deps) -> State:
    """Score the top candidates jointly with the question; keep the evidence in that order."""
    items = list(state["evidence"].values())[: deps.rerank_candidates]
    try:
        scores = [
            float(s) for s in await _call(deps.rerank, state["question"], [e.text for e in items])
        ]
    except Exception as exc:  # noqa: BLE001 - a broken reranker must not break the answer
        return {"trace": [f"rerank error: {type(exc).__name__}"]}
    if len(scores) != len(items):
        return {"trace": ["rerank error: wrong score count"]}
    kept = _rank_evidence({e.id: e for e in items}, scores, deps.rerank_min_score)
    return {
        "evidence": kept,
        "trace": [f"rerank: {len(kept)}/{len(items)} kept, top {max(scores):.2f}"],
    }


def draft_prompt(state: State) -> str:
    shown = prompt_evidence(state["evidence"])
    lines = [f"[{e.id}] {e.text[:MAX_EVIDENCE_CHARS]}" for e in shown.values()]
    feedback = f"\nملاحظة: {state['feedback']}" if state.get("feedback") else ""
    return (
        "أجب عن السؤال المطروح فقط بالعربية الفصحى وحدها، دون أي كلمة أجنبية، في جملتين إلى "
        "ثلاث جمل، اعتمادًا على المصادر التالية فقط. ضع بعد كل جملة رمز المصدر الذي اعتمدت "
        "عليه، مثل [e1]. لا تتحدث عن موضوع آخر ولا تضف معلومة ليست في المصادر. إذا لم تكفِ "
        f"المصادر للإجابة فاكتب فقط: {NO_INFO}\n"
        f"السؤال: {state['question']}\nالمصادر:\n" + "\n".join(lines) + feedback
    )


async def draft_node(state: State, deps: Deps) -> State:
    try:
        raw = await deps.answerer.generate_text(draft_prompt(state), max_tokens=ANSWER_TOKENS)
    except LLMError as exc:
        return _fail(f"draft {exc}")
    draft = parse_written(raw, prompt_evidence(state["evidence"]))
    return {"draft": draft, "no_info": NO_INFO in raw, "trace": ["draft"]}


def _can_web(state: State, deps: Deps) -> bool:
    return deps.web is not None and not state.get("web_used")


def decide(state: State, deps: Deps) -> tuple[str, str]:
    """Pure decision after a draft: (verdict, feedback)."""
    problem = check_draft(state.get("draft"), state["evidence"])
    if problem is None:
        return "finalize", ""
    if state.get("no_info"):  # the writer says the passages do not answer the question
        return ("web_search" if _can_web(state, deps) else "fallback"), problem
    if state.get("loops", 0) < deps.max_loops:
        return "revise", problem
    if _can_web(state, deps):
        return "web_search", problem
    # The evidence is there and only the small model failed to format a draft: answer from it.
    return ("finalize" if state["evidence"] else "fallback"), problem


async def critique_node(state: State, deps: Deps) -> State:
    verdict, feedback = decide(state, deps)
    return {"verdict": verdict, "feedback": feedback, "trace": [f"critique: {verdict}"]}


async def revise_node(state: State, deps: Deps) -> State:
    return {"loops": state.get("loops", 0) + 1, "trace": ["revise"]}


async def web_node(state: State, deps: Deps) -> State:
    if deps.on_step and _can_web(state, deps):
        deps.on_step("web_search")
    passages: list = []
    if _can_web(state, deps):
        try:
            passages = await _call(deps.web, state["question"])
        except Exception as exc:  # noqa: BLE001 - web failure means no web evidence
            return {"web_used": True, "failed": True, "trace": [f"web error: {type(exc).__name__}"]}
    merged = {**state.get("evidence", {}), **evidence_from_web(passages)}
    return {
        "web_used": True,
        "evidence": merged,
        "failed": not passages,
        "loops": 0,
        "trace": [f"web: {len(passages)} passages"],
    }


async def finalize_node(state: State, deps: Deps) -> State:
    draft = state["draft"]
    if check_draft(draft, state["evidence"]) is not None:
        draft = extractive_draft(state["evidence"])
    citations = render(draft["citations"], state["evidence"])
    text = compose_text(draft["lead_in"], citations)
    return {"answer": Answer(text, citations, state.get("trace", []) + ["finalize"])}


async def fallback_node(state: State, deps: Deps) -> State:
    trace = state.get("trace", []) + ["fallback"]
    return {"answer": Answer(FALLBACK_SENTENCE, [], trace, fallback=True)}


def _after_route(state: State) -> str:
    return "fallback" if state.get("failed") else "retrieve"


def _after_retrieve(deps: Deps) -> Callable[[State], str]:
    def edge(state: State) -> str:
        if state["evidence"]:
            return "draft"
        return "web_search" if _can_web(state, deps) else "fallback"

    return edge


def _before_rerank(deps: Deps) -> Callable[[State], str]:
    after = _after_retrieve(deps)

    def edge(state: State) -> str:
        step = after(state)
        return "rerank" if step == "draft" else step

    return edge


def _after_draft(state: State) -> str:
    return "fallback" if state.get("failed") else "critique"


def _after_critique(state: State) -> str:
    return state["verdict"]


def _after_web(state: State) -> str:
    return "fallback" if state.get("failed") else "draft"


def _bind(fn: Callable[..., Any], deps: Deps) -> Callable[[State], Any]:
    async def node(state: State) -> State:
        return await fn(state, deps)

    return node


def build_graph(deps: Deps) -> Any:
    g = StateGraph(State)
    nodes = {
        "route": route_node,
        "retrieve": retrieve_node,
        "draft": draft_node,
        "critique": critique_node,
        "revise": revise_node,
        "web_search": web_node,
        "finalize": finalize_node,
        "fallback": fallback_node,
    }
    for name, fn in nodes.items():
        g.add_node(name, _bind(fn, deps))
    g.add_edge(START, "route")
    g.add_conditional_edges("route", _after_route, ["retrieve", "fallback"])
    if deps.rerank is None:
        g.add_conditional_edges(
            "retrieve", _after_retrieve(deps), ["draft", "web_search", "fallback"]
        )
    else:
        g.add_node("rerank", _bind(rerank_node, deps))
        g.add_conditional_edges(
            "retrieve", _before_rerank(deps), ["rerank", "web_search", "fallback"]
        )
        g.add_conditional_edges(
            "rerank", _after_retrieve(deps), ["draft", "web_search", "fallback"]
        )
    g.add_conditional_edges("draft", _after_draft, ["critique", "fallback"])
    g.add_conditional_edges(
        "critique", _after_critique, ["revise", "web_search", "finalize", "fallback"]
    )
    g.add_edge("revise", "draft")
    g.add_conditional_edges("web_search", _after_web, ["draft", "fallback"])
    g.add_edge("finalize", END)
    g.add_edge("fallback", END)
    return g.compile()


async def answer_question(question: str, deps: Deps) -> Answer:
    """Run the graph. Anything unexpected still yields the fixed fallback."""
    try:
        final = await build_graph(deps).ainvoke(
            {"question": question, "loops": 0, "web_used": False, "trace": []},
            {"recursion_limit": 6 * (deps.max_loops + 3) + 10},
        )
        return final["answer"]
    except Exception:  # noqa: BLE001 - safety net: never leak partial generated content
        return Answer(FALLBACK_SENTENCE, [], ["fallback: graph error"], fallback=True)
