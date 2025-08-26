from typing import Optional, List
import io

def _download_via_requests(url: str) -> bytes:
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DonorIntelBot/1.0)"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.content

def _download_via_blob_client(url: str) -> bytes:
    from azure.storage.blob import BlobClient
    bc = BlobClient.from_blob_url(url)
    return bc.download_blob().readall()

def download_blob_bytes(url: str) -> bytes:
    # Try Azure SDK first (handles SAS, ranges, retries better), fallback to requests
    try:
        return _download_via_blob_client(url)
    except Exception:
        return _download_via_requests(url)

def pdf_bytes_to_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        chunks = []
        for p in reader.pages:
            chunks.append(p.extract_text() or "")
        text = "\n".join(chunks).strip()
        return text
    except Exception:
        # Keep the pipeline alive even if PDF parsing fails
        return ""

def fetch_uploaded_doc_texts(docs: List[dict], hard_limit: int = 100_000) -> str:
    """Return a single concatenated text (truncated) from the uploaded docs list."""
    parts: List[str] = []
    for d in docs or []:
        url = d.get("document_url")
        if not url:
            continue
        try:
            b = download_blob_bytes(url)
            t = pdf_bytes_to_text(b)
            if t:
                parts.append(t)
        except Exception:
            # swallow and continue
            continue

    full = "\n\n---\n\n".join([p for p in parts if p]).strip()
    # Store a bounded amount in Cosmos to avoid 2MB item limits
    if len(full) > hard_limit:
        full = full[:hard_limit] + "\n[TRUNCATED]"
    return full
