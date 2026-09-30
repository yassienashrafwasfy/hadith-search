"""
Re-encode hadith embeddings with a fine-tuned model and run evaluation.

Usage:
    python -m scripts.finetune_eval --mode combined
    python -m scripts.finetune_eval --mode triplet --skip-encode

This script:
1. Loads the fine-tuned model (base E5 + LoRA adapter)
2. Re-encodes all hadith embeddings (EN + AR)
3. Overwrites the vectors in the hadith_embeddings table
4. Runs the evaluation pipeline
5. Saves results to data/finetuned_results.json
"""

import argparse
import os
import sys

import numpy as np

from database import get_sync_session, read_hadiths_df
from models import Hadith
from scripts.embedding_store import store_embeddings

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPTS_DIR, "..", "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "finetuned")


def reencode_embeddings(adapter_path, batch_size=32):
    """Re-encode all hadith embeddings using the fine-tuned model."""
    import torch
    from peft import PeftModel
    from transformers import AutoModel, AutoTokenizer

    print("=== Re-encoding Embeddings ===")
    print(f"Adapter: {adapter_path}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    df = read_hadiths_df(Hadith.id, Hadith.English_Matn, Hadith.Arabic_Matn, order_by=Hadith.id)

    print(f"Loaded {len(df)} hadiths")
    ids = df["id"].astype(int).tolist()

    model_name = "intfloat/multilingual-e5-large"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    base_model = AutoModel.from_pretrained(model_name)
    model = PeftModel.from_pretrained(base_model, adapter_path)
    model = model.merge_and_unload()
    model.to(device)
    model.eval()

    def encode_batch(texts):
        prefixed = [f"passage: {t}" for t in texts]
        batch = tokenizer(
            prefixed,
            max_length=512,
            padding=True,
            truncation=True,
            return_tensors="pt",
        ).to(device)
        with torch.no_grad():
            outputs = model(**batch)
            mask = batch["attention_mask"].unsqueeze(-1).float()
            embeddings = (outputs.last_hidden_state * mask).sum(1)
            mask_sum = mask.sum(1).clamp(min=1e-9)
            embeddings = embeddings / mask_sum
            embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
        return embeddings.cpu().numpy()

    print("Encoding English embeddings...")
    en_texts = df["English_Matn"].tolist()
    en_embeddings = []
    for i in range(0, len(en_texts), batch_size):
        batch = en_texts[i : i + batch_size]
        en_embeddings.append(encode_batch(batch))
        if (i // batch_size + 1) % 50 == 0:
            print(f"  {i + len(batch)}/{len(en_texts)}")
    en_embeddings = np.vstack(en_embeddings)
    with get_sync_session() as session:
        store_embeddings(session, ids, en_embeddings, "EN")
    print(f"Stored {en_embeddings.shape} English embeddings in PostgreSQL")

    print("Encoding Arabic embeddings...")
    from camel_tools.utils.dediac import dediac_ar

    from scripts import normalize_arabic_text

    ar_texts = [normalize_arabic_text(dediac_ar(text)) for text in df["Arabic_Matn"]]
    ar_embeddings = []
    for i in range(0, len(ar_texts), batch_size):
        batch = ar_texts[i : i + batch_size]
        ar_embeddings.append(encode_batch(batch))
        if (i // batch_size + 1) % 50 == 0:
            print(f"  {i + len(batch)}/{len(ar_texts)}")
    ar_embeddings = np.vstack(ar_embeddings)
    with get_sync_session() as session:
        store_embeddings(session, ids, ar_embeddings, "AR")
    print(f"Stored {ar_embeddings.shape} Arabic embeddings in PostgreSQL")

    print("Done re-encoding.\n")


def run_evaluation(mode, k=20):
    """Run the evaluation pipeline with the fine-tuned model."""
    from scripts.eval_pipeline import OutputNames, run_pipeline

    print(f"=== Evaluation (fine-tuned: {mode}) ===\n")
    names = OutputNames(
        results=f"finetuned_results_{mode}.json",
        stats=f"finetuned_stats_{mode}.json",
        table=f"finetuned_results_table_{mode}.tex",
    )
    run_pipeline(names, k)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate fine-tuned model on eval queries")
    parser.add_argument(
        "--mode",
        choices=["triplet", "kv_pairs", "combined"],
        default="combined",
        help="Which fine-tuned adapter to use",
    )
    parser.add_argument(
        "--adapter-path",
        default=None,
        help="Custom adapter path (overrides --mode)",
    )
    parser.add_argument(
        "--skip-encode",
        action="store_true",
        help="Skip re-encoding embeddings (use the vectors already in the database)",
    )
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--k", type=int, default=20)
    args = parser.parse_args()

    adapter_path = args.adapter_path
    if adapter_path is None:
        adapter_path = os.path.join(OUTPUT_DIR, args.mode)

    if not os.path.exists(adapter_path):
        print(f"ERROR: Adapter not found at {adapter_path}")
        print("Run finetune.py first to train and save the adapter.")
        sys.exit(1)

    os.environ["FINETUNED_ADAPTER_PATH"] = adapter_path

    if not args.skip_encode:
        reencode_embeddings(adapter_path, args.batch_size)

    from scripts import get_model

    get_model.cache_clear()

    run_evaluation(args.mode, args.k)
