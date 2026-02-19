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

    # Wzorce do filtrowania nagłówków/stopek
    _HEADER_FOOTER_PATTERNS = re.compile(
        r'^(\d{1,3})\s*$'             # sam numer strony
        r'|^www\.\S+\.\S+'            # URL jak www.audio.com.pl
        r'|^https?://\S+'             # pełny URL
        r'|^\d{1,3}\s+www\.\S+'       # "41 www.audio.com.pl"
        r'|^str\.\s*\d+'              # "str. 41"
        r'|^AUDIO\s'                  # nagłówki redakcyjne
        , re.IGNORECASE | re.MULTILINE
    )

    def _extract_text_with_layout(self, page) -> str:
        """Ekstrakcja tekstu z uwzględnieniem layoutu wielokolumnowego.

        Algorytm:
        1. Pobierz bloki tekstowe z bounding-boxami
        2. Odfiltruj nagłówki/stopki (marginesy górny/dolny)
        3. Wykryj kolumny za pomocą gap-based clustering
        4. Oddziel podpisy do zdjęć od tekstu głównego
        5. Sortuj: podpisy (góra→dół), potem kolumny (lewa→prawa, góra→dół)
        """
        blocks = page.get_text("blocks")
        # Filtruj tylko bloki tekstowe (typ 0), pomiń obrazy (typ 1)
        text_blocks = [b for b in blocks if b[6] == 0]

        if not text_blocks:
            return ""

        if len(text_blocks) == 1:
            return text_blocks[0][4].strip()

        # Wymiary strony
        page_rect = page.rect
        page_height = page_rect.height
        page_width = page_rect.width

        # 1. Filtruj nagłówki/stopki (górne/dolne 6% strony z typowymi wzorcami)
        margin_top = page_height * 0.06
        margin_bottom = page_height * 0.94
        filtered_blocks = []
        for b in text_blocks:
            text = b[4].strip()
            if not text:
                continue
            # Blok w marginesie górnym lub dolnym
            is_in_margin = (b[1] < margin_top) or (b[3] > margin_bottom)
            if is_in_margin and self._is_header_footer(text):
                continue
            filtered_blocks.append(b)

        if not filtered_blocks:
            # Jeśli wszystko odfiltrowane, zwróć oryginalne bloki
            filtered_blocks = [b for b in text_blocks if b[4].strip()]

        if len(filtered_blocks) == 1:
            return filtered_blocks[0][4].strip()

        # 2. Pobierz pozycje obrazów na stronie (do detekcji podpisów)
        image_rects = self._get_image_rects(page)

        # 3. Oddziel podpisy od tekstu głównego
        captions = []
        body_blocks = []
        for b in filtered_blocks:
            text = b[4].strip()
            if self._is_caption(b, text, image_rects, page_width):
                captions.append(b)
            else:
                body_blocks.append(b)

        if not body_blocks:
            body_blocks = filtered_blocks
            captions = []

        # 4. Wykryj kolumny w blokach głównych
        columns = self._detect_columns(body_blocks, page_width)

        # 5. Sortuj kolumny od lewej do prawej
        columns.sort(key=lambda col: min(b[0] for b in col))

        # 6. Buduj tekst wynikowy
        result_parts = []

        # Najpierw podpisy — posortowane po pozycji Y (góra→dół)
        captions.sort(key=lambda b: b[1])
        for block in captions:
            text = block[4].strip()
            if text:
                result_parts.append(text)

        # Potem tekst główny — kolumna po kolumnie
        for col_blocks in columns:
            col_blocks.sort(key=lambda b: b[1])  # sortuj po y0
            for block in col_blocks:
                text = block[4].strip()
                if text:
                    result_parts.append(text)

        return "\n\n".join(result_parts)

    def _is_header_footer(self, text: str) -> bool:
        """Sprawdza czy tekst to typowy nagłówek/stopka strony."""
        text = text.strip()
        if len(text) > 100:
            return False  # za długi na nagłówek/stopkę
        return bool(self._HEADER_FOOTER_PATTERNS.match(text))

    @staticmethod
    def _get_image_rects(page) -> list:
        """Zwraca listę prostokątów (x0, y0, x1, y1) obrazów na stronie."""
        rects = []
        try:
            for img in page.get_images(full=True):
                xref = img[0]
                for inst in page.get_image_rects(xref):
                    rects.append((inst.x0, inst.y0, inst.x1, inst.y1))
        except Exception:
            pass
        return rects

    @staticmethod
    def _is_caption(block, text: str, image_rects: list,
                    page_width: float) -> bool:
        """Sprawdza czy blok to podpis do zdjęcia.

        Kryteria: krótki tekst (< 200 znaków), blisko obrazu (w pionie),
        i nie rozciąga się na pełną szerokość strony.
        """
        if len(text) > 200:
            return False

        block_width = block[2] - block[0]
        # Podpis zwykle nie zajmuje więcej niż ~55% szerokości strony
        if block_width > page_width * 0.55:
            return False

        # Sprawdź bliskość pionową do obrazu (w granicach 30px)
        proximity = 30
        b_y0, b_y1 = block[1], block[3]
        for img_rect in image_rects:
            img_y0, img_y1 = img_rect[1], img_rect[3]
            # Podpis tuż pod lub tuż nad obrazem
            if abs(b_y0 - img_y1) < proximity or abs(img_y0 - b_y1) < proximity:
                return True

        return False

    @staticmethod
    def _detect_columns(text_blocks: list, page_width: float = None) -> list:
        """Grupuje bloki tekstowe w kolumny za pomocą gap-based clustering.

        Zamiast stałego progu procentowego, szukamy naturalnych przerw
        (gaps) w pozycjach X-center bloków. Duża przerwa = granica kolumny.
        To działa zarówno dla 2-, jak i 3-kolumnowych layoutów.
        """
        if not text_blocks:
            return []

        if len(text_blocks) == 1:
            return [text_blocks[:]]

        # Oblicz szerokość strony jeśli nie podana
        if page_width is None:
            all_x0 = [b[0] for b in text_blocks]
            all_x1 = [b[2] for b in text_blocks]
            page_width = max(all_x1) - min(all_x0) if all_x1 else 600

        # Oblicz środek X dla każdego bloku
        blocks_with_cx = [(b, (b[0] + b[2]) / 2) for b in text_blocks]
        blocks_with_cx.sort(key=lambda item: item[1])

        # Oblicz przerwy (gaps) między kolejnymi mid-X
        cx_values = [cx for _, cx in blocks_with_cx]

        if len(cx_values) < 2:
            return [text_blocks[:]]

        gaps = []
        for i in range(1, len(cx_values)):
            gap = cx_values[i] - cx_values[i - 1]
            gaps.append((gap, i))

        # Mediana przerw — przerwy znacznie większe od mediany to granice kolumn
        gap_values = sorted([g for g, _ in gaps])
        median_gap = gap_values[len(gap_values) // 2]

        # Próg: przerwa musi być > 5% szerokości strony I > 3× mediana
        min_gap = max(page_width * 0.05, median_gap * 3, 20)

        # Znajdź indeksy podziałów
        split_indices = [0]
        for gap, idx in gaps:
            if gap >= min_gap:
                split_indices.append(idx)
        split_indices.append(len(blocks_with_cx))

        # Podziel bloki na kolumny
        columns = []
        for i in range(len(split_indices) - 1):
            start = split_indices[i]
            end = split_indices[i + 1]
            col = [b for b, _ in blocks_with_cx[start:end]]
            if col:
                columns.append(col)

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
