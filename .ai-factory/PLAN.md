# Планировщик и автоматический сбор цен

**Дата:** 2026-04-28  
**Ветка:** feature/ozon-scraper (текущая)

## Settings

- **Testing:** да — unit-тесты для планировщика и DB-функций
- **Logging:** verbose — детальные DEBUG-логи на каждый продукт
- **Docs:** нет обязательного чекпоинта

## Roadmap Linkage

**Milestone:** "Планировщик и автоматический сбор цен"  
**Rationale:** Прямая реализация следующего пункта роадмапа — cron-задача 7:00/13:00/20:00, обход продуктов, запись PriceRecord.

## Контекст

APScheduler уже есть в `requirements.txt`, но не подключён. `CHECK_TIMES` определён в `bot/config.py`, но не используется. Модели `Product` и `PriceRecord` готовы. Скраперы (`OzonScraper`, `WbScraper`) имеют `scrape_brand_with_filters()`. `save_price_record()` сейчас дублируется в каждом скрапере — нужно централизовать.

## Задачи

### Фаза 1 — Слой данных

**Задача 1: Добавить DB-функции для планировщика** (`bot/database/db.py`)

- `list_all_products_with_urls() -> list[Product]` — все Product где `wb_url IS NOT NULL OR ozon_url IS NOT NULL`
- `save_price_record(product_id, scraped: ScrapedProduct) -> PriceRecord` — централизованная запись (убрать дубли из скраперов)
- DEBUG-логи: кол-во найденных продуктов, записанный price/platform

### Фаза 2 — Планировщик

**Задача 2: Создать `bot/scheduler.py`** (блокируется Задачей 1)

```
async def collect_prices(app) -> None
```

- Вызывает `list_all_products_with_urls()`
- Для каждого: определяет платформу → вызывает `OzonScraper` или `WbScraper`
- Сохраняет через `save_price_record()`
- try/except per product — ошибка одного не останавливает обход
- DEBUG на каждый продукт: `product_id`, `platform`, `price`, время выполнения
- INFO итог: `N продуктов проверено, M записей сохранено, K ошибок`

**Задача 3: Интегрировать APScheduler в `bot/main.py`** (блокируется Задачей 2)

- `setup_scheduler(app, settings) -> AsyncIOScheduler`
- Парсит `settings.CHECK_TIMES` (`"7:00,13:00,20:00"`)
- Добавляет `CronTrigger` на каждое время
- `scheduler.start()` / `scheduler.shutdown()` в жизненном цикле бота
- INFO при старте: расписание и кол-во продуктов в мониторинге

### Фаза 3 — Тесты

**Задача 4: Написать тесты** (блокируется Задачами 1 и 2)

`tests/test_scheduler.py`:
- `test_collect_prices_calls_correct_scraper` — Ozon-продукт → OzonScraper, WB-продукт → WbScraper
- `test_collect_prices_saves_price_record` — результат скрапера записан в БД
- `test_collect_prices_continues_on_error` — исключение по одному продукту не останавливает остальные
- `test_collect_prices_skips_product_without_url` — продукт без URL не передаётся в скрапер

`tests/test_products_db.py` (дополнить):
- `test_list_all_products_with_urls` — возвращает только продукты с URL
- `test_save_price_record_creates_record` — запись появляется в БД с правильными полями

## Commit Plan

| Коммит | Задачи | Сообщение |
|--------|--------|-----------|
| 1 | 1 | `feat(db): add list_all_products_with_urls and centralise save_price_record` |
| 2 | 2, 3 | `feat(scheduler): add APScheduler price collection job wired to CHECK_TIMES` |
| 3 | 4 | `test(scheduler): add unit tests for scheduler job and DB functions` |
