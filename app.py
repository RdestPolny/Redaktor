"""
Redaktor AI — Flask Application
Interaktywny procesor dokumentów z Gemini 3 Flash Preview.
"""

import os
import io
import base64
import json
import logging
from pathlib import Path

from flask import (
    Flask, request, jsonify, render_template,
    send_file, session,
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
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB

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
}


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

@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload pliku i parsowanie."""
    if 'file' not in request.files:
        return jsonify(error="Nie przesłano pliku."), 400

    file = request.files['file']
    if not file.filename:
        return jsonify(error="Pusta nazwa pliku."), 400

    try:
        file_bytes = file.read()
        doc = DocumentHandler(file_bytes, file.filename)

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
        if mode == 'all':
            start_idx = 0
            end_idx = _state['total_pages'] - 1
        elif mode == 'range':
            start_idx = data.get('start_page', 1) - 1
            end_idx = data.get('end_page', _state['total_pages']) - 1
        elif mode == 'article':
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
            return jsonify(error=f"Nieznany tryb: {mode}"), 400

        # Przetwarzanie strona po stronie (all / range)
        processed = []
        for i in range(start_idx, end_idx + 1):
            content = doc.get_page_content(i)
            result = ai.process_page(content)
            _state['extracted_pages'][i] = result
            processed.append({
                'page_number': i + 1,
                'type': result.get('type', 'nieznany'),
            })

        return jsonify(
            ok=True,
            mode=mode,
            processed=processed,
            message=f"Przetworzono {len(processed)} stron.",
        )

    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        logger.error("Processing error: %s", e)
        return jsonify(error=str(e)), 500


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
    """Generuje wersję SEO."""
    page_index = page_num - 1
    result = _state['extracted_pages'][page_index]

    if not result or 'raw_markdown' not in result:
        return jsonify(error="Brak artykułu do analizy."), 400

    ai = _get_ai()
    seo = ai.generate_seo_article(result['raw_markdown'])
    _state['seo_articles'][page_index] = seo

    return jsonify(seo)


# ===== ROUTES: DOWNLOAD =====

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
