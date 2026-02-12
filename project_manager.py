"""
Redaktor AI — Project Manager
Zapis i odczyt projektów na dysku.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path("pdf_processor_projects")


def ensure_projects_dir() -> bool:
    """Tworzy katalog projektów jeśli nie istnieje."""
    try:
        PROJECTS_DIR.mkdir(exist_ok=True)
        return True
    except Exception as e:
        logger.error("Nie można utworzyć katalogu projektów: %s", e)
        return False


def get_existing_projects() -> List[str]:
    """Zwraca listę istniejących projektów."""
    if not ensure_projects_dir():
        return []
    return [d.name for d in PROJECTS_DIR.iterdir() if d.is_dir()]


def save_project(project_name: str, state: Dict) -> Dict:
    """Zapisuje projekt do pliku. Zwraca status."""
    if not project_name or not ensure_projects_dir():
        return {"error": "Brak nazwy projektu."}

    project_path = PROJECTS_DIR / project_name
    project_path.mkdir(exist_ok=True)

    try:
        with open(project_path / "project_state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return {"ok": True, "message": f"Projekt '{project_name}' zapisany."}
    except Exception as e:
        logger.error("Błąd zapisu projektu: %s", e)
        return {"error": str(e)}


def load_project(project_name: str) -> Optional[Dict]:
    """Ładuje projekt z pliku. Zwraca dane lub None."""
    project_file = PROJECTS_DIR / project_name / "project_state.json"

    if not project_file.exists():
        return None

    try:
        with open(project_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Błąd ładowania projektu: %s", e)
        return None
