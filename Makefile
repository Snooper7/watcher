.PHONY: help install run test logs status restart stop setup update

# Default target
help:
	@echo "Whatcher — Telegram price monitoring bot"
	@echo ""
	@echo "Development:"
	@echo "  make install   Install Python dependencies and Playwright browser"
	@echo "  make run       Start the bot locally"
	@echo "  make test      Run test suite"
	@echo ""
	@echo "VPS management (run on the server):"
	@echo "  make logs      Stream bot logs (journalctl)"
	@echo "  make status    Show service status"
	@echo "  make restart   Restart the bot service"
	@echo "  make stop      Stop the bot service"
	@echo ""
	@echo "Deployment:"
	@echo "  make setup     First-time VPS setup (run as root)"
	@echo "  make update    Pull latest code and restart (run as root)"

# ── Development ──────────────────────────────────────────────────────

install:
	pip install -r requirements.txt
	playwright install chromium --with-deps

run:
	python -m bot.main

test:
	pytest tests/ -v

# ── VPS management ───────────────────────────────────────────────────

logs:
	journalctl -u whatcher -f

status:
	systemctl status whatcher --no-pager

restart:
	systemctl restart whatcher

stop:
	systemctl stop whatcher

# ── Deployment ───────────────────────────────────────────────────────

setup:
	bash deploy/setup.sh

update:
	bash deploy/update.sh
