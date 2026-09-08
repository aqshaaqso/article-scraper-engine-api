FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system scraper \
    && adduser --system --ingroup scraper scraper

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir --no-deps . \
    && mkdir -p /app/data \
    && chown -R scraper:scraper /app/data

USER scraper

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3)"]

CMD ["uvicorn", "article_scraper_lab.main:app", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers"]
