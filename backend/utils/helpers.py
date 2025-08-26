from jinja2 import Template


def load_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


import requests
import io
import logging
from pypdf import PdfReader
from pypdf.errors import PdfReadError

def render_prompt(template_text: str, variables: dict) -> str:
    return Template(template_text).render(**(variables or {}))



def get_text_from_pdf_url(url: str, hard_limit: int = 100_000) -> str:
    """
    Download a PDF (SAS or public) and extract text.
    Returns a trimmed string; never bytes. On any failure returns "".
    """
    try:
        # Prefer Azure SDK (handles SAS robustly), fallback to requests
        try:
            from azure.storage.blob import BlobClient
            bc = BlobClient.from_blob_url(url)
            pdf_bytes = bc.download_blob().readall()
        except Exception:
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (compatible; DonorIntelBot/1.0)"}
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            pdf_bytes = r.content

        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for p in reader.pages:
            parts.append(p.extract_text() or "")
        text = "\n".join(parts).strip()

        if len(text) > hard_limit:
            text = text[:hard_limit] + "\n[TRUNCATED]"
        return text
    except Exception as e:
        logging.warning(f"get_text_from_pdf_url: failed to read {url}: {e}")
        return ""
# def get_text_from_pdf_url(pdf_url: str) -> str:
#     """
#     Downloads a PDF from a URL, extracts text, and returns it as a string.
#     Includes robust error handling for download and parsing issues.
#     """
#     try:
#         response = requests.get(pdf_url, timeout=30) # Add a timeout for the request
#         response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

#         if not response.content:
#             logging.warning(f"Downloaded PDF from {pdf_url} is empty (0 bytes).")
#             return ""

#         pdf_file = io.BytesIO(response.content)
#         reader = PdfReader(pdf_file)

#         text = ""
#         for page in reader.pages:
#             text += page.extract_text() or ""

#         if not text:
#             logging.warning(f"PDF from {pdf_url} contained no extractable text.")

#         return text

#     except requests.exceptions.RequestException as e:
#         logging.error(f"Error downloading PDF from {pdf_url}: {e}")
#         return ""
#     except PdfReadError as e:
#         logging.error(f"Failed to read PDF from {pdf_url}. The document may be corrupted, password-protected, or empty. Details: {e}")
#         return ""
#     except Exception as e:
#         logging.error(f"An unexpected error occurred while processing PDF from {pdf_url}: {e}")
#         return ""