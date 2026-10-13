"""Retrieval over one Quran source: filtered cosine search, auto-merging and passage lookup.

Search runs over the ayah leaves only. Every text returned is the ORIGINAL text of a node, never a
summary. All reads are ORM constructs over the table from `quran.models.node_table`.
"""

import re
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from quran.models import node_table
from quran.types import Filters, Level, NodeRef


def _ref(source: str, row, score: float = 0.0) -> NodeRef:
    return NodeRef(
        source=source,
        node_id=row.id,
        level=Level(row.level),
        text=row.text,
        surah=row.surah,
        ayah_from=row.ayah_from,
        ayah_to=row.ayah_to,
        score=score,
    )


def _conditions(table, filters: Filters) -> list:
    """WHERE clauses for the set filters; an ayah range keeps every node that overlaps it."""
    c = table.c
    f = filters
    clauses = {
        "surah": lambda: c.surah == f.surah,
        "ayah_from": lambda: c.ayah_to >= f.ayah_from,
        "ayah_to": lambda: c.ayah_from <= f.ayah_to,
        "juz": lambda: c.juz == f.juz,
        "place": lambda: c.place == getattr(f.place, "value", f.place),
        "author": lambda: c.author == f.author,
    }
    return [build() for name, build in clauses.items() if getattr(f, name) is not None]


RRF_K = 60
# A keyword is evidence only if it is distinctive: it occurs in at least one ayah but in no more than
# this share of them (a floor keeps tiny corpora usable). Common words such as "يوم" prove nothing.
MAX_DF_SHARE = 0.03
MIN_DF_CAP = 3
# ... and at least this share of the question's content words must be distinctive hits, so one
# lucky word in an off-topic question is not enough.
MIN_RARE_SHARE = 0.4
WORD = re.compile(r"\w+")
# Clitics that attach to an Arabic word: a keyword matches the whole word with these around it, so
# "كرسي" finds "كرسيه" and "الصبر" finds "والصبر" but "ذكي" does not match inside "الذكر".
PREFIXES = "(و|ف)?(ب|ل|ك)?(ال)?"
SUFFIXES = "(ه|ها|هم|هما|ك|كم|نا|ي|ة|ات|ان|ين|ون)?"
MIN_TOKEN_LENGTH = 3
# Question words and particles that match nearly every ayah
STOP_WORDS = frozenset(
    (
        "ما ماذا من هو هي هل في على عن الى الي الذي التي هذا هذه ذلك كيف لماذا اين متى كم قال يقول "
        # words about the request, and the names of the tafsir authors, not about the topic
        "تفسير فسر معنى شرح اشرح آية اية سورة بيان المراد يقصد القرآن قرآن الكريم "
        "ابن كثير الطبري السعدي الميسر البغوي ذكر ورد جاء"
    ).split()
)


def keyword_tokens(query: str, encode_text=None) -> list[str]:
    """Content words of a query in the form `search_text` uses, without the article ال."""
    if encode_text is None:
        from scripts.arabic_encoder import encoding_text as encode_text
    tokens = []
    for word in WORD.findall(encode_text(query)):
        if word in STOP_WORDS:
            continue
        stem = word[2:] if word.startswith("ال") and len(word) - 2 >= MIN_TOKEN_LENGTH else word
        if len(stem) >= MIN_TOKEN_LENGTH and stem not in tokens:
            tokens.append(stem)
    return tokens


def _has_word(table, token: str):
    """SQL condition: `search_text` contains `token` as a whole word (with its clitics)."""
    pattern = f"(^| ){PREFIXES}{re.escape(token)}{SUFFIXES}( |$)"
    return table.c.search_text.op("~")(pattern)


def _dense(table, qvec, filters: Filters, k: int):
    distance = table.c.embedding.cosine_distance([float(x) for x in qvec])
    return (
        select(table, (1 - distance).label("score"))
        .where(table.c.level == Level.AYAH.value, table.c.embedding.is_not(None))
        .where(*_conditions(table, filters))
        .order_by(distance, table.c.id)
        .limit(k)
    )


def _keyword(table, tokens: list[str], filters: Filters, k: int):
    matched = sum((case((_has_word(table, t), 1), else_=0) for t in tokens))
    return (
        select(table, matched.label("score"))
        .where(table.c.level == Level.AYAH.value, matched > 0)
        .where(*_conditions(table, filters))
        .order_by(matched.desc(), table.c.id)
        .limit(k)
    )


def _fuse(source: str, ranked: list[list], k: int) -> list[NodeRef]:
    """Reciprocal rank fusion of several ranked row lists; the score is the fused score."""
    fused: dict[int, float] = defaultdict(float)
    rows: dict[int, object] = {}
    for rows_of_one in ranked:
        for rank, row in enumerate(rows_of_one, start=1):
            fused[row.id] += 1.0 / (RRF_K + rank)
            rows.setdefault(row.id, row)
    best = sorted(fused, key=lambda node_id: (-fused[node_id], node_id))[:k]
    return [_ref(source, rows[node_id], fused[node_id]) for node_id in best]


def _rare_tokens(session: Session, table, tokens: list[str], filters: Filters) -> list[str]:
    """The tokens that occur in some ayah but in few: the ones that can justify an answer."""
    if not tokens:
        return []
    counts = [func.count(case((_has_word(table, t), 1))) for t in tokens]
    stmt = (
        select(func.count(), *counts)
        .where(table.c.level == Level.AYAH.value)
        .where(*_conditions(table, filters))
    )
    total, *dfs = session.execute(stmt).one()
    cap = max(MIN_DF_CAP, MAX_DF_SHARE * total)
    rare = [t for t, df in zip(tokens, dfs) if 0 < df <= cap]
    return rare if len(rare) / len(tokens) >= MIN_RARE_SHARE else []


def search_source(
    session: Session,
    source: str,
    qvec: Sequence[float],
    filters: Filters,
    k: int,
    query: str | None = None,
) -> list[NodeRef]:
    """Top-k ayah leaves, best first; filters apply before the top-k cut.

    With only `qvec` the order is cosine similarity and the score is the similarity. With the
    question text too, results are the ayat that contain a distinctive question word, ordered by
    reciprocal-rank fusion of the keyword and vector rankings. No distinctive word means no
    result: a 64-dimension encoder cannot tell an on-topic question from an off-topic one
    (similarities overlap), so lexical evidence is what lets the agent say "no information".
    """
    if k <= 0:
        return []
    table = node_table(source)
    if not query:
        dense = list(session.execute(_dense(table, qvec, filters, k)))
        return [_ref(source, row, float(row.score)) for row in dense]
    tokens = _rare_tokens(session, table, keyword_tokens(query), filters)
    if not tokens:
        return []
    keyword = list(session.execute(_keyword(table, tokens, filters, k)))
    dense = list(session.execute(_dense(table, qvec, filters, k)))
    kept = {row.id for row in keyword}
    return _fuse(source, [[r for r in dense if r.id in kept], keyword], k)


def _parents_of(session: Session, source: str, ids: list[int]) -> dict[int, int]:
    table = node_table(source)
    stmt = select(table.c.id, table.c.parent_id).where(
        table.c.id.in_(ids), table.c.parent_id.is_not(None)
    )
    return {node_id: parent for node_id, parent in session.execute(stmt)}


def _child_counts(session: Session, source: str, parents: list[int]) -> dict[int, int]:
    table = node_table(source)
    stmt = (
        select(table.c.parent_id, func.count())
        .where(table.c.parent_id.in_(parents))
        .group_by(table.c.parent_id)
    )
    return {parent: count for parent, count in session.execute(stmt)}


def _parent_refs(session: Session, source: str, ids: list[int]) -> dict[int, object]:
    table = node_table(source)
    return {row.id: row for row in session.execute(select(table).where(table.c.id.in_(ids)))}


def _full_parents(
    session: Session, source: str, hits: list[NodeRef], fraction: float
) -> dict[int, float]:
    """Parent ids whose share of hit children reaches `fraction`, with their best child score."""
    parent_of = _parents_of(session, source, [h.node_id for h in hits])
    children: dict[int, list[NodeRef]] = defaultdict(list)
    for hit in hits:
        if hit.node_id in parent_of:
            children[parent_of[hit.node_id]].append(hit)
    totals = _child_counts(session, source, list(children))
    return {
        parent: max(h.score for h in hs)
        for parent, hs in children.items()
        if len({h.node_id for h in hs}) / totals[parent] >= fraction
    }


def _merge_source(
    session: Session, source: str, hits: list[NodeRef], fraction: float
) -> dict[int, NodeRef]:
    """For one source: child node id -> the parent NodeRef that replaces it."""
    full = _full_parents(session, source, hits, fraction)
    if not full:
        return {}
    rows = _parent_refs(session, source, list(full))
    parent_of = _parents_of(session, source, [h.node_id for h in hits])
    return {
        child: _ref(source, rows[parent], full[parent])
        for child, parent in parent_of.items()
        if parent in full
    }


def auto_merge(session: Session, hits: Sequence[NodeRef], fraction: float) -> list[NodeRef]:
    """Replace sibling hits by their parent passage when at least `fraction` of its children hit.

    The parent's original text is returned (never its summary), scored as its best child. Order
    follows the first hit of each group; a parent appears once. Hits with no parent stay as is.
    """
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    by_source: dict[str, list[NodeRef]] = defaultdict(list)
    for hit in hits:
        by_source[hit.source].append(hit)
    replace = {
        (source, child): parent
        for source, group in by_source.items()
        for child, parent in _merge_source(session, source, group, fraction).items()
    }
    merged: list[NodeRef] = []
    seen: set[tuple[str, int]] = set()
    for hit in hits:
        node = replace.get((hit.source, hit.node_id), hit)
        key = (node.source, node.node_id)
        if key not in seen:
            seen.add(key)
            merged.append(node)
    return merged


def get_passage(session: Session, source: str, surah: int, a: int, b: int) -> list[NodeRef]:
    """The ayah leaves of `surah` that overlap ayat a..b, in reading order."""
    if b < a:
        a, b = b, a
    table = node_table(source)
    stmt = (
        select(table)
        .where(
            table.c.level == Level.AYAH.value,
            table.c.surah == surah,
            table.c.ayah_to >= a,
            table.c.ayah_from <= b,
        )
        .order_by(table.c.ayah_from, table.c.id)
    )
    return [_ref(source, row) for row in session.execute(stmt)]
