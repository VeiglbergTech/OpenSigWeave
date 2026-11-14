FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd --gid 10001 opensigweave \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin opensigweave \
    && mkdir -p /data/signatures /app/.idp \
    && chown -R 10001:10001 /app /data

USER 10001:10001

EXPOSE 8085

# Apply database migrations before starting the web process.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8085"]
