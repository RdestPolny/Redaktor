"""
Redaktor AI — Project Manager
Zapis i odczyt projektów na dysku.
"""

import json
import streamlit as st
from pathlib import Path
from typing import List

PROJECTS_DIR = Path("pdf_processor_projects")


def ensure_projects_dir() -> bool:
    """Tworzy katalog projektów jeśli nie istnieje."""
    try:
        PROJECTS_DIR.mkdir(exist_ok=True)
        return True
    except Exception as e:
        st.error(f"Nie można utworzyć katalogu projektów: {e}")
        return False


def get_existing_projects() -> List[str]:
    """Zwraca listę istniejących projektów."""
    if not ensure_projects_dir():
        return []
    return [d.name for d in PROJECTS_DIR.iterdir() if d.is_dir()]


def save_project():
    """Zapisuje projekt do pliku."""
    if not st.session_state.project_name or not ensure_projects_dir():
        st.error("Nie można zapisać projektu: brak nazwy projektu.")
        return

    project_path = PROJECTS_DIR / st.session_state.project_name
    project_path.mkdir(exist_ok=True)

    state_to_save = {
        k: v for k, v in st.session_state.items()
        if k not in ['document', 'project_loaded_and_waiting_for_file']
    }
    state_to_save['extracted_pages'] = [
        p for p in st.session_state.extracted_pages if p is not None
    ]

    try:
        with open(project_path / "project_state.json", "w", encoding="utf-8") as f:
            json.dump(state_to_save, f, indent=2, ensure_ascii=False)
        st.toast(
            f"✅ Projekt '{st.session_state.project_name}' został zapisany!",
            icon="💾"
        )
    except Exception as e:
        st.error(f"Błąd podczas zapisywania projektu: {e}")


def load_project(project_name: str):
    """Ładuje projekt z pliku."""
    project_file = PROJECTS_DIR / project_name / "project_state.json"

    if not project_file.exists():
        st.error(f"Plik projektu '{project_name}' nie istnieje.")
        return

    try:
        with open(project_file, "r", encoding="utf-8") as f:
            state_to_load = json.load(f)

        for key, value in state_to_load.items():
            if key != 'document':
                st.session_state[key] = value

        total_pages = st.session_state.get('total_pages', 0)
        st.session_state.extracted_pages = [None] * total_pages

        for page_data in state_to_load.get('extracted_pages', []):
            page_num_one_based = page_data.get('page_number')
            if page_num_one_based and 1 <= page_num_one_based <= total_pages:
                st.session_state.extracted_pages[page_num_one_based - 1] = page_data

        st.session_state.document = None
        st.session_state.project_loaded_and_waiting_for_file = True

        st.success(
            f"✅ Załadowano projekt '{project_name}'. "
            "Wgraj powiązany plik, aby kontynuować."
        )
    except Exception as e:
        st.error(f"Błąd podczas ładowania projektu: {e}")
