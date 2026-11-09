# Образ backend Kali. Сборка: docker build -t kali-backend ./backend
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала зависимости (лучше кэшируется)
COPY pyproject.toml ./
COPY kali ./kali
RUN pip install --upgrade pip && pip install .

# Непривилегированный пользователь
RUN useradd --create-home --uid 1000 kali
USER kali

ENV KALI_HOST=0.0.0.0 \
    KALI_PORT=8000 \
    KALI_ENVIRONMENT=prod

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "kali"]
