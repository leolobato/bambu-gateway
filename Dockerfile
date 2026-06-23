# syntax=docker/dockerfile:1

# ------------------------------------------------------------------
# Stage 1: build the Bambu cloud subprocess host (C++ binary).
# ------------------------------------------------------------------
FROM debian:bookworm-slim AS cloud_host_builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential cmake ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY tools/bambu_cloud_host /src
RUN cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /build --parallel


# --- Stage 2: Build the React frontend ---
FROM node:20-alpine AS web-builder

WORKDIR /web

# Copy lockfile first for better layer caching
COPY web/package.json web/package-lock.json ./
# `npm install` instead of `npm ci`: the lockfile records optional
# platform-specific binaries (e.g. esbuild's per-OS native packages)
# that npm 10's `ci` mode rejects with EBADPLATFORM on Linux x64
# even though they're optional. `install` honours the lockfile but
# tolerates the cross-platform optional entries.
RUN npm install --no-audit --prefer-offline

# Copy the rest of the frontend sources and build
COPY web/ ./
RUN npm run build
# npm run build writes to /web/../app/static/dist per vite.config.ts,
# so the output lands at /app/static/dist inside the build container.


# --- Stage 3: Python runtime ---
FROM python:3.13-slim

COPY --from=cloud_host_builder /build/bambu_cloud_host /usr/local/bin/bambu_cloud_host

# Trust BambuLab's printer CA (CN=BBL CA2 RSA) so the network plugin can verify
# a printer's LOCAL MQTT TLS certificate — it's signed by BBL's private CA,
# which isn't in any public trust store. Appended to the system bundle that the
# plugin's set_cert_file points at (/etc/ssl/certs/ca-certificates.crt), so both
# the cloud (public CA) and local (BBL CA) MQTT connections verify. Sourced from
# OrcaSlicer's resources/cert/printer.cer.
COPY tools/bambu_cloud_host/certs/bbl_printer_ca.cer /usr/local/share/ca-certificates/bbl_printer_ca.pem
RUN cat /usr/local/share/ca-certificates/bbl_printer_ca.pem >> /etc/ssl/certs/ca-certificates.crt

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

# Overlay the frontend build output from stage 1
COPY --from=web-builder /app/static/dist /app/app/static/dist

# Optional: stamp the git SHA at build time so it shows in the startup log
# (`--build-arg GATEWAY_GIT_SHA=$(git rev-parse --short HEAD)`). When unset,
# the log still carries a runtime code fingerprint, so the version is always
# identifiable. Last so it never busts the dependency-install cache layer.
ARG GATEWAY_GIT_SHA=""
ENV GATEWAY_GIT_SHA=${GATEWAY_GIT_SHA}

VOLUME /data

EXPOSE 4844

CMD ["python", "-m", "app", "-c", "/data/printers.json"]
