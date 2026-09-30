from scripts import recall_proxy as rp


def test_ranked_ids_sorts_by_score():
    assert rp.ranked_ids({1: 0.2, 2: 0.9, 3: 0.5}) == [2, 3, 1]


def test_known_item_query_is_first_half_and_counts_copies():
    text = " ".join(f"w{i}" for i in range(14))
    queries = rp.known_item_queries({1: text, 2: text + " extra", 3: "short one"}, n=5, seed=1)
    assert len(queries) == 2  # the short text is skipped
    query, relevant = queries[0]
    assert query == " ".join(f"w{i}" for i in range(7))
    assert relevant == {1, 2}  # the copy that starts the same way is relevant too


def test_chapter_queries_group_books_and_skip_blank_titles():
    rows = [(1, "كتاب الصيام"), (2, "كتاب الصيام"), (3, "  "), (4, None), (5, "كتاب الحج")]
    assert rp.chapter_queries(rows) == [("كتاب الحج", {5}), ("كتاب الصيام", {1, 2})]


def test_print_report_shows_recall_only(capsys):
    report = {
        "ks": [3],
        "known_item_queries": 2,
        "chapter_queries": 1,
        "methods": {
            "m": {
                "known_item": {"k=3": {"recall": 0.5}},
                "chapter": {"k=3": {"capped_recall": 0.25}},
            }
        },
    }
    rp.print_report(report)
    out = capsys.readouterr().out
    assert "recall@3 0.500" in out and "capped_recall@3 0.250" in out
