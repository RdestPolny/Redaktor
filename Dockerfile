FROM python:3.12-slim

WORKDIR /app

# Zależności systemowe dla PyMuPDF
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Zależności Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kod aplikacji
COPY . .

# Cloud Run przekazuje PORT przez zmienną środowiskową (domyślnie 8080)
EXPOSE 8080

# WAŻNE: --config musi być jawnie podany, gunicorn nie szuka gunicorn.conf.py automatycznie!
# Bez tego startuje z domyślnym bind=127.0.0.1:8000 → Cloud Run health check odpada.
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
