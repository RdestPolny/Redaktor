"""
Redaktor AI — Document Handler
Obsługa różnych formatów dokumentów (PDF, DOCX, DOC).
"""

import io
import re
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Opcjonalne importy
try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import mammoth
    MAMMOTH_AVAILABLE = True
except ImportError:
    MAMMOTH_AVAILABLE = False


@dataclass
class PageContent:
    """Reprezentuje zawartość pojedynczej strony."""
    page_number: int
    text: str
    images: List[Dict] = field(default_factory=list)


class DocumentHandler:
    """Klasa do obsługi różnych formatów dokumentów."""

    def __init__(self, file_bytes: bytes, filename: str):
        self.file_bytes = file_bytes
        self.filename = filename
        self.file_type = self._detect_file_type(filename)
        self._document = None
        self._html_content = None
        self._load_document()

    def _detect_file_type(self, filename: str) -> str:
        ext = Path(filename).suffix.lower()
        if ext == '.pdf':
            return 'pdf'
        elif ext == '.docx':
            if not DOCX_AVAILABLE:
                raise ValueError(
                    "Format DOCX nie jest obsługiwany. Zainstaluj: pip install python-docx"
                )
            return 'docx'
        elif ext == '.doc':
            if not MAMMOTH_AVAILABLE:
                raise ValueError(
                    "Format DOC nie jest obsługiwany. Zainstaluj: pip install mammoth"
                )
            return 'doc'
        else:
            raise ValueError(f"Nieobsługiwany format pliku: {ext}")

    def _load_document(self):
        if self.file_type == 'pdf':
            self._document = fitz.open(stream=self.file_bytes, filetype="pdf")
        elif self.file_type == 'docx':
            self._document = DocxDocument(io.BytesIO(self.file_bytes))
        elif self.file_type == 'doc':
            result = mammoth.convert_to_html(io.BytesIO(self.file_bytes))
            self._html_content = result.value
            self._document = None

    def get_page_count(self) -> int:
        if self.file_type == 'pdf':
            return len(self._document)
        elif self.file_type == 'docx':
            all_text = '\n\n'.join([p.text for p in self._document.paragraphs])
            words = all_text.split()
            return max(1, len(words) // 500 + (1 if len(words) % 500 > 0 else 0))
        elif self.file_type == 'doc':
            words = self._html_content.split()
            return max(1, len(words) // 500 + (1 if len(words) % 500 > 0 else 0))
        return 0

    def get_page_content(self, page_index: int) -> PageContent:
        if self.file_type == 'pdf':
            page = self._document.load_page(page_index)
            text = page.get_text("text")
            images = self._extract_images_from_pdf_page(page_index)
            return PageContent(page_index + 1, text, images)
        elif self.file_type == 'docx':
            return self._get_docx_page_content(page_index)
        elif self.file_type == 'doc':
            return self._get_doc_page_content(page_index)

    def _get_docx_page_content(self, page_index: int) -> PageContent:
        all_paragraphs = self._document.paragraphs
        words_per_page = 500
        all_text = '\n\n'.join([p.text for p in all_paragraphs])
        words = all_text.split()
        start_word = page_index * words_per_page
        end_word = min(start_word + words_per_page, len(words))
        page_text = ' '.join(words[start_word:end_word])
        images = self._extract_images_from_docx()
        return PageContent(page_index + 1, page_text, images)

    def _get_doc_page_content(self, page_index: int) -> PageContent:
        text = re.sub('<[^<]+?>', '', self._html_content)
        words = text.split()
        words_per_page = 500
        start_word = page_index * words_per_page
        end_word = min(start_word + words_per_page, len(words))
        page_text = ' '.join(words[start_word:end_word])
        return PageContent(page_index + 1, page_text, [])

    def _extract_images_from_pdf_page(self, page_index: int) -> List[Dict]:
        images = []
        if self.file_type != 'pdf':
            return images
        try:
            page = self._document.load_page(page_index)
            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                base_image = self._document.extract_image(xref)
                if (base_image
                        and base_image.get("width", 0) > 100
                        and base_image.get("height", 0) > 100):
                    images.append({
                        'image': base_image['image'],
                        'ext': base_image['ext'],
                        'index': img_index
                    })
        except Exception as e:
            logger.warning(
                "Nie udało się wyekstraktować obrazów ze strony %d: %s",
                page_index + 1, e,
            )
        return images

    def _extract_images_from_docx(self) -> List[Dict]:
        images = []
        try:
            for rel in self._document.part.rels.values():
                if "image" in rel.target_ref:
                    img_data = rel.target_part.blob
                    ext = rel.target_ref.split('.')[-1]
                    images.append({
                        'image': img_data,
                        'ext': ext,
                        'index': len(images)
                    })
        except Exception as e:
            logger.warning("Nie udało się wyekstraktować obrazów z DOCX: %s", e)
        return images

    def render_page_as_image(self, page_index: int) -> Optional[bytes]:
        if self.file_type != 'pdf':
            return None
        try:
            page = self._document.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            return pix.tobytes("png")
        except Exception as e:
            logger.error(
                "Błąd podczas renderowania strony %d: %s", page_index + 1, e
            )
            return None
