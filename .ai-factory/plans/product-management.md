# Implementation Plan: Product Management (/add, /remove, /list)

Branch: feature/product-management
Created: 2026-04-27

## Settings
- Testing: yes
- Logging: verbose
- Docs: no

## Roadmap Linkage
Milestone: "Управление списком товаров (`/add`, `/remove`, `/list`)"
Rationale: Реализует добавление/удаление конкретных товаров в БД для периодического мониторинга — фундамент для планировщика и групповых отчётов.

## Architecture Notes

**Затрагиваемые файлы:**
```
bot/database/db.py         — 5 новых функций для Product/User
bot/handlers/products.py   — /add (ConversationHandler), /list, /remove + callback
bot/main.py                — регистрация 3 обработчиков и обновление команд меню
tests/test_products_db.py  — unit-тесты DB-функций (in-memory SQLite)
```

**URL-детектор при /add:**
- `wildberries.ru` или `wb.ru` в тексте → `wb_url = text`, `ozon_url = None`
- `ozon.ru` в тексте → `wb_url = None`, `ozon_url = text`
- иначе → сохранять как `name` без URL (`wb_url=None`, `ozon_url=None`)

**User-resolution:** перед `add_product` вызывать `get_or_create_user(telegram_id, username)` → получить `user.id`

**Паттерн /remove:** аналогичен `/favorites` — inline-кнопки `callback_data="rm_prod:{id}"`, callback отдельно зарегистрирован в main.py

## Commit Plan
- **Commit 1** (задачи 1–2): `feat(db): add product management DB functions and /add handler`
- **Commit 2** (задача 3): `feat(handlers): add /list and /remove commands`
- **Commit 3** (задача 4): `feat(bot): register product management handlers in main.py`
- **Commit 4** (задача 5): `test: add unit tests for product DB functions`

## Tasks

### Phase 1: DB-слой

- [x] Task 1: Добавить DB-функции в `bot/database/db.py`

  Добавить импорт `Product`, `User` из `bot.database.models`.

  **`get_or_create_user(telegram_id: int, username: str | None) -> User`**
  - `SELECT * FROM users WHERE telegram_id = ?`; если найден — expunge + вернуть
  - Если не найден — INSERT `User(telegram_id=..., username=...)`, flush, expunge, вернуть
  - LOGGING: DEBUG "found" vs "created", telegram_id

  **`add_product(user_id: int, name: str, wb_url: str | None = None, ozon_url: str | None = None) -> Product`**
  - INSERT `Product(user_id=..., name=..., wb_url=..., ozon_url=...)`
  - flush, expunge, вернуть
  - LOGGING: DEBUG аргументы; INFO после INSERT: `product.id`, `name`

  **`list_products(user_id: int) -> list[Product]`**
  - `SELECT * FROM products WHERE user_id = ? ORDER BY added_at DESC`
  - expunge_all(), вернуть список
  - LOGGING: DEBUG `user_id`, `count`

  **`get_product_by_id(product_id: int) -> Product | None`**
  - `session.get(Product, product_id)`, expunge если найден
  - LOGGING: DEBUG `product_id`, found/not-found

  **`remove_product(product_id: int) -> None`**
  - `session.get(Product, product_id)` → если None, WARNING и return
  - `session.delete(product)`, commit
  - LOGGING: INFO `product_id`, `name` при удалении; WARNING если не найден

  Files: `bot/database/db.py`

### Phase 2: Handler

- [x] Task 2: Создать `bot/handlers/products.py` — /add ConversationHandler

  **`WAITING_URL = 0`**

  **`_detect_platform(text: str) -> tuple[str, str | None, str | None]`**
  Вспомогательная, возвращает `(name, wb_url, ozon_url)`:
  - `"wildberries.ru"` или `"wb.ru"` в text → `(text, text, None)`
  - `"ozon.ru"` в text → `(text, None, text)`
  - иначе → `(text, None, None)`
  - LOGGING: DEBUG text + detected platform label

  **`_add_start(update, context) -> int`**
  - Ответить: "Отправьте URL товара с WB или Ozon, или введите название товара:"
  - LOGGING: DEBUG telegram_id
  - Вернуть `WAITING_URL`

  **`_got_url(update, context) -> int`**
  - `text = update.message.text.strip()`
  - Вызвать `_detect_platform(text)` → `(name, wb_url, ozon_url)`
  - `user = get_or_create_user(update.effective_user.id, update.effective_user.username)`
  - `product = add_product(user.id, name, wb_url, ozon_url)`
  - Определить метку: `"WB"` / `"Ozon"` / `"без ссылки"`
  - Ответить:
    ```
    ✅ Товар добавлен в список мониторинга:
    📦 {name}
    🏪 {platform_label}
    ```
  - LOGGING: INFO product.id, name, platform_label
  - Вернуть `ConversationHandler.END`

  **`_cancel(update, context) -> int`**
  - Ответить: "Добавление отменено."
  - Вернуть `ConversationHandler.END`

  **`add_handler = ConversationHandler(...)`**
  - entry_points: `[CommandHandler("add", _add_start)]`
  - states: `{WAITING_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, _got_url)]}`
  - fallbacks: `[CommandHandler("cancel", _cancel)]`

  Files: `bot/handlers/products.py`

- [x] Task 3: Добавить /list и /remove handlers в `bot/handlers/products.py`

  **`list_products_handler(update, context) -> None`**
  - `user = get_or_create_user(telegram_id, username)`
  - `products = list_products(user.id)`
  - Если пусто:
    ```
    У вас нет товаров в списке мониторинга.
    Добавьте товар командой /add
    ```
  - Иначе формировать строку:
    ```
    📋 Ваши товары для мониторинга:

    1. {name}
       🏪 WB: {wb_url}
    2. {name}
       🏪 Ozon: {ozon_url}
    3. {name}
       🏪 без ссылки
    ```
  - LOGGING: DEBUG telegram_id, count

  **`remove_handler(update, context) -> None`**
  - `user = get_or_create_user(...)`, `products = list_products(user.id)`
  - Если пусто: "Список мониторинга пуст."
  - Иначе: inline-кнопки:
    ```python
    buttons = [
        [InlineKeyboardButton(f"🗑 {p.name[:40]}", callback_data=f"rm_prod:{p.id}")]
        for p in products
    ]
    ```
  - `await update.message.reply_text("Выберите товар для удаления:", reply_markup=InlineKeyboardMarkup(buttons))`
  - LOGGING: DEBUG telegram_id, count

  **`remove_product_callback(update, context) -> None`**
  - `q = update.callback_query; await q.answer()`
  - `product_id = int(q.data.split(":")[1])`
  - `product = get_product_by_id(product_id)`
  - Если None: `await q.edit_message_text("❌ Товар не найден.")`; return
  - `name = product.name`; `remove_product(product_id)`
  - `await q.edit_message_text(f"✅ Товар «{name}» удалён из мониторинга.")`
  - LOGGING: INFO product_id, name; WARNING если не найден

  Files: `bot/handlers/products.py`

<!-- Commit checkpoint: tasks 1–3 — "feat(db): add product management DB functions and /add /list /remove handlers" -->

### Phase 3: Интеграция

- [x] Task 4: Зарегистрировать handlers в `bot/main.py`

  **Импорт:**
  ```python
  from bot.handlers.products import (
      add_handler, list_products_handler, remove_handler, remove_product_callback
  )
  ```

  **В `_register_handlers` после `run_fav` callback:**
  ```python
  app.add_handler(add_handler)
  app.add_handler(CommandHandler("list", list_products_handler))
  app.add_handler(CommandHandler("remove", remove_handler))
  app.add_handler(CallbackQueryHandler(remove_product_callback, pattern=r"^rm_prod:\d+$"))
  ```

  **В `set_my_commands`:**
  ```python
  BotCommand("add", "Добавить товар в список мониторинга"),
  BotCommand("list", "Показать список товаров"),
  BotCommand("remove", "Удалить товар из списка"),
  ```

  LOGGING: DEBUG после каждого `app.add_handler`.

  Files: `bot/main.py`

<!-- Commit checkpoint: task 4 — "feat(bot): register product management handlers in main.py" -->

### Phase 4: Тесты

- [x] Task 5: Написать `tests/test_products_db.py`

  **Фикстура `db_session`:**
  - `init_db("sqlite:///:memory:")`
  - `Base.metadata.create_all(get_engine())`
  - yield сессию (или просто init_db для каждого теста через autouse)

  **`test_get_or_create_user_creates_new`**
  - `user = get_or_create_user(111, "alice")`
  - assert `user.telegram_id == 111`, `user.username == "alice"`

  **`test_get_or_create_user_idempotent`**
  - Вызвать дважды с одним `telegram_id`
  - assert `user1.id == user2.id` (не создаётся дубликат)

  **`test_add_product_wb_url`**
  - `user = get_or_create_user(222, None)`
  - `p = add_product(user.id, "WB Product", wb_url="https://wildberries.ru/test")`
  - assert `p.wb_url == "https://wildberries.ru/test"`, `p.ozon_url is None`

  **`test_add_product_ozon_url`**
  - Аналогично для `ozon_url`

  **`test_add_product_no_url`**
  - `p = add_product(user.id, "Plain Product")`
  - assert `p.wb_url is None`, `p.ozon_url is None`

  **`test_list_products_returns_all`**
  - Добавить 3 товара → `products = list_products(user.id)`
  - assert `len(products) == 3`

  **`test_list_products_empty`**
  - Новый user без товаров → `list_products(user.id) == []`

  **`test_remove_product`**
  - Добавить → `remove_product(p.id)` → `get_product_by_id(p.id) is None`

  Files: `tests/test_products_db.py`

<!-- Commit checkpoint: task 5 — "test: add unit tests for product DB functions" -->
