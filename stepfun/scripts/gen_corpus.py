"""Deterministic corpus generator for the stepfun track.

Generates 200 mixed Chinese/English knowledge-base documents (150 CN + 50 EN)
from the hand-written topics.json, plus a labeled query set for retrieval
quality evaluation (Recall@K / nDCG@K).

Reproducibility: fixed seed, sorted topic iteration, per-(topic, variant) RNG.
Run: python scripts/gen_corpus.py   (outputs under data/)
"""
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = Path(__file__).resolve().parent / "topics.json"
DATA_DIR = ROOT / "data"
CORPUS_DIR = DATA_DIR / "corpus"
SEED = 42

# Which variants each topic gets -> total docs per topic
# 200 = 40*5: all topics c0,c1,c2,e0 (160); t01-t10 +c3,+e1 (20); t11-t30 +c3 (20)
# CN total = 120 + 10 + 20 = 150, EN total = 40 + 10 = 50


def variant_plan(topic_index: int) -> list[str]:
    plan = ["c0", "c1", "c2", "e0"]
    if topic_index < 10:            # t01..t10
        plan += ["c3", "e1"]
    elif topic_index < 30:          # t11..t30
        plan += ["c3"]
    return plan


def rng_for(tid: str, variant: str) -> random.Random:
    digest = hashlib.sha256(f"{tid}:{variant}:{SEED}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def cn_variant(facts: list[str], synonyms: dict, variant: str, rng: random.Random) -> str:
    out = []
    for fact in facts:
        text = fact
        for word, alts in sorted(synonyms.items(), key=lambda kv: -len(kv[0])):
            if word in text:
                text = text.replace(word, rng.choice(alts))
        out.append(text)
    drop = rng.randrange(len(out))
    out.pop(drop)
    rng.shuffle(out)
    return "".join(out)


def en_variant(facts: list[str], variant: str, rng: random.Random) -> str:
    out = list(facts)
    drop = rng.randrange(len(out))
    out.pop(drop)
    rng.shuffle(out)
    return " ".join(out)


def main() -> None:
    topics = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    docs, queries = [], []
    tids = sorted(topics.keys())

    for idx, tid in enumerate(tids):
        t = topics[tid]
        for variant in variant_plan(idx):
            rng = rng_for(tid, variant)
            lang = "cn" if variant.startswith("c") else "en"
            if lang == "cn":
                title = t["title_cn"]
                body = (
                    "".join(t["facts_cn"]) if variant == "c0"
                    else cn_variant(t["facts_cn"], t.get("synonyms", {}), variant, rng)
                )
            else:
                title = t["title_en"]
                body = (
                    " ".join(t["facts_en"]) if variant == "e0"
                    else en_variant(t["facts_en"], variant, rng)
                )
            doc_id = f"{tid}-{variant}"
            docs.append({"id": doc_id, "topic": tid, "lang": lang, "title": title, "content": body})

        q = t.get("query_cn") or t.get("query_en")
        if q:
            queries.append({
                "id": f"q-{tid}",
                "topic": tid,
                "text": q,
                "lang": "cn" if t.get("query_cn") else "en",
                "relevant": [d["id"] for d in docs if d["topic"] == tid],
            })

    for d in docs:
        (CORPUS_DIR / f"{d['id']}.txt").write_text(
            f"{d['title']}\n\n{d['content']}", encoding="utf-8"
        )
    (DATA_DIR / "corpus.json").write_text(
        json.dumps({"meta": {"seed": SEED, "doc_count": len(docs), "generator": "scripts/gen_corpus.py"},
                    "docs": docs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (DATA_DIR / "queries.json").write_text(
        json.dumps({"meta": {"seed": SEED, "query_count": len(queries), "generator": "scripts/gen_corpus.py"},
                    "queries": queries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cn = sum(1 for d in docs if d["lang"] == "cn")
    en = sum(1 for d in docs if d["lang"] == "en")
    sizes = sorted(len(d["content"]) for d in docs)
    print(f"docs: {len(docs)} (CN {cn} / EN {en}), queries: {len(queries)}")
    print(f"content length: min {sizes[0]}, median {sizes[len(sizes)//2]}, max {sizes[-1]}")
    print(f"written to {CORPUS_DIR}, {DATA_DIR/'corpus.json'}, {DATA_DIR/'queries.json'}")


if __name__ == "__main__":
    main()
