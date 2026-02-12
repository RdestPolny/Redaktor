"""
Redaktor AI — Utilities
Konwersja markdown→HTML, sanityzacja, archiwizacja, parsowanie zakresów stron.
"""

import io
import re
import zipfile
from typing import List, Dict, Optional


def markdown_to_html(text: str) -> str:
    """Konwertuje markdown na HTML z obsługą list i indeksów."""
    text = text.replace('\n---\n', '\n<hr>\n')
    text = re.sub(r'^\s*# (.*?)\s*$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*## (.*?)\s*$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*### (.*?)\s*$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)

    lines = text.split('\n')
    new_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(('- ', '* ')):
            if not in_list:
                new_lines.append("<ul>")
                in_list = True
            content = stripped[2:]
            new_lines.append(f"<li>{content}</li>")
        else:
            if in_list:
                new_lines.append("</ul>")
                in_list = False
            new_lines.append(line)

    if in_list:
        new_lines.append("</ul>")

    text = '\n'.join(new_lines)

    paragraphs = text.split('\n\n')
    html_content = []

    for para in paragraphs:
        stripped_para = para.strip()
        if not stripped_para:
            continue
        if stripped_para.startswith(('<h', '<hr', '<ul', '<li')):
            html_content.append(stripped_para)
        else:
            formatted_para = stripped_para.replace(chr(10), '<br>')
            html_content.append(f"<p>{formatted_para}</p>")

    return ''.join(html_content)


def markdown_to_clean_html(markdown_text: str, page_number: int = None) -> str:
    """Konwertuje markdown na czysty HTML bez stylowania."""
    html = markdown_text

    html = html.replace('\n---\n', '\n<hr>\n')
    html = html.replace('\n--- \n', '\n<hr>\n')

    html = re.sub(r'^\s*# (.*?)\s*$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    html = re.sub(r'^\s*## (.*?)\s*$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^\s*### (.*?)\s*$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^\s*#### (.*?)\s*$', r'<h4>\1</h4>', html, flags=re.MULTILINE)
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html)

    lines = html.split('\n')
    new_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(('- ', '* ')):
            if not in_list:
                new_lines.append("<ul>")
                in_list = True
            content = stripped[2:]
            new_lines.append(f"<li>{content}</li>")
        else:
            if in_list:
                new_lines.append("</ul>")
                in_list = False
            new_lines.append(line)

    if in_list:
        new_lines.append("</ul>")

    html = '\n'.join(new_lines)

    paragraphs = html.split('\n\n')
    formatted_paragraphs = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if para.startswith(('<h1', '<h2', '<h3', '<h4', '<hr', '<p', '<ul')):
            formatted_paragraphs.append(para)
        else:
            para_with_breaks = para.replace('\n', '<br>\n')
            formatted_paragraphs.append(f'<p>{para_with_breaks}</p>')

    return '\n'.join(formatted_paragraphs)


def generate_full_html_document(
    content: str,
    title: str = "Artykuł",
    meta_title: str = None,
    meta_description: str = None
) -> str:
    """Generuje pełny dokument HTML z czystą strukturą (bez CSS)."""
    meta_tags = ""
    if meta_title:
        meta_tags += f'    <meta name="title" content="{meta_title}">\n'
    if meta_description:
        meta_tags += f'    <meta name="description" content="{meta_description}">\n'

    html_doc = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
{meta_tags}</head>
<body>
{content}
</body>
</html>"""

    return html_doc


def sanitize_filename(name: str) -> str:
    """Sanityzuje nazwę pliku."""
    if not name:
        return "unnamed_project"
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", str(name))
    return re.sub(r'_{2,}', "_", sanitized).strip("_") or "unnamed_project"


def create_zip_archive(data: List[Dict]) -> bytes:
    """Tworzy archiwum ZIP."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in data:
            zf.writestr(item['name'], item['content'])
    return zip_buffer.getvalue()


def parse_page_groups(input_text: str, total_pages: int) -> List[List[int]]:
    """Parsuje zakresy stron z tekstu wejściowego."""
    if not input_text:
        raise ValueError("Nie podano zakresów stron.")

    groups = []
    used_pages = set()

    for line in re.split(r'[;\n]+', input_text):
        line = line.strip()
        if not line:
            continue

        pages = []
        for part in re.split(r'[;,]+', line):
            part = part.strip()
            if not part:
                continue

            if '-' in part:
                start_str, end_str = part.split('-', 1)
                if not start_str.isdigit() or not end_str.isdigit():
                    raise ValueError(f"Niepoprawny zakres stron: '{part}'.")
                start, end = int(start_str), int(end_str)
                if start > end:
                    raise ValueError(f"Zakres stron musi być rosnący: '{part}'.")
                if start < 1 or end > total_pages:
                    raise ValueError(
                        f"Zakres '{part}' wykracza poza liczbę stron dokumentu."
                    )
                pages.extend(range(start, end + 1))
            else:
                if not part.isdigit():
                    raise ValueError(f"Niepoprawny numer strony: '{part}'.")
                page = int(part)
                if page < 1 or page > total_pages:
                    raise ValueError(f"Strona '{page}' wykracza poza dokument.")
                pages.append(page)

        if not pages:
            continue

        pages = sorted(dict.fromkeys(pages))

        if any(p in used_pages for p in pages):
            raise ValueError(
                f"Strony {pages} zostały już przypisane do innego artykułu."
            )

        used_pages.update(pages)
        groups.append(pages)

    if not groups:
        raise ValueError("Nie znaleziono żadnych poprawnych zakresów stron.")

    return groups
