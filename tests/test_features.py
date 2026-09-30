import pytest

from features import Features, load_features


def test_defaults_follow_jina_key():
    assert load_features({}).cross_encoder is False
    assert load_features({"JINA_API_KEY": "k"}).cross_encoder is True


def test_annotation_preset_disables_search_stack():
    f = load_features({"APP_MODE": "annotation"})
    assert (f.search, f.benchmark, f.dense_retrieval, f.cross_encoder) == (False,) * 4
    assert f.annotation and f.kv_pairs


def test_research_preset_loads_model_eagerly():
    assert load_features({"APP_MODE": "research"}).eager_model is True
    assert load_features({"APP_MODE": "search"}).eager_model is False


def test_explicit_flag_beats_preset():
    f = load_features({"APP_MODE": "annotation", "FEATURE_SEARCH": "true"})
    assert f.search is True


@pytest.mark.parametrize("raw,expected", [("1", True), ("On", True), ("no", False), ("0", False)])
def test_bool_parsing(raw, expected):
    assert load_features({"FEATURE_KV_PAIRS": raw}).kv_pairs is expected


def test_invalid_flag_value_raises():
    with pytest.raises(ValueError, match="FEATURE_SEARCH"):
        load_features({"FEATURE_SEARCH": "maybe"})


def test_adapter_path_and_is_enabled():
    f = load_features({"FINETUNED_ADAPTER_PATH": "/x"})
    assert f.finetuned_adapter_path == "/x"
    assert Features(search=False).is_enabled("search") is False
