PYTHON := python3
RUN_DIR := .run
PID_FILE := $(RUN_DIR)/binder.pid
LOG_FILE := $(RUN_DIR)/binder.log

.PHONY: up down restart db-up db-down migrate

up:
	@mkdir -p $(RUN_DIR)
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "Binder is already running (PID $$(cat $(PID_FILE)))."; \
	else \
		nohup $(PYTHON) -m binder.app > $(LOG_FILE) 2>&1 & \
		echo $$! > $(PID_FILE); \
		sleep 0.3; \
		echo "Binder started (PID $$(cat $(PID_FILE))). Logs: $(LOG_FILE)"; \
	fi

down:
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		kill $$(cat $(PID_FILE)); \
		rm -f $(PID_FILE); \
		echo "Binder stopped."; \
	else \
		echo "Binder is not running."; \
		rm -f $(PID_FILE); \
	fi

restart: down up

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	alembic upgrade head
