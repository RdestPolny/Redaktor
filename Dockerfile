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

# Kod aplikacji (w tym .streamlit/config.toml!)
COPY . .

# Cloud Run używa portu 8080 — ustawiamy go jawnie w config.toml
# PORT jest też dostępny jako zmienna środowiskowa
EXPOSE 8080

# Streamlit na Cloud Run:
# - port 8080 (Cloud Run wymaga dokładnie tego portu)
# - 0.0.0.0 żeby Cloud Run mógł połączyć się z kontenerem
# - headless=true wyłącza próby otwarcia przeglądarki
CMD ["streamlit", "run", "app.py", \
    "--server.port=8080", \
    "--server.address=0.0.0.0", \
    "--server.headless=true", \
    "--server.enableCORS=false", \
    "--server.enableXsrfProtection=false"]
