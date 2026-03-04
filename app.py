"""
Redaktor AI — Streamlit App
Tryby: Bieżąca strona / Zakres stron / Cały dokument / Artykuł SEO / Grafiki
"""

import io
import os
import logging
from pathlib import Path


import concurrent.futures
import streamlit as st

from document_handler import DocumentHandler, DOCX_AVAILABLE, MAMMOTH_AVAILABLE
from ai_processor import AIProcessor, MODEL_REDAKCJA, MODEL_ARTYKUL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== KONFIGURACJA STRONY =====

st.set_page_config(
    page_title="Redaktor AI",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ===== STYLE =====

st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stSidebar"] { background: #0e0e1a; border-right: 1px solid #1e1e2e; }
.stTabs [data-baseweb="tab"] { font-size: 0.9rem; font-weight: 600; }
.block-container { padding-top: 1.5rem; }

/* Karty modeli */
.model-badge {
    display: inline-block;
    background: #1e1e2e;
    border: 1px solid #2d2d3f;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 0.75rem;
    color: #a78bfa;
    font-family: monospace;
}

/* Karty na ekranie powitalnym */
.welcome-card {
    background: #1e1e2e;
    border: 1px solid #2d2d3f;
    border-radius: 12px;
    padding: 1.5rem 1.8rem;
    min-width: 180px;
    max-width: 220px;
    transition: all 0.3s ease;
    cursor: default;
}
.welcome-card:hover {
    transform: translateY(-5px);
    border-color: #7c3aed;
    box-shadow: 0 10px 20px rgba(0,0,0,0.3);
}
</style>
""", unsafe_allow_html=True)

# ===== SESSION STATE =====

def _init():
    defaults = {
        "doc": None,
        "filename": None,
        "file_id": None,
        "total_pages": 0,
        "current_page": 1,
        "transcriptions": {},   # {page_num: edited_text}
        "processing": False,
        "seo_result": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

# ===== HELPERS =====

def _load_document(uploaded_file) -> "DocumentHandler | None":
    try:
        return DocumentHandler(uploaded_file, uploaded_file.name)
    except Exception as e:
        st.error(f"Błąd wczytywania: {e}")
        return None

def _redact_page(page_num: int):
    """Worker function for parallel redaction."""
    try:
        doc = st.session_state.doc
        pc = doc.extract_page_content(page_num - 1)
        if not pc.text.strip():
            return page_num, ""
        
        ai = AIProcessor.redakcja()
        result = ai.edit_page_text(pc.text)
        return page_num, result
    except Exception as e:
        logger.error(f"Error redacting page {page_num}: {e}")
        return page_num, None

# ===== SIDEBAR =====

with st.sidebar:
    st.markdown("### 📄 Redaktor AI")
    st.caption("Ekstrakcja i redakcja treści z dokumentów PDF")
    st.divider()

    # Upload
    accept = ["pdf"]
    if DOCX_AVAILABLE:
        accept.append("docx")
    if MAMMOTH_AVAILABLE:
        accept.append("doc")

    uploaded = st.file_uploader(
        "Wgraj dokument",
        type=accept,
        help="PDF, DOCX, DOC. Max 500 MB.",
    )

    if uploaded is not None:
        file_id = f"{uploaded.name}_{uploaded.size}"
        if file_id != st.session_state.file_id:
            with st.spinner("Wczytywanie…"):
                doc = _load_document(uploaded)
            if doc:
                st.session_state.doc = doc
                st.session_state.filename = uploaded.name
                st.session_state.file_id = file_id
                st.session_state.total_pages = doc.get_page_count()
                st.session_state.current_page = 1
                st.session_state.transcriptions = {}
                st.session_state.seo_result = None
                st.rerun()

    # Nawigacja (tylko gdy dokument załadowany)
    if st.session_state.doc:
        st.divider()
        total = st.session_state.total_pages
        cur = st.session_state.current_page

        st.caption(f"Strona **{cur}** / **{total}**")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("⬅️", use_container_width=True, disabled=cur <= 1):
                st.session_state.current_page -= 1
                st.rerun()
        with c2:
            if st.button("➡️", use_container_width=True, disabled=cur >= total):
                st.session_state.current_page += 1
                st.rerun()

        pg = st.number_input("Idź do strony:", 1, total, cur, label_visibility="collapsed")
        if pg != cur:
            st.session_state.current_page = pg
            st.rerun()

        # Podsumowanie
        st.divider()
        done = len(st.session_state.transcriptions)
        if done:
            st.caption(f"✅ Zredagowane strony: **{done}**")

        # Info o modelach
        st.markdown(
            f"**Redakcja:** `{MODEL_REDAKCJA}`\n\n"
            f"**Artykuł:** `{MODEL_ARTYKUL}`"
        )
        
        st.divider()
        if st.button("🚀 Redaguj wszystko (Równolegle)", type="primary", use_container_width=True, disabled=st.session_state.processing):
            pages_to_do = [p for p in range(1, total + 1) if p not in st.session_state.transcriptions]
            if pages_to_do:
                st.session_state.processing = True
                progress_bar = st.progress(0, text="Uruchamiam przetwarzanie równoległe...")
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    future_to_page = {executor.submit(_redact_page, p): p for p in pages_to_do}
                    done_count = 0
                    for future in concurrent.futures.as_completed(future_to_page):
                        page_num, result = future.result()
                        if result is not None:
                            st.session_state.transcriptions[page_num] = result
                        done_count += 1
                        progress_bar.progress(done_count / len(pages_to_do), text=f"Postęp: {done_count}/{len(pages_to_do)} stron")
                
                st.session_state.processing = False
                st.success("Przetwarzanie zakończone!")
                st.rerun()

# ===== WELCOME SCREEN =====

if not st.session_state.doc:
    welcome_html = f"""
<div style="text-align:center;padding:4rem 2rem">
<div style="font-size:4rem;margin-bottom:1rem">📄</div>
<h1 style="color:#c4b5fd;margin-bottom:.5rem">Redaktor AI</h1>
<p style="color:#64748b;font-size:1.1rem;margin-bottom:2.5rem">
Wgraj dokument PDF lub Word w panelu po lewej stronie
</p>
<div style="display:flex;gap:1.2rem;justify-content:center;flex-wrap:wrap">
""" + "".join(f"""
<div class="welcome-card">
<div style="font-size:2rem">{icon}</div>
<h3 style="color:#a78bfa;margin:.5rem 0 .3rem">{name}</h3>
<p style="color:#94a3b8;font-size:.85rem;margin:0">{desc}</p>
</div>
""" for icon, name, desc in [
    ("📄", "Bieżąca strona", "Redakcja jednej strony + podgląd PDF"),
    ("📋", "Zakres stron", "Redakcja zakresu strona-po-stronie"),
    ("📚", "Cały dokument", "Cały dokument, każda strona osobno"),
    ("🔍", "Artykuł SEO", "Nowy artykuł z kontekstem wielu stron"),
    ("🖼️", "Grafiki", "Wyodrębnianie zdjęć i grafik"),
]) + """
</div>
</div>
"""
    st.markdown(welcome_html, unsafe_allow_html=True)
    st.stop()

# ===== GŁÓWNY INTERFEJS =====

doc: DocumentHandler = st.session_state.doc
current_page: int = st.session_state.current_page
total_pages: int = st.session_state.total_pages

# ══════════════════════════════════════════════════════════════
# GŁÓWNY INTERFEJS — SIDE BY SIDE
# ══════════════════════════════════════════════════════════════

st.subheader(f"Strona {current_page} z {total_pages}")

col_orig, col_redacted = st.columns(2, gap="large")

with col_orig:
    st.markdown("### 📄 Oryginał (PDF)")
    img = doc.render_page_as_image(current_page - 1)
    if img:
        st.image(img, use_container_width=True)
    else:
        st.info("Podgląd niedostępny.")

with col_redacted:
    st.markdown("### 🤖 Redakcja AI")
    
    if current_page in st.session_state.transcriptions:
        edited = st.text_area(
            "Edytowany tekst:",
            value=st.session_state.transcriptions[current_page],
            height=600,
            key=f"edit_{current_page}",
        )
        st.session_state.transcriptions[current_page] = edited
        
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "⬇️ Pobierz TXT",
                data=edited.encode("utf-8"),
                file_name=f"{Path(doc.filename).stem}_str{current_page}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with c2:
            if st.button("🔄 Cofnij", use_container_width=True):
                del st.session_state.transcriptions[current_page]
                st.rerun()
    else:
        pc = doc.extract_page_content(current_page - 1)
        if not pc.text.strip():
            st.warning("Brak tekstu na tej stronie.")
        else:
            st.text_area("Surowy tekst:", value=pc.text, height=400, disabled=True)
            if st.button("🤖 Redaguj tę stronę", type="primary", use_container_width=True):
                with st.spinner("Przetwarzanie..."):
                    _, result = _redact_page(current_page)
                    if result:
                        st.session_state.transcriptions[current_page] = result
                        st.rerun()

st.divider()

# TABS for other tools
tab_seo, tab_grafiki, tab_batch = st.tabs([
    "🔍 Artykuł SEO",
    "🖼️ Grafiki",
    "📋 Narzędzia zbiorcze"
])

with tab_batch:
    st.subheader("📋 Narzędzia zbiorcze")
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🚀 Redaguj Brakujące Strony", use_container_width=True):
            # To samo co w sidebarze ale tutaj widoczne
            pages_to_do = [p for p in range(1, total_pages + 1) if p not in st.session_state.transcriptions]
            if pages_to_do:
                with st.status("Przetwarzanie równoległe...") as status:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                        future_to_page = {executor.submit(_redact_page, p): p for p in pages_to_do}
                        for future in concurrent.futures.as_completed(future_to_page):
                            p_num, res = future.result()
                            if res:
                                st.session_state.transcriptions[p_num] = res
                            st.write(f"Zakończono stronę {p_num}")
                    status.update(label="Gotowe!", state="complete")
                    st.rerun()
    
    with c2:
        if st.button("🗑️ Wyczyść wszystko", type="secondary", use_container_width=True):
            st.session_state.transcriptions = {}
            st.rerun()

    # Eksport całości
    done_count = len(st.session_state.transcriptions)
    if done_count:
        st.divider()
        _all = "\n\n".join(
            f"{'='*60}\nSTRONA {pg}\n{'='*60}\n\n{txt}"
            for pg, txt in sorted(st.session_state.transcriptions.items())
        )
        st.download_button(
            f"⬇️ Pobierz wszystkie zredagowane strony ({done_count})",
            data=_all.encode("utf-8"),
            file_name=f"{Path(doc.filename).stem}_redakcja_calosc.txt",
            mime="text/plain",
            use_container_width=True,
        )


# ══════════════════════════════════════════════════════════════
# TAB 4 — ARTYKUŁ SEO
# ══════════════════════════════════════════════════════════════

with tab_seo:
    st.subheader("🔍 Generator artykułu SEO")
    st.caption(
        f"Wybrane strony są przekazywane do AI **jako całość w jednym zapytaniu** — "
        f"model: `{MODEL_ARTYKUL}`.\n\n"
        "AI ma pełny kontekst wszystkich stron naraz, co pozwala na spójny artykuł "
        "z zachowaniem logicznego przepływu treści."
    )

    col_range, col_params = st.columns([1, 1], gap="large")

    with col_range:
        st.markdown("**Zakres stron źródłowych:**")
        s_from = st.number_input("Od strony:", 1, total_pages, 1, key="seo_from")
        s_to = st.number_input("Do strony:", 1, total_pages,
                               min(total_pages, 10), key="seo_to")
        if s_from <= s_to:
            n = s_to - s_from + 1
            st.caption(f"Wybrано **{n}** stron ({s_from}–{s_to})")
        else:
            st.error("'Od' musi być ≤ 'Do'.")

    with col_params:
        st.markdown("**Parametry artykułu:**")
        keywords = st.text_input(
            "🔑 Słowa kluczowe *",
            placeholder="np. wyposażenie kuchni hotelu, garnki indukcyjne",
        )
        audience = st.text_input(
            "👥 Grupa docelowa",
            placeholder="np. szefowie kuchni, właściciele restauracji",
        )
        topic_hint = st.text_input(
            "💡 Temat / kąt (opcjonalnie)",
            placeholder="Zostaw puste — AI wybierze",
        )

    can_gen = s_from <= s_to and bool(keywords.strip())
    if not can_gen:
        st.info("Podaj słowa kluczowe i poprawny zakres stron.")

    if st.button("🚀 Generuj artykuł SEO", type="primary",
                 disabled=not can_gen):
        with st.spinner(f"Pobieram treść {s_to - s_from + 1} stron i generuję artykuł…"):
            try:
                texts = []
                for pg in range(s_from, s_to + 1):
                    pc = doc.extract_page_content(pg - 1)
                    if pc.text.strip():
                        texts.append(f"[Strona {pg}]\n{pc.text}")

                if not texts:
                    st.error("Wybrane strony nie zawierają tekstu.")
                else:
                    result = AIProcessor.artykul().generate_seo_article(
                        texts,
                        keywords=keywords,
                        audience=audience,
                        topic_hint=topic_hint,
                    )
                    st.session_state.seo_result = result
            except Exception as e:
                st.error(f"Błąd AI: {e}")

    # Wynik
    if st.session_state.seo_result:
        r = st.session_state.seo_result
        st.divider()

        col_meta, col_dl = st.columns([3, 1])
        with col_meta:
            if r.get("title"):
                st.markdown(f"### {r['title']}")
                tc = len(r["title"])
                st.caption(f"Title: {tc} znaków {'✅' if tc <= 60 else '⚠️ za długi'}")
            if r.get("meta_description"):
                st.info(f"**Meta:** {r['meta_description']}")
                mc = len(r["meta_description"])
                st.caption(f"Meta description: {mc} znaków {'✅' if mc <= 160 else '⚠️'}")

        with col_dl:
            full = (f"# {r.get('title', '')}\n\n"
                    f"> {r.get('meta_description', '')}\n\n"
                    f"{r.get('article', '')}")
            st.download_button("⬇️ Markdown", full.encode(), "artykul.md", "text/markdown",
                               use_container_width=True)
            st.download_button("⬇️ TXT", full.encode(), "artykul.txt", "text/plain",
                               use_container_width=True)

        st.markdown("---")
        st.markdown(r.get("article", ""), unsafe_allow_html=True)

        if st.button("🗑️ Wyczyść wynik"):
            st.session_state.seo_result = None
            st.rerun()

# ══════════════════════════════════════════════════════════════
# TAB 5 — GRAFIKI
# ══════════════════════════════════════════════════════════════

with tab_grafiki:
    st.subheader(f"🖼️ Grafiki ze strony {current_page}")
    st.caption("Nawiguj stronami w panelu bocznym aby zobaczyć grafiki z innych stron.")

    if doc.file_type != "pdf":
        st.info("Ekstrakcja grafik dostępna tylko dla plików PDF.")
    else:
        images = doc.extract_page_images(current_page - 1)
        if not images:
            st.info("Brak grafik na tej stronie (lub są za małe — min. 80×80 px).")
        else:
            st.success(f"Znaleziono **{len(images)}** grafik")
            cols = st.columns(min(3, len(images)), gap="medium")
            for i, img in enumerate(images):
                with cols[i % 3]:
                    ext = img.get("ext", "png")
                    st.image(img["bytes"],
                             caption=f"Grafika {i+1} · {img['width']}×{img['height']} px",
                             use_container_width=True)
                    st.download_button(
                        "⬇️ Pobierz",
                        data=img["bytes"],
                        file_name=f"grafika_str{current_page}_{i+1}.{ext}",
                        mime=f"image/{ext}",
                        key=f"dl_{current_page}_{i}",
                        use_container_width=True,
                    )

        # Miniatura strony
        with st.expander("📄 Podgląd strony", expanded=False):
            img_bytes = doc.render_page_as_image(current_page - 1)
            if img_bytes:
                st.image(img_bytes, use_container_width=True)
