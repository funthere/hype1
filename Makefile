# HYPE Trading Bot Makefile
# Convenience commands for running and managing the trading bot

.PHONY: help install run-paper run-testnet run-mainnet dashboard clean kill-bot kill-dashboard log-paper log-testnet db-view db-backup test lint format

# Default port for API server
API_PORT ?= 8000
# Dashboard port
DASHBOARD_PORT ?= 8501

# Default target
help:
	@echo "HYPE Trading Bot Commands:"
	@echo ""
	@echo "  make install          - Install dependencies"
	@echo "  make run-paper        - Run paper trading bot (simulated)"
	@echo "  make run-testnet      - Run testnet bot (real orders, no real money)"
	@echo "  make run-mainnet      - Run mainnet bot (REAL MONEY - be careful!)"
	@echo "  make dashboard        - Run Streamlit dashboard"
	@echo ""
	@echo "  make kill-bot         - Kill any running bot instance"
	@echo "  make kill-dashboard   - Kill any running dashboard"
	@echo "  make clean            - Clean generated files"
	@echo ""
	@echo "  make log-paper        - Tail paper trading logs"
	@echo "  make log-testnet      - Tail testnet logs"
	@echo ""
	@echo "  make db-view          - View database contents"
	@echo "  make db-backup        - Backup database to timestamped file"
	@echo ""
	@echo "  make test             - Run tests"
	@echo "  make lint             - Run linter"
	@echo "  make format           - Format code with black"
	@echo ""
	@echo "Options:"
	@echo "  API_PORT=$(API_PORT)    # API server port (default: 8000)"
	@echo "  DASHBOARD_PORT=$(DASHBOARD_PORT)  # Dashboard port (default: 8501)"

# Install dependencies
install:
	@echo "Installing dependencies..."
	pip install -r requirements.txt
	@echo "✓ Dependencies installed"

# Kill any process using the API port
kill-port:
	@pid=$$(lsof -ti :$(API_PORT) 2>/dev/null); \
	if [ -n "$$pid" ]; then \
		echo "Killing process $$pid using port $(API_PORT)..."; \
		kill -9 $$pid 2>/dev/null || true; \
		sleep 1; \
	else \
		echo "Port $(API_PORT) is free"; \
	fi

# Kill dashboard port
kill-dashboard-port:
	@pid=$$(lsof -ti :$(DASHBOARD_PORT) 2>/dev/null); \
	if [ -n "$$pid" ]; then \
		echo "Killing dashboard process $$pid using port $(DASHBOARD_PORT)..."; \
		kill -9 $$pid 2>/dev/null || true; \
		sleep 1; \
	else \
		echo "Port $(DASHBOARD_PORT) is free"; \
	fi

# Run paper trading bot
run-paper: kill-port
	@echo "Starting paper trading bot..."
	python3 run_paper_bot.py

# Run testnet bot
run-testnet: kill-port
	@echo "Starting testnet bot..."
	@if [ -f .env ]; then \
		python3 run_testnet_bot.py; \
	else \
		echo "❌ Error: .env file required for testnet trading"; \
		echo "   Copy .env.example to .env and fill in your credentials"; \
		exit 1; \
	fi

# Run mainnet bot (REAL MONEY!)
run-mainnet: kill-port
	@echo "⚠️  WARNING: Starting MAINNET bot with REAL MONEY!"
	@echo "Press Ctrl+C within 5 seconds to cancel..."
	@sleep 5
	@if [ -f .env ]; then \
		python3 run_mainnet_bot.py; \
	else \
		echo "❌ Error: .env file required for mainnet trading"; \
		echo "   Copy .env.example to .env and fill in your credentials"; \
		exit 1; \
	fi

# Run dashboard
dashboard: kill-dashboard-port
	@echo "Starting dashboard on http://localhost:$(DASHBOARD_PORT)..."
	@streamlit run hype_dashboard.py --server.port $(DASHBOARD_PORT)

# Kill bot
kill-bot:
	@echo "Killing running bot..."
	@pkill -f "run_paper_bot.py" 2>/dev/null || echo "No paper bot process found"
	@pkill -f "run_testnet_bot.py" 2>/dev/null || echo "No testnet bot process found"
	@pkill -f "run_mainnet_bot.py" 2>/dev/null || echo "No mainnet bot process found"
	@make kill-port
	@echo "✓ Bot stopped"

# Kill dashboard
kill-dashboard:
	@echo "Killing dashboard..."
	@pkill -f "streamlit run hype_dashboard" 2>/dev/null || echo "No dashboard process found"
	@make kill-dashboard-port
	@echo "✓ Dashboard stopped"

# Clean generated files
clean:
	@echo "Cleaning generated files..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@find . -type f -name "*.coverage" -delete 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf htmlcov/ .coverage 2>/dev/null || true
	@echo "✓ Cleaned"

# View paper trading logs
log-paper:
	@echo "Tailing paper trading logs (Ctrl+C to exit)..."
	@if [ -f hype_paper_bot.log ]; then \
		tail -f hype_paper_bot.log; \
	else \
		echo "Paper trading log file not found (hype_paper_bot.log)"; \
	fi

# View testnet logs
log-testnet:
	@echo "Tailing testnet logs (Ctrl+C to exit)..."
	@if [ -f hype_testnet_bot.log ]; then \
		tail -f hype_testnet_bot.log; \
	else \
		echo "Testnet log file not found (hype_testnet_bot.log)"; \
	fi

# View database
db-view:
	@echo "Opening database viewer..."
	@if [ -f trading_bot.db ]; then \
		sqlite3 trading_bot.db "SELECT * FROM positions ORDER BY entry_time DESC LIMIT 10;"; \
	else \
		echo "Database not found at trading_bot.db"; \
	fi

# Backup database
db-backup:
	@if [ -f trading_bot.db ]; then \
		BACKUP="trading_bot_backup_$$(date +%Y%m%d_%H%M%S).db"; \
		cp trading_bot.db "$$BACKUP"; \
		echo "✓ Database backed up to $$BACKUP"; \
	else \
		echo "Database not found at trading_bot.db"; \
	fi

# Run tests
test:
	@echo "Running tests..."
	@python3 -m pytest tests/ -v --cov=src --cov-report=html

# Run linter
lint:
	@echo "Running linter..."
	@ruff check src/ tests/ run_*.py bot_api_server.py hype_dashboard.py

# Format code
format:
	@echo "Formatting code..."
	@ruff format src/ tests/ run_*.py bot_api_server.py hype_dashboard.py
