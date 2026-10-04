import pandas as pd
import pytest

from scripts import data_creation as dc


def _frame(**overrides):
    row = {
        f"{lang}_{field}": ""
        for lang in ("English", "Arabic")
        for field in (
            "Text",
            "Hadith",
            "Isnad",
            "Matn",
            "Text_Source",
            "Isnad_Source",
            "Matn_Source",
        )
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_text_rebuilt_from_isnad_and_matn():
    df = _frame(English_Isnad="A told B", English_Matn="prayer is key")
    summary = dc.apply_deterministic_reconstruction(df)
    assert df.at[0, "English_Text"] == "A told B prayer is key"
    assert df.at[0, "English_Text_Source"] == "reconstructed_from_isnad_matn"
    assert summary["English_Text_reconstructed_from_isnad_matn"] == 1
    assert summary["Arabic_Text_reconstructed_from_isnad_matn"] == 0


def test_matn_is_full_minus_isnad():
    df = _frame(Arabic_Text="سند متن الحديث", Arabic_Isnad="سند")
    summary = dc.apply_deterministic_reconstruction(df)
    assert df.at[0, "Arabic_Matn"] == "متن الحديث"
    assert df.at[0, "Arabic_Matn_Source"] == "reconstructed_from_full_minus_isnad"
    assert summary["Arabic_Matn_reconstructed_from_full_minus_isnad"] == 1


def test_isnad_is_full_minus_matn():
    df = _frame(English_Text="A told B prayer is key", English_Matn="prayer is key")
    summary = dc.apply_deterministic_reconstruction(df)
    assert df.at[0, "English_Isnad"] == "A told B"
    assert df.at[0, "English_Isnad_Source"] == "reconstructed_from_full_minus_matn"
    assert summary["English_Isnad_reconstructed_from_full_minus_matn"] == 1


def test_chain_of_reconstructions_uses_new_text():
    df = _frame(English_Isnad="A told B", English_Matn="")
    df.at[0, "English_Matn"] = "x"
    dc.apply_deterministic_reconstruction(df)
    assert df.at[0, "English_Text"] == "A told B x"


def test_nothing_to_reconstruct_leaves_row_untouched():
    df = _frame(English_Text="full", English_Isnad="a", English_Matn="b")
    summary = dc.apply_deterministic_reconstruction(df)
    assert set(summary.values()) == {0}
    assert df.at[0, "English_Text"] == "full"


def test_prefix_that_does_not_match_is_not_removed():
    df = _frame(English_Text="unrelated", English_Isnad="isnad")
    dc.apply_deterministic_reconstruction(df)
    assert df.at[0, "English_Matn"] == ""


def test_summary_keys_order_and_count():
    keys = list(dc.apply_deterministic_reconstruction(_frame()))
    assert keys[:2] == [
        "English_Text_reconstructed_from_isnad_matn",
        "Arabic_Text_reconstructed_from_isnad_matn",
    ]
    assert len(keys) == 6


@pytest.mark.parametrize(
    "value,expected", [(None, False), ("nan", False), ("  ", False), ("x", True)]
)
def test_has_text(value, expected):
    assert dc._has_text(value) is expected


def test_drop_rows_missing_bilingual_matn():
    df = pd.DataFrame(
        {
            "id_before_drop": [1, 2, 3],
            "Has_English_Matn": [1, 1, 0],
            "Has_Arabic_Matn": [1, 0, 1],
            "LK_Book": ["a", "b", "c"],
            "Book": ["a", "b", "c"],
            "Source_File": ["f"] * 3,
            "Chapter_Number": [1, 2, 3],
            "Hadith_Number": [1, 2, 3],
        }
    )
    kept, audit = dc.drop_rows_missing_bilingual_matn(df)
    assert list(kept["Book"]) == ["a"] and "id_before_drop" not in kept
    assert [a["id_before_drop"] for a in audit] == [2, 3]


def test_number_labels_keep_ranges_and_drop_float_suffix():
    from scripts import data_creation as dc

    assert dc._number_label("622 -623") == "622 -623"
    assert dc._number_label("5, 6") == "5, 6"
    assert dc._number_label(5.0) == "5"
    records = dc._hadith_records(
        pd.DataFrame([{**{c: None for c in dc._MODEL_COLUMNS}, "id": 1, "Hadith_Number": "1-2"}])
    )
    assert records[0]["Hadith_Number"] == "1-2"
