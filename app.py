"""
Redaktor AI — Interaktywny Procesor Dokumentów
================================================

Wersja Cloud Run z Gemini 3 Flash Preview.

URUCHOMIENIE LOKALNE:
    export GOOGLE_API_KEY="twój-klucz"
    pip install -r requirements.txt
    streamlit run app.py

DOCKER:
    docker build -t redaktor-ai .
    docker run -p 8080:8080 -e GOOGLE_API_KEY="twój-klucz" redaktor-ai
"""

import os
import asyncio
import streamlit as st
from pathlib import Path
from typing import Dict, List, Optional

from document_handler import DocumentHandler, PageContent, DOCX_AVAILABLE, MAMMOTH_AVAILABLE
from ai_processor import AIProcessor, DEFAULT_MODEL
from project_manager import (
    save_project, load_project, get_existing_projects,
)
from utils import (
    markdown_to_html, markdown_to_clean_html,
    generate_full_html_document, sanitize_filename,
    create_zip_archive, parse_page_groups,
)

# ===== KONFIGURACJA =====

BATCH_SIZE = 10

SESSION_STATE_DEFAULTS = {
    'processing_status': 'idle',
    'document': None,
    'current_page': 0,
    'total_pages': 0,
    'extracted_pages': [],
    'project_name': None,
    'next_batch_start_index': 0,
    'uploaded_filename': None,
    'api_key': None,
    'model': DEFAULT_MODEL,
    'meta_tags': {},
    'seo_articles': {},
    'project_loaded_and_waiting_for_file': False,
    'processing_mode': 'all',
    'start_page': 1,
    'end_page': 1,
    'processing_end_page_index': 0,
    'article_page_groups_input': '',
    'article_groups': [],
    'next_article_index': 0,
    'file_type': None,
}


# ===== HELPERS =====

def get_article_html_from_page(page_index: int) -> Optional[Dict]:
    """Pobiera czysty HTML artykułu dla danej strony."""
    page_result = st.session_state.extracted_pages[page_index]

    if not page_result or page_result.get('type') != 'artykuł':
        return None
    if 'raw_markdown' not in page_result:
        return None

    group_pages = page_result.get('group_pages', [])

    if group_pages and len(group_pages) > 1:
        first_page_index = group_pages[0] - 1
        first_page_result = st.session_state.extracted_pages[first_page_index]
        markdown_content = first_page_result.get('raw_markdown', '')
        title = f"Artykuł ze stron {group_pages[0]}-{group_pages[-1]}"
        pages = group_pages
    else:
        markdown_content = page_result.get('raw_markdown', '')
        title = f"Artykuł ze strony {page_index + 1}"
        pages = [page_index + 1]

    html_content = markdown_to_clean_html(markdown_content)

    meta_title = None
    meta_description = None

    if page_index in st.session_state.meta_tags:
        tags = st.session_state.meta_tags[page_index]
        if 'error' not in tags:
            meta_title = tags.get('meta_title')
            meta_description = tags.get('meta_description')

    html_document = generate_full_html_document(
        html_content,
        title=title,
        meta_title=meta_title,
        meta_description=meta_description,
    )

    return {
        'html_content': html_content,
        'html_document': html_document,
        'title': title,
        'pages': pages,
        'meta_title': meta_title,
        'meta_description': meta_description,
    }


# ===== OBSŁUGA PLIKÓW =====

def handle_file_upload(uploaded_file):
    """Obsługuje wgranie pliku."""
    try:
        with st.spinner("Ładowanie pliku..."):
            file_bytes = uploaded_file.read()
            document = DocumentHandler(file_bytes, uploaded_file.name)

            if st.session_state.project_loaded_and_waiting_for_file:
                if document.get_page_count() != st.session_state.total_pages:
                    st.error(
                        f"Błąd: Wgrany plik ma {document.get_page_count()} stron, "
                        f"a projekt oczekuje {st.session_state.total_pages}. "
                        "Wgraj właściwy plik."
                    )
                    return

                st.session_state.document = document
                st.session_state.uploaded_filename = uploaded_file.name
                st.session_state.file_type = document.file_type
                st.session_state.project_loaded_and_waiting_for_file = False
                st.success("✅ Plik pomyślnie dopasowany do projektu.")
            else:
                for key, value in SESSION_STATE_DEFAULTS.items():
                    if key != 'api_key':
                        st.session_state[key] = value

                st.session_state.document = document
                st.session_state.uploaded_filename = uploaded_file.name
                st.session_state.file_type = document.file_type
                st.session_state.project_name = sanitize_filename(
                    Path(uploaded_file.name).stem
                )
                st.session_state.total_pages = document.get_page_count()
                st.session_state.extracted_pages = [None] * document.get_page_count()
                st.session_state.end_page = document.get_page_count()

                st.success(
                    f"✅ Załadowano plik: {uploaded_file.name} "
                    f"({document.file_type.upper()})"
                )

    except Exception as e:
        st.error(f"❌ Błąd ładowania pliku: {e}")
        st.session_state.document = None

    st.rerun()


# ===== PRZETWARZANIE AI =====

async def process_batch(ai_processor: AIProcessor, start_index: int):
    """Przetwarza batch stron."""
    processing_limit = st.session_state.processing_end_page_index + 1
    end_index = min(start_index + BATCH_SIZE, processing_limit)

    tasks = []
    for i in range(start_index, end_index):
        if st.session_state.document:
            page_content = st.session_state.document.get_page_content(i)
            tasks.append(ai_processor.process_page(page_content))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        page_index = start_index + i
        if isinstance(result, Exception):
            st.session_state.extracted_pages[page_index] = {
                "page_number": page_index + 1,
                "type": "błąd",
                "formatted_content": f"Błąd: {result}",
            }
        else:
            st.session_state.extracted_pages[page_index] = result


def start_ai_processing():
    """Rozpoczyna przetwarzanie AI."""
    if st.session_state.processing_mode == 'article':
        try:
            groups = parse_page_groups(
                st.session_state.article_page_groups_input,
                st.session_state.total_pages,
            )
            for group in groups:
                for page in group:
                    st.session_state.extracted_pages[page - 1] = None

            st.session_state.article_groups = groups
            st.session_state.next_article_index = 0
            st.session_state.processing_status = 'in_progress'

            if groups:
                st.session_state.current_page = groups[0][0] - 1

        except ValueError as e:
            st.error(str(e))
            return
    else:
        if st.session_state.processing_mode == 'all':
            start_idx = 0
            end_idx = st.session_state.total_pages - 1
        else:
            start_idx = st.session_state.start_page - 1
            end_idx = st.session_state.end_page - 1

        if start_idx > end_idx:
            st.error("Strona początkowa nie może być większa niż końcowa.")
            return

        for i in range(start_idx, end_idx + 1):
            st.session_state.extracted_pages[i] = None

        st.session_state.processing_status = 'in_progress'
        st.session_state.next_batch_start_index = start_idx
        st.session_state.processing_end_page_index = end_idx
        st.session_state.current_page = start_idx


def run_ai_processing_loop():
    """Główna pętla przetwarzania AI."""
    if not st.session_state.api_key:
        st.error("Klucz API Google nie jest skonfigurowany.")
        st.session_state.processing_status = 'idle'
        return

    ai_processor = AIProcessor(st.session_state.api_key, st.session_state.model)

    if st.session_state.processing_mode == 'article':
        if st.session_state.next_article_index < len(st.session_state.article_groups):
            article_pages = st.session_state.article_groups[
                st.session_state.next_article_index
            ]

            pages_content = []
            for page_num in article_pages:
                if (st.session_state.document
                        and 0 <= page_num - 1 < st.session_state.total_pages):
                    pages_content.append(
                        st.session_state.document.get_page_content(page_num - 1)
                    )

            article_result = asyncio.run(
                ai_processor.process_article_group(pages_content)
            )

            for page in article_pages:
                page_index = page - 1
                if 0 <= page_index < len(st.session_state.extracted_pages):
                    entry = {
                        key: value
                        for key, value in article_result.items()
                        if key != 'page_numbers'
                    }
                    entry['page_number'] = page
                    entry['group_pages'] = article_pages
                    entry['is_group_lead'] = (page == article_pages[0])
                    st.session_state.extracted_pages[page_index] = entry

            st.session_state.next_article_index += 1
        else:
            st.session_state.processing_status = 'complete'
    else:
        if (st.session_state.next_batch_start_index
                <= st.session_state.processing_end_page_index):
            asyncio.run(
                process_batch(
                    ai_processor, st.session_state.next_batch_start_index
                )
            )
            st.session_state.next_batch_start_index += BATCH_SIZE
        else:
            st.session_state.processing_status = 'complete'

    st.rerun()


def handle_page_reroll(page_index: int):
    """Przetwarza stronę ponownie z kontekstem."""
    with st.spinner("Przetwarzanie strony z kontekstem..."):
        prev_text = ""
        if page_index > 0:
            prev_content = st.session_state.document.get_page_content(
                page_index - 1
            )
            prev_text = prev_content.text

        curr_content = st.session_state.document.get_page_content(page_index)
        curr_text = curr_content.text

        next_text = ""
        if page_index < st.session_state.total_pages - 1:
            next_content = st.session_state.document.get_page_content(
                page_index + 1
            )
            next_text = next_content.text

        context_text = (
            f"KONTEKST (POPRZEDNIA STRONA):\n{prev_text}\n\n"
            f"--- STRONA DOCELOWA ---\n{curr_text}\n\n"
            f"KONTEKST (NASTĘPNA STRONA):\n{next_text}"
        )

        ai_processor = AIProcessor(
            st.session_state.api_key, st.session_state.model
        )
        page_content = PageContent(page_index + 1, context_text)
        new_result = asyncio.run(ai_processor.process_page(page_content))

        st.session_state.extracted_pages[page_index] = new_result

    st.rerun()


def handle_meta_tag_generation(page_index: int, raw_markdown: str):
    """Generuje meta tagi dla artykułu."""
    with st.spinner("Generowanie meta tagów..."):
        ai_processor = AIProcessor(
            st.session_state.api_key, st.session_state.model
        )
        tags = asyncio.run(ai_processor.generate_meta_tags(raw_markdown))
        st.session_state.meta_tags[page_index] = tags

    st.rerun()


def handle_seo_generation(page_index: int, raw_markdown: str):
    """Generuje zoptymalizowaną wersję artykułu."""
    with st.spinner("🚀 Optymalizowanie artykułu dla SEO... To może chwilę potrwać."):
        ai_processor = AIProcessor(
            st.session_state.api_key, st.session_state.model
        )
        result = asyncio.run(ai_processor.generate_seo_article(raw_markdown))
        st.session_state.seo_articles[page_index] = result

    st.rerun()


# ===== UI COMPONENTS =====

def init_session_state():
    """Inicjalizuje stan sesji."""
    # Klucz API: najpierw env var, potem st.secrets jako fallback
    if 'api_key' not in st.session_state or st.session_state.api_key is None:
        env_key = os.environ.get("GOOGLE_API_KEY")
        if env_key:
            st.session_state.api_key = env_key
        else:
            st.session_state.api_key = (
                st.secrets.get("google", {}).get("api_key")
            )

    for key, value in SESSION_STATE_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_sidebar():
    """Renderuje panel boczny."""
    with st.sidebar:
        st.header("⚙️ Konfiguracja Projektu")

        # --- Klucz API ---
        if not st.session_state.api_key:
            manual_key = st.text_input(
                "🔑 Klucz API Google",
                type="password",
                placeholder="Wklej swój GOOGLE_API_KEY...",
            )
            if manual_key:
                st.session_state.api_key = manual_key
                st.rerun()

        # --- Projekty ---
        projects = get_existing_projects()
        selected_project = st.selectbox(
            "Wybierz istniejący projekt",
            ["Nowy projekt"] + projects,
        )

        if st.button(
            "Załaduj projekt",
            disabled=(selected_project == "Nowy projekt"),
        ):
            load_project(selected_project)
            st.rerun()

        st.divider()

        # --- Upload pliku ---
        supported_formats = ["pdf"]
        if DOCX_AVAILABLE:
            supported_formats.append("docx")
        if MAMMOTH_AVAILABLE:
            supported_formats.append("doc")

        file_label = (
            f"Wybierz plik ({', '.join(f.upper() for f in supported_formats)})"
        )

        uploaded_file = st.file_uploader(file_label, type=supported_formats)

        if uploaded_file:
            if (st.session_state.project_loaded_and_waiting_for_file
                    or uploaded_file.name != st.session_state.get('uploaded_filename')):
                handle_file_upload(uploaded_file)

        # --- Opcje przetwarzania ---
        if st.session_state.document:
            st.divider()
            st.subheader("🤖 Opcje Przetwarzania")

            st.radio(
                "Wybierz tryb:",
                ('all', 'range', 'article'),
                captions=[
                    "Cały dokument (strona po stronie)",
                    "Zakres stron (strona po stronie)",
                    "Artykuł wielostronicowy (jedno zapytanie)",
                ],
                key='processing_mode',
                horizontal=False,
            )

            if st.session_state.processing_mode == 'range':
                c1, c2 = st.columns(2)
                c1.number_input(
                    "Od strony",
                    min_value=1,
                    max_value=st.session_state.total_pages,
                    key='start_page',
                )
                c2.number_input(
                    "Do strony",
                    min_value=st.session_state.start_page,
                    max_value=st.session_state.total_pages,
                    key='end_page',
                )

            elif st.session_state.processing_mode == 'article':
                st.info(
                    "Podaj grupy stron dla artykułów wielostronicowych. "
                    "Każda grupa zostanie przetworzona w jednym zapytaniu do AI."
                )
                st.text_area(
                    "Zakresy stron artykułów (np. 1-3; 5,6)",
                    key='article_page_groups_input',
                    placeholder="1-3\n5,6\n8-10",
                    height=100,
                )

            st.divider()

            # --- Wybór modelu ---
            st.text_input(
                "Model AI",
                key='model',
                placeholder=DEFAULT_MODEL,
                help="Domyślnie: gemini-3-flash-preview",
            )

            st.divider()

            processing_disabled = (
                st.session_state.processing_status == 'in_progress'
                or not st.session_state.api_key
            )

            button_text = (
                "🔄 Przetwarzanie..."
                if st.session_state.processing_status == 'in_progress'
                else "🚀 Rozpocznij Przetwarzanie"
            )

            if st.button(
                button_text,
                use_container_width=True,
                type="primary",
                disabled=processing_disabled,
            ):
                start_ai_processing()
                st.rerun()

            st.divider()

            st.info(f"**Projekt:** {st.session_state.project_name}")
            st.metric("Liczba stron", st.session_state.total_pages)
            st.caption(f"**Format:** {st.session_state.file_type.upper()}")


def render_processing_status():
    """Renderuje status przetwarzania."""
    if st.session_state.processing_status == 'idle' or not st.session_state.document:
        return

    processed_count = sum(
        1 for p in st.session_state.extracted_pages if p is not None
    )

    if st.session_state.processing_mode == 'article':
        total_groups = len(st.session_state.article_groups)
        processed_groups = st.session_state.next_article_index
        progress = processed_groups / total_groups if total_groups > 0 else 0

        if st.session_state.processing_status == 'complete':
            st.success(
                f"✅ Przetwarzanie zakończone! "
                f"Przetworzono {total_groups} artykuł(ów)."
            )
            if st.session_state.article_groups:
                if st.button("📖 Przejdź do pierwszego artykułu", type="secondary"):
                    st.session_state.current_page = (
                        st.session_state.article_groups[0][0] - 1
                    )
                    st.rerun()
        else:
            st.info(
                f"🔄 Przetwarzanie artykułów... "
                f"({processed_groups}/{total_groups})"
            )
            st.progress(progress)
    else:
        total = st.session_state.total_pages
        progress = processed_count / total if total > 0 else 0

        if st.session_state.processing_status == 'complete':
            st.success("✅ Przetwarzanie zakończone!")

            if st.session_state.processing_mode == 'range':
                nav_cols = st.columns(2)
                if nav_cols[0].button(
                    "📖 Przejdź do początku zakresu", type="secondary"
                ):
                    st.session_state.current_page = st.session_state.start_page - 1
                    st.rerun()
                if nav_cols[1].button(
                    "📖 Przejdź do końca zakresu", type="secondary"
                ):
                    st.session_state.current_page = st.session_state.end_page - 1
                    st.rerun()
        else:
            st.info(
                f"🔄 Przetwarzanie w toku... "
                f"(Ukończono {processed_count}/{total} stron)"
            )
            st.progress(progress)

    c1, c2, _ = st.columns([1, 1, 3])

    if c1.button("💾 Zapisz postęp", use_container_width=True):
        save_project()

    articles = [
        p for p in st.session_state.extracted_pages
        if p and p.get('type') == 'artykuł' and p.get('is_group_lead', True)
    ]

    if articles:
        zip_data = [
            {
                'name': f"artykul_ze_str_{a['page_number']}.txt",
                'content': a['raw_markdown'].encode('utf-8'),
            }
            for a in articles
            if 'raw_markdown' in a
        ]

        if zip_data:
            c2.download_button(
                "📥 Pobierz artykuły",
                create_zip_archive(zip_data),
                f"{st.session_state.project_name}_artykuly.zip",
                "application/zip",
                use_container_width=True,
            )


def render_navigation():
    """Renderuje nawigację między stronami."""
    if st.session_state.total_pages <= 1:
        return

    st.subheader("📖 Nawigacja")

    if st.session_state.processing_mode == 'range':
        processing_range = (
            f"{st.session_state.start_page}-{st.session_state.end_page}"
        )
        st.info(f"🎯 Przetwarzany zakres: strony {processing_range}")

        nav_cols = st.columns(3)
        if nav_cols[0].button("⏮️ Początek zakresu", use_container_width=True):
            st.session_state.current_page = st.session_state.start_page - 1
            st.rerun()
        if nav_cols[1].button("⏭️ Koniec zakresu", use_container_width=True):
            st.session_state.current_page = st.session_state.end_page - 1
            st.rerun()
        if nav_cols[2].button("🏠 Początek dokumentu", use_container_width=True):
            st.session_state.current_page = 0
            st.rerun()

        st.divider()

    elif (st.session_state.processing_mode == 'article'
          and st.session_state.article_groups):
        st.info(
            f"🎯 Liczba artykułów: {len(st.session_state.article_groups)}"
        )

        article_nav_cols = st.columns(
            min(len(st.session_state.article_groups), 5)
        )
        for idx, group in enumerate(st.session_state.article_groups[:5]):
            label = f"Art. {idx + 1}"
            if len(group) > 1:
                label += f" ({group[0]}-{group[-1]})"
            else:
                label += f" (str. {group[0]})"

            if article_nav_cols[idx % 5].button(
                label, use_container_width=True, key=f"nav_art_{idx}"
            ):
                st.session_state.current_page = group[0] - 1
                st.rerun()

        if len(st.session_state.article_groups) > 5:
            st.caption(
                f"... i jeszcze "
                f"{len(st.session_state.article_groups) - 5} artykułów"
            )

        st.divider()

    # Standardowa nawigacja
    c1, c2, c3 = st.columns([1, 2, 1])

    if c1.button(
        "⬅️ Poprzednia",
        use_container_width=True,
        disabled=(st.session_state.current_page == 0),
    ):
        st.session_state.current_page -= 1
        st.rerun()

    c2.metric(
        "Strona",
        f"{st.session_state.current_page + 1} / {st.session_state.total_pages}",
    )

    if c3.button(
        "Następna ➡️",
        use_container_width=True,
        disabled=(
            st.session_state.current_page >= st.session_state.total_pages - 1
        ),
    ):
        st.session_state.current_page += 1
        st.rerun()

    new_page = st.slider(
        "Przejdź do strony:",
        1,
        st.session_state.total_pages,
        st.session_state.current_page + 1,
    ) - 1

    if new_page != st.session_state.current_page:
        st.session_state.current_page = new_page
        st.rerun()


def render_page_view():
    """Renderuje widok strony."""
    st.divider()

    page_index = st.session_state.current_page
    page_content = st.session_state.document.get_page_content(page_index)

    pdf_col, text_col = st.columns(2, gap="large")

    with pdf_col:
        st.subheader(f"📄 Oryginał (Strona {page_index + 1})")

        if st.session_state.file_type == 'pdf':
            image_data = st.session_state.document.render_page_as_image(
                page_index
            )
            if image_data:
                st.image(image_data, use_container_width=True)
            else:
                st.error("Nie można wyświetlić podglądu strony.")
        else:
            st.info(
                f"Podgląd nie jest dostępny dla plików "
                f"{st.session_state.file_type.upper()}."
            )

        if page_content.images:
            with st.expander(
                f"🖼️ Pokaż/ukryj {len(page_content.images)} obraz(y)"
            ):
                for img in page_content.images:
                    st.image(
                        img['image'],
                        caption=f"Obraz {img['index'] + 1}",
                        use_container_width=True,
                    )

            img_zip = create_zip_archive([
                {
                    'name': (
                        f"str_{page_index + 1}_img_{i['index']}.{i['ext']}"
                    ),
                    'content': i['image'],
                }
                for i in page_content.images
            ])

            st.download_button(
                "Pobierz obrazy",
                img_zip,
                f"obrazy_strona_{page_index + 1}.zip",
                "application/zip",
                use_container_width=True,
            )

    with text_col:
        st.subheader("🤖 Tekst przetworzony przez AI")

        with st.expander("👁️ Pokaż surowy tekst wejściowy"):
            st.text_area(
                "Surowy tekst",
                page_content.text,
                height=200,
                disabled=True,
                key=f"raw_text_{page_index}",
            )

        page_result = st.session_state.extracted_pages[page_index]

        if page_result:
            _render_page_result(page_index, page_result)
        else:
            if st.session_state.processing_status == 'in_progress':
                st.info("⏳ Strona oczekuje na przetworzenie...")
            else:
                st.info("Uruchom przetwarzanie w panelu bocznym.")


def _render_page_result(page_index: int, page_result: Dict):
    """Renderuje wynik przetwarzania strony."""
    page_type = page_result.get('type', 'nieznany')
    color_map = {
        "artykuł": "green",
        "reklama": "orange",
        "pominięta": "grey",
        "błąd": "red",
    }
    color = color_map.get(page_type, "red")

    st.markdown(
        f"**Status:** <span style='color:{color}; text-transform:uppercase;'>"
        f"**{page_type}**</span>",
        unsafe_allow_html=True,
    )

    group_pages = page_result.get('group_pages', [])
    if group_pages and len(group_pages) > 1:
        st.info(
            f"Ten artykuł obejmuje strony: "
            f"{', '.join(str(p) for p in group_pages)}."
        )

    st.markdown(
        f"<div class='page-text-wrapper'>"
        f"{page_result.get('formatted_content', '')}</div>",
        unsafe_allow_html=True,
    )

    # --- PRZYCISKI AKCJI ---
    st.write("---")
    st.markdown("**Akcje Redakcyjne:**")

    allow_actions = (
        page_type == 'artykuł'
        and 'raw_markdown' in page_result
        and page_result.get('is_group_lead', True)
    )

    action_cols = st.columns(4)

    if action_cols[0].button(
        "🔄 Przetwórz ponownie",
        key=f"reroll_{page_index}",
        use_container_width=True,
    ):
        handle_page_reroll(page_index)

    if action_cols[1].button(
        "✨ Generuj Meta",
        key=f"meta_{page_index}",
        use_container_width=True,
        disabled=not allow_actions,
    ):
        handle_meta_tag_generation(page_index, page_result['raw_markdown'])

    if action_cols[2].button(
        "🚀 Optymalizuj dla SEO",
        key=f"seo_{page_index}",
        use_container_width=True,
        disabled=not allow_actions,
        help="Przepisz artykuł zgodnie z zasadami SEO",
    ):
        handle_seo_generation(page_index, page_result['raw_markdown'])

    show_html = action_cols[3].checkbox(
        "📄 Pokaż HTML",
        key=f"show_html_checkbox_{page_index}",
        disabled=not allow_actions,
        help="Pokaż i pobierz czysty HTML artykułu",
    )

    # --- HTML PREVIEW ---
    if show_html and allow_actions:
        _render_html_preview(page_index)

    # --- META TAGI ---
    if page_index in st.session_state.meta_tags:
        tags = st.session_state.meta_tags[page_index]
        if "error" in tags:
            st.error(f"Błąd generowania meta tagów: {tags['error']}")
        else:
            with st.expander("Wygenerowane Meta Tagi ✨", expanded=False):
                st.text_input(
                    "Meta Title",
                    value=tags.get("meta_title", ""),
                    key=f"mt_{page_index}",
                )
                st.text_area(
                    "Meta Description",
                    value=tags.get("meta_description", ""),
                    key=f"md_{page_index}",
                )

    # --- SEO ARTICLE ---
    if page_index in st.session_state.seo_articles:
        seo_result = st.session_state.seo_articles[page_index]
        with st.expander("🤖 Zoptymalizowany Artykuł SEO", expanded=True):
            if "error" in seo_result:
                st.error(
                    f"Błąd podczas optymalizacji SEO: {seo_result['error']}"
                )
                st.json(seo_result)
            else:
                seo_title = seo_result.get("seo_title", "Brak tytułu")
                seo_markdown = seo_result.get(
                    "seo_article_markdown", "Brak treści."
                )

                st.markdown(f"### {seo_title}")
                st.markdown(seo_markdown, unsafe_allow_html=True)
                st.download_button(
                    label="📥 Pobierz wersję SEO (.txt)",
                    data=f"# {seo_title}\n\n{seo_markdown}",
                    file_name=f"{sanitize_filename(seo_title)}.txt",
                    mime="text/plain",
                    use_container_width=True,
                    key=f"download_seo_{page_index}",
                )


def _render_html_preview(page_index: int):
    """Renderuje podgląd HTML artykułu."""
    html_data = get_article_html_from_page(page_index)
    if not html_data:
        return

    st.divider()

    with st.expander("📄 Czysty HTML artykułu", expanded=True):
        st.caption(f"**{html_data['title']}**")

        tab1, tab2 = st.tabs([
            "💻 Kod HTML (zawartość)",
            "📰 Pełny dokument HTML",
        ])

        with tab1:
            st.code(
                html_data['html_content'],
                language='html',
                line_numbers=True,
            )
            st.download_button(
                label="📥 Pobierz zawartość HTML",
                data=html_data['html_content'],
                file_name=(
                    f"{sanitize_filename(html_data['title'])}_content.html"
                ),
                mime="text/html",
                use_container_width=True,
                key=f"download_content_{page_index}",
            )

        with tab2:
            st.code(
                html_data['html_document'],
                language='html',
                line_numbers=True,
            )
            st.download_button(
                label="📥 Pobierz pełny dokument HTML",
                data=html_data['html_document'],
                file_name=f"{sanitize_filename(html_data['title'])}.html",
                mime="text/html",
                use_container_width=True,
                key=f"download_full_{page_index}",
            )

        if html_data['meta_title'] or html_data['meta_description']:
            st.info("ℹ️ Ten HTML zawiera wygenerowane meta tagi SEO")


# ===== GŁÓWNA APLIKACJA =====

def main():
    st.set_page_config(
        layout="wide",
        page_title="Redaktor AI - Procesor Dokumentów",
        page_icon="🚀",
    )

    st.markdown("""
    <style>
    .page-text-wrapper {
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 20px;
        background-color: #f9f9f9;
        max-height: 600px;
        overflow-y: auto;
    }
    .error-box {
        background-color: #ffebee;
        border-left: 4px solid #f44336;
        padding: 12px;
        border-radius: 4px;
        margin: 10px 0;
    }
    .stButton button {
        border-radius: 8px;
    }
    h2, h3, h4 {
        margin-top: 1em;
        margin-bottom: 0.5em;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("🚀 Redaktor AI — Procesor Dokumentów")
    st.caption("Gemini 3 Flash Preview · Cloud Run Ready")

    init_session_state()

    # Ostrzeżenie o brakujących formatach
    if not DOCX_AVAILABLE or not MAMMOTH_AVAILABLE:
        missing = []
        if not DOCX_AVAILABLE:
            missing.append("DOCX (zainstaluj: pip install python-docx)")
        if not MAMMOTH_AVAILABLE:
            missing.append("DOC (zainstaluj: pip install mammoth)")

        with st.sidebar:
            with st.expander("⚠️ Ograniczona funkcjonalność", expanded=False):
                st.warning("Niektóre formaty plików nie są dostępne:")
                for fmt in missing:
                    st.write(f"- {fmt}")

    if not st.session_state.api_key:
        st.error("❌ Brak klucza API Google!")
        st.info(
            "Ustaw zmienną środowiskową `GOOGLE_API_KEY` lub "
            "wpisz klucz w panelu bocznym."
        )
        render_sidebar()
        st.stop()

    render_sidebar()

    if not st.session_state.document:
        if not st.session_state.project_loaded_and_waiting_for_file:
            st.info(
                "👋 Witaj! Aby rozpocząć, wgraj plik (PDF/DOCX/DOC) "
                "lub załaduj istniejący projekt z panelu bocznego."
            )

            with st.expander("📖 Jak korzystać z aplikacji?"):
                st.markdown("""
                ### Tryby przetwarzania:

                1. **Cały dokument** — Przetwarza każdą stronę osobno
                2. **Zakres stron** — Przetwarza wybrany zakres stron osobno
                3. **Artykuł wielostronicowy** — Łączy wybrane strony w jedno zapytanie

                ### Obsługiwane formaty:
                - PDF (z podglądem i wyciąganiem grafik)
                - DOCX (Microsoft Word)
                - DOC (starsze pliki Word)

                ### Funkcje:
                - Zapisywanie i ładowanie projektów
                - Wyciąganie grafik ze stron
                - Generowanie meta tagów SEO
                - Optymalizacja artykułów pod kątem SEO
                - Eksport do czystego HTML
                - Ponowne przetwarzanie stron z kontekstem
                """)
        return

    render_processing_status()

    if st.session_state.processing_status == 'in_progress':
        run_ai_processing_loop()
    else:
        render_navigation()
        render_page_view()


if __name__ == "__main__":
    main()
