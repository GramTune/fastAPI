# Utilise l'image Playwright officielle
FROM mcr.microsoft.com/playwright:focal

# Définit le dossier de travail
WORKDIR /app

# Copie les fichiers
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Lance l'app FastAPI avec Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]
