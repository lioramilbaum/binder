.PHONY: up down restart backup

up:
	docker compose up -d --build --wait

down:
	docker compose down

restart: down up

# Dump the database to a timestamped file. Written to a temporary name first so
# a failed dump never leaves a truncated backup behind. On success, all previous
# backups are deleted so only the latest survives.
backup:
	@mkdir -p .private-backups && chmod 700 .private-backups
	@file=.private-backups/binder-$$(date +%Y%m%d-%H%M%S).dump; \
	if docker compose exec -T postgres sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -Fc' > "$$file.tmp" && [ -s "$$file.tmp" ]; then \
	  chmod 600 "$$file.tmp" && mv "$$file.tmp" "$$file" && echo "Backup written to $$file ($$(wc -c < "$$file" | tr -d ' ') bytes)"; \
	  ls -1 .private-backups/binder-*.dump 2>/dev/null | grep -v "$$file" | while read old; do rm -f "$$old" && echo "Removed old backup $$old"; done; \
	else \
	  rm -f "$$file.tmp"; echo "Backup failed. Is the database running? Try: make up" >&2; exit 1; \
	fi
