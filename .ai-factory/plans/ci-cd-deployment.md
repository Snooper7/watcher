# Implementation Plan: CI/CD и деплой на боевой сервер

Branch: feature/ci-cd-deployment
Created: 2026-04-28

## Settings

- **Testing:** нет (инфраструктура)
- **Logging:** standard — echo-прогресс в shell-скриптах
- **Docs:** нет обязательного чекпоинта

## Roadmap Linkage

Milestone: "none"
Rationale: Роадмап полностью закрыт; это post-release инфраструктурная задача.

## Контекст

**Репозиторий:** `git@github.com:Snooper7/watcher.git`, ветка `master`  
**VPS:** AmneziaVPN развёрнут как СЕРВЕР (клиенты подключаются к VPS).
Бот использует только исходящий HTTPS (443) через основной сетевой интерфейс.
VPN-интерфейс (awg0/wg0) обслуживает клиентский трафик и не затрагивает процессы самого сервера.

**Схема CI/CD:**
```
git push origin master
    ↓
GitHub Actions (ubuntu-latest)
    ↓ SSH (appleboy/ssh-action)
VPS root@<host>
    ↓
bash /opt/whatcher/deploy/update.sh
    ↓
systemctl is-active whatcher  →  exit 1 если упал  →  pipeline красный
```

**Secrets для GitHub Actions (Settings → Secrets → Actions):**
| Secret | Значение |
|--------|----------|
| `DEPLOY_HOST` | IP или домен VPS |
| `DEPLOY_USER` | `root` |
| `DEPLOY_KEY` | Приватный SSH-ключ (см. инструкцию ниже) |

**Создание deploy SSH-ключа (на VPS, один раз):**
```bash
ssh-keygen -t ed25519 -C "github-actions" -f /root/.ssh/deploy_key -N ""
cat /root/.ssh/deploy_key.pub >> /root/.ssh/authorized_keys
cat /root/.ssh/deploy_key   # скопировать в DEPLOY_KEY secret
```

## Commit Plan

Все 3 задачи — один коммит:  
`feat(ci): add GitHub Actions deploy pipeline and VPN diagnostic`

## Tasks

### Фаза 1 — Исправление update.sh

- [x] Задача 8: Исправить `deploy/update.sh`
  - `git pull origin main` → `git pull origin master`
  - Заменить `sleep 2 && systemctl status` на `systemctl is-active` с `exit 1` при падении
  - journalctl -n 30 при ошибке для диагностики в pipeline

### Фаза 2 — GitHub Actions

- [x] Задача 9: Создать `.github/workflows/deploy.yml` (depends on 8)
  - Trigger: push → master
  - `appleboy/ssh-action@v1`, SSH как root
  - `script_stop: true`, `timeout: 120s`
  - Secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_KEY`

### Фаза 3 — VPN диагностика

- [x] Задача 10: Создать `deploy/check-vpn.sh`
  - Проверяет активные VPN-интерфейсы (awg0/wg0)
  - Проверяет default route (должен идти через eth0/ens3, не через awg0)
  - curl к `api.telegram.org` от имени пользователя whatcher
  - Отчёт ✅/❌ по каждому пункту

<!-- Commit checkpoint: задачи 8–10 -->
