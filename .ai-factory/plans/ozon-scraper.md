# Implementation Plan: Ozon Scraper

Branch: feature/ozon-scraper
Created: 2026-04-27

## Settings
- Testing: yes
- Logging: verbose
- Docs: no

## Roadmap Linkage
Milestone: "Скрапер Ozon"
Rationale: Реализует headless-скрапер для Ozon с тем же интерфейсом BaseScraper, что и WB — позволяет /check и будущим планировщику/отчётам работать с обоими маркетплейсами.

## Architecture Notes

**Подход — аналог WB-скрапера:**
- `OzonScraper(BaseScraper)` в `bot/scrapers/ozon_scraper.py`
- Тот же набор вспомогательных функций: `build_search_url`, `_parse_price`, `_parse_card`, `_find_cheapest`, `save_price_record`
- Метод `scrape_brand_with_filters(brand, filter_items)` с поддержкой прямого URL (как в WB)
- playwright-stealth, random delay, User-Agent — идентично WB

**Ozon-специфика:**
- Search URL: `https://www.ozon.ru/search/?text={encoded}&from_global=true`
- Контейнер результатов: `[data-widget="searchResultsV2"]`
- Карточки: `[data-widget="searchResultsV2"] [data-index]` (атрибут `data-index` стабилен)
- Ссылка на товар: `a[href^="/product/"]` внутри карточки
- Цена: JS-evaluate — ищем `span` с ₽ внутри карточки (`/\d[\d\s]*₽/`)
- Изображение: `img` внутри карточки (src содержит `ozon.ru` или `cdn`)
- Фильтры: `[data-widget="catalogFilters"]` или `[data-widget="searchFilters"]`
- Прямой URL-фильтр: содержит `ozon.ru`

> **Важно:** CSS-классы Ozon обфусцированы и меняются. Все селекторы опираются на `data-*` атрибуты или структурные паттерны DOM, а не на class-имена. При первом запуске — проверить через `_dump_html()`.

**Структура модулей:**
```
bot/scrapers/
├── __init__.py
├── base.py           — ScrapedProduct, BaseScraper (без изменений)
├── wb_scraper.py     — WbScraper (без изменений)
└── ozon_scraper.py   — OzonScraper + save_price_record()
tests/
└── test_ozon_scraper.py
```

## Commit Plan
- **Commit 1** (задачи 1–2): `feat(scraper): add OzonScraper with headless search and brand+filter support`
- **Commit 2** (задача 3): `feat(scraper): add Ozon price record DB persistence`
- **Commit 3** (задача 4): `test: add unit tests for Ozon scraper`

## Tasks

### Phase 1: OzonScraper — поиск и парсинг

- [x] Task 1: Создать `bot/scrapers/ozon_scraper.py` — утилиты и `OzonScraper.scrape()`

  Создать файл `bot/scrapers/ozon_scraper.py`.

  **`build_search_url(query: str) -> str`**
  - Возвращает `https://www.ozon.ru/search/?text={urllib.parse.quote_plus(query)}&from_global=true`

  **`_parse_price(raw: str) -> float`**
  - Убрать `₽`, `\xa0`, пробелы, неразрывные пробелы, разделители тысяч
  - Конвертировать в `float`; при ошибке — поднять `ValueError`

  **`_parse_card(tile, fallback_url: str) -> ScrapedProduct | None`**
  - Найти `a[href^="/product/"]` → `product_url` (добавить `https://www.ozon.ru` если href относительный)
  - Найти цену через JS evaluate внутри tile:
    ```python
    raw_price = await tile.evaluate(
        """el => {
            const spans = el.querySelectorAll('span');
            for (const s of spans) {
                if (/\\d[\\d\\s]*₽/.test(s.innerText)) return s.innerText;
            }
            return null;
        }"""
    )
    ```
  - Если цена не найдена → `return None`
  - Найти название: `a[href^="/product/"]` → `.inner_text()` (или ближайший `span`/заголовок)
  - Найти изображение: первый `img` в tile → `src` или `data-src` (проверить что содержит `ozon`)
  - Вернуть `ScrapedProduct(platform="ozon", currency="RUB", ...)`

  **`async def _dump_html(page, name: str) -> None`**
  - Аналогично WB: сохранить HTML в `logs/debug_{name}.html` для отладки селекторов

  **`class OzonScraper(BaseScraper):`**
  - `platform = "ozon"`
  - `async def scrape(self, query: str) -> ScrapedProduct | None`:
    - `search_url = build_search_url(query)`
    - `async with async_playwright() as p:` → `browser = await p.chromium.launch(headless=True)`
    - `context = await browser.new_context(user_agent=_USER_AGENT)` (тот же UA что в WB)
    - `await _STEALTH.apply_stealth_async(page)`
    - `await asyncio.sleep(random.uniform(1.5, 3.5))`
    - `await page.goto(search_url, timeout=30_000)`
    - Ожидание: `await page.wait_for_selector('[data-widget="searchResultsV2"]', timeout=15_000)`
    - `tiles = await page.query_selector_all('[data-widget="searchResultsV2"] [data-index]')`
    - Если пусто — `await _dump_html(page, "ozon_search")` + `return None`
    - Первый tile → `_parse_card(tiles[0], search_url)`
    - `product.query = query`
    - При `PlaywrightTimeoutError` → WARNING + `return None`
    - При прочих исключениях → ERROR с traceback + `return None`

  LOGGING:
  - DEBUG при старте: `query`, `search_url`
  - DEBUG после загрузки: количество tiles
  - DEBUG в `_parse_card`: raw price text, raw name text, href
  - INFO при успехе: `name`, `price`, `product_url`
  - WARNING если tiles не найдены
  - WARNING при таймауте (query, url)
  - ERROR при неожиданном исключении

  Files: `bot/scrapers/ozon_scraper.py`

- [x] Task 2: Реализовать `scrape_brand_with_filters()` в OzonScraper

  Добавить в `OzonScraper`:

  **`async def scrape_brand_with_filters(self, brand: str, filter_items: list[str]) -> ScrapedProduct | None`**

  Логика аналогична WB:
  1. Если в `filter_items` есть URL начинающийся на `https://www.ozon.ru` → использовать его как `search_url`, `filters_to_apply = []`
  2. Иначе `search_url = build_search_url(brand)`, `filters_to_apply = filter_items`
  3. Открыть браузер, перейти на `search_url`, дождаться `[data-widget="searchResultsV2"]`
  4. Для каждого `filter_text` в `filters_to_apply` — вызвать `_apply_filter(page, filter_text)`:
     - Пробовать селекторы: `[data-widget="catalogFilters"] button`, `[data-widget="searchFilters"] button`, `[data-widget="catalogHorizontalFilters"] button`
     - Фильтровать по тексту через `.filter(has_text=filter_text)`
     - После клика: `await asyncio.sleep(2.0)`, повторно дождаться tiles
     - WARNING если фильтр не найден + `_dump_html(page, "ozon_filter")`
  5. Найти все tiles, вызвать `_find_cheapest(tiles, search_url)` — аналог WB
  6. Если найден `cheapest` и есть `filter_items` — вызвать `_resolve_variant()`:
     - Перейти на `cheapest.product_url`
     - Попробовать выбрать вариант (размер/цвет): `[data-widget="webGallery"] button`, `[data-widget="webAddToCart"] button` с matching текстом
     - Извлечь актуальную цену с product page: `[data-widget="webPrice"]` или `[data-widget="webSale"]`
     - Вернуть `(url, price, image_url)`
  7. Вернуть `cheapest` с обновлёнными полями

  LOGGING — аналогично WB `scrape_brand_with_filters`.

  Files: `bot/scrapers/ozon_scraper.py`

<!-- Commit checkpoint: tasks 1–2 — "feat(scraper): add OzonScraper with headless search and brand+filter support" -->

### Phase 2: Сохранение в БД

- [x] Task 3: Добавить `save_price_record()` для Ozon

  Добавить в `bot/scrapers/ozon_scraper.py` функцию:

  ```python
  def save_price_record(product_id: int, scraped: ScrapedProduct) -> PriceRecord:
  ```

  - Создаёт `PriceRecord(product_id=product_id, platform=Platform.ozon, price=scraped.price, currency=scraped.currency)`
  - Использует `with get_session() as session:`
  - Если `product.ozon_url` пустой — обновляет `scraped.product_url` в той же сессии
  - Возвращает сохранённую запись

  LOGGING:
  - DEBUG перед сохранением: `product_id`, `price`, `platform`
  - INFO после сохранения: `record.id`, `price`, `checked_at`
  - DEBUG если `ozon_url` обновлён (старое → новое значение)

  Files: `bot/scrapers/ozon_scraper.py`

<!-- Commit checkpoint: task 3 — "feat(scraper): add Ozon price record DB persistence" -->

### Phase 3: Тесты

- [x] Task 4: Написать `tests/test_ozon_scraper.py`

  Следовать паттерну из `tests/test_wb_scraper.py` — переиспользовать `_make_playwright_mock` (или создать аналог).

  **`test_build_search_url`** — параметризованный:
  ```python
  @pytest.mark.parametrize("query,expected_fragment", [
      ("Nike Air Force 1", "Nike+Air+Force+1"),
      ("Samsung Galaxy S24", "Samsung+Galaxy+S24"),
  ])
  ```
  Проверяет: `"ozon.ru"` в URL, `expected_fragment` в URL.

  **`test_parse_price`** — параметризованный:
  ```python
  @pytest.mark.parametrize("raw,expected", [
      ("49 999 ₽", 49999.0),
      ("1 234 567 ₽", 1234567.0),
      ("0 ₽", 0.0),
  ])
  ```

  **`test_scrape_extracts_first_result`** — mock playwright:
  - Патчим `async_playwright`, `_STEALTH`, `asyncio.sleep`
  - `page.wait_for_selector` → без ошибки
  - `page.query_selector_all` → `[mock_tile]`
  - `mock_tile.evaluate` → `"49 999 ₽"`
  - `mock_tile.query_selector("a[href^='/product/']")` → mock с href `/product/test-1/`
  - Проверяем: `result.price == 49999.0`, `result.platform == "ozon"`, `result.currency == "RUB"`

  **`test_scrape_returns_none_on_no_results`**:
  - `page.wait_for_selector` поднимает `PlaywrightTimeoutError`
  - Проверяем: `result is None`

  **`test_scrape_returns_none_on_navigation_timeout`**:
  - `page.goto` поднимает `asyncio.TimeoutError`
  - Проверяем: `result is None`, без исключения

  LOGGING: DEBUG в каждом тесте при подстановке mock-данных.
  Files: `tests/test_ozon_scraper.py`

<!-- Commit checkpoint: task 4 — "test: add unit tests for Ozon scraper" -->
