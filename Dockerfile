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

# Cloud Run port
EXPOSE 8080

# Gunicorn — produkcyjny serwer WSGI
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "--timeout", "300", "app:app"]
