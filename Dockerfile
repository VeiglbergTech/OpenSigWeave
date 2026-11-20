# Pin the CKEditor build stage to an exact Node/Alpine version; Dependabot tracks updates.
FROM node:22.23.5-alpine3.24 AS ckeditor

WORKDIR /build
COPY static/js/ckeditor-entry.js ./ckeditor-entry.js
RUN npm install --prefix /tmp/ckeditor5 --no-save --no-package-lock ckeditor5@49.1.0 esbuild@0.24.2 \
    && mkdir -p /assets \
    && ln -s /tmp/ckeditor5/node_modules /build/node_modules \
    && /tmp/ckeditor5/node_modules/.bin/esbuild /build/ckeditor-entry.js --bundle --format=iife --outfile=/assets/ckeditor5.js \
    && cp /tmp/ckeditor5/node_modules/ckeditor5/LICENSE.md /assets/LICENSE.md

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Apply security updates available for the pinned Debian base image.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=ckeditor /assets/ckeditor5.js /app/static/vendor/ckeditor5/49.1.0/ckeditor5.js
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
