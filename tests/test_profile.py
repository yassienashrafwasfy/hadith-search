import pandas as pd
import pytest

from scripts import profile as pf


@pytest.mark.parametrize(
    "values,expected",
    [
        (("Sahih",), {"Sahih"}),
        (("authentic hadith",), {"Sahih"}),
        (("حديث صحيح",), {"Sahih"}),
        (("hasan",), {"Hasan"}),
        (("Da'if",), {"Da'if"}),
        (("weak", "حسن"), {"Da'if", "Hasan"}),
        (("mawdu",), {"Mawdu"}),
        (("fabricated",), {"Mawdu"}),
        (("موضوع",), {"Mawdu"}),
        (("",), set()),
        ((None, "nan"), set()),
        (("nothing here",), set()),
    ],
)
def test_grade_flags(values, expected):
    assert pf._grade_flags(*values) == expected


@pytest.mark.parametrize(
    "row,reason",
    [
        ({"Normalized_Grade": "Sahih"}, "not_unknown"),
        ({"Normalized_Grade": "Unknown"}, "missing_both"),
        (
            {"Normalized_Grade": "Unknown", "English_Grade": "sahih", "Arabic_Grade": "ضعيف"},
            "mixed_conflicting",
        ),
        (
            {"Normalized_Grade": "Unknown", "English_Grade": "", "Arabic_Grade": "xx"},
            "english_missing_arabic_unrecognized",
        ),
        (
            {"Normalized_Grade": "Unknown", "English_Grade": "xx", "Arabic_Grade": ""},
            "arabic_missing_english_unrecognized",
        ),
        (
            {"Normalized_Grade": "Unknown", "English_Grade": "xx", "Arabic_Grade": "yy"},
            "unrecognized_both",
        ),
    ],
)
def test_unknown_grade_reason(row, reason):
    assert pf._unknown_grade_reason(row) == reason


def test_coverage_and_length_rows():
    df = pd.DataFrame({"id": [1, 2, 3, 4], "English_Text": ["abc", "", None, "de"]})
    row = pf._coverage_row(df, "English_Text")
    assert row["present"] == 2 and row["missing_or_empty"] == 2 and row["coverage_pct"] == 50.0
    length = pf._length_row(df, "English_Text")
    assert length["min"] == 0 and length["max"] == 3
    assert length["mean"] == 1.2
