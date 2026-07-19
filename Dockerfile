FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir . \
    && python -c "import app.main; print('import_ok', app.main.app.title)"

RUN chown -R app:app /app
USER app

EXPOSE 8080

# Use python -m uvicorn so PATH/scripts quirks cannot block startup.
# Module path is app.main:app (not main:app / botpodergun.main:app).
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
