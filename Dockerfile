FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# zaleznosci osobno, zeby korzystac z cache warstw
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY templates/ ./templates/
COPY scripts/ ./scripts/
COPY db/ ./db/

# nie-root + katalog na dane.
# WAZNE: Docker inicjuje nowy nazwany wolumen uprawnieniami katalogu z obrazu,
# wiec /data musi istniec i nalezec do appuser JUZ w obrazie - inaczej panel
# nie ma prawa zapisu i wysylanie zdjec konczy sie bledem 500.
RUN useradd -m appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8080
CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8080"]
