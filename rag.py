"""Runtime RAG retrieval over the green-finance corpus.

Lazy-loads ChromaDB + sentence-transformers on first call so module import
stays cheap. If the store is missing or empty (ingest not yet run), every
retrieve() returns an empty list and the rest of the system degrades to
its pre-RAG behavior.
"""
import logging
import os
import warnings
from pathlib import Path

# Silence HF Hub's 'unauthenticated requests' nag. We only ever load a public
# model that's already cached after ingest — no token needed, no rate-limit risk.
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")
warnings.filterwarnings("ignore", message=".*HF_TOKEN.*")

STORE_DIR = Path(__file__).resolve().parent / "rag_store"
COLLECTION_NAME = "green_finance"
EMBED_MODEL = "all-MiniLM-L6-v2"

_client = None
_collection = None
_embedder = None


def _lazy_init():
    global _client, _collection, _embedder
    if _collection is not None:
        return
    if not STORE_DIR.exists():
        return
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
        _client = chromadb.PersistentClient(path=str(STORE_DIR))
        try:
            _collection = _client.get_collection(COLLECTION_NAME)
        except Exception:
            _collection = None
            return
        _embedder = SentenceTransformer(EMBED_MODEL)
    except Exception:
        _client = None
        _collection = None
        _embedder = None


def retrieve(query: str, k: int = 3) -> list[dict]:
    """Top-k chunks for query. Returns [] if RAG isn't ingested yet."""
    if not query or not query.strip():
        return []
    _lazy_init()
    if _collection is None or _embedder is None:
        return []
    try:
        emb = _embedder.encode([query], normalize_embeddings=True).tolist()
        res = _collection.query(query_embeddings=emb, n_results=k)
    except Exception:
        return []
    out = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for text, meta, dist in zip(docs, metas, dists):
        out.append({
            "text": text,
            "source": (meta or {}).get("source", "unknown"),
            "page": (meta or {}).get("page", -1),
            "score": round(1.0 - float(dist), 4),
        })
    return out


def format_chunks(chunks: list[dict], max_chars_per_chunk: int = 600) -> str:
    """Render retrieved chunks for prompt injection. Trims each to keep token budget bounded."""
    if not chunks:
        return ""
    lines = []
    for i, c in enumerate(chunks, 1):
        text = (c.get("text") or "").strip().replace("\n", " ")
        if len(text) > max_chars_per_chunk:
            text = text[:max_chars_per_chunk].rsplit(" ", 1)[0] + "…"
        src = c.get("source", "unknown")
        page = c.get("page", -1)
        cite = f"{src} p.{page}" if page and page > 0 else src
        lines.append(f"[{i}] ({cite}) {text}")
    return "\n".join(lines)


def is_ready() -> bool:
    _lazy_init()
    return _collection is not None and _embedder is not None


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Mexico energy transition fossil fuel subsidies"
    chunks = retrieve(q, k=3)
    print(f"ready={is_ready()}  query={q!r}")
    print(format_chunks(chunks) or "(empty — run python rag_ingest.py first)")
