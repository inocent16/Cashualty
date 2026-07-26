FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY cashualty ./cashualty
COPY migrations ./migrations

RUN pip install --no-cache-dir .

COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
