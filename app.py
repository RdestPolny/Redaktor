"""
Redaktor AI — Flask Application
Interaktywny procesor dokumentów z Gemini 2.5 Flash-Lite Preview.
"""

import os
import io
import base64
import json
import logging
import threading
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# tkinter do natywnego okna wyboru pliku (stdlib, brak instalacji)
try:
    import tkinter as tk
    from tkinter import filedialog as tk_filedialog
    _TKINTER_AVAILABLE = True
except ImportError:
    _TKINTER_AVAILABLE = False

from flask import (
    Flask, request, jsonify, render_template,
    send_file, session, Response, stream_with_context,
)

from document_handler import DocumentHandler, DOCX_AVAILABLE, MAMMOTH_AVAILABLE
from ai_processor import AIProcessor, DEFAULT_MODEL
from project_manager import (
    save_project, load_project, get_existing_projects,
)
from utils import (
    markdown_to_html, markdown_to_clean_html,
    generate_full_html_document, sanitize_filename,
    create_zip_archive, parse_page_groups,
)

# ===== SETUP =====

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB

# ===== ERROR HANDLERS (zawsze JSON, nigdy HTML) =====

@app.errorhandler(413)
def request_entity_too_large(e):
    """Plik za duży — zwraca JSON zamiast domyślnego HTML."""
    return jsonify(
        error="Plik jest za duży. Maksymalny rozmiar to 500 MB.",
        code=413,
    ), 413

@app.errorhandler(400)
def bad_request(e):
    return jsonify(error=f"Błędne żądanie: {e}", code=400), 400

@app.errorhandler(500)
def internal_error(e):
    logger.error("Internal server error: %s", e)
    return jsonify(error="Wewnętrzny błąd serwera. Spróbuj ponownie.", code=500), 500

# ==================================================

# Stan dokumentów w pamięci serwera (single-user per instance)
# Na Cloud Run każdy kontener obsługuje jednego użytkownika naraz
_state = {
    'document': None,
    'filename': None,
    'file_type': None,
    'total_pages': 0,
    'extracted_pages': [],
    'meta_tags': {},
    'seo_articles': {},
    'project_name': None,
    'temp_file_path': None,
}

def cleanup_temp_file():
    temp_path = _state.get('temp_file_path')
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except Exception as e:
            logger.warning("Nie udało się usunąć pliku tymczasowego: %s", e)
    _state['temp_file_path'] = None


def _get_ai() -> AIProcessor:
    """Zwraca instancję AI procesora."""
    return AIProcessor()


# ===== ROUTES: PAGES =====

@app.route('/')
def index():
    """Strona główna."""
    supported = ['PDF']
    if DOCX_AVAILABLE:
        supported.append('DOCX')
    if MAMMOTH_AVAILABLE:
        supported.append('DOC')
    return render_template('index.html', supported_formats=supported)


# ===== ROUTES: UPLOAD =====

# Wykryj środowisko: Cloud Run ma zmienną K_SERVICE
_IS_CLOUD_RUN = bool(os.environ.get('K_SERVICE'))
# Cloud Run twardym limitem requestu jest 32MB
_MAX_UPLOAD_BYTES = 32 * 1024 * 1024 if _IS_CLOUD_RUN else (500 * 1024 * 1024)


@app.route('/upload/limits')
def upload_limits():
    """Zwraca limity uploadu dla bieżącego środowiska."""
    return jsonify(
        max_bytes=_MAX_UPLOAD_BYTES,
        max_mb=round(_MAX_UPLOAD_BYTES / 1024 / 1024),
        is_cloud_run=_IS_CLOUD_RUN,
        message=(
            "Cloud Run: max 32 MB. Dla większych plików uruchom aplikację lokalnie."
            if _IS_CLOUD_RUN else
            "Lokalny serwer: max 500 MB."
        ),
    )


@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload pliku i parsowanie."""
    if 'file' not in request.files:
        return jsonify(error="Nie przesłano pliku."), 400

    file = request.files['file']
    if not file.filename:
        return jsonify(error="Pusta nazwa pliku."), 400

    try:
        cleanup_temp_file()
        
        ext = Path(file.filename).suffix
        fd, temp_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        
        file.save(temp_path)
        _state['temp_file_path'] = temp_path
        
        doc = DocumentHandler(temp_path, file.filename)

        _state['document'] = doc
        _state['filename'] = file.filename
        _state['file_type'] = doc.file_type
        _state['total_pages'] = doc.get_page_count()
        _state['extracted_pages'] = [None] * doc.get_page_count()
        _state['meta_tags'] = {}
        _state['seo_articles'] = {}
        _state['project_name'] = sanitize_filename(
            Path(file.filename).stem
        )

        return jsonify(
            filename=file.filename,
            file_type=doc.file_type,
            total_pages=doc.get_page_count(),
            project_name=_state['project_name'],
        )

    except Exception as e:
        logger.error("Upload error: %s", e)
        return jsonify(error=str(e)), 400


@app.route('/open-path', methods=['POST'])
def open_path():
    """Otwiera plik bezpośrednio ze ścieżki lokalnej na serwerze.

    Nie wymaga uploadu HTTP — idealne dla dużych plików (100+ MB) na lokalnym deploy.
    Body JSON: { "path": "/ścieżka/do/pliku.pdf" }
    """
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify(error="Podaj pole 'path' w JSON."), 400

    file_path = data['path'].strip()

    # Podstawowe sprawdzenia bezpieczeństwa
    if not file_path:
        return jsonify(error="Ścieżka jest pusta."), 400

    path_obj = Path(file_path)

    if not path_obj.exists():
        return jsonify(error=f"Plik nie istnieje: {file_path}"), 404

    if not path_obj.is_file():
        return jsonify(error=f"Podana ścieżka nie jest plikiem: {file_path}"), 400

    # Obsługiwane rozszerzenia
    ext = path_obj.suffix.lower()
    if ext not in ('.pdf', '.docx', '.doc'):
        return jsonify(error=f"Nieobsługiwany format: {ext}. Obsługiwane: PDF, DOCX, DOC"), 400

    try:
        cleanup_temp_file()  # Wyczyść poprzedni temp jeśli był

        doc = DocumentHandler(str(path_obj), path_obj.name)

        _state['document'] = doc
        _state['filename'] = path_obj.name
        _state['file_type'] = doc.file_type
        _state['total_pages'] = doc.get_page_count()
        _state['extracted_pages'] = [None] * doc.get_page_count()
        _state['meta_tags'] = {}
        _state['seo_articles'] = {}
        _state['project_name'] = sanitize_filename(path_obj.stem)
        _state['temp_file_path'] = None  # plik lokalny — nie usuwamy

        file_size_mb = round(path_obj.stat().st_size / 1024 / 1024, 1)
        logger.info("Otwarto plik lokalny: %s (%.1f MB, %d stron)",
                    path_obj.name, file_size_mb, doc.get_page_count())

        return jsonify(
            filename=path_obj.name,
            file_type=doc.file_type,
            total_pages=doc.get_page_count(),
            project_name=_state['project_name'],
            file_size_mb=file_size_mb,
        )

    except Exception as e:
        logger.error("open-path error: %s", e)
        return jsonify(error=str(e)), 400


@app.route('/pick-file')
def pick_file():
    """Otwiera natywne okno wyboru pliku po stronie serwera (tylko lokalnie).

    Zwraca JSON:
    - { available: true, path: "/wybrana/ścieżka.pdf" } — plik wybrany
    - { available: true, path: null }                   — użytkownik anulował
    - { available: false, reason: "..." }               — tkinter niedostępny (Cloud Run)
    """
    if _IS_CLOUD_RUN:
        return jsonify(
            available=False,
            reason="Okno wyboru pliku działa tylko na lokalnym serwerze."
        )

    if not _TKINTER_AVAILABLE:
        return jsonify(
            available=False,
            reason="tkinter niedostępny w tym środowisku Python."
        )

    try:
        # tkinter wymaga głównego wątku na macOS — używamy osobnego miniwindow
        root = tk.Tk()
        root.withdraw()          # ukryj główne okno
        root.lift()              # wysuń na wierzch
        root.attributes("-topmost", True)  # zawsze na wierzchu
        root.update()

        path = tk_filedialog.askopenfilename(
            parent=root,
            title="Wybierz dokument",
            filetypes=[
                ("Dokumenty", "*.pdf *.docx *.doc"),
                ("PDF", "*.pdf"),
                ("Word", "*.docx *.doc"),
                ("Wszystkie pliki", "*.*"),
            ],
        )
        root.destroy()

        return jsonify(
            available=True,
            path=path if path else None,
        )

    except Exception as e:
        logger.error("pick-file error: %s", e)
        return jsonify(available=False, reason=str(e))


# ===== ROUTES: PAGE DATA =====

@app.route('/page/<int:page_num>/preview')
def page_preview(page_num):
    """Renderuje stronę PDF jako PNG."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    page_index = page_num - 1
    if page_index < 0 or page_index >= _state['total_pages']:
        return jsonify(error="Nieprawidłowy numer strony."), 400

    image_data = doc.render_page_as_image(page_index)
    if image_data:
        return send_file(
            io.BytesIO(image_data),
            mimetype='image/png',
            download_name=f'page_{page_num}.png',
        )
    else:
        return jsonify(error="Podgląd niedostępny dla tego formatu."), 404


@app.route('/page/<int:page_num>/preview/highres')
def page_preview_highres(page_num):
    """Renderuje stronę PDF jako PNG w wysokiej rozdzielczości (216 DPI)."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    page_index = page_num - 1
    if page_index < 0 or page_index >= _state['total_pages']:
        return jsonify(error="Nieprawidłowy numer strony."), 400

    if _state.get('file_type') != 'pdf':
        return jsonify(error="Eksport strony dostępny tylko dla PDF."), 400

    image_data = doc.render_page_highres(page_index)
    if image_data:
        project = _state.get('project_name', 'strona')
        return send_file(
            io.BytesIO(image_data),
            mimetype='image/png',
            as_attachment=True,
            download_name=f'{project}_str{page_num}_highres.png',
        )
    else:
        return jsonify(error="Nie udało się wyrenderować strony."), 500


@app.route('/page/<int:page_num>/text')
def page_text(page_num):
    """Zwraca surowy tekst strony."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    page_index = page_num - 1
    if page_index < 0 or page_index >= _state['total_pages']:
        return jsonify(error="Nieprawidłowy numer strony."), 400

    content = doc.get_page_content(page_index)
    return jsonify(
        page_number=page_num,
        text=content.text,
        image_count=len(content.images),
    )


@app.route('/page/<int:page_num>/complexity')
def page_complexity(page_num):
    """Analizuje złożoność strony i rekomenduje tryb przetwarzania (text/vision)."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    page_index = page_num - 1
    if page_index < 0 or page_index >= _state['total_pages']:
        return jsonify(error="Nieprawidłowy numer strony."), 400

    try:
        analysis = doc.analyze_page_complexity(page_index)
        analysis['page_number'] = page_num
        return jsonify(analysis)
    except Exception as e:
        logger.error("Błąd analizy strony %d: %s", page_num, e)
        return jsonify(error=str(e)), 500


@app.route('/page/<int:page_num>/result')
def page_result(page_num):
    """Zwraca wynik przetwarzania strony."""
    page_index = page_num - 1
    if page_index < 0 or page_index >= len(_state['extracted_pages']):
        return jsonify(error="Nieprawidłowy numer strony."), 400

    result = _state['extracted_pages'][page_index]
    if result is None:
        return jsonify(processed=False)

    response = {
        'processed': True,
        'type': result.get('type', 'nieznany'),
        'formatted_content': result.get('formatted_content', ''),
        'raw_markdown': result.get('raw_markdown', ''),
        'group_pages': result.get('group_pages', []),
        'is_group_lead': result.get('is_group_lead', True),
    }

    # Dołącz meta tagi jeśli istnieją
    if page_index in _state['meta_tags']:
        response['meta_tags'] = _state['meta_tags'][page_index]

    # Dołącz SEO jeśli istnieje
    if page_index in _state['seo_articles']:
        response['seo_article'] = _state['seo_articles'][page_index]

    return jsonify(response)


# ===== ROUTES: AI PROCESSING =====

@app.route('/process', methods=['POST'])
def process_pages():
    """Przetwarza strony przez Gemini."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    data = request.get_json()
    mode = data.get('mode', 'all')
    ai = _get_ai()

    try:
        if mode == 'article':
            groups_input = data.get('groups', '')
            groups = parse_page_groups(groups_input, _state['total_pages'])
            results = []

            for group in groups:
                pages_content = [
                    doc.get_page_content(p - 1) for p in group
                ]
                article_result = ai.process_article_group(pages_content)

                for page in group:
                    page_index = page - 1
                    entry = {
                        k: v for k, v in article_result.items()
                        if k != 'page_numbers'
                    }
                    entry['page_number'] = page
                    entry['group_pages'] = group
                    entry['is_group_lead'] = (page == group[0])
                    _state['extracted_pages'][page_index] = entry

                results.append({
                    'pages': group,
                    'type': article_result.get('type', 'nieznany'),
                })

            return jsonify(
                ok=True,
                mode='article',
                results=results,
                message=f"Przetworzono {len(groups)} artykuł(ów).",
            )
        else:
            # Dla trybów 'all' i 'range' przekieruj na SSE stream
            return jsonify(
                error="Użyj endpointu /process-stream dla tego trybu."
            ), 400

    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        logger.error("Processing error: %s", e)
        return jsonify(error=str(e)), 500


# Maksymalna liczba równoległych zapytań do AI
MAX_PARALLEL_WORKERS = 4


@app.route('/process-stream', methods=['POST'])
def process_stream():
    """Przetwarza strony równolegle i streamuje postęp przez SSE."""
    doc = _state.get('document')
    if not doc:
        return Response(
            f"data: {json.dumps({'error': 'Brak dokumentu.'})}\n\n",
            mimetype='text/event-stream',
        )

    data = request.get_json()
    mode = data.get('mode', 'all')
    smart_mode = data.get('smart', False)  # true = auto-wybór text/vision per strona

    if mode == 'all':
        start_idx = 0
        end_idx = _state['total_pages'] - 1
    elif mode == 'range':
        start_idx = data.get('start_page', 1) - 1
        end_idx = data.get('end_page', _state['total_pages']) - 1
    else:
        return Response(
            f"data: {json.dumps({'error': f'Nieznany tryb: {mode}'})}\n\n",
            mimetype='text/event-stream',
        )

    total = end_idx - start_idx + 1

    # Analiza złożoności stron (przed przetwarzaniem, operacje I/O — szybkie)
    page_contents = {}
    page_modes = {}  # page_idx -> 'text' lub 'vision'
    for i in range(start_idx, end_idx + 1):
        page_contents[i] = doc.get_page_content(i)
        if smart_mode and doc.file_type == 'pdf':
            complexity = doc.analyze_page_complexity(i)
            page_modes[i] = complexity['recommended_mode']
        else:
            page_modes[i] = 'text'

    def generate():
        lock = threading.Lock()
        completed = [0]

        def process_one(page_idx):
            """Przetwarza jedną stronę — wywoływane w wątku."""
            ai = _get_ai()  # Nowa instancja per wątek
            chosen_mode = page_modes.get(page_idx, 'text')

            if chosen_mode == 'vision' and doc.file_type == 'pdf':
                # Tryb wizualny: renderuj stronę i wyślij jako obraz do Gemini
                image_data = doc.render_page_as_image(page_idx)
                if image_data:
                    result = ai.process_page_vision(image_data)
                    result['page_number'] = page_idx + 1
                    result['processing_mode'] = 'vision'
                else:
                    # Fallback na tekst jeśli renderowanie się nie uda
                    content = page_contents[page_idx]
                    result = ai.process_page(content)
                    result['processing_mode'] = 'text_fallback'
            else:
                content = page_contents[page_idx]
                result = ai.process_page(content)
                result['processing_mode'] = 'text'

            with lock:
                _state['extracted_pages'][page_idx] = result
                completed[0] += 1
            return page_idx, result

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
            futures = {
                executor.submit(process_one, i): i
                for i in range(start_idx, end_idx + 1)
            }

            for future in as_completed(futures):
                page_idx = futures[future]
                try:
                    _, result = future.result()
                    event = {
                        'page_number': page_idx + 1,
                        'type': result.get('type', 'nieznany'),
                        'processing_mode': result.get('processing_mode', 'text'),
                        'completed': completed[0],
                        'total': total,
                        'progress': round(completed[0] / total * 100),
                    }
                except Exception as e:
                    logger.error("Błąd strony %d: %s", page_idx + 1, e)
                    with lock:
                        completed[0] += 1
                    event = {
                        'page_number': page_idx + 1,
                        'type': 'błąd',
                        'error': str(e),
                        'completed': completed[0],
                        'total': total,
                        'progress': round(completed[0] / total * 100),
                    }

                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # Końcowy event
        done_event = {
            'done': True,
            'message': f"Przetworzono {total} stron.",
            'completed': total,
            'total': total,
            'progress': 100,
        }
        yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@app.route('/process-page/<int:page_num>', methods=['POST'])
def process_single_page(page_num):
    """Przetwarza jedną stronę (reroll z kontekstem)."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    page_index = page_num - 1
    if page_index < 0 or page_index >= _state['total_pages']:
        return jsonify(error="Nieprawidłowy numer strony."), 400

    ai = _get_ai()

    # Zbierz kontekst
    prev_text = ""
    if page_index > 0:
        prev_text = doc.get_page_content(page_index - 1).text

    curr_text = doc.get_page_content(page_index).text

    next_text = ""
    if page_index < _state['total_pages'] - 1:
        next_text = doc.get_page_content(page_index + 1).text

    context_text = (
        f"KONTEKST (POPRZEDNIA STRONA):\n{prev_text}\n\n"
        f"--- STRONA DOCELOWA ---\n{curr_text}\n\n"
        f"KONTEKST (NASTĘPNA STRONA):\n{next_text}"
    )

    from document_handler import PageContent
    page_content = PageContent(page_num, context_text)
    result = ai.process_page(page_content)
    _state['extracted_pages'][page_index] = result

    return jsonify(
        ok=True,
        type=result.get('type', 'nieznany'),
        formatted_content=result.get('formatted_content', ''),
        raw_markdown=result.get('raw_markdown', ''),
    )


@app.route('/process-page-vision/<int:page_num>', methods=['POST'])
def process_page_vision(page_num):
    """Przetwarza stronę przez analizę wizualną (obraz → AI)."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    page_index = page_num - 1
    if page_index < 0 or page_index >= _state['total_pages']:
        return jsonify(error="Nieprawidłowy numer strony."), 400

    if doc.file_type != 'pdf':
        return jsonify(error="Analiza wizualna dostępna tylko dla PDF."), 400

    image_data = doc.render_page_as_image(page_index)
    if not image_data:
        return jsonify(error="Nie udało się wyrenderować strony."), 500

    ai = _get_ai()
    result = ai.process_page_vision(image_data)
    result['page_number'] = page_num
    _state['extracted_pages'][page_index] = result

    return jsonify(
        ok=True,
        type=result.get('type', 'nieznany'),
        formatted_content=result.get('formatted_content', ''),
        raw_markdown=result.get('raw_markdown', ''),
    )


@app.route('/meta/<int:page_num>', methods=['POST'])
def generate_meta(page_num):
    """Generuje meta tagi."""
    page_index = page_num - 1
    result = _state['extracted_pages'][page_index]

    if not result or 'raw_markdown' not in result:
        return jsonify(error="Brak artykułu do analizy."), 400

    ai = _get_ai()
    tags = ai.generate_meta_tags(result['raw_markdown'])
    _state['meta_tags'][page_index] = tags

    return jsonify(tags)


@app.route('/seo/<int:page_num>', methods=['POST'])
def generate_seo(page_num):
    """Generuje wersję SEO na podstawie wybranych stron źródłowych."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    data = request.get_json() or {}
    source_pages_input = data.get('source_pages', str(page_num))
    keywords = data.get('keywords', '')

    # Parsuj strony źródłowe
    try:
        source_groups = parse_page_groups(source_pages_input, _state['total_pages'])
        source_page_nums = []
        for group in source_groups:
            source_page_nums.extend(group)
        source_page_nums = sorted(set(source_page_nums))
    except ValueError as e:
        return jsonify(error=str(e)), 400

    # Zbierz tekst ze stron źródłowych
    # Preferuj przetworzony tekst (raw_markdown), ale fallback na surowy
    source_parts = []
    for pn in source_page_nums:
        pi = pn - 1
        result = _state['extracted_pages'][pi] if pi < len(_state['extracted_pages']) else None
        if result and 'raw_markdown' in result:
            source_parts.append(f"--- STRONA {pn} (przetworzona) ---\n{result['raw_markdown']}")
        else:
            content = doc.get_page_content(pi)
            source_parts.append(f"--- STRONA {pn} (surowa) ---\n{content.text}")

    source_text = "\n\n".join(source_parts)

    ai = _get_ai()
    seo = ai.generate_seo_article(source_text, keywords=keywords)
    page_index = page_num - 1
    _state['seo_articles'][page_index] = seo

    return jsonify(seo)


# ===== ROUTES: DOWNLOAD =====

@app.route('/page/<int:page_num>/images')
def page_images(page_num):
    """Eksportuje wszystkie grafiki ze strony PDF jako archiwum ZIP."""
    doc = _state.get('document')
    if not doc:
        return jsonify(error="Brak dokumentu."), 400

    if _state.get('file_type') != 'pdf':
        return jsonify(error="Eksport grafik dostępny tylko dla PDF."), 400

    page_index = page_num - 1
    if page_index < 0 or page_index >= _state['total_pages']:
        return jsonify(error="Nieprawidłowy numer strony."), 400

    try:
        content = doc.get_page_content(page_index)
        images = content.images

        if not images:
            return jsonify(error="Brak grafik na tej stronie."), 404

        zip_data = []
        for img in images:
            ext = img.get('ext', 'png')
            idx = img.get('index', 0)
            filename = f"strona_{page_num}_grafika_{idx + 1}.{ext}"
            zip_data.append({'name': filename, 'content': img['image']})

        zip_bytes = create_zip_archive(zip_data)
        project = _state.get('project_name', 'dokument')

        return send_file(
            io.BytesIO(zip_bytes),
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'{project}_str{page_num}_grafiki.zip',
        )

    except Exception as e:
        logger.error("Błąd eksportu grafik: %s", e)
        return jsonify(error=str(e)), 500


@app.route('/page/<int:page_num>/images/count')
def page_images_count(page_num):
    """Zwraca liczbę grafik na stronie PDF."""
    doc = _state.get('document')
    if not doc or _state.get('file_type') != 'pdf':
        return jsonify(count=0)

    page_index = page_num - 1
    if page_index < 0 or page_index >= _state['total_pages']:
        return jsonify(count=0)

    try:
        content = doc.get_page_content(page_index)
        count = len(content.images)
        return jsonify(count=count, page=page_num)
    except Exception:
        return jsonify(count=0)


@app.route('/download/articles')
def download_articles():
    """Pobiera all artykuły jako ZIP."""
    articles = [
        p for p in _state['extracted_pages']
        if p and p.get('type') == 'artykuł'
        and p.get('is_group_lead', True)
        and 'raw_markdown' in p
    ]

    if not articles:
        return jsonify(error="Brak artykułów do pobrania."), 404

    zip_data = [
        {
            'name': f"artykul_str_{a['page_number']}.txt",
            'content': a['raw_markdown'].encode('utf-8'),
        }
        for a in articles
    ]

    zip_bytes = create_zip_archive(zip_data)
    name = _state.get('project_name', 'artykuly')

    return send_file(
        io.BytesIO(zip_bytes),
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{name}_artykuly.zip',
    )


@app.route('/download/html/<int:page_num>')
def download_html(page_num):
    """Pobiera HTML artykułu."""
    page_index = page_num - 1
    result = _state['extracted_pages'][page_index]

    if not result or 'raw_markdown' not in result:
        return jsonify(error="Brak artykułu."), 404

    md = result['raw_markdown']
    html_content = markdown_to_clean_html(md)

    meta_title = None
    meta_desc = None
    if page_index in _state['meta_tags']:
        tags = _state['meta_tags'][page_index]
        if 'error' not in tags:
            meta_title = tags.get('meta_title')
            meta_desc = tags.get('meta_description')

    html_doc = generate_full_html_document(
        html_content,
        title=f"Artykuł ze strony {page_num}",
        meta_title=meta_title,
        meta_description=meta_desc,
    )

    return send_file(
        io.BytesIO(html_doc.encode('utf-8')),
        mimetype='text/html',
        as_attachment=True,
        download_name=f'artykul_str_{page_num}.html',
    )


@app.route('/download/seo/<int:page_num>')
def download_seo(page_num):
    """Pobiera wersję SEO artykułu."""
    page_index = page_num - 1
    seo = _state['seo_articles'].get(page_index)

    if not seo or 'error' in seo:
        return jsonify(error="Brak wersji SEO."), 404

    title = seo.get('seo_title', 'Artykuł SEO')
    content = f"# {title}\n\n{seo.get('seo_article_markdown', '')}"

    return send_file(
        io.BytesIO(content.encode('utf-8')),
        mimetype='text/plain',
        as_attachment=True,
        download_name=f'{sanitize_filename(title)}.txt',
    )


# ===== ROUTES: PROJECTS =====

@app.route('/projects')
def list_projects():
    """Lista projektów."""
    return jsonify(projects=get_existing_projects())


@app.route('/project/save', methods=['POST'])
def save_project_route():
    """Zapisuje projekt."""
    name = _state.get('project_name')
    if not name:
        return jsonify(error="Brak nazwy projektu."), 400

    state_to_save = {
        'filename': _state['filename'],
        'file_type': _state['file_type'],
        'total_pages': _state['total_pages'],
        'extracted_pages': [
            p for p in _state['extracted_pages'] if p is not None
        ],
        'meta_tags': {
            str(k): v for k, v in _state['meta_tags'].items()
        },
        'seo_articles': {
            str(k): v for k, v in _state['seo_articles'].items()
        },
        'project_name': name,
    }

    result = save_project(name, state_to_save)
    if 'error' in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route('/project/load/<name>', methods=['POST'])
def load_project_route(name):
    """Ładuje projekt."""
    data = load_project(name)
    if data is None:
        return jsonify(error=f"Projekt '{name}' nie istnieje."), 404

    _state['filename'] = data.get('filename')
    _state['file_type'] = data.get('file_type')
    _state['total_pages'] = data.get('total_pages', 0)
    _state['project_name'] = data.get('project_name', name)
    _state['meta_tags'] = {
        int(k): v for k, v in data.get('meta_tags', {}).items()
    }
    _state['seo_articles'] = {
        int(k): v for k, v in data.get('seo_articles', {}).items()
    }

    # Odtwórz extracted_pages
    _state['extracted_pages'] = [None] * _state['total_pages']
    for page_data in data.get('extracted_pages', []):
        pn = page_data.get('page_number')
        if pn and 1 <= pn <= _state['total_pages']:
            _state['extracted_pages'][pn - 1] = page_data

    return jsonify(
        ok=True,
        project_name=_state['project_name'],
        filename=_state['filename'],
        total_pages=_state['total_pages'],
        needs_file=True,
        message=f"Załadowano projekt '{name}'. Wgraj powiązany plik.",
    )


# ===== ROUTES: STATUS =====

@app.route('/status')
def status():
    """Status aktualnego dokumentu."""
    return jsonify(
        has_document=_state['document'] is not None,
        filename=_state.get('filename'),
        file_type=_state.get('file_type'),
        total_pages=_state.get('total_pages', 0),
        project_name=_state.get('project_name'),
        processed_count=sum(
            1 for p in _state['extracted_pages'] if p is not None
        ),
    )


# ===== MAIN =====

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
