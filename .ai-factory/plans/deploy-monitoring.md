# Implementation Plan: Деплой и мониторинг

Branch: feature/deploy-monitoring
Created: 2026-04-28

## Settings

- **Testing:** нет
- **Logging:** verbose — детальные DEBUG-логи
- **Docs:** нет обязательного чекпоинта

## Roadmap Linkage

Milestone: "Деплой и мониторинг"
Rationale: Последний пункт роадмапа — VPS-деплой с автозапуском и уведомлениями об ошибках.

## Контекст

Бот уже полностью функционален. Нужно подготовить инфраструктуру для продакшена:
- `TelegramErrorHandler` — отправляет ERROR/CRITICAL в GROUP_CHAT_ID через urllib (stdlib)
- systemd-сервис с автозапуском и `Restart=on-failure`
- Скрипты первичной установки и обновления на Ubuntu VPS
- `.env.example` — шаблон конфигурации
- `Makefile` — единая точка входа для dev и ops команд

Уведомления об ошибках идут в `GROUP_CHAT_ID` (тот же чат, что и ценовые отчёты) — без отдельного ADMIN_CHAT_ID.

## Commit Plan

- **Commit 1** (после задач 1–2): `feat(logger): add Telegram error notifications for ERROR/CRITICAL`
- **Commit 2** (после задач 3–6): `chore(deploy): add systemd service, setup scripts and .env.example`
- **Commit 3** (после задачи 7): `chore: add Makefile for project and VPS management`

## Tasks

### Фаза 1 — Уведомления об ошибках

- [x] Задача 1: Добавить `TelegramErrorHandler` в `bot/logger.py`
  - `class TelegramErrorHandler(logging.Handler)` — `bot_token`, `chat_id`
  - `emit()` через `urllib.request.urlopen` (POST /sendMessage), без внешних зависимостей
  - Формат: `🔴 [LEVELNAME] logger_name:\n<текст>\n<traceback>`
  - Все исключения → `self.handleError(record)`, никогда не роняет процесс
  - `setup_logging()` получает опциональные `bot_token` и `chat_id`

- [x] Задача 2: Подключить `TelegramErrorHandler` в `bot/main.py` (depends on 1)
  - В `_post_init()` создать и добавить handler к root logger с уровнем ERROR
  - Использует `settings.BOT_TOKEN` и `settings.GROUP_CHAT_ID`

<!-- Commit checkpoint: задачи 1–2 -->

### Фаза 2 — Deploy артефакты

- [x] Задача 3: Создать `.env.example` в корне
  - Все переменные из `bot/config.py` с комментариями
  - `LOG_LEVEL=INFO` как продакшен-default

- [x] Задача 4: Создать `deploy/whatcher.service` (systemd unit)
  - User/Group: `whatcher`, WorkingDirectory: `/opt/whatcher`
  - ExecStart: `/opt/whatcher/venv/bin/python -m bot.main`
  - `Restart=on-failure`, `RestartSec=10s`, `EnvironmentFile=/opt/whatcher/.env`
  - `StandardOutput=journal`, `StandardError=journal`

- [x] Задача 5: Создать `deploy/setup.sh` (первичная установка, 12 шагов)
  - apt install python3/pip/git, useradd whatcher, git clone, venv
  - pip install, playwright install chromium --with-deps
  - cp .env.example → .env, chown, systemctl enable+start

- [x] Задача 6: Создать `deploy/update.sh` (обновление на VPS) (depends on 4, 5)
  - `set -e`, git pull, pip install, systemctl restart, статус

<!-- Commit checkpoint: задачи 3–6 -->

### Фаза 3 — Makefile

- [x] Задача 7: Создать `Makefile` в корне
  - Dev: `install`, `run`, `test`
  - VPS: `logs`, `status`, `restart`, `stop`
  - Deploy: `setup`, `update`
  - Default target `help` с описанием всех команд

<!-- Commit checkpoint: задача 7 -->
