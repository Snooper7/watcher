# Implementation Plan: Wildberries Scraper

Branch: feature/wb-scraper
Created: 2026-04-26

## Settings
- Testing: yes
- Logging: verbose
- Docs: no

## Roadmap Linkage
Milestone: "Wildberries Scraper"
Rationale: Реализует ядро мониторинга цен — headless-скрапер WB, который ищет товары по названию и производителю через поиск на сайте без использования API.

## Architecture Notes

**Подход к поиску и извлечению цены:**
- Пользователь добавляет товар командой `/add <название> [производитель]`
- Скрапер формирует поисковый запрос: `"{производитель} {название}"` (или просто `"{название}"`)
- Playwright открывает страницу поиска WB напрямую по URL:
  `https://www.wildberries.ru/catalog/0/search.aspx?search={query_encoded}`
- Ждёт загрузки карточек товаров
- Берёт **первый результат** — извлекает цену, название и ссылку на товар
- CSS-селекторы для страницы поиска WB:
  - Карточка: `.product-card-list .product-card` (первый элемент)
  - Цена: `.price-block__final-price`
  - Название: `.product-card__name`
  - Ссылка: `.product-card__link` (атрибут `href`)
- playwright-stealth скрывает признаки автоматизации
- **Никакого API не используется** — только навигация и CSS-селекторы

**Структура модулей:**
```
bot/scrapers/
├── __init__.py
├── base.py          — ScrapedProduct dataclass, BaseScraper ABC
└── wb_scraper.py    — WbScraper(BaseScraper) + save_price_record()
tests/
└── test_wb_scraper.py
```

**Сигнатура BaseScraper:**
```python
async def scrape(self, query: str) -> ScrapedProduct | None
# query = "Samsung Galaxy S24" или "Nike Air Force 1 Nike"
```

**ScrapedProduct включает найденный URL** — после скрапинга он сохраняется в `product.wb_url` для следующих проверок.

## Commit Plan
- **Commit 1** (задачи 1–2): `feat: add playwright-stealth dep and scraper base interface`
- **Commit 2** (задачи 3–4): `feat: implement WbScraper with search-based scraping and DB persistence`
- **Commit 3** (задача 5): `test: add unit tests for WB scraper`

## Tasks

### Phase 1: Зависимости и базовый интерфейс

- [x] Task 1: Добавить playwright-stealth в requirements.txt и установить браузер

  Добавить в `requirements.txt`:
  ```
  playwright-stealth
  ```
  Запустить `pip install playwright-stealth` и `playwright install chromium`.
  Проверить импорт: `from playwright_stealth import stealth_async`.

  LOGGING: DEBUG при инициализации скрапера — версия playwright, путь к браузеру.
  Files: `requirements.txt`

- [x] Task 2: Создать пакет bot/scrapers/ с базовым интерфейсом и датаклассом

  Создать `bot/scrapers/__init__.py` (пустой).

  Создать `bot/scrapers/base.py`:
  ```python
  @dataclass
  class ScrapedProduct:
      name: str             # название товара с сайта
      price: float | None   # None если не удалось получить
      currency: str         # "RUB"
      product_url: str      # ссылка на найденный товар (сохраняется в product.wb_url)
      platform: str         # "wb" | "ozon"
      query: str            # поисковый запрос, которым нашли товар
      scraped_at: datetime

  class BaseScraper(ABC):
      @abstractmethod
      async def scrape(self, query: str) -> ScrapedProduct | None:
          """query — название товара, опционально с производителем: 'Nike Air Force 1 Nike'"""
          ...
  ```

  LOGGING:
  - DEBUG при создании экземпляра скрапера (класс, platform)

  Files: `bot/scrapers/__init__.py`, `bot/scrapers/base.py`

<!-- Commit checkpoint: tasks 1–2 — "feat: add playwright-stealth dep and scraper base interface" -->

### Phase 2: WB скрапер и сохранение в БД

- [x] Task 3: Реализовать WbScraper — поиск по названию/производителю через браузер

  Создать `bot/scrapers/wb_scraper.py` с классом `WbScraper(BaseScraper)`:

  **`build_search_url(query: str) -> str`**
  - Возвращает `https://www.wildberries.ru/catalog/0/search.aspx?search={urllib.parse.quote(query)}`

  **`async scrape(self, query: str) -> ScrapedProduct | None`**
  - `async with async_playwright() as p:`
  - Запуск: `p.chromium.launch(headless=True)`
  - `await stealth_async(page)` перед навигацией
  - Заголовок: `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36`
  - Случайная задержка: `await asyncio.sleep(random.uniform(1.5, 3.5))`
  - Навигация: `await page.goto(build_search_url(query), timeout=30_000)`
  - Ожидание карточек: `await page.wait_for_selector(".product-card-list .product-card", timeout=15_000)`
  - Извлечение первого результата:
    - Цена: `.product-card:first-child .price-block__final-price` — текст → `float`
    - Название: `.product-card:first-child .product-card__name` — текст
    - Ссылка: `.product-card:first-child .product-card__link` — атрибут `href`
  - Парсинг цены: убрать пробелы, символ `₽`, преобразовать в `float`
  - При ошибке (timeout, selector not found, parse error) → `return None`
  - Таймаут всей операции: 45 секунд

  LOGGING:
  - DEBUG при старте скрапинга: query, search_url
  - DEBUG после загрузки страницы: количество найденных карточек
  - DEBUG при извлечении данных: raw price text, raw name text
  - INFO при успехе: name, price, product_url
  - WARNING если карточки не найдены (пустая выдача)
  - WARNING при таймауте (с query и url)
  - ERROR при неожиданном исключении (с traceback)

  Files: `bot/scrapers/wb_scraper.py`

- [x] Task 4: Реализовать сохранение результата скрапинга в БД

  Добавить в `bot/scrapers/wb_scraper.py` функцию:

  ```python
  def save_price_record(product_id: int, scraped: ScrapedProduct) -> PriceRecord:
  ```

  - Создаёт `PriceRecord(product_id=product_id, platform=Platform.wb, price=scraped.price, currency=scraped.currency)`
  - Использует `with get_session() as session:` (существующий паттерн из `bot/database/db.py`)
  - Если `product.wb_url` пустой — обновляет его `scraped.product_url` в той же сессии
  - Возвращает сохранённую запись

  LOGGING:
  - DEBUG перед сохранением: product_id, price, platform
  - INFO после сохранения: record.id, price, checked_at
  - DEBUG если wb_url обновлён (старое → новое значение)

  Files: `bot/scrapers/wb_scraper.py`

<!-- Commit checkpoint: tasks 3–4 — "feat: implement WbScraper with search-based scraping and DB persistence" -->

### Phase 3: Тесты

- [x] Task 5: Написать тесты для WB скрапера

  Создать `tests/test_wb_scraper.py`:

  **`test_build_search_url`** — параметризованный тест:
  ```python
  @pytest.mark.parametrize("query,expected_fragment", [
      ("Nike Air Force 1", "Nike+Air+Force+1"),
      ("Samsung Galaxy S24 Samsung", "Samsung+Galaxy+S24"),
  ])
  ```
  Проверяет, что URL содержит корректно закодированный запрос.

  **`test_scrape_extracts_first_result`** — mock браузера:
  - Патчим `async_playwright` через `unittest.mock.AsyncMock`
  - `page.query_selector` возвращает mock-элементы с `.inner_text()` → `"49 999 ₽"` и `"Nike Air Max"`
  - Проверяем `ScrapedProduct.price == 49999.0`, `name == "Nike Air Max"`

  **`test_scrape_returns_none_on_no_results`** — пустая выдача:
  - `page.wait_for_selector` поднимает `TimeoutError`
  - Проверяем что `scrape()` возвращает `None`

  **`test_scrape_returns_none_on_navigation_timeout`** — таймаут навигации:
  - `page.goto` поднимает `asyncio.TimeoutError`
  - Проверяем что `scrape()` возвращает `None`, не бросает исключение

  **`test_parse_price`** — юнит-тест парсинга цены:
  - `"49 999 ₽"` → `49999.0`
  - `"1 234 567 ₽"` → `1234567.0`
  - `"0 ₽"` → `0.0`

  LOGGING: DEBUG в тестах при подстановке mock-данных.
  Files: `tests/test_wb_scraper.py`

<!-- Commit checkpoint: task 5 — "test: add unit tests for WB scraper" -->
