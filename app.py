"""
Redaktor AI — Streamlit App
Trzy tryby: Transkrypcja / SEO / Grafiki
"""

import io
import os
import tempfile
import logging
from pathlib import Path

import streamlit as st

from document_handler import DocumentHandler, DOCX_AVAILABLE, MAMMOTH_AVAILABLE
from ai_processor import AIProcessor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== KONFIGURACJA STRONY =====

st.set_page_config(
    page_title="Redaktor AI",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": "Redaktor AI — ekstrakcja i redakcja treści z dokumentów PDF",
    },
)

# ===== STYLE =====

st.markdown("""
<style>
/* Ukryj branding Streamlit */
#MainMenu, footer, header { visibility: hidden; }

/* Nagłówek aplikacji */
.app-header {
    background: linear-gradient(135deg, #1a0533 0%, #0f1629 100%);
    border-bottom: 1px solid #2d1b69;
    padding: 1rem 1.5rem;
    margin: -1rem -1rem 1.5rem -1rem;
    display: flex;
    align-items: center;
    gap: 12px;
}
.app-header h1 { color: #c4b5fd; font-size: 1.4rem; margin: 0; }
.app-header .badge {
    background: #2d1b69;
    color: #a78bfa;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.75rem;
    border: 1px solid #7c3aed40;
}

/* Karty z wynikami */
.result-card {
    background: #1e1e2e;
    border: 1px solid #2d2d3f;
    border-radius: 12px;
    padding: 1.5rem;
    margin-top: 1rem;
}

/* Etykiety statusu */
.status-ok { color: #4ade80; font-weight: 600; }
.status-warn { color: #fbbf24; font-weight: 600; }

/* Tabs */
.stTabs [data-baseweb="tab"] { font-size: 0.95rem; font-weight: 600; padding: 0.5rem 1.2rem; }

/* Przyciski download */
.stDownloadButton > button { width: 100%; margin-top: 4px; }

/* Podgląd PDF */
.pdf-frame img {
    border-radius: 8px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    width: 100%;
}

/* Sidebar */
[data-testid="stSidebar"] { background: #0f0f1a; border-right: 1px solid #1e1e2e; }
[data-testid="stSidebar"] .stMarkdown h3 { color: #a78bfa; }
</style>
""", unsafe_allow_html=True)

# ===== SESSION STATE =====

def _init():
    defaults = {
        "doc": None,           # DocumentHandler instance
        "filename": None,
        "file_id": None,       # hash do wykrywania nowego pliku
        "total_pages": 0,
        "current_page": 1,
        "transcriptions": {},  # {page_num: edited_text}
        "seo_result": None,
        "temp_path": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

# ===== HELPERY =====

def _load_document(uploaded_file):
    """Wczytuje dokument z UploadedFile Streamlit."""
    # Zapisz do tymczasowego pliku (DocumentHandler potrzebuje bytes)
    suffix = Path(uploaded_file.name).suffix
    try:
        doc = DocumentHandler(uploaded_file, uploaded_file.name)
        return doc
    except Exception as e:
        st.error(f"Błąd wczytywania: {e}")
        return None

def _reset_doc(uploaded_file, doc):
    """Zapisz nowy dokument do session_state."""
    st.session_state.doc = doc
    st.session_state.filename = uploaded_file.name
    st.session_state.file_id = f"{uploaded_file.name}_{uploaded_file.size}"
    st.session_state.total_pages = doc.get_page_count()
    st.session_state.current_page = 1
    st.session_state.transcriptions = {}
    st.session_state.seo_result = None

# ===== SIDEBAR =====

with st.sidebar:
    st.markdown("### 📄 Redaktor AI")
    st.caption("Ekstrakcja i redakcja treści z dokumentów")

    st.divider()

    # ---- Upload ----
    accept = [".pdf"]
    if DOCX_AVAILABLE:
        accept.append(".docx")
    if MAMMOTH_AVAILABLE:
        accept.append(".doc")

    uploaded = st.file_uploader(
        "Wgraj dokument",
        type=[a.lstrip(".") for a in accept],
        help="PDF (bez limitu rozmiaru na lokalnym serwerze)",
    )

    if uploaded is not None:
        file_id = f"{uploaded.name}_{uploaded.size}"
        if file_id != st.session_state.file_id:
            with st.spinner("Wczytywanie…"):
                doc = _load_document(uploaded)
            if doc:
                _reset_doc(uploaded, doc)
                st.success(f"✅ {uploaded.name}")

    # ---- Nawigacja ----
    if st.session_state.doc:
        st.divider()
        st.markdown("### Nawigacja")

        total = st.session_state.total_pages
        current = st.session_state.current_page

        st.caption(f"Strona **{current}** z **{total}**")

        col_p, col_n = st.columns(2)
        with col_p:
            if st.button("⬅️ Poprz.", use_container_width=True, disabled=current <= 1):
                st.session_state.current_page -= 1
                st.rerun()
        with col_n:
            if st.button("Nast. ➡️", use_container_width=True, disabled=current >= total):
                st.session_state.current_page += 1
                st.rerun()

        new_page = st.number_input(
            "Przejdź do strony:",
            min_value=1,
            max_value=total,
            value=current,
            step=1,
            label_visibility="collapsed",
        )
        if new_page != current:
            st.session_state.current_page = new_page
            st.rerun()

        st.divider()
        # Info o dokumencie
        doc = st.session_state.doc
        pc = doc.extract_page_content(current - 1)
        st.caption(
            f"📊 Kolumny: **{pc.estimated_columns}** · "
            f"Grafiki: **{'tak' if pc.has_images else 'nie'}**"
        )
        if pc.is_mostly_image:
            st.warning("⚠️ Strona głównie graficzna — mało tekstu")

# ===== MAIN CONTENT =====

if not st.session_state.doc:
    # ---- Ekran powitalny ----
    st.markdown("""
    <div style="text-align:center; padding: 4rem 2rem;">
        <div style="font-size:4rem; margin-bottom:1rem;">📄</div>
        <h1 style="color:#c4b5fd; margin-bottom:0.5rem;">Redaktor AI</h1>
        <p style="color:#64748b; font-size:1.1rem; margin-bottom:2rem;">
            Wgraj dokument PDF lub Word w panelu po lewej stronie
        </p>
        <div style="display:flex; gap:1rem; justify-content:center; flex-wrap:wrap;">
            <div style="background:#1e1e2e; border:1px solid #2d2d3f; border-radius:12px; padding:1.5rem 2rem; min-width:200px;">
                <div style="font-size:2rem">📝</div>
                <h3 style="color:#a78bfa">Transkrypcja</h3>
                <p style="color:#94a3b8; font-size:0.9rem">Ekstrakcja tekstu z lekką redakcją AI</p>
            </div>
            <div style="background:#1e1e2e; border:1px solid #2d2d3f; border-radius:12px; padding:1.5rem 2rem; min-width:200px;">
                <div style="font-size:2rem">🔍</div>
                <h3 style="color:#a78bfa">SEO</h3>
                <p style="color:#94a3b8; font-size:0.9rem">Artykuł SEO na bazie wybranych stron</p>
            </div>
            <div style="background:#1e1e2e; border:1px solid #2d2d3f; border-radius:12px; padding:1.5rem 2rem; min-width:200px;">
                <div style="font-size:2rem">🖼️</div>
                <h3 style="color:#a78bfa">Grafiki</h3>
                <p style="color:#94a3b8; font-size:0.9rem">Wyodrębnione zdjęcia i grafiki z PDF</p>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ===== DOKUMENT ZAŁADOWANY =====

doc: DocumentHandler = st.session_state.doc
current_page: int = st.session_state.current_page
ai = AIProcessor()

tab_trans, tab_seo, tab_grafiki = st.tabs(["📝 Transkrypcja", "🔍 Generator SEO", "🖼️ Grafiki"])

# ══════════════════════════════════════════════════════════════
# TAB 1 — TRANSKRYPCJA
# ══════════════════════════════════════════════════════════════

with tab_trans:
    col_pdf, col_text = st.columns([1, 1], gap="large")

    # ---- Podgląd PDF ----
    with col_pdf:
        st.subheader(f"Podgląd — strona {current_page}")
        img_bytes = doc.render_page_as_image(current_page - 1)
        if img_bytes:
            st.image(img_bytes, use_container_width=True)
        else:
            st.info("Podgląd niedostępny dla tego formatu.")

    # ---- Tekst ----
    with col_text:
        st.subheader(f"Treść strony {current_page}")
        page_content = doc.extract_page_content(current_page - 1)
        raw_text = page_content.text

        if not raw_text.strip():
            st.warning("Brak tekstu do wyciągnięcia z tej strony (może być graficzna).")
        else:
            # Sprawdź czy strona była już przetworzona przez AI
            if current_page in st.session_state.transcriptions:
                st.success("✅ Strona zredagowana przez AI")
                edited = st.text_area(
                    "Treść po redakcji AI:",
                    value=st.session_state.transcriptions[current_page],
                    height=520,
                    key=f"edited_{current_page}",
                )
                st.session_state.transcriptions[current_page] = edited

                col_dl, col_reset = st.columns(2)
                with col_dl:
                    st.download_button(
                        "⬇️ Pobierz TXT",
                        data=edited.encode("utf-8"),
                        file_name=f"{Path(doc.filename).stem}_str{current_page}.txt",
                        mime="text/plain",
                        use_container_width=True,
                    )
                with col_reset:
                    if st.button("🔄 Cofnij redakcję", use_container_width=True):
                        del st.session_state.transcriptions[current_page]
                        st.rerun()

            else:
                st.text_area(
                    "Surowy tekst (wyekstrahowany z PDF):",
                    value=raw_text,
                    height=400,
                    disabled=True,
                    key=f"raw_{current_page}",
                )

                if st.button(
                    "🤖 Redaguj AI (lekka korekta)",
                    key=f"btn_edit_{current_page}",
                    type="primary",
                    use_container_width=True,
                    help="Poprawi formatowanie, akapity i literówki. Nie zmienia treści.",
                ):
                    with st.spinner("AI redaguje tekst…"):
                        try:
                            edited = ai.edit_page_text(raw_text)
                            st.session_state.transcriptions[current_page] = edited
                            st.rerun()
                        except Exception as e:
                            st.error(f"Błąd AI: {e}")

    # ---- Batch: przetworzono wszystkie strony? ----
    done_count = len(st.session_state.transcriptions)
    if done_count > 0:
        st.divider()
        with st.expander(f"📥 Eksport — przetworzone strony ({done_count})", expanded=False):
            all_text = ""
            for pg in sorted(st.session_state.transcriptions):
                all_text += f"\n\n{'='*60}\nSTRONA {pg}\n{'='*60}\n\n"
                all_text += st.session_state.transcriptions[pg]

            st.download_button(
                f"⬇️ Pobierz wszystkie {done_count} stron (TXT)",
                data=all_text.encode("utf-8"),
                file_name=f"{Path(doc.filename).stem}_wszystkie.txt",
                mime="text/plain",
                use_container_width=True,
            )

# ══════════════════════════════════════════════════════════════
# TAB 2 — GENERATOR SEO
# ══════════════════════════════════════════════════════════════

with tab_seo:
    st.subheader("🔍 Generator artykułu SEO")
    st.caption(
        "Wybierz strony źródłowe (tekst z nich posłuży jako baza merytoryczna), "
        "podaj słowa kluczowe i AI napisze nowy, zoptymalizowany artykuł SEO."
    )

    # ---- Konfiguracja ----
    col_pages, col_params = st.columns([1, 1], gap="large")

    with col_pages:
        st.markdown("**Źródłowe strony z PDF:**")
        selected_pages = st.multiselect(
            "Strony",
            options=list(range(1, st.session_state.total_pages + 1)),
            default=[current_page],
            format_func=lambda x: f"Strona {x}",
            label_visibility="collapsed",
        )
        if selected_pages:
            st.caption(f"Wybrано {len(selected_pages)} stron")

    with col_params:
        keywords = st.text_input(
            "🔑 Główne słowa kluczowe *",
            placeholder="np. wyposażenie kuchni hotelu, garnki indukcyjne profesjonalne",
            help="Główna fraza i synonimy — AI wplecie je naturalnie",
        )
        audience = st.text_input(
            "👥 Grupa docelowa",
            placeholder="np. szefowie kuchni, właściciele restauracji",
        )
        topic_hint = st.text_input(
            "💡 Sugerowany temat / kąt (opcjonalnie)",
            placeholder="Zostaw puste — AI wybierze najlepszy kąt SEO",
        )

    # ---- Generuj ----
    can_generate = bool(selected_pages and keywords.strip())
    if not can_generate:
        st.info("👆 Wybierz co najmniej jedną stronę i podaj słowa kluczowe.")

    if st.button(
        "🚀 Generuj artykuł SEO",
        type="primary",
        disabled=not can_generate,
        use_container_width=False,
    ):
        with st.spinner("Generuję artykuł… (może potrwać do minuty)"):
            try:
                source_texts = []
                for pg in selected_pages:
                    pc = doc.extract_page_content(pg - 1)
                    if pc.text.strip():
                        source_texts.append(f"[Strona {pg}]\n{pc.text}")

                if not source_texts:
                    st.error("Wybrane strony nie zawierają tekstu.")
                else:
                    result = ai.generate_seo_article(
                        source_texts,
                        keywords=keywords,
                        audience=audience,
                        topic_hint=topic_hint,
                    )
                    st.session_state.seo_result = result
            except Exception as e:
                st.error(f"Błąd AI: {e}")

    # ---- Wynik SEO ----
    if st.session_state.seo_result:
        result = st.session_state.seo_result
        st.divider()

        col_meta, col_dl = st.columns([3, 1])
        with col_meta:
            if result.get("title"):
                st.markdown(f"### {result['title']}")
                char_count = len(result['title'])
                color = "green" if char_count <= 60 else "orange"
                st.caption(f"Title: :{color}[{char_count} znaków] {'✅' if char_count <= 60 else '⚠️ za długi'}")

            if result.get("meta_description"):
                st.info(f"**Meta:** {result['meta_description']}")
                mdc = len(result["meta_description"])
                st.caption(f"Meta description: {mdc} znaków {'✅' if mdc <= 160 else '⚠️'}")

        with col_dl:
            article_md = result.get("article", "")
            full_export = f"# {result.get('title', '')}\n\n> {result.get('meta_description', '')}\n\n{article_md}"
            st.download_button(
                "⬇️ Pobierz Markdown",
                data=full_export.encode("utf-8"),
                file_name=f"seo_artykul.md",
                mime="text/markdown",
                use_container_width=True,
            )
            st.download_button(
                "⬇️ Pobierz TXT",
                data=full_export.encode("utf-8"),
                file_name=f"seo_artykul.txt",
                mime="text/plain",
                use_container_width=True,
            )

        st.markdown("---")
        st.markdown(result.get("article", ""), unsafe_allow_html=True)

        if st.button("🗑️ Wyczyść wynik", use_container_width=False):
            st.session_state.seo_result = None
            st.rerun()

# ══════════════════════════════════════════════════════════════
# TAB 3 — GRAFIKI
# ══════════════════════════════════════════════════════════════

with tab_grafiki:
    st.subheader(f"🖼️ Grafiki ze strony {current_page}")

    if doc.file_type != "pdf":
        st.info("Ekstrakcja grafik dostępna tylko dla plików PDF.")
    else:
        images = doc.extract_page_images(current_page - 1)

        if not images:
            st.info(
                "Brak grafik na tej stronie (lub są zbyt małe — poniżej 80×80 px).\n\n"
                "Spróbuj innej strony za pomocą nawigacji w panelu bocznym."
            )
        else:
            st.success(f"Znaleziono **{len(images)}** grafik na stronie {current_page}")

            # Siatka 3 kolumn
            n_cols = min(3, len(images))
            cols = st.columns(n_cols, gap="medium")

            for i, img in enumerate(images):
                with cols[i % n_cols]:
                    ext = img.get("ext", "png")
                    fname = f"grafika_str{current_page}_{i+1}.{ext}"

                    st.image(
                        img["bytes"],
                        caption=f"Grafika {i+1} · {img['width']}×{img['height']} px",
                        use_container_width=True,
                    )
                    st.download_button(
                        f"⬇️ Pobierz",
                        data=img["bytes"],
                        file_name=fname,
                        mime=f"image/{ext}",
                        key=f"dl_img_{current_page}_{i}",
                        use_container_width=True,
                    )

        # Pokaż podgląd strony miniaturowo poniżej
        st.divider()
        with st.expander("📄 Podgląd strony", expanded=False):
            img_bytes = doc.render_page_as_image(current_page - 1)
            if img_bytes:
                st.image(img_bytes, use_container_width=True)
