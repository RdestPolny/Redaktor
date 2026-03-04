# gunicorn.conf.py — konfiguracja Gunicorn dla Redaktor AI
# Używany automatycznie gdy gunicorn jest uruchomiony z katalogu /app

import os

# Port z env (Cloud Run ustawia PORT=8080)
port = os.environ.get("PORT", "8080")
bind = f"0.0.0.0:{port}"

# Single-user app — 1 worker wystarcza, szybszy start (krytyczne dla Cloud Run health check)
workers = 1

# Timeout requestu: 5 minut (dla przetwarzania dużych PDF przez AI)
timeout = 300

# Preload: importuj app przed forkiem — błędy importu widoczne natychmiast w logach
preload_app = True

# Logi do stdout (Cloud Logging je zbiera)
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Worker klasa — synchroniczna (domyślna), odpowiednia dla tej aplikacji
worker_class = "sync"

# UWAGA: Gunicorn NIE obsługuje limitowania rozmiaru body żądania.
# Limit 500MB jest ustawiony przez Flask MAX_CONTENT_LENGTH w app.py.
# Na Cloud Run i tak obowiązuje limit 32MB niezależnie od tej konfiguracji.
