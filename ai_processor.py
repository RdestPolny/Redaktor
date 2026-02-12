"""
Redaktor AI — AI Processor
Integracja z Gemini 3 Flash Preview przez google-genai SDK.
"""

import json
import logging
import time
from typing import Dict, List

from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from document_handler import PageContent
from utils import markdown_to_html

logger = logging.getLogger(__name__)

# ===== KONFIGURACJA =====

MAX_RETRIES = 3
DEFAULT_MODEL = "gemini-3-flash-preview"
API_KEY = "AIzaSyAl69MEfwxVIiU7sX6M3u-KOqVI_c782Yc"


# ===== PYDANTIC SCHEMAS =====

class ArticleResponse(BaseModel):
    """Schema odpowiedzi dla przetwarzania artykułu."""
    type: str = Field(
        description="Typ treści: 'ARTYKUŁ' jeśli to tekst merytoryczny, 'REKLAMA' jeśli to reklama/ogłoszenie"
    )
    formatted_text: str = Field(
        description="Sformatowany tekst artykułu w markdown, z nagłówkami, listami i pogrubieniami"
    )


class MetaTagsResponse(BaseModel):
    """Schema odpowiedzi dla meta tagów."""
    meta_title: str = Field(
        description="Meta title artykułu, max 60 znaków"
    )
    meta_description: str = Field(
        description="Meta description artykułu, max 160 znaków"
    )


class SEOArticleResponse(BaseModel):
    """Schema odpowiedzi dla optymalizacji SEO."""
    seo_title: str = Field(
        description="Nowy, zoptymalizowany pod SEO tytuł artykułu"
    )
    seo_article_markdown: str = Field(
        description="Pełna treść przepisanego artykułu w formacie Markdown"
    )


# ===== PROMPTY =====

ARTICLE_SYSTEM_PROMPT = """Jesteś precyzyjnym asystentem redakcyjnym. Twoim celem jest przekształcenie surowego tekstu w czytelny, dobrze zorganizowany artykuł internetowy.

ZASADA NADRZĘDNA: WIERNOŚĆ TREŚCI, ELASTYCZNOŚĆ FORMY.
- Nie zmieniaj oryginalnych sformułowań ani nie parafrazuj tekstu (chyba że to konieczne dla czytelności). Przenieś treść 1:1.
- Twoja rola polega na dodawaniu elementów strukturalnych i czyszczeniu śmieci.

INSTRUKCJE SPECJALNE (KRYTYCZNE):
1. **LISTY:** Jeśli widzisz wyliczenia (punkty, myślniki), formatuj je jako standardową listę Markdown:
   - Element listy 1
   - Element listy 2
2. **PRZYPISY/INDEKSY:** Jeśli wykryjesz indeksy przypisów (małe cyfry na końcu zdań lub słów), formatuj je używając tagu HTML: `<sup>1</sup>`, `<sup>2</sup>`.
3. **USUWANIE PODPISÓW I ŚMIECI:** BEZWZGLĘDNIE USUWAJ:
   - Podpisy pod zdjęciami (np. "Rys. 1. Widok...", "Fot. Jan Kowalski").
   - Źródła grafik i tabel (np. "Źródło: opracowanie własne").
   - Numery stron, nagłówki i stopki redakcyjne.
   - Etykiety typu "NEWS FLASH".

DOZWOLONE MODYFIKACJE STRUKTURALNE:
1. Tytuł Główny: `# Tytuł`
2. Śródtytuły: `## Śródtytuł` (używaj ich do rozbijania 'ściany tekstu').
3. Pogrubienia: `**tekst**` (dla kluczowych terminów i nazw własnych).
4. Podział na sekcje: `---` (jeśli na stronie są dwa niepowiązane tematy).

WAŻNE:
- Ustaw pole "type" na "ARTYKUŁ" jeśli treść jest merytorycznym tekstem (artykuł, esej, raport itp.).
- Ustaw pole "type" na "REKLAMA" jeśli to reklama, ogłoszenie, spis treści, lub strona z samymi grafikami.
- Pole "formatted_text" zawiera sformatowany tekst w Markdown."""

META_TAGS_SYSTEM_PROMPT = """Jesteś ekspertem SEO. Na podstawie poniższego tekstu artykułu, wygeneruj chwytliwy meta title i zwięzły meta description.

WYMAGANIA:
- meta_title: max 60 znaków, chwytliwy i zawierający główne słowo kluczowe.
- meta_description: max 160 znaków, zwięzły opis zachęcający do kliknięcia."""

SEO_SYSTEM_PROMPT = """Jesteś światowej klasy strategiem SEO i copywriterem. Twoim zadaniem jest przepisanie dostarczonego artykułu, aby był maksymalnie zoptymalizowany pod kątem wyszukiwarek i angażujący dla czytelników online.

ZASADY KRYTYCZNE:
1.  **WIERNOŚĆ FAKTÓW**: Musisz bazować WYŁĄCZNIE na informacjach zawartych w oryginalnym tekście. Nie dodawaj żadnych nowych faktów, danych ani opinii. Twoja rola to restrukturyzacja i optymalizacja.
2.  **ODWRÓCONA PIRAMIDA**: Zastosuj zasadę odwróconej piramidy. Najważniejsze informacje, kluczowe wnioski i odpowiedzi na potencjalne pytania czytelnika umieść na samym początku artykułu.
3.  **STRUKTURA I CZYTELNOŚĆ**:
    *   Stwórz nowy, chwytliwy tytuł zoptymalizowany pod kątem potencjalnych fraz kluczowych (H1).
    *   Podziel tekst na logiczne sekcje za pomocą śródtytułów (H2, H3).
    *   Używaj list punktowanych, jeśli to możliwe, aby zwiększyć czytelność.
    *   Stosuj pogrubienia (`**tekst**`) dla najważniejszych terminów.
4.  **JĘZYK**: Używaj aktywnego, dynamicznego języka. Unikaj strony biernej. Pisz bezpośrednio do czytelnika."""


# ===== KLASA AI PROCESSOR =====

class AIProcessor:
    """Klasa obsługująca komunikację z Gemini API."""

    def __init__(self, api_key: str = None, model: str = DEFAULT_MODEL):
        self.client = genai.Client(api_key=api_key or API_KEY)
        self.model = model

    def _generate(
        self,
        text: str,
        system_prompt: str,
        response_schema,
        temperature: float = 0.2,
    ) -> Dict:
        """Generuje odpowiedź z Gemini z retry logic."""
        last_error = None
        raw_text = ""

        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=text,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    ),
                )

                raw_text = response.text
                if not raw_text:
                    raise ValueError("API zwróciło pustą odpowiedź.")

                return json.loads(raw_text)

            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(
                    "Próba %d/%d: Błąd dekodowania JSON. Ponawiam...",
                    attempt + 1, MAX_RETRIES,
                )
                time.sleep(1.5 * (attempt + 1))
                continue
            except Exception as e:
                last_error = e
                logger.warning(
                    "Próba %d/%d: Błąd API: %s. Ponawiam...",
                    attempt + 1, MAX_RETRIES, str(e)[:200],
                )
                time.sleep(2.0 * (attempt + 1))
                continue

        return {
            "error": f"Błąd po {MAX_RETRIES} próbach.",
            "last_known_error": str(last_error),
            "raw_response": raw_text,
        }

    def process_page(self, page_content: PageContent) -> Dict:
        """Przetwarza pojedynczą stronę."""
        page_data = {"page_number": page_content.page_number}

        if len(page_content.text.split()) < 20:
            page_data["type"] = "pominięta"
            page_data["formatted_content"] = (
                "<i>Strona zawiera zbyt mało tekstu.</i>"
            )
            return page_data

        result = self._generate(
            text=page_content.text,
            system_prompt=ARTICLE_SYSTEM_PROMPT,
            response_schema=ArticleResponse,
            temperature=0.2,
        )

        if "error" in result:
            page_data["type"] = "błąd"
            page_data["formatted_content"] = (
                f"<div class='error-box'>"
                f"<strong>{result['error']}</strong><br>"
                f"<i>Ostatni błąd: {result['last_known_error']}</i>"
                f"</div>"
            )
        else:
            page_data["type"] = result.get("type", "nieznany").lower()
            formatted_text = result.get("formatted_text", "")

            if page_data["type"] == "artykuł":
                page_data["formatted_content"] = markdown_to_html(formatted_text)
                page_data["raw_markdown"] = formatted_text
            else:
                page_data["formatted_content"] = (
                    f"<i>Zidentyfikowano jako: "
                    f"<strong>{page_data['type'].upper()}</strong>.</i>"
                )

        return page_data

    def process_article_group(self, pages_content: List[PageContent]) -> Dict:
        """Przetwarza grupę stron jako jeden artykuł."""
        page_numbers = [p.page_number for p in pages_content]

        combined_text = "\n\n".join([
            f"--- STRONA {p.page_number} ---\n{p.text.strip()}"
            for p in pages_content
        ])

        result = self._generate(
            text=combined_text,
            system_prompt=ARTICLE_SYSTEM_PROMPT,
            response_schema=ArticleResponse,
            temperature=0.2,
        )

        article_data = {"page_numbers": page_numbers}

        if "error" in result:
            article_data["type"] = "błąd"
            article_data["formatted_content"] = (
                f"<div class='error-box'>"
                f"<strong>{result['error']}</strong><br>"
                f"<i>Ostatni błąd: {result['last_known_error']}</i>"
                f"</div>"
            )
        else:
            article_data["type"] = result.get("type", "nieznany").lower()
            formatted_text = result.get("formatted_text", "")

            if article_data["type"] == "artykuł":
                article_data["formatted_content"] = markdown_to_html(formatted_text)
                article_data["raw_markdown"] = formatted_text
            else:
                article_data["formatted_content"] = (
                    f"<i>Zidentyfikowano jako: "
                    f"<strong>{article_data['type'].upper()}</strong>.</i>"
                )

        return article_data

    def generate_meta_tags(self, article_text: str) -> Dict:
        """Generuje meta tagi dla artykułu."""
        return self._generate(
            text=article_text[:4000],
            system_prompt=META_TAGS_SYSTEM_PROMPT,
            response_schema=MetaTagsResponse,
            temperature=0.3,
        )

    def generate_seo_article(self, article_text: str) -> Dict:
        """Przepisuje artykuł pod kątem SEO."""
        return self._generate(
            text=article_text,
            system_prompt=SEO_SYSTEM_PROMPT,
            response_schema=SEOArticleResponse,
            temperature=0.4,
        )
