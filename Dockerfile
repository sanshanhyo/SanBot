FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY backend ./backend
COPY bot ./bot
COPY javlibrary_crawler ./javlibrary_crawler
COPY config ./config
COPY i18n ./i18n
COPY assets ./assets

RUN python -m pip install --upgrade pip \
    && python -m pip install -e . \
    && python -c "import backend.main, bot.main, javlibrary_crawler" \
    && ffmpeg -version >/dev/null

CMD ["python", "-m", "backend.main"]
