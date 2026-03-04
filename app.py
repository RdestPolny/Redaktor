"""
Redaktor AI — Streamlit App
Tryby: Bieżąca strona / Zakres stron / Cały dokument / Artykuł SEO / Grafiki
"""

import io
import os
import re
import logging
from pathlib import Path


import concurrent.futures
import streamlit as st

from document_handler import DocumentHandler, DOCX_AVAILABLE, MAMMOTH_AVAILABLE
from ai_processor import AIProcessor, MODEL_REDAKCJA, MODEL_ARTYKUL, MODEL_SONAR, query_perplexity_sonar

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
        # SEO pipeline state
        "seo_result": None,
        "seo_analysis": None,      # Etap 1: wynik analizy Gemini
        "seo_research": None,      # Etap 2: wynik researchu Perplexity
        "seo_source_texts": None,  # teksty stron użyte w pipeline
        "seo_page_range": "",      # zakres stron np. '10-15'
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "use_perplexity" not in st.session_state:
        st.session_state.use_perplexity = True

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

    st.divider()
    with st.expander("⚙️ Ustawienia i Modele", expanded=False):
        st.markdown(f"**Redakcja:** `{MODEL_REDAKCJA}`")
        st.markdown(f"**Artykuł:** `{MODEL_ARTYKUL}`")
        st.markdown(f"**Research:** `{MODEL_SONAR}`")
        st.divider()
        st.session_state.use_perplexity = st.toggle(
            "Używaj Perplexity Research",
            value=st.session_state.use_perplexity,
            help="Wyłączenie spowoduje pominięcie Etapu 2 (researchu merytorycznego) w pipeline SEO."
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

        # Podsumowanie
        done = len(st.session_state.transcriptions)
        if done:
            st.caption(f"✅ Zredagowane strony: **{done}** / {st.session_state.total_pages}")

        st.divider()
        if st.button("🚀 Redaguj wszystko (Równolegle)", type="primary", use_container_width=True, disabled=st.session_state.processing):
            pages_to_do = [p for p in range(1, st.session_state.total_pages + 1) if p not in st.session_state.transcriptions]
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

# ══════════════════════════════════════════════════════════════
# GŁÓWNY INTERFEJS — NAWIGACJA
# ══════════════════════════════════════════════════════════════

nav1, nav2, nav3 = st.columns([2, 1, 2])
with nav1:
    st.subheader(f"Strona {current_page} z {total_pages}")
with nav2:
    cc1, cc2 = st.columns(2)
    with cc1:
        if st.button("⬅️", use_container_width=True, disabled=current_page <= 1, key="nav_prev"):
            st.session_state.current_page -= 1
            st.rerun()
    with cc2:
        if st.button("➡️", use_container_width=True, disabled=current_page >= total_pages, key="nav_next"):
            st.session_state.current_page += 1
            st.rerun()
with nav3:
    pg = st.number_input("Idź do strony:", 1, total_pages, current_page, key="nav_input", label_visibility="collapsed")
    if pg != current_page:
        st.session_state.current_page = pg
        st.rerun()

st.divider()

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
        edited = st.session_state.transcriptions[current_page]
        
        # Podgląd HTML
        with st.expander("👁️ Podgląd HTML", expanded=True):
            st.markdown(f'<div style="background: white; color: black; padding: 20px; border-radius: 8px; max-height: 600px; overflow-y: auto;">{edited}</div>', unsafe_allow_html=True)
        
        # Edycja Raw HTML
        edited_new = st.text_area(
            "Kod HTML:",
            value=edited,
            height=400,
            key=f"edit_{current_page}",
        )
        st.session_state.transcriptions[current_page] = edited_new
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button(
                "⬇️ Pobierz HTML",
                data=edited_new.encode("utf-8"),
                file_name=f"{Path(doc.filename).stem}_str{current_page}.html",
                mime="text/html",
                use_container_width=True,
            )
        with c2:
            st.download_button(
                "⬇️ Pobierz TXT",
                data=edited_new.encode("utf-8"),
                file_name=f"{Path(doc.filename).stem}_str{current_page}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with c3:
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
        _all = "<html><head><meta charset='utf-8'><style>body{font-family:sans-serif;line-height:1.6;max-width:800px;margin:40px auto;padding:20px;} .page-divider{border-top:2px solid #eee;margin:40px 0;padding-top:20px;color:#888;font-size:0.8em;text-transform:uppercase;}</style></head><body>"
        for pg, txt in sorted(st.session_state.transcriptions.items()):
            _all += f"<div class='page-divider'>Strona {pg}</div>\n{txt}\n"
        _all += "</body></html>"
        st.download_button(
            f"⬇️ Pobierz wszystkie zredagowane strony ({done_count}) - Kod HTML",
            data=_all.encode("utf-8"),
            file_name=f"{Path(doc.filename).stem}_redakcja_calosc.html",
            mime="text/html",
            use_container_width=True,
        )


# ══════════════════════════════════════════════════════════════
# TAB 4 — ARTYKUŁ SEO (3-etapowy pipeline)
# ══════════════════════════════════════════════════════════════

with tab_seo:
    st.subheader("🔍 Generator artykułu SEO")
    st.markdown(
        f"""
        <div style="background:#1a1a2e;border:1px solid #2d2d4f;border-radius:10px;padding:1rem 1.2rem;margin-bottom:1rem">
        <strong style="color:#a78bfa">Pipeline 3-etapowy:</strong><br>
        <span style="color:#94a3b8;font-size:0.85rem">
        🧠 <b>Etap 1</b> — <code>{MODEL_ARTYKUL}</code> analizuje tekst i dobiera słowo kluczowe SEO + temat<br>
        🔎 <b>Etap 2</b> — <code>{MODEL_SONAR}</code> (Perplexity) zbiera research merytoryczny<br>
        ✍️ <b>Etap 3</b> — <code>{MODEL_ARTYKUL}</code> pisze artykuł (zasada odwróconej piramidy)
        </span>
        </div>
        """,
        unsafe_allow_html=True
    )

    # ── INPUT: zakres stron ──────────────────────────────────────
    def _parse_page_range(text: str, total: int):
        """Parsuje '10-15' → (10, 15) lub None jeśli błąd."""
        text = text.strip()
        if not text:
            return None
        # Akceptuj formaty: '10-15', '10–15', '10 - 15'
        m = re.match(r'^(\d+)\s*[-–]\s*(\d+)$', text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 1 <= a <= b <= total:
                return a, b
        # Pojedyncza strona
        m2 = re.match(r'^(\d+)$', text)
        if m2:
            a = int(m2.group(1))
            if 1 <= a <= total:
                return a, a
        return None

    import re as _re_seo  # noqa: F811 (lokalnie, re już zaimportowane wyżej)

    col_input, col_hint = st.columns([1, 2], gap="large")
    with col_input:
        st.markdown("**📄 Zakres stron źródłowych:**")
        range_input = st.text_input(
            "Zakres stron",
            value=st.session_state.seo_page_range,
            placeholder=f"np. 10-15  (dokument ma {total_pages} stron)",
            key="seo_range_input",
            label_visibility="collapsed",
        )
        st.caption(f"Wpisz zakres stron, np. `5-12` lub pojedynczą stronę `7`. Max: {total_pages}.")

    parsed_range = _parse_page_range(range_input, total_pages)

    with col_hint:
        if parsed_range:
            s_from, s_to = parsed_range
            n_pages = s_to - s_from + 1
            st.success(f"✅ Zakres poprawny: strony **{s_from}–{s_to}** ({n_pages} {'strona' if n_pages == 1 else 'stron'})")
        elif range_input.strip():
            st.error(f"❌ Niepoprawny zakres. Podaj np. `10-15` (max {total_pages}).")
        else:
            st.info("⬅️ Podaj zakres stron aby rozpocząć.")

    can_run = parsed_range is not None

    st.divider()

    # ── PRZYCISKI ETAPÓW ────────────────────────────────────────
    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([2, 2, 2, 1])

    with btn_col1:
        run_step1 = st.button(
            "▶ Etap 1: Analiza SEO",
            type="primary" if (can_run and not st.session_state.seo_analysis) else "secondary",
            disabled=not can_run,
            use_container_width=True,
            key="seo_btn_step1",
        )
    with btn_col2:
        run_step2 = st.button(
            "▶ Etap 2: Research",
            type="primary" if st.session_state.seo_analysis and not st.session_state.seo_research else "secondary",
            disabled=not bool(st.session_state.seo_analysis),
            use_container_width=True,
            key="seo_btn_step2",
        )
    with btn_col3:
        run_step3 = st.button(
            "▶ Etap 3: Pisz artykuł",
            type="primary" if (st.session_state.seo_analysis and st.session_state.seo_research and not st.session_state.seo_result) else "secondary",
            disabled=not (st.session_state.seo_analysis and st.session_state.seo_research),
            use_container_width=True,
            key="seo_btn_step3",
        )
    with btn_col4:
        if st.button("🗑️ Reset", use_container_width=True, key="seo_btn_reset"):
            st.session_state.seo_analysis = None
            st.session_state.seo_research = None
            st.session_state.seo_result = None
            st.session_state.seo_source_texts = None
            st.session_state.seo_page_range = ""
            st.rerun()

    # ── ETAP 1: ANALIZA SEO ─────────────────────────────────────
    if run_step1 and parsed_range:
        s_from, s_to = parsed_range
        st.session_state.seo_page_range = range_input.strip()
        with st.spinner(f"🧠 Etap 1: Gemini analizuje strony {s_from}–{s_to} pod kątem SEO…"):
            try:
                texts = []
                for pg in range(s_from, s_to + 1):
                    pc = doc.extract_page_content(pg - 1)
                    if pc.text.strip():
                        texts.append(f"[Strona {pg}]\n{pc.text}")

                if not texts:
                    st.error("Wybrane strony nie zawierają tekstu.")
                else:
                    analysis = AIProcessor.artykul().analyze_for_seo(texts)
                    st.session_state.seo_analysis = analysis
                    st.session_state.seo_source_texts = texts
                    st.session_state.seo_research = None
                    st.session_state.seo_result = None
                    st.rerun()
            except Exception as e:
                st.error(f"Błąd Etapu 1 (Gemini): {e}")

    # ── ETAP 2: PERPLEXITY SONAR RESEARCH ───────────────────────
    if run_step2 and st.session_state.seo_analysis:
        if not st.session_state.use_perplexity:
            st.session_state.seo_research = "Research Perplexity został wyłączony w ustawieniach. Artykuł zostanie napisany wyłącznie na podstawie materiałów źródłowych."
            st.rerun()
            
        with st.spinner("🔎 Etap 2: Perplexity Sonar zbiera research merytoryczny…"):
            try:
                research = query_perplexity_sonar(st.session_state.seo_analysis)
                st.session_state.seo_research = research
                st.session_state.seo_result = None
                st.rerun()
            except Exception as e:
                st.error(f"Błąd Etapu 2 (Perplexity Sonar): {e}")

    # ── ETAP 3: GEMINI PISZE ARTYKUŁ ────────────────────────────
    if run_step3 and st.session_state.seo_analysis and st.session_state.seo_research:
        with st.spinner("✍️ Etap 3: Gemini pisze artykuł SEO (zasada odwróconej piramidy)…"):
            try:
                result = AIProcessor.artykul().generate_article_from_research(
                    seo_analysis=st.session_state.seo_analysis,
                    research_content=st.session_state.seo_research,
                    source_texts=st.session_state.seo_source_texts or [],
                )
                st.session_state.seo_result = result
                st.rerun()
            except Exception as e:
                st.error(f"Błąd Etapu 3 (Gemini): {e}")

    # ── WYNIKI ETAPU 1 ───────────────────────────────────────────
    if st.session_state.seo_analysis:
        a = st.session_state.seo_analysis
        with st.expander("✅ Etap 1 — Wynik analizy SEO (Gemini)", expanded=not bool(st.session_state.seo_result)):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**🔑 Główne słowo kluczowe:**")
                st.code(a.get('keyword', '—'))
                st.markdown(f"**📋 Temat artykułu:**")
                st.info(a.get('topic', '—'))
                st.markdown(f"**👥 Grupa docelowa:**")
                st.write(a.get('audience', '—'))
            with c2:
                st.markdown(f"**🔗 Frazy wspierające:**")
                for kw in a.get('secondary_keywords', []):
                    st.markdown(f"- `{kw}`")
                st.markdown(f"**📐 Kąt narracyjny:**")
                st.write(a.get('angle', '—'))
                st.markdown(f"**📝 Kontekst źródłowy:**")
                st.caption(a.get('context_summary', '—'))

    # ── WYNIKI ETAPU 2 ───────────────────────────────────────────
    if st.session_state.seo_research:
        with st.expander("✅ Etap 2 — Research (Perplexity Sonar)", expanded=not bool(st.session_state.seo_result)):
            st.markdown(st.session_state.seo_research)

    # ── WYNIKI ETAPU 3 — ARTYKUŁ ─────────────────────────────────
    if st.session_state.seo_result:
        r = st.session_state.seo_result
        st.divider()
        st.markdown("### ✍️ Etap 3 — Gotowy artykuł SEO")

        col_meta, col_dl = st.columns([3, 1])
        with col_meta:
            if r.get("title"):
                st.markdown(f"#### {r['title']}")
                tc = len(r["title"])
                st.caption(f"Title tag: {tc} znaków {'✅' if tc <= 60 else '⚠️ za długi (max 60)'}")
            if r.get("meta_description"):
                st.info(f"**Meta description:** {r['meta_description']}")
                mc = len(r["meta_description"])
                st.caption(f"Meta description: {mc} znaków {'✅' if mc <= 160 else '⚠️ za długi (max 160)'}")

        with col_dl:
            full_html = (
                f"<h1>{r.get('title', '')}</h1>\n\n"
                f"<!-- Meta description: {r.get('meta_description', '')} -->\n\n"
                f"{r.get('article', '')}"
            )
            st.download_button("⬇️ HTML", full_html.encode(), "artykul.html", "text/html",
                               use_container_width=True)
            st.download_button("⬇️ TXT", full_html.encode(), "artykul.txt", "text/plain",
                               use_container_width=True)

        st.markdown("---")
        st.markdown(
            f'<div style="background:white;color:black;padding:24px;border-radius:8px;'
            f'max-height:900px;overflow-y:auto;line-height:1.7">'
            f'{r.get("article", "")}'
            f'</div>',
            unsafe_allow_html=True
        )

        with st.expander("📄 Kod HTML (do skopiowania)", expanded=False):
            st.code(r.get("article", ""), language="html")

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
