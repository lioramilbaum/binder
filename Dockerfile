FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

ENV BINDER_STATIC_DIR=/app/static

EXPOSE 8000
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
