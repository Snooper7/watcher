# Групповые отчёты

**Дата:** 2026-04-28  
**Ветка:** feature/ozon-scraper (текущая)

## Settings

- **Testing:** да — unit-тесты для форматтера и DB-функции
- **Logging:** verbose — детальные DEBUG-логи
- **Docs:** нет обязательного чекпоинта

## Roadmap Linkage

**Milestone:** "Групповые отчёты"  
**Rationale:** Прямая реализация следующего пункта роадмапа — отправка отчёта с ценами в GROUP_CHAT_ID по расписанию CHECK_TIMES.

## Контекст

`GROUP_CHAT_ID` уже определён в `Settings`. Планировщик (`bot/scheduler.py`) запускает `collect_prices` по расписанию. `PriceRecord` хранит цену, платформу и `checked_at`. Нужно: добавить DB-запрос для последних цен, создать форматтер отчёта, подключить отправку к тому же расписанию.

## Задачи

### Фаза 1 — Слой данных

**Задача 5: Добавить `get_latest_price_records()`** (`bot/database/db.py`)

- Возвращает `list[tuple[Product, PriceRecord]]` — одна строка на пару (product, platform), запись с максимальным `checked_at`
- Реализация через subquery: `SELECT product_id, platform, MAX(checked_at) GROUP BY product_id, platform`, затем JOIN
- DEBUG-лог: кол-во строк

### Фаза 2 — Репортер

**Задача 6: Создать `bot/reporter.py`** (блокируется Задачей 5)

- `format_price_report(rows) -> str` — группирует по product.id, форматирует список с иконками WB/Ozon и ценами
- `send_group_report(app) -> None` — запрашивает данные, форматирует, отправляет в `GROUP_CHAT_ID`
- try/except на отправку — ошибка логируется, не поднимается

**Задача 7: Обновить `bot/scheduler.py`** (блокируется Задачей 6)

- Добавить `collect_and_report(app)` — вызывает `collect_prices` затем `send_group_report`
- В `setup_scheduler()` регистрировать `collect_and_report` вместо `collect_prices`

### Фаза 3 — Тесты

**Задача 8: Написать тесты** (блокируется Задачами 5 и 6)

`tests/test_reporter.py`:
- `test_format_price_report_empty`
- `test_format_price_report_ozon_only`
- `test_format_price_report_wb_only`
- `test_format_price_report_both_platforms`
- `test_send_group_report_calls_send_message`
- `test_send_group_report_handles_send_error`

`tests/test_products_db.py` (дополнить):
- `test_get_latest_price_records_returns_latest_per_platform`
