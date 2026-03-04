"""
Redaktor AI — AI Processor
Integracja z Gemini 2.5 Flash-Lite Preview przez google-genai SDK.
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
DEFAULT_MODEL = "gemini-3.5-flash-lite-preview-06-17"
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
- Pole "formatted_text" zawiera sformatowany tekst w Markdown.

UWAGA O FORMACIE WEJŚCIOWYM — ARTEFAKTY Z WIELOKOLUMNOWYCH PDF:
- Tekst pochodzi z automatycznej ekstrakcji PDF, w tym z dokumentów 2- i 3-kolumnowych.
- System ekstrakcji stara się odczytać tekst po kolumnach (lewa→prawa), ale mogą nadal wystąpić artefakty:
  • Podpisy do zdjęć mogą pojawić się przed głównym tekstem lub między kolumnami.
  • Zdania mogą być ucinane w pół słowa na końcu wiersza (np. "Texas Instru-\\nments" = "Texas Instruments").
  • Numery stron lub URL-e redakcyjne (np. "41\\nwww.audio.com.pl") mogą pojawić się w środku tekstu.
  • Fragmenty z różnych kolumn mogą się przeplatać, zwłaszcza na granicach kolumn.
- KLUCZOWE: Staraj się zrekonstruować logiczny przepływ tekstu. Jeśli zdanie jest urwane i kontynuowane dalej, połącz je w całość. Jeśli fragmenty z podpisów zdjęć wmieszały się w tekst, wyodrębnij je lub usuń.
- Dzielenie wyrazów na końcu wiersza (np. "transforma-\\ntorem") — połącz w jedno słowo."""

META_TAGS_SYSTEM_PROMPT = """Jesteś ekspertem SEO. Na podstawie poniższego tekstu artykułu, wygeneruj chwytliwy meta title i zwięzły meta description.

WYMAGANIA:
- meta_title: max 60 znaków, chwytliwy i zawierający główne słowo kluczowe.
- meta_description: max 160 znaków, zwięzły opis zachęcający do kliknięcia."""

SEO_SYSTEM_PROMPT = """Jesteś doświadczonym copywriterem SEO i strategiem treści z 15-letnim doświadczeniem w polskojęzycznym content marketingu. Tworzysz artykuły, które naturalnie rankują w Google, ponieważ rozumiesz intencje wyszukiwania, kolokacje językowe i semantyczne powiązania między frazami.

Twój proces twórczy składa się z następujących kroków (realizujesz je mentalnie, a wynikiem jest gotowy artykuł):

## KROK 1: ANALIZA ODBIORCY
- Na podstawie treści źródłowej określ, kto jest typowym czytelnikiem (np. audiofil, profesjonalista IT, hobbystyczny majsterkowicz, manager).
- Dostosuj styl, ton i poziom techniczny do zidentyfikowanego odbiorcy.
- Jeśli treść jest techniczna/specjalistyczna — pisz jak ekspert do znawcy, ale z przystępnością. Unikaj protekcjonalności.
- Jeśli treść jest ogólna — pisz przystępnym, ciepłym językiem.

## KROK 2: MAPA SEMANTYCZNA (Query Fan-Out)
- Zidentyfikuj główną frazę kluczową artykułu.
- Rozwiń ją w klaster powiązanych zapytań, jakie użytkownik mógłby wpisywać w Google.
- Naturalnie wplecij te zapytania w strukturę artykułu (nagłówki H2/H3, treść akapitów).
- NIE upychaj słów kluczowych sztucznie — każda fraza musi brzmieć naturalnie w kontekście zdania.

## KROK 3: KOLOKACJE I NATURALNOŚĆ JĘZYKA
- Używaj naturalnych kolokacji polskiego języka (np. "przeprowadzić test" zamiast "zrobić test", "oferować funkcjonalność" zamiast "mieć funkcjonalność").
- Stosuj synonimy i warianty fraz kluczowych, aby tekst brzmiał naturalnie i nie był powtarzalny.
- Pamiętaj o odmianach przez przypadki — frazy kluczowe muszą być poprawnie odmienione w kontekście zdania.
- Unikaj kalkowania z angielskiego. Pisz po polsku naturalnie.

## KROK 4: STRUKTURA ODWRÓCONEJ PIRAMIDY
- **Pierwszy akapit (lead)**: Odpowiedz na pytanie czytelnika — co, dlaczego, dla kogo. To jest najważniejszy akapit.
- **Rozwinięcie**: Szczegóły techniczne, porównania, kontekst — w kolejności malejącej ważności.
- **Zamknięcie**: Podsumowanie, rekomendacje, perspektywa.

## KROK 5: FORMATOWANIE SEO
- **Tytuł (seo_title)**: Chwytliwy, z główną frazą kluczową, max 60 znaków. Musi wywoływać ciekawość.
- **Śródtytuły H2/H3**: Każdy śródtytuł powinien odpowiadać na potencjalne pytanie użytkownika lub zawierać frazę kluczową. Śródtytuły NIE powinny być sztampowe (unikaj "Podsumowanie", "Wstęp").
- **Listy punktowane**: Używaj ich do specyfikacji, porównań, zalet/wad.
- **Pogrubienia**: Kluczowe terminy techniczne, nazwy produktów, najważniejsze wnioski.
- **Akapity**: Krótkie (2-4 zdania). Każdy akapit = jedna myśl.

## ZASADY KRYTYCZNE:
1. **WIERNOŚĆ FAKTÓW**: Bazuj WYŁĄCZNIE na informacjach z dostarczonych stron źródłowych. Nie wymyślaj faktów, danych, cen ani specyfikacji.
2. **NATURALNOŚĆ**: Artykuł musi brzmieć jak napisany przez człowieka-eksperta, nie przez AI. Unikaj szablonowych fraz typu "W dzisiejszych czasach", "Warto zauważyć, że", "Nie da się ukryć".
3. **WARTOŚĆ DLA CZYTELNIKA**: Każde zdanie musi wnosić wartość. Usuń watę słowną, ogólniki i truizmy.
4. **POLISH SEO**: Pisz po polsku z uwzględnieniem polskiej specyfiki SEO (odmiana fraz, naturalny szyk zdania)."""

VISION_ARTICLE_PROMPT = """Jesteś precyzyjnym asystentem redakcyjnym z umiejętnością analizy wizualnej dokumentów.

DOSTAJESZ OBRAZ STRONY z PDF. Twoim zadaniem jest:
1. Wizualnie rozpoznać układ strony (liczba kolumn, położenie tytułów, zdjęć, podpisów).
2. Odczytać TEKST ze strony w prawidłowej kolejności lektury (kolumna po kolumnie, góra→dół).
3. Przekształcić go w czytelny artykuł.

ZASADA NADRZĘDNA: WIERNOŚĆ TREŚCI, ELASTYCZNOŚĆ FORMY.
- Nie zmieniaj oryginalnych sformułowań, przenieś tekst 1:1.
- Twoja rola polega na dodawaniu elementów strukturalnych i czyszczeniu śmieci.

INSTRUKCJE DLA ANALIZY WIZUALNEJ:
- Czytaj kolumny OD LEWEJ DO PRAWEJ, każdą kolumnę OD GÓRY DO DOŁU.
- Jeśli strona ma 2 lub 3 kolumny, traktuj każdą jako oddzielny ciąg tekstu.
- IGNORUJ: numery stron, stopki redakcyjne, URL-e typ "www.audio.com.pl", podpisy pod zdjęciami ("Fot.", "Rys.").
- IGNORUJ reklamy, ogłoszenia i elementy graficzne bez treści merytorycznej.

FORMATOWANIE:
1. Tytuł Główny: `# Tytuł`
2. Śródtytuły: `## Śródtytuł`
3. Pogrubienia: `**tekst**` (kluczowe terminy)
4. Listy: standardowe listy Markdown
5. Przypisy: `<sup>1</sup>`

WAŻNE:
- Ustaw "type" na "ARTYKUŁ" jeśli to tekst merytoryczny.
- Ustaw "type" na "REKLAMA" jeśli to reklama, spis treści lub strona graficzna.
- Pole "formatted_text" zawiera sformatowany tekst w Markdown."""


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

    def generate_seo_article(self, source_text: str, keywords: str = '') -> Dict:
        """Generuje artykuł SEO na podstawie tekstu źródłowego."""
        user_text = source_text
        if keywords:
            user_text = (
                f"DODATKOWE SŁOWA KLUCZOWE DO UWZGLĘDNIENIA: {keywords}\n\n"
                f"TREŚĆ ŹRÓDŁOWA:\n{source_text}"
            )
        return self._generate(
            text=user_text,
            system_prompt=SEO_SYSTEM_PROMPT,
            response_schema=SEOArticleResponse,
            temperature=0.4,
        )

    def _generate_multimodal(
        self,
        parts: list,
        system_prompt: str,
        response_schema,
        temperature: float = 0.2,
    ) -> Dict:
        """Generuje odpowiedź z Gemini z treścią multimodalną (tekst + obraz)."""
        last_error = None
        raw_text = ""

        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=parts,
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
                    "Vision próba %d/%d: Błąd dekodowania JSON.",
                    attempt + 1, MAX_RETRIES,
                )
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                last_error = e
                logger.warning(
                    "Vision próba %d/%d: Błąd API: %s",
                    attempt + 1, MAX_RETRIES, str(e)[:200],
                )
                time.sleep(2.0 * (attempt + 1))

        return {
            "error": f"Błąd po {MAX_RETRIES} próbach.",
            "last_known_error": str(last_error),
            "raw_response": raw_text,
        }

    def process_page_vision(self, image_bytes: bytes) -> Dict:
        """Przetwarza stronę na podstawie obrazu (analiza wizualna)."""
        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type="image/png",
        )

        result = self._generate_multimodal(
            parts=["Przeanalizuj tę stronę dokumentu i odczytaj tekst w prawidłowej kolejności:", image_part],
            system_prompt=VISION_ARTICLE_PROMPT,
            response_schema=ArticleResponse,
            temperature=0.2,
        )

        page_data = {}
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
