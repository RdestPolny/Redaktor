FROM python:3.12-slim

WORKDIR /app

# Zainstaluj zależności systemowe dla PyMuPDF
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Zainstaluj zależności Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Skopiuj kod aplikacji
COPY . .

# Cloud Run wymaga portu 8080
EXPOSE 8080

# Healthcheck
HEALTHCHECK CMD curl --fail http://localhost:8080/_stcore/health || exit 1

# Uruchom Streamlit
CMD ["streamlit", "run", "app.py", \
     "--server.port=8080", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false"]
