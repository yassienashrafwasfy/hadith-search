"""
Fine-tune intfloat/multilingual-e5-large with LoRA for hadith retrieval.

Modes:
  triplet  — LLM-graded qrels -> (query, positive hadith) pairs
  kv_pairs — verified KV pairs -> (concept, hadith) pairs for cross-concept alignment
  combined — both datasets merged

Loss: Multiple Negative Ranking Loss (MNRL) with in-batch negatives
Split: 85/15 train/val with early stopping
"""

import argparse
import json
import os
import random

import numpy as np
import torch
import torch.nn.functional as F
from peft import LoraConfig, get_peft_model
from sqlalchemy import select
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModel, AutoTokenizer

from database import get_sync_session
from models import Hadith

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPTS_DIR, "..", "data")

MODEL_NAME = "intfloat/multilingual-e5-large"
MAX_LENGTH = 512

LORA_R = int(os.getenv("LORA_R", "16"))
LORA_ALPHA = int(os.getenv("LORA_ALPHA", "32"))
LORA_DROPOUT = float(os.getenv("LORA_DROPOUT", "0.1"))

BATCH_SIZE = int(os.getenv("FINETUNE_BATCH_SIZE", "16"))
LEARNING_RATE = float(os.getenv("FINETUNE_LR", "2e-5"))
EPOCHS = int(os.getenv("FINETUNE_EPOCHS", "20"))
PATIENCE = int(os.getenv("FINETUNE_PATIENCE", "3"))
VAL_SPLIT = float(os.getenv("FINETUNE_VAL_SPLIT", "0.15"))
TEMPERATURE = float(os.getenv("MNRL_TEMPERATURE", "0.05"))
SEED = int(os.getenv("FINETUNE_SEED", "42"))

OUTPUT_DIR = os.path.join(DATA_DIR, "finetuned")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _clean_text(value):
    return str(value).strip() if value else ""


def _format_passage(language, matn):
    matn = _clean_text(matn)
    if not matn:
        return ""
    if language == "AR":
        from camel_tools.utils.dediac import dediac_ar

        from scripts.preprocess import normalize_arabic_text

        matn = normalize_arabic_text(dediac_ar(matn))
    return f"passage: {matn}"


def _load_hadith_passages(language, hadith_ids):
    matn_col = Hadith.Arabic_Matn if language == "AR" else Hadith.English_Matn
    with get_sync_session() as session:
        rows = session.execute(
            select(Hadith.id, matn_col).where(Hadith.id.in_([int(h) for h in hadith_ids]))
        ).all()
    passages = {}
    for row in rows:
        passage = _format_passage(language, row[1])
        if passage:
            passages[row[0]] = passage
    return passages


def _read_required_json(path, label):
    if not os.path.exists(path):
        raise FileNotFoundError(f"{label} not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _query_anchor(language, text):
    """E5 `query:` string; Arabic text is diacritic-stripped and normalised like the corpus."""
    if language == "AR":
        from camel_tools.utils.dediac import dediac_ar

        from scripts.preprocess import normalize_arabic_text

        text = normalize_arabic_text(dediac_ar(text))
    return f"query: {text}"


def _query_language(qid):
    return "AR" if qid.startswith("AR") else "EN"


def _triplet_pairs(qid, query_text, grades):
    """(anchor, passage, qid) for every hadith graded >= 1 that has a passage."""
    language = _query_language(qid)
    passages = _load_hadith_passages(language, [int(hid) for hid in grades])
    anchor = None
    pairs = []
    for hid_str, grade in grades.items():
        passage = passages.get(int(hid_str))
        if passage is None or grade < 1:
            continue
        anchor = anchor or _query_anchor(language, query_text)
        pairs.append((anchor, passage, qid))
    return pairs


def load_triplet_data():
    """Load (query, positive hadith) pairs from LLM-graded training qrels."""
    qrels = _read_required_json(
        os.path.join(DATA_DIR, "training_qrels_graded.json"), "Training qrels"
    )
    queries = _read_required_json(
        os.path.join(DATA_DIR, "training_queries.json"), "Training queries"
    )
    pairs = []
    for qid, grades in qrels.items():
        if qid in queries:
            pairs.extend(_triplet_pairs(qid, queries[qid], grades))
    return pairs


def _kv_pairs_for_row(kv, passages_by_language):
    hadith_id = int(kv["hadith_id"]) if kv.get("hadith_id") else None
    pairs = []
    for language in ("EN", "AR"):
        concept = kv.get(f"concept_{language.lower()}", "").strip()
        passage = passages_by_language[language].get(hadith_id)
        if concept and passage:
            anchor = _query_anchor(language, concept)
            pairs.append((anchor, passage, f"kv_{kv.get('id', 0)}_{language.lower()}"))
    return pairs


def load_kv_data():
    """Load (concept, hadith) pairs from verified KV pairs."""
    kv_pairs = _read_required_json(
        os.path.join(DATA_DIR, "kv_pairs_verified.json"), "Verified KV pairs"
    )
    hadith_ids = [int(kv["hadith_id"]) for kv in kv_pairs if kv.get("hadith_id")]
    passages = {
        lang: _load_hadith_passages(lang, hadith_ids) if hadith_ids else {} for lang in ("EN", "AR")
    }
    return [pair for kv in kv_pairs for pair in _kv_pairs_for_row(kv, passages)]


def load_combined_data():
    """Load both triplet and KV pair data."""
    triplet = load_triplet_data()
    kv = load_kv_data()
    return triplet + kv


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def mean_pool(last_hidden_state, attention_mask):
    mask = attention_mask.unsqueeze(-1).float()
    sum_embeddings = (last_hidden_state * mask).sum(1)
    sum_mask = mask.sum(1).clamp(min=1e-9)
    return sum_embeddings / sum_mask


def encode_texts(model, tokenizer, texts, device, max_length=MAX_LENGTH):
    batch = tokenizer(
        texts,
        max_length=max_length,
        padding=True,
        truncation=True,
        return_tensors="pt",
    ).to(device)
    outputs = model(**batch)
    embeddings = mean_pool(outputs.last_hidden_state, batch["attention_mask"])
    embeddings = F.normalize(embeddings, p=2, dim=1)
    return embeddings


def mnrl_loss(anchor_embs, positive_embs, temperature=TEMPERATURE):
    """Multiple Negative Ranking Loss with in-batch negatives."""
    sim = anchor_embs @ positive_embs.T / temperature
    labels = torch.arange(sim.size(0), device=sim.device)
    return F.cross_entropy(sim, labels)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class PairDataset(Dataset):
    def __init__(self, pairs):
        self.anchors = [p[0] for p in pairs]
        self.positives = [p[1] for p in pairs]

    def __len__(self):
        return len(self.anchors)

    def __getitem__(self, idx):
        return self.anchors[idx], self.positives[idx]


def collate_fn(batch):
    anchors = [item[0] for item in batch]
    positives = [item[1] for item in batch]
    return anchors, positives


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


_LOADERS = {"triplet": load_triplet_data, "kv_pairs": load_kv_data, "combined": load_combined_data}


def _load_pairs(mode):
    if mode not in _LOADERS:
        raise ValueError(f"Unknown mode: {mode}")
    print("Loading training data...")
    pairs = _LOADERS[mode]()
    print(f"Total pairs: {len(pairs)}")
    return pairs


def _make_loaders(pairs, batch_size):
    random.shuffle(pairs)
    val_size = max(1, int(len(pairs) * VAL_SPLIT))
    val_pairs, train_pairs = pairs[:val_size], pairs[val_size:]
    print(f"Train: {len(train_pairs)}, Val: {len(val_pairs)}")
    train_loader = DataLoader(
        PairDataset(train_pairs),
        batch_size=batch_size,
        shuffle=True,
        collate_fn=collate_fn,
        drop_last=True,
    )
    val_loader = DataLoader(
        PairDataset(val_pairs),
        batch_size=batch_size,
        shuffle=False,
        collate_fn=collate_fn,
        drop_last=False,
    )
    return train_loader, val_loader, len(train_pairs), len(val_pairs)


def _print_config(mode, batch_size, lr, epochs, patience, seed):
    print(f"=== LoRA Fine-tuning ({mode}) ===")
    print(f"Model: {MODEL_NAME}")
    print(f"LoRA: r={LORA_R}, alpha={LORA_ALPHA}, dropout={LORA_DROPOUT}")
    print(f"Batch size: {batch_size}, LR: {lr}, Epochs: {epochs}")
    print(f"Temperature: {TEMPERATURE}, Patience: {patience}")
    print(f"Seed: {seed}")
    print()


def _build_model(device):
    print("Loading base model...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    lora_config = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=["query", "value"],
        bias="none",
        task_type="FEATURE_EXTRACTION",
    )
    model = get_peft_model(AutoModel.from_pretrained(MODEL_NAME), lora_config)
    model.to(device)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"Trainable params: {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")
    return model, tokenizer


def _pick_device():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cpu":
        print("WARNING: Training on CPU will be very slow.")
    return device


def _batch_loss(model, tokenizer, device, anchors, positives):
    anchor_embs = encode_texts(model, tokenizer, anchors, device)
    positive_embs = encode_texts(model, tokenizer, positives, device)
    return mnrl_loss(anchor_embs, positive_embs)


def _train_epoch(model, tokenizer, device, loader, optimizer, epoch):
    model.train()
    trainable = [p for p in model.parameters() if p.requires_grad]
    loss_sum = 0.0
    for batch_idx, (anchors, positives) in enumerate(loader):
        optimizer.zero_grad()
        loss = _batch_loss(model, tokenizer, device, anchors, positives)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        optimizer.step()
        loss_sum += loss.item()
        if (batch_idx + 1) % 10 == 0:
            print(f"  Epoch {epoch} [{batch_idx+1}/{len(loader)}] loss={loss.item():.4f}")
    return loss_sum / max(len(loader), 1)


def _validate(model, tokenizer, device, loader):
    model.eval()
    loss_sum = 0.0
    with torch.no_grad():
        for anchors, positives in loader:
            loss_sum += _batch_loss(model, tokenizer, device, anchors, positives).item()
    return loss_sum / max(len(loader), 1)


class _EarlyStopping:
    def __init__(self, patience):
        self.patience = patience
        self.best = float("inf")
        self.misses = 0

    def update(self, val_loss):
        """Record a validation loss; returns True when it is a new best."""
        if val_loss < self.best:
            self.best, self.misses = val_loss, 0
            return True
        self.misses += 1
        return False

    @property
    def exhausted(self):
        return self.misses >= self.patience


def _save_history(mode_dir, config, history, best_val_loss):
    path = os.path.join(mode_dir, "training_history.json")
    payload = {
        **config,
        "best_val_loss": best_val_loss,
        "epochs_run": len(history),
        "history": history,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def _fit(model, tokenizer, device, loaders, optimizer, epochs, patience, mode_dir):
    train_loader, val_loader = loaders
    stopper = _EarlyStopping(patience)
    history = []
    for epoch in range(1, epochs + 1):
        train_loss = _train_epoch(model, tokenizer, device, train_loader, optimizer, epoch)
        val_loss = _validate(model, tokenizer, device, val_loader)
        print(f"Epoch {epoch}/{epochs} — train_loss={train_loss:.4f}, val_loss={val_loss:.4f}")
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        if stopper.update(val_loss):
            print(f"  New best val loss: {stopper.best:.4f} — saving adapter")
            model.save_pretrained(mode_dir)
            tokenizer.save_pretrained(mode_dir)
            continue
        print(f"  No improvement ({stopper.misses}/{patience})")
        if stopper.exhausted:
            print(f"  Early stopping at epoch {epoch}")
            break
    return history, stopper.best


def train(
    mode,
    output_dir=OUTPUT_DIR,
    epochs=EPOCHS,
    batch_size=BATCH_SIZE,
    lr=LEARNING_RATE,
    patience=PATIENCE,
    seed=SEED,
):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    _print_config(mode, batch_size, lr, epochs, patience, seed)

    train_loader, val_loader, n_train, n_val = _make_loaders(_load_pairs(mode), batch_size)
    device = _pick_device()
    model, tokenizer = _build_model(device)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr)

    mode_dir = os.path.join(output_dir, mode)
    os.makedirs(mode_dir, exist_ok=True)
    history, best_val_loss = _fit(
        model, tokenizer, device, (train_loader, val_loader), optimizer, epochs, patience, mode_dir
    )

    config = {
        "mode": mode,
        "model": MODEL_NAME,
        "lora_r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "lora_dropout": LORA_DROPOUT,
        "batch_size": batch_size,
        "learning_rate": lr,
        "temperature": TEMPERATURE,
        "train_pairs": n_train,
        "val_pairs": n_val,
    }
    history_path = _save_history(mode_dir, config, history, best_val_loss)
    print(f"\nDone. Best val loss: {best_val_loss:.4f}")
    print(f"Adapter saved to: {mode_dir}")
    print(f"History saved to: {history_path}")
    return mode_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LoRA fine-tuning for multilingual-e5-large")
    parser.add_argument(
        "--mode",
        choices=["triplet", "kv_pairs", "combined"],
        default="combined",
        help="Training mode (default: combined)",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help="Output directory for adapter weights",
    )
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=LEARNING_RATE)
    parser.add_argument("--patience", type=int, default=PATIENCE)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    train(
        mode=args.mode,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        patience=args.patience,
        seed=args.seed,
    )
