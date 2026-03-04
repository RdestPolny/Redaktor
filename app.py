"""
Redaktor AI — Streamlit App
Tryby: Bieżąca strona / Zakres stron / Cały dokument / Artykuł SEO / Grafiki
"""

import io
import os
import logging
from pathlib import Path


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

tab_biezaca, tab_zakres, tab_caly, tab_seo, tab_grafiki = st.tabs([
    "📄 Bieżąca strona",
    "📋 Zakres stron",
    "📚 Cały dokument",
    "🔍 Artykuł SEO",
    "🖼️ Grafiki",
])

# ══════════════════════════════════════════════════════════════
# TAB 1 — BIEŻĄCA STRONA
# ══════════════════════════════════════════════════════════════

with tab_biezaca:
    col_pdf, col_txt = st.columns([1, 1], gap="large")

    with col_pdf:
        st.subheader(f"Podgląd — strona {current_page}")
        img = doc.render_page_as_image(current_page - 1)
        if img:
            st.image(img, use_container_width=True)
        else:
            st.info("Podgląd niedostępny.")

    with col_txt:
        st.subheader(f"Treść strony {current_page}")
        pc = doc.extract_page_content(current_page - 1)

        if not pc.text.strip():
            st.warning("Brak tekstu na tej stronie.")
        elif current_page in st.session_state.transcriptions:
            st.success("✅ Przetworzona przez AI")
            edited = st.text_area(
                "Treść po redakcji:",
                value=st.session_state.transcriptions[current_page],
                height=500,
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
            st.text_area("Surowy tekst:", value=pc.text, height=400,
                         disabled=True, key=f"raw_{current_page}")

            if st.button("🤖 Redaguj AI", type="primary",
                         use_container_width=True, key=f"btn_{current_page}"):
                with st.spinner("Redaguję…"):
                    try:
                        result = AIProcessor.redakcja().edit_page_text(pc.text)
                        st.session_state.transcriptions[current_page] = result
                        st.rerun()
                    except Exception as e:
                        st.error(f"Błąd AI: {e}")

    # Eksport wszystkich zredagowanych
    done_count = len(st.session_state.transcriptions)
    if done_count:
        st.divider()
        with st.expander(f"📥 Eksport wszystkich zredagowanych stron ({done_count})", expanded=False):
            _all = "\n\n".join(
                f"{'='*60}\nSTRONA {pg}\n{'='*60}\n\n{txt}"
                for pg, txt in sorted(st.session_state.transcriptions.items())
            )
            st.download_button(
                f"⬇️ Pobierz {done_count} stron (TXT)",
                data=_all.encode("utf-8"),
                file_name=f"{Path(doc.filename).stem}_redakcja.txt",
                mime="text/plain",
                use_container_width=True,
            )

# ══════════════════════════════════════════════════════════════
# TAB 2 — ZAKRES STRON
# ══════════════════════════════════════════════════════════════

with tab_zakres:
    st.subheader("📋 Redakcja zakresu stron")
    st.caption(
        f"Każda strona jest redagowana osobno — model: `{MODEL_REDAKCJA}`. "
        "Wyniki trafiają do tej samej puli co strony z zakładki Bieżąca."
    )

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        z_from = st.number_input("Od strony:", 1, total_pages, 1)
    with c2:
        z_to = st.number_input("Do strony:", 1, total_pages, min(total_pages, 10))
    with c3:
        st.write("")  # padding

    if z_from > z_to:
        st.error("'Od' musi być ≤ 'Do'.")
    else:
        n_pages = z_to - z_from + 1
        already_done = sum(1 for p in range(z_from, z_to + 1)
                           if p in st.session_state.transcriptions)
        st.caption(
            f"Zakres: **{n_pages}** stron ({z_from}–{z_to}). "
            f"Już zredagowane: {already_done}/{n_pages}."
        )

        btn_label = f"🤖 Redaguj strony {z_from}–{z_to}"
        if st.button(btn_label, type="primary"):
            progress = st.progress(0, text="Przygotowanie…")
            errors = []
            ai = AIProcessor.redakcja()

            for i, page_num in enumerate(range(z_from, z_to + 1)):
                progress.progress(
                    i / n_pages,
                    text=f"Redaguję stronę {page_num} ({i+1}/{n_pages})…"
                )
                if page_num in st.session_state.transcriptions:
                    continue  # pomijaj już zredagowane
                pc = doc.extract_page_content(page_num - 1)
                if not pc.text.strip():
                    continue
                try:
                    result = ai.edit_page_text(pc.text)
                    st.session_state.transcriptions[page_num] = result
                except Exception as e:
                    errors.append(f"Strona {page_num}: {e}")

            progress.progress(1.0, text="✅ Gotowe!")
            if errors:
                st.warning("Błędy:\n" + "\n".join(errors))
            else:
                st.success(f"✅ Zredagowano {n_pages} stron!")
            st.rerun()

        # Podgląd wyników dla zakresu
        range_done = {p: st.session_state.transcriptions[p]
                      for p in range(z_from, z_to + 1)
                      if p in st.session_state.transcriptions}
        if range_done:
            st.divider()
            st.caption(f"Zredagowane strony w zakresie: {len(range_done)}/{n_pages}")
            all_text = "\n\n".join(
                f"{'='*60}\nSTRONA {pg}\n{'='*60}\n\n{txt}"
                for pg, txt in sorted(range_done.items())
            )
            st.download_button(
                f"⬇️ Pobierz zakres {z_from}–{z_to} ({len(range_done)} stron, TXT)",
                data=all_text.encode("utf-8"),
                file_name=f"{Path(doc.filename).stem}_str{z_from}-{z_to}.txt",
                mime="text/plain",
            )
            with st.expander("Podgląd zredagowanych stron", expanded=False):
                for pg, txt in sorted(range_done.items()):
                    st.markdown(f"**Strona {pg}**")
                    st.text(txt[:500] + ("…" if len(txt) > 500 else ""))
                    st.divider()

# ══════════════════════════════════════════════════════════════
# TAB 3 — CAŁY DOKUMENT
# ══════════════════════════════════════════════════════════════

with tab_caly:
    st.subheader("📚 Redakcja całego dokumentu")
    st.caption(
        f"Każda strona redagowana osobno — model: `{MODEL_REDAKCJA}`. "
        f"Łącznie **{total_pages}** stron. Każda strona = osobne zapytanie do AI."
    )

    done_total = len(st.session_state.transcriptions)
    remaining = total_pages - done_total
    st.caption(f"Zredagowane: **{done_total}** / pominięte lub pozostałe: **{remaining}**")

    col_btn, col_skip = st.columns([2, 1])
    with col_btn:
        if st.button("🚀 Redaguj cały dokument", type="primary",
                     disabled=remaining == 0):
            progress = st.progress(0, text="Przygotowanie…")
            errors = []
            ai = AIProcessor.redakcja()

            for i in range(total_pages):
                page_num = i + 1
                progress.progress(
                    i / total_pages,
                    text=f"Redaguję stronę {page_num}/{total_pages}…"
                )
                if page_num in st.session_state.transcriptions:
                    continue  # pomijaj już zredagowane
                pc = doc.extract_page_content(i)
                if not pc.text.strip():
                    continue
                try:
                    result = ai.edit_page_text(pc.text)
                    st.session_state.transcriptions[page_num] = result
                except Exception as e:
                    errors.append(f"Strona {page_num}: {e}")

            progress.progress(1.0, text="✅ Gotowe!")
            if errors:
                st.warning("Błędy na stronach:\n" + "\n".join(errors))
            st.success(f"✅ Dokument zredagowany!")
            st.rerun()

    with col_skip:
        if st.button("🗑️ Wyczyść wszystkie", type="secondary",
                     disabled=done_total == 0):
            st.session_state.transcriptions = {}
            st.rerun()

    # Eksport całości
    if st.session_state.transcriptions:
        st.divider()
        all_text = "\n\n".join(
            f"{'='*60}\nSTRONA {pg}\n{'='*60}\n\n{txt}"
            for pg, txt in sorted(st.session_state.transcriptions.items())
        )
        st.download_button(
            f"⬇️ Pobierz cały dokument ({done_total} stron, TXT)",
            data=all_text.encode("utf-8"),
            file_name=f"{Path(doc.filename).stem}_cały.txt",
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
