import json

import pytest
import torch

from scripts import finetune as ft


def test_mean_pool_ignores_padding():
    hidden = torch.tensor([[[1.0, 1.0], [3.0, 3.0], [100.0, 100.0]]])
    mask = torch.tensor([[1, 1, 0]])
    assert ft.mean_pool(hidden, mask).tolist() == [[2.0, 2.0]]


def test_mnrl_loss_prefers_matching_pairs():
    eye = torch.eye(3)
    assert ft.mnrl_loss(eye, eye).item() < 1e-3
    assert ft.mnrl_loss(eye, eye.flip(0)).item() > 1.0


def test_pair_dataset_and_collate():
    ds = ft.PairDataset([("a1", "p1", "q1"), ("a2", "p2", "q2")])
    assert len(ds) == 2 and ds[1] == ("a2", "p2")
    assert ft.collate_fn([ds[0], ds[1]]) == (["a2"[:0] + "a1", "a2"], ["p1", "p2"])


def test_early_stopping():
    stop = ft._EarlyStopping(patience=2)
    assert stop.update(1.0) is True
    assert stop.update(1.5) is False and not stop.exhausted
    assert stop.update(1.2) is False and stop.exhausted
    assert stop.update(0.5) is True and stop.misses == 0 and stop.best == 0.5


def test_query_helpers():
    assert ft._query_language("AR_1") == "AR" and ft._query_language("EN_1") == "EN"
    assert ft._query_anchor("EN", "prayer") == "query: prayer"
    assert ft._query_anchor("AR", "الصَّلَاة") == "query: الصلاه"


def test_format_passage(monkeypatch):
    assert ft._format_passage("EN", "  text ") == "passage: text"
    assert ft._format_passage("EN", None) == ""
    assert ft._format_passage("AR", "الصَّلَاة") == "passage: الصلاه"


def test_read_required_json_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="Thing not found"):
        ft._read_required_json(str(tmp_path / "x.json"), "Thing")


@pytest.fixture
def _training_data(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, "DATA_DIR", str(tmp_path))
    passages = {"EN": {1: "passage: one", 2: "passage: two"}, "AR": {3: "passage: ثلاثة"}}
    monkeypatch.setattr(
        ft,
        "_load_hadith_passages",
        lambda lang, ids: {i: passages[lang][i] for i in ids if i in passages[lang]},
    )
    return tmp_path


def test_load_triplet_data(_training_data):
    (_training_data / "training_queries.json").write_text(
        json.dumps({"EN1": "prayer", "AR1": "صلاة", "EN_unknown_grades": "z"})
    )
    (_training_data / "training_qrels_graded.json").write_text(
        json.dumps(
            {
                "EN1": {"1": 2, "2": 0, "99": 2},
                "AR1": {"3": 1},
                "MISSING": {"1": 2},
                "EN_unknown_grades": {},
            }
        )
    )
    assert ft.load_triplet_data() == [
        ("query: prayer", "passage: one", "EN1"),
        ("query: صلاه", "passage: ثلاثة", "AR1"),
    ]


def test_load_triplet_data_requires_files(_training_data):
    with pytest.raises(FileNotFoundError, match="Training qrels"):
        ft.load_triplet_data()


def test_load_kv_data(_training_data):
    (_training_data / "kv_pairs_verified.json").write_text(
        json.dumps(
            [
                {"id": 7, "hadith_id": 1, "concept_en": " fasting ", "concept_ar": ""},
                {"id": 8, "hadith_id": 3, "concept_en": "x", "concept_ar": "الصَّوم"},
                {"id": 9, "concept_en": "no hadith", "concept_ar": ""},
            ]
        )
    )
    assert ft.load_kv_data() == [
        ("query: fasting", "passage: one", "kv_7_en"),
        ("query: الصوم", "passage: ثلاثة", "kv_8_ar"),
    ]


def test_load_kv_data_without_hadith_ids(_training_data):
    (_training_data / "kv_pairs_verified.json").write_text(
        json.dumps([{"id": 1, "concept_en": "x"}])
    )
    assert ft.load_kv_data() == []


def test_load_pairs_dispatch_and_error(monkeypatch, capsys):
    monkeypatch.setitem(ft._LOADERS, "triplet", lambda: [("a", "p", "q")])
    assert ft._load_pairs("triplet") == [("a", "p", "q")]
    with pytest.raises(ValueError, match="Unknown mode: nope"):
        ft._load_pairs("nope")


def test_load_combined_concatenates(monkeypatch):
    monkeypatch.setattr(ft, "load_triplet_data", lambda: [1])
    monkeypatch.setattr(ft, "load_kv_data", lambda: [2])
    assert ft.load_combined_data() == [1, 2]


def test_make_loaders_split():
    pairs = [(f"a{i}", f"p{i}", f"q{i}") for i in range(20)]
    train, val, n_train, n_val = ft._make_loaders(pairs, batch_size=4)
    assert (n_train, n_val) == (17, 3)
    assert len(train) == 4  # drop_last
    assert len(val) == 1


class _FakeTokenizer:
    saved = []

    def __call__(self, texts, **_kwargs):
        class Batch(dict):
            def to(self, device):
                return self

        ids = torch.tensor([[float(len(t))] for t in texts])
        return Batch(x=ids, attention_mask=torch.ones(len(texts), 1))

    def save_pretrained(self, path):
        self.saved.append(path)


class _FakeModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = torch.nn.Linear(1, 4)
        self.saved = []

    def forward(self, x, attention_mask):
        class Out:
            pass

        out = Out()
        out.last_hidden_state = self.linear(x).unsqueeze(1)
        return out

    def save_pretrained(self, path):
        self.saved.append(path)


def _loader(n=4):
    return [(["aa", "bbb"], ["aa", "bbb"]) for _ in range(n)]


def test_train_epoch_and_validate_run_on_fake_model():
    model, tok = _FakeModel(), _FakeTokenizer()
    opt = torch.optim.SGD(model.parameters(), lr=0.1)
    train_loss = ft._train_epoch(model, tok, torch.device("cpu"), _loader(10), opt, 1)
    val_loss = ft._validate(model, tok, torch.device("cpu"), _loader(2))
    assert train_loss > 0 and val_loss > 0
    assert ft._validate(model, tok, torch.device("cpu"), []) == 0.0


def test_fit_saves_best_and_stops_early(tmp_path, monkeypatch):
    losses = iter([1.0, 0.5, 0.6, 0.7, 0.8])
    monkeypatch.setattr(ft, "_train_epoch", lambda *a, **k: 0.1)
    monkeypatch.setattr(ft, "_validate", lambda *a, **k: next(losses))
    model, tok = _FakeModel(), _FakeTokenizer()
    history, best = ft._fit(model, tok, "cpu", ([], []), None, epochs=10, patience=2, mode_dir="d")
    assert best == 0.5
    assert [h["epoch"] for h in history] == [1, 2, 3, 4]  # stops after 2 misses
    assert model.saved == ["d", "d"]  # saved on epochs 1 and 2 only


def test_save_history(tmp_path):
    path = ft._save_history(str(tmp_path), {"mode": "triplet"}, [{"epoch": 1}], 0.25)
    saved = json.loads(open(path).read())
    assert saved == {
        "mode": "triplet",
        "best_val_loss": 0.25,
        "epochs_run": 1,
        "history": [{"epoch": 1}],
    }
