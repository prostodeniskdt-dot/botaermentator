FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir . \
    && python -c "import app.main; print('import_ok', app.main.app.title)"

COPY scripts/start.sh /start.sh
RUN chmod +x /start.sh && chown -R app:app /app /start.sh

USER app

EXPOSE 8080

# Timeweb waits for Docker HEALTHCHECK status "healthy" during deploy.
# Use curl + explicit IPv4 loopback; bind app on :: so localhost (IPv6) also works.
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/health >/dev/null || exit 1

CMD ["/start.sh"]
