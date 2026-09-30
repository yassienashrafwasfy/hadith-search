import asyncio
import json
import os
import re
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import select

from models import KvPair

load_dotenv()

LLM_API_BASE = os.getenv("LLM_API_BASE", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPTS_DIR, "..", "data")
DB_PATH = os.path.join(DATA_DIR, "hadiths.db")

TOPICS = [
    "prayer (salah)",
    "fasting (sawm)",
    "charity (zakat and sadaqah)",
    "pilgrimage (hajj and umrah)",
    "patience and perseverance (sabr)",
    "kinship and family ties",
    "manners and etiquette (adab)",
    "seeking knowledge (ilm)",
    "repentance and forgiveness (tawbah)",
    "trade and business ethics",
]

PAIRS_PER_TOPIC = int(os.getenv("KV_PAIRS_PER_TOPIC", "100"))
BATCH_SIZE = int(os.getenv("KV_BATCH_SIZE", "20"))

SYSTEM_PROMPT_EN = (
    "You are an expert in Islamic Hadith science and jurisprudence. "
    "Your task is to generate concept-entity pairs for cross-concept alignment training. "
    "Each pair has:\n"
    "  - concept: an ABSTRACT Islamic principle or teaching (general, uses conceptual language)\n"
    "  - entity: a CONCRETE searchable phrase that would retrieve relevant hadiths (specific, uses named entities, events, or actions)\n"
    "The concept and entity must be semantically related but use DIFFERENT vocabulary so the model learns cross-concept alignment.\n"
    "Respond with ONLY a JSON array of objects, each with 'concept_en', 'concept_ar', 'entity_en', 'entity_ar' keys. "
    "Do not include any explanation or additional text."
)

USER_PROMPT_EN = """Generate {n} concept-entity pairs about the topic: "{topic}".

Requirements:
- concept: abstract principle (e.g., "the virtue of patience during hardship")
- entity: concrete searchable phrase (e.g., "Prophet Ayyub enduring affliction")
- concept and entity MUST use different vocabulary
- Both English and Arabic versions for each
- Each pair must be unique and specific
- Vary the angle: sometimes focus on reward, sometimes on warning, sometimes on method

Example pairs for "prayer":
  {{"concept_en": "the spiritual reward of praying on time", "concept_ar": "...", "entity_en": "Prophet Muhammad praying the five daily prayers", "entity_ar": "..."}}
  {{"concept_en": "the consequence of neglecting prayer out of laziness", "concept_ar": "...", "entity_en": "a man who delayed his prayers until sunset", "entity_ar": "..."}}

Generate exactly {n} pairs as a JSON array:"""


def build_messages(topic, n):
    system = SYSTEM_PROMPT_EN
    user = USER_PROMPT_EN.format(n=n, topic=topic)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call_llm(messages):
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 4000,
    }

    url = LLM_API_BASE.rstrip("/") + "/chat/completions"

    for attempt in range(LLM_MAX_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content
        except requests.exceptions.RequestException as e:
            if attempt < LLM_MAX_RETRIES - 1:
                wait = 2**attempt
                print(f"    Retry {attempt+1}/{LLM_MAX_RETRIES} after {wait}s: {e}")
                time.sleep(wait)
            else:
                raise
    return None


def parse_pairs(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []

    try:
        pairs = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    valid = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        concept_en = p.get("concept_en", "").strip()
        concept_ar = p.get("concept_ar", "").strip()
        entity_en = p.get("entity_en", "").strip()
        entity_ar = p.get("entity_ar", "").strip()
        if concept_en and concept_ar and entity_en and entity_ar:
            valid.append(
                {
                    "concept_en": concept_en,
                    "concept_ar": concept_ar,
                    "entity_en": entity_en,
                    "entity_ar": entity_ar,
                }
            )

    return valid


def generate_pairs_for_topic(topic, total_needed, batch_size=BATCH_SIZE):
    all_pairs = []
    batches = (total_needed + batch_size - 1) // batch_size

    for batch_idx in range(batches):
        remaining = total_needed - len(all_pairs)
        if remaining <= 0:
            break
        n = min(batch_size, remaining)

        print(f"  Batch {batch_idx+1}/{batches} — requesting {n} pairs...")
        messages = build_messages(topic, n)
        content = call_llm(messages)
        if content is None:
            print("    Failed to get response")
            continue

        pairs = parse_pairs(content)
        print(f"    Parsed {len(pairs)} valid pairs")
        all_pairs.extend(pairs)

    return all_pairs[:total_needed]


def retrieve_hadith(query_text, language, inverted_index, document_lengths):
    from scripts import bm25

    scores = bm25(query_text, language, inverted_index, document_lengths)
    if not scores:
        return None, None
    top_id = next(iter(scores))

    from database import get_sync_session
    from models import Hadith

    col = Hadith.Arabic_Text if language == "AR" else Hadith.English_Text
    with get_sync_session() as session:
        row = session.execute(select(Hadith.id, col).where(Hadith.id == top_id)).first()

    if row and row[1]:
        return row[0], row[1]
    return top_id, None


def store_pairs(pairs, topic, inverted_index_en, inverted_index_ar, document_lengths):
    return asyncio.run(
        _store_pairs(pairs, topic, inverted_index_en, inverted_index_ar, document_lengths)
    )


async def _store_pairs(pairs, topic, inverted_index_en, inverted_index_ar, document_lengths):
    from database import get_session, now_iso

    session = get_session()
    stored = 0

    for pair in pairs:
        en_hadith_id, en_hadith_text = retrieve_hadith(
            pair["entity_en"], "EN", inverted_index_en, document_lengths
        )
        ar_hadith_id, ar_hadith_text = retrieve_hadith(
            pair["entity_ar"], "AR", inverted_index_ar, document_lengths
        )

        hadith_id = en_hadith_id or ar_hadith_id
        if hadith_id is None:
            continue

        session.add(
            KvPair(
                topic=topic,
                language="BOTH",
                concept_en=pair["concept_en"],
                concept_ar=pair["concept_ar"],
                entity_en=pair["entity_en"],
                entity_ar=pair["entity_ar"],
                hadith_id=hadith_id,
                hadith_en=en_hadith_text,
                hadith_ar=ar_hadith_text,
                status="pending",
                created_at=now_iso(),
            )
        )
        stored += 1

    await session.commit()
    await session.close()
    return stored


def generate_all(pairs_per_topic=PAIRS_PER_TOPIC, topics=None):
    topics = topics or TOPICS

    print("=== KV Pair Generation Pipeline ===")
    print(f"Topics: {len(topics)}")
    print(f"Pairs per topic: {pairs_per_topic}")
    print(f"Total target: {len(topics) * pairs_per_topic}")
    print(f"LLM endpoint: {LLM_API_BASE}")
    print(f"LLM model: {LLM_MODEL}")
    print()

    from database import init_kv_pairs_table
    from scripts import (
        get_arabic_inverted_index,
        get_document_lengths,
        get_english_inverted_index,
    )

    asyncio.run(init_kv_pairs_table())

    print("Loading retrieval resources...")
    en_index = get_english_inverted_index()
    ar_index = get_arabic_inverted_index()
    doc_lengths = get_document_lengths()
    print("Resources loaded.\n")

    total_stored = 0

    for i, topic in enumerate(topics):
        print(f"[{i+1}/{len(topics)}] Topic: {topic}")
        pairs = generate_pairs_for_topic(topic, pairs_per_topic)
        print(f"  Generated {len(pairs)} pairs")

        if not pairs:
            print("  No pairs generated, skipping")
            continue

        stored = store_pairs(pairs, topic, en_index, ar_index, doc_lengths)
        print(f"  Stored {stored} KV pairs (with retrieved hadiths)")
        total_stored += stored
        print()

    print(f"\nDone. Total KV pairs stored: {total_stored}")
    print("Run the verification UI to review and verify pairs.")

    return total_stored


async def _fetch_verified():
    from database import get_session

    async with get_session() as session:
        result = await session.execute(
            select(
                KvPair.id,
                KvPair.topic,
                KvPair.language,
                KvPair.concept_en,
                KvPair.concept_ar,
                KvPair.entity_en,
                KvPair.entity_ar,
                KvPair.hadith_id,
                KvPair.hadith_en,
                KvPair.hadith_ar,
            )
            .where(KvPair.status == "verified")
            .order_by(KvPair.id)
        )
        return result.all()


def export_verified(output_path=None):
    if output_path is None:
        output_path = os.path.join(DATA_DIR, "kv_pairs_verified.json")

    rows = asyncio.run(_fetch_verified())

    pairs = []
    for row in rows:
        pairs.append(
            {
                "id": row[0],
                "topic": row[1],
                "language": row[2],
                "concept_en": row[3],
                "concept_ar": row[4],
                "entity_en": row[5],
                "entity_ar": row[6],
                "hadith_id": row[7],
                "hadith_en": row[8],
                "hadith_ar": row[9],
            }
        )

    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pairs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, output_path)

    print(f"Exported {len(pairs)} verified KV pairs to {output_path}")
    return pairs


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="KV pair generation pipeline")
    parser.add_argument(
        "--pairs-per-topic",
        type=int,
        default=PAIRS_PER_TOPIC,
        help="Number of concept-entity pairs to generate per topic",
    )
    parser.add_argument(
        "--export", action="store_true", help="Export verified pairs instead of generating"
    )
    parser.add_argument("--output", default=None, help="Output path for export")
    args = parser.parse_args()

    if args.export:
        export_verified(args.output)
    else:
        generate_all(pairs_per_topic=args.pairs_per_topic)
