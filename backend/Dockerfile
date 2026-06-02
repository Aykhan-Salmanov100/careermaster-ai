# CareerMaster AI — backend API container
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps needed to build spaCy / pdfplumber wheels
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first so the layer caches across code changes
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application code
COPY main.py ./

# Run as a non-root user
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

# Hosts (Fly, Render, Railway) inject $PORT; default to 8080 locally
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
