# Dashboard Clio dans un conteneur (Docker Desktop Windows / macOS / Linux).
# Voir la section « Docker » du README.
FROM python:3.12-slim

# openssl : certificat HTTPS auto-signé créé au premier lancement dans certs/
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
# Écoute sur toutes les interfaces du conteneur ; le port n'est publié que sur 127.0.0.1 côté hôte (compose).
ENTRYPOINT ["python", "dashboard_clio.py", "--bind", "0.0.0.0"]
