# Plan: Ozon API Scraper (без браузера)

**Branch:** `feature/ozon-api-scraper`
**Created:** 2026-05-31
**Type:** Feature

## Settings

- **Testing:** Yes — unit-тесты для парсинга JSON
- **Logging:** Verbose (DEBUG) — логировать все HTTP запросы/ответы/парсинг
- **Docs:** No

## Контекст

Ozon блокирует браузерный скрапер по IP датацентра (45.12.73.76). Страница "Доступ ограничен"
возвращается даже при полном stealth. Решение: HTTP-запросы к внутреннему JSON API Ozon
(`/api/composer-api.bx/page/json/v2`) — тот же endpoint, который вызывает браузер через XHR,
но без браузерного fingerprinting.

**Текущий скрапер (`OzonScraper`) сохраняется без изменений.** Переключение через
env var `OZON_SCRAPER_BACKEND`. Дефолт — `browser` (старый), `api` — новый.

## Архитектура решения

```
bot/scrapers/
├── base.py              # без изменений
├── wb_scraper.py        # без изменений
├── ozon_scraper.py      # без изменений (браузерный, оставляем как fallback)
├── ozon_api_scraper.py  # NEW — HTTP API реализация
└── factory.py           # NEW — make_ozon_scraper(settings) -> BaseScraper
```

**Endpoint Ozon:**
```
GET https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/search/?text=QUERY&from_global=true
GET https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/category/SLUG/?PARAMS
```
Ответ — JSON с widgets, в т.ч. `tileGridDesktop` / `searchResultsV2` с массивом товаров.

## Tasks

### Phase 1 — Зависимости и конфиг

- [x] **Task 1:** Добавить `httpx` в `requirements.txt`
  - Файл: `requirements.txt`
  - Добавить строку `httpx>=0.27.0` (async HTTP клиент, без браузера)
  - Логи: нет (файл зависимостей)

- [x] **Task 2:** Добавить `OZON_SCRAPER_BACKEND` в конфиг
  - Файл: `bot/config.py`
  - Добавить поле `OZON_SCRAPER_BACKEND: str = os.getenv("OZON_SCRAPER_BACKEND", "browser")`
  - Допустимые значения: `"browser"` (старый), `"api"` (новый)
  - Лог при инициализации: `[Settings] OZON_SCRAPER_BACKEND=%s`

### Phase 2 — Реализация OzonApiScraper

- [x] **Task 3:** Создать `bot/scrapers/ozon_api_scraper.py` — скелет класса
  - Файл: `bot/scrapers/ozon_api_scraper.py` (новый)
  - `class OzonApiScraper(BaseScraper): platform = "ozon"`
  - Константы: URL endpoint, headers (User-Agent, Accept-Language, Accept)
  - Метод `async scrape(query)` — делегирует в `scrape_brand_with_filters`
  - Метод `async scrape_brand_with_filters(brand, filter_items)` — точка входа
  - Логи: `[OzonApiScraper] start brand=%r url=%s`

- [x] **Task 4:** Реализовать `_fetch_page(url)` — HTTP запрос к Ozon API
  - Файл: `bot/scrapers/ozon_api_scraper.py`
  - `async def _fetch_page(self, page_url: str) -> dict | None`
  - Строит `api_url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url={encoded_page_url}"`
  - Использует `httpx.AsyncClient` с timeout=15s, retries=2
  - Поддерживает `OZON_PROXY` env var через `httpx` proxies
  - Логи DEBUG: запрос, статус, размер ответа; WARNING при ошибках

- [x] **Task 5:** Реализовать `_parse_products(data)` — извлечение товаров из JSON
  - Файл: `bot/scrapers/ozon_api_scraper.py`
  - `def _parse_products(self, data: dict) -> list[ScrapedProduct]`
  - Ищет виджеты `tileGridDesktop`, `searchResultsV2`, `catalogResultsV2` в `data["widgetStates"]`
  - Из каждого тайла извлекает: `name`, `price` (finalPrice / price), `url`, `image`
  - Обрабатывает разные форматы цен Ozon (int, str, dict с `text`)
  - Логи: `[OzonApiScraper] parsed %d products`; DEBUG per-item для отладки

- [x] **Task 6:** Собрать `scrape_brand_with_filters` с фильтрацией по весу
  - Файл: `bot/scrapers/ozon_api_scraper.py`
  - Переиспользует `_weight_from_url`, `_extract_weight_grams`, `_cheapest`, `_parse_price` из `ozon_scraper.py` — вынести в `bot/scrapers/_ozon_utils.py` чтобы не дублировать
  - Логи: финальный результат `[OzonApiScraper] result name=%r price=%s`

### Phase 3 — Фабрика и интеграция

- [x] **Task 7:** Создать `bot/scrapers/factory.py`
  - Файл: `bot/scrapers/factory.py` (новый)
  - `def make_ozon_scraper(settings) -> BaseScraper`
  - Если `settings.OZON_SCRAPER_BACKEND == "api"` → `OzonApiScraper()`
  - Иначе → `OzonScraper()`
  - Лог: `[factory] ozon backend=%s`

- [x] **Task 8:** Обновить 3 точки инстанциации
  - Файлы: `bot/handlers/check.py`, `bot/handlers/products.py`, `bot/scheduler.py`
  - В каждом: убрать `from bot.scrapers.ozon_scraper import OzonScraper`
  - Добавить `from bot.scrapers.factory import make_ozon_scraper`
  - Изменить `_ozon_scraper = OzonScraper()` → `_ozon_scraper = make_ozon_scraper(settings)`
  - В scheduler settings передаётся из `app.bot_data["settings"]`

### Phase 4 — Тесты

- [x] **Task 9:** Написать тесты для `_parse_products`
  - Файл: `tests/test_ozon_api_scraper.py` (новый)
  - Тесты с fixture JSON-ответов Ozon (реальная структура widgetStates)
  - Тест: товар найден с правильной ценой
  - Тест: пустой ответ → пустой список
  - Тест: виджет не того типа → пропускается
  - Тест: невалидная цена → товар пропускается

## Commit Plan

```
Commit 1 (после Task 1-2):
feat(ozon-api): add httpx dependency and OZON_SCRAPER_BACKEND config

Commit 2 (после Task 3-6):
feat(ozon-api): implement OzonApiScraper with HTTP JSON API

Commit 3 (после Task 7-8):
feat(ozon-api): add scraper factory and wire into handlers/scheduler

Commit 4 (после Task 9):
test(ozon-api): add unit tests for OzonApiScraper._parse_products
```

## Переключение в Coolify

После деплоя добавить в Coolify Environment Variables:
```
OZON_SCRAPER_BACKEND=api
```
Для отката — вернуть `browser` или удалить переменную.

## Файлы затронутые планом

| Файл | Тип |
|------|-----|
| `requirements.txt` | modify |
| `bot/config.py` | modify |
| `bot/scrapers/ozon_api_scraper.py` | create |
| `bot/scrapers/_ozon_utils.py` | create (рефакторинг shared utils) |
| `bot/scrapers/factory.py` | create |
| `bot/handlers/check.py` | modify |
| `bot/handlers/products.py` | modify |
| `bot/scheduler.py` | modify |
| `tests/test_ozon_api_scraper.py` | create |
| `bot/scrapers/ozon_scraper.py` | **без изменений** |
