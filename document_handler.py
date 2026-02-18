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
            text = self._extract_text_with_layout(page)
            images = self._extract_images_from_pdf_page(page_index)
            return PageContent(page_index + 1, text, images)
        elif self.file_type == 'docx':
            return self._get_docx_page_content(page_index)
        elif self.file_type == 'doc':
            return self._get_doc_page_content(page_index)

    def _extract_text_with_layout(self, page) -> str:
        """Ekstrakcja tekstu z uwzględnieniem layoutu wielokolumnowego.

        Zamiast page.get_text('text'), używamy bloków z bounding-boxami,
        wykrywamy kolumny na podstawie pozycji X i sortujemy tekst
        w kolejności: kolumna lewa→prawa, w kolumnie góra→dół.
        """
        blocks = page.get_text("blocks")
        # Filtruj tylko bloki tekstowe (typ 0), pomiń obrazy (typ 1)
        text_blocks = [b for b in blocks if b[6] == 0]

        if not text_blocks:
            return ""

        if len(text_blocks) == 1:
            return text_blocks[0][4].strip()

        # Wykryj kolumny na podstawie pozycji X bloków
        columns = self._detect_columns(text_blocks)

        # Sortuj kolumny od lewej do prawej
        columns.sort(key=lambda col: min(b[0] for b in col))

        # Buduj tekst: kolumna po kolumnie, wewnątrz sortując po Y
        result_parts = []
        for col_blocks in columns:
            col_blocks.sort(key=lambda b: b[1])  # sortuj po y0
            for block in col_blocks:
                text = block[4].strip()
                if text:
                    result_parts.append(text)

        return "\n\n".join(result_parts)

    @staticmethod
    def _detect_columns(text_blocks: list) -> list:
        """Grupuje bloki tekstowe w kolumny na podstawie pozycji X.

        Algorytm: sortujemy środki X bloków, łączymy bloki w kolumny
        jeśli ich środki X są bliżej niż próg (10% szerokości strony).
        """
        if not text_blocks:
            return []

        # Oblicz szerokość strony z bloków
        all_x0 = [b[0] for b in text_blocks]
        all_x1 = [b[2] for b in text_blocks]
        page_width = max(all_x1) - min(all_x0) if all_x1 else 600
        threshold = page_width * 0.1  # 10% szerokości jako próg

        # Oblicz środek X dla każdego bloku
        blocks_with_cx = [(b, (b[0] + b[2]) / 2) for b in text_blocks]
        blocks_with_cx.sort(key=lambda item: item[1])

        # Grupowanie zachłanne: bloki z podobną pozycją X → ta sama kolumna
        columns = []
        current_col = [blocks_with_cx[0][0]]
        current_cx = blocks_with_cx[0][1]

        for block, cx in blocks_with_cx[1:]:
            if abs(cx - current_cx) <= threshold:
                current_col.append(block)
                # Aktualizuj środek kolumny jako średnią
                total = len(current_col)
                current_cx = (current_cx * (total - 1) + cx) / total
            else:
                columns.append(current_col)
                current_col = [block]
                current_cx = cx

        columns.append(current_col)
        return columns

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
