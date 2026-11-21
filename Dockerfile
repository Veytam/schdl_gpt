FROM python:3.12-slim AS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev
RUN pip install --no-cache-dir --target /deps opentele==1.15.1 telethon==1.44.0 'psycopg[binary]==3.3.9'
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0t64 && rm -rf /var/lib/apt/lists/*
COPY --from=dependencies /deps /deps
COPY collector.py /app/collector.py
ENV PYTHONPATH=/deps PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/app/collector.py"]
