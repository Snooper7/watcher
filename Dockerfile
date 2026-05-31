FROM python:3.11-slim

WORKDIR /app

# curl нужен playwright install-deps внутри
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Зависимости Python (отдельный слой — кешируется при изменении кода)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Playwright: системные зависимости + браузер Chromium одной командой
RUN playwright install --with-deps chromium

# Код приложения
COPY bot/ bot/

# SQLite хранится здесь — Coolify примонтирует volume
RUN mkdir -p data

CMD ["python", "-m", "bot.main"]
