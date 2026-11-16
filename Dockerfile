# Pin the multi-platform image index; Dependabot tracks digest updates.
FROM node:22-alpine AS ckeditor

WORKDIR /build
RUN npm install --prefix /tmp/ckeditor5 --no-save --no-package-lock ckeditor5@49.1.0 \\
    && mkdir -p /assets \\
    && cp /tmp/ckeditor5/node_modules/ckeditor5/dist/ckeditor5.umd.js /assets/ \\
    && cp /tmp/ckeditor5/node_modules/ckeditor5/dist/ckeditor5.css /assets/ \\
    && cp /tmp/ckeditor5/node_modules/ckeditor5/LICENSE.md /assets/LICENSE.md

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=ckeditor /assets/ckeditor5.umd.js /app/static/vendor/ckeditor5/49.1.0/ckeditor5.umd.js
COPY --from=ckeditor /assets/ckeditor5.css /app/static/vendor/ckeditor5/49.1.0/ckeditor5.css
COPY --from=ckeditor /assets/LICENSE.md /app/static/vendor/ckeditor5/49.1.0/LICENSE.md

RUN groupadd --gid 10001 opensigweave \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin opensigweave \
    && mkdir -p /data/signatures /app/.idp \
    && chown -R 10001:10001 /app /data

USER 10001:10001

EXPOSE 8085

# Apply database migrations before starting the web process.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8085"]
