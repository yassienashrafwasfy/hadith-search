import pytest

from scripts import search as s


def test_rrf_fusion_combines_lists():
    fused = s.rrf_fusion([{1: 1, 2: 2}, {2: 1, 3: 2}], k=60)
    assert next(iter(fused)) == 2
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
