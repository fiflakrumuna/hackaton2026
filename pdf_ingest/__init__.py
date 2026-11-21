"""pdf_ingest – warstwa wejściowa Odwołania2json: PDF/skan -> czysty tekst + metadane."""
from .config import IngestConfig
from .loader import PdfLoadError
from .models import PAGE_SEPARATOR, DocumentResult, PageResult, Word
from .pipeline import extract_document, extract_many

__all__ = ["IngestConfig", "PdfLoadError", "DocumentResult", "PageResult", "Word",
           "PAGE_SEPARATOR", "extract_document", "extract_many"]
__version__ = "0.1.0"
