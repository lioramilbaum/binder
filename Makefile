PYTHON := python3
RUN_DIR := .run
PID_FILE := $(RUN_DIR)/binder.pid
LOG_FILE := $(RUN_DIR)/binder.log

.PHONY: up down restart db-up db-down migrate backup

# Number of timestamped backups to keep in .private-backups/.
KEEP ?= 30

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

# Dump the database to a timestamped file. Written to a temporary name first so
# a failed dump never leaves a truncated backup behind.
backup:
	@mkdir -p .private-backups && chmod 700 .private-backups
	@file=.private-backups/binder-$$(date +%Y%m%d-%H%M%S).dump; \
	if docker compose exec -T postgres sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -Fc' > "$$file.tmp" && [ -s "$$file.tmp" ]; then \
	  chmod 600 "$$file.tmp" && mv "$$file.tmp" "$$file" && echo "Backup written to $$file ($$(wc -c < "$$file" | tr -d ' ') bytes)"; \
	else \
	  rm -f "$$file.tmp"; echo "Backup failed. Is the database running? Try: make db-up" >&2; exit 1; \
	fi
	@ls -1t .private-backups/binder-*.dump 2>/dev/null | tail -n +$$(($(KEEP) + 1)) | while read old; do rm -f "$$old" && echo "Removed old backup $$old"; done
