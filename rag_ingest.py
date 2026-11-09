"""One-shot ingest: download green-finance PDFs → docs/, chunk, embed, persist to rag_store/.

Wider canonical-URL list than before (IPCC AR6 + IRENA + World Bank Open Knowledge),
biased toward sources that publish PDFs at stable paths. Every download is validated
twice: (1) %PDF magic bytes at the head of the file, (2) pypdf can extract >500 chars
from the first three pages. Anything that fails either check is deleted, never embedded.

Additionally scans docs/ at the end and ingests any *.pdf the user dropped manually,
so falling back to 'wget my own copy' Just Works.

Idempotent. Safe to re-run.

Usage:
    python rag_ingest.py            # download + ingest all
    python rag_ingest.py --skip-download   # only ingest whatever's already in docs/
"""
import logging
import os
import sys
import time
import warnings
from pathlib import Path

import httpx

# Silence HF Hub's 'unauthenticated requests' nag — the embedder is a public
# model and we hit it once at ingest time.
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")
warnings.filterwarnings("ignore", message=".*HF_TOKEN.*")

HERE = Path(__file__).resolve().parent
DOCS_DIR = HERE / "docs"
STORE_DIR = HERE / "rag_store"
COLLECTION_NAME = "green_finance"
EMBED_MODEL = "all-MiniLM-L6-v2"

# Canonical-host PDFs. IPCC (ipcc.ch/report/...) and World Bank Open Knowledge
# (openknowledge.worldbank.org/server/api/core/bitstreams/...) publish at stable
# paths; IEA blob URLs change asset IDs frequently and are excluded.
# Each entry: {filename: (primary_url, [optional_mirrors])}. Mirrors are tried
# in order if the primary 404s.
SOURCES = {
    # --- IPCC AR6 (climate science, mitigation, adaptation) ---
    "IPCC_AR6_WG1_SPM.pdf": (
        "https://www.ipcc.ch/report/ar6/wg1/downloads/report/IPCC_AR6_WGI_SPM.pdf",
        [],
    ),
    "IPCC_AR6_WG2_SPM.pdf": (
        "https://www.ipcc.ch/report/ar6/wg2/downloads/report/IPCC_AR6_WGII_SummaryForPolicymakers.pdf",
        [],
    ),
    "IPCC_AR6_WG3_SPM.pdf": (
        "https://www.ipcc.ch/report/ar6/wg3/downloads/report/IPCC_AR6_WGIII_SummaryForPolicymakers.pdf",
        [],
    ),
    "IPCC_AR6_Synthesis_SPM.pdf": (
        "https://www.ipcc.ch/report/ar6/syr/downloads/report/IPCC_AR6_SYR_SPM.pdf",
        [],
    ),
    "IPCC_AR6_WG3_TechSummary.pdf": (
        "https://www.ipcc.ch/report/ar6/wg3/downloads/report/IPCC_AR6_WGIII_TechnicalSummary.pdf",
        [],
    ),
    "IPCC_SR15_SPM.pdf": (
        "https://www.ipcc.ch/site/assets/uploads/sites/2/2022/06/SPM_version_report_LR.pdf",
        [],
    ),
    # --- World Bank Open Knowledge Repository (green finance, carbon, transition) ---
    "WorldBank_CarbonPricing_2024.pdf": (
        "https://openknowledge.worldbank.org/server/api/core/bitstreams/253e6cdd-9631-4db2-8cc5-1d013956de15/content",
        [],
    ),
    "WorldBank_CountryClimate_Mexico.pdf": (
        "https://openknowledge.worldbank.org/server/api/core/bitstreams/4d2fffdd-7d8b-46e8-a888-1f9a7b3a9d59/content",
        [],
    ),
    # --- IRENA (renewable transition, energy outlook) ---
    "IRENA_WETO_2023.pdf": (
        "https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2023/Jun/IRENA_World_energy_transitions_outlook_2023.pdf",
        [],
    ),
    "IRENA_RE_Capacity_2024.pdf": (
        "https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2024/Mar/IRENA_RE_Capacity_Statistics_2024.pdf",
        [],
    ),
    "IRENA_Renewable_Energy_Statistics_2024.pdf": (
        "https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2024/Jul/IRENA_Renewable_energy_statistics_2024.pdf",
        [],
    ),
    # --- Stanford / NBER / RFF research (open access) ---
    "RFF_GlobalEnergyOutlook_2023.pdf": (
        "https://media.rff.org/documents/Report_23-02_RFF_Global_Energy_Outlook_2023.pdf",
        [],
    ),
}

CHUNK_CHAR_TARGET = 2000   # ~500 tokens at 4 chars/token rough avg
CHUNK_MIN_CHARS = 200      # discard scraps (footers, page numbers, etc.)
VALIDATION_MIN_TEXT_CHARS = 500
VALIDATION_MAX_PAGES_CHECKED = 3


def _is_real_pdf(path: Path) -> bool:
    """Check %PDF magic bytes at start. Cheap pre-filter for HTML error pages saved as .pdf."""
    try:
        with path.open("rb") as f:
            head = f.read(5)
        return head.startswith(b"%PDF-")
    except Exception:
        return False


def _pdf_yields_text(path: Path) -> bool:
    """pypdf must extract VALIDATION_MIN_TEXT_CHARS across the first few pages.
    Filters image-only PDFs (would need OCR) and any garbage that survives magic-byte check."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        chars = 0
        for page in reader.pages[:VALIDATION_MAX_PAGES_CHECKED]:
            try:
                chars += len(page.extract_text() or "")
            except Exception:
                continue
            if chars >= VALIDATION_MIN_TEXT_CHARS:
                return True
        return chars >= VALIDATION_MIN_TEXT_CHARS
    except Exception:
        return False


def _try_url(name: str, url: str) -> Path | None:
    out = DOCS_DIR / name
    try:
        with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as r:
            r.raise_for_status()
            with out.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
        size_kb = out.stat().st_size // 1024
        if size_kb < 30:
            print(f"    ! {size_kb} KB — too small, deleting")
            out.unlink()
            return None
        if not _is_real_pdf(out):
            print(f"    ! not a PDF (no %PDF magic), deleting")
            out.unlink()
            return None
        if not _pdf_yields_text(out):
            print(f"    ! pypdf extraction yields <{VALIDATION_MIN_TEXT_CHARS} chars, deleting")
            out.unlink()
            return None
        print(f"    ✓ {size_kb} KB, validated")
        return out
    except Exception as e:
        print(f"    ✗ {type(e).__name__}: {str(e)[:120]}")
        if out.exists():
            out.unlink()
        return None


def download(name: str, primary: str, mirrors: list[str]) -> Path | None:
    out = DOCS_DIR / name
    if out.exists() and out.stat().st_size > 1024:
        # Cached — but still validate, in case a prior run cached a bad file.
        if _is_real_pdf(out) and _pdf_yields_text(out):
            print(f"  ✓ cached: {name} ({out.stat().st_size // 1024} KB)")
            return out
        else:
            print(f"  ! cached {name} fails validation, re-downloading")
            out.unlink()
    print(f"  ↓ downloading: {name}")
    print(f"    primary: {primary}")
    result = _try_url(name, primary)
    if result:
        return result
    for i, mirror in enumerate(mirrors, 1):
        print(f"    mirror {i}: {mirror}")
        result = _try_url(name, mirror)
        if result:
            return result
    return None


def extract_chunks(pdf_path: Path) -> list[dict]:
    """Extract per-page text, group paragraphs into ~2000-char chunks."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        print(f"    ✗ pypdf failed on {pdf_path.name}: {type(e).__name__}")
        return []

    chunks = []
    for page_idx, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        text = text.strip()
        if not text:
            continue
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text]
        buf = ""
        chunk_idx = 0
        for para in paragraphs:
            if len(buf) + len(para) + 2 > CHUNK_CHAR_TARGET and buf:
                if len(buf) >= CHUNK_MIN_CHARS:
                    chunks.append({
                        "text": buf,
                        "source": pdf_path.name,
                        "page": page_idx,
                        "chunk_idx": chunk_idx,
                    })
                    chunk_idx += 1
                buf = para
            else:
                buf = (buf + "\n\n" + para) if buf else para
        if buf and len(buf) >= CHUNK_MIN_CHARS:
            chunks.append({
                "text": buf,
                "source": pdf_path.name,
                "page": page_idx,
                "chunk_idx": chunk_idx,
            })
    return chunks


def main():
    DOCS_DIR.mkdir(exist_ok=True)
    STORE_DIR.mkdir(exist_ok=True)
    skip_download = "--skip-download" in sys.argv

    print("=== caer_y_reconstruir RAG ingest ===\n")

    if not skip_download:
        print(f"Step 1/3: downloading + validating {len(SOURCES)} sources to {DOCS_DIR}/")
        for name, (primary, mirrors) in SOURCES.items():
            download(name, primary, mirrors)
            time.sleep(0.3)
        print()
    else:
        print("Step 1/3: skipped (--skip-download)\n")

    # Pick up anything in docs/ — whether downloaded by SOURCES or dropped manually.
    pdfs = sorted(DOCS_DIR.glob("*.pdf"))
    if not pdfs:
        print("No PDFs in docs/. Either every download failed or --skip-download was set "
              "with no local files. Drop PDFs into docs/ and re-run with --skip-download.")
        return

    # Final validation pass — anything that fails extraction is left on disk but
    # excluded from ingest. User can inspect docs/ to see what survived.
    valid_pdfs = []
    for p in pdfs:
        if _is_real_pdf(p) and _pdf_yields_text(p):
            valid_pdfs.append(p)
        else:
            print(f"  ! skipping {p.name} (failed validation)")
    pdfs = valid_pdfs

    print(f"\nStep 2/3: extracting + chunking {len(pdfs)} validated PDFs")
    all_chunks = []
    for pdf in pdfs:
        chunks = extract_chunks(pdf)
        print(f"  {pdf.name}: {len(chunks)} chunks")
        all_chunks.extend(chunks)
    if not all_chunks:
        print("No chunks extracted. Aborting.")
        return
    print(f"  total: {len(all_chunks)} chunks\n")

    print(f"Step 3/3: embedding (model={EMBED_MODEL}) + persisting to {STORE_DIR}/")
    from sentence_transformers import SentenceTransformer
    import chromadb

    embedder = SentenceTransformer(EMBED_MODEL)
    client = chromadb.PersistentClient(path=str(STORE_DIR))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    texts = [c["text"] for c in all_chunks]
    print(f"  encoding {len(texts)} chunks...")
    embeddings = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=True).tolist()

    ids = [f"{c['source']}::p{c['page']}::c{c['chunk_idx']}" for c in all_chunks]
    metas = [{"source": c["source"], "page": c["page"]} for c in all_chunks]
    collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metas)

    print(f"\n  ✓ ingested {len(ids)} chunks into collection '{COLLECTION_NAME}'")
    print(f"  collection now has {collection.count()} total chunks")
    print(f"  sources represented: {sorted(set(c['source'] for c in all_chunks))}")
    print("\nDone. Test with: python rag.py 'Mexico energy transition'")


if __name__ == "__main__":
    main()
