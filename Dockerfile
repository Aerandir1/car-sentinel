# Dashboard Fiesta dans un conteneur (Docker Desktop Windows / macOS / Linux).
# Voir la section « Docker » du README.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Dossiers de travail à l'utilisateur du conteneur (1000) : un volume Docker vide (FIESTA_DATA=fiesta-data)
# en reprend le propriétaire, sinon la base ne pourrait pas y être créée.
RUN mkdir -p data logs && chown 1000:1000 data logs

ENV PYTHONUNBUFFERED=1
EXPOSE 8082
# Écoute sur toutes les interfaces du conteneur ; le port n'est publié que sur 127.0.0.1 côté hôte (compose).
ENTRYPOINT ["python", "src/dashboard_fiesta.py", "--bind", "0.0.0.0"]
