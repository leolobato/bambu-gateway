# syntax=docker/dockerfile:1

# --- Stage 1: Build the React frontend ---
FROM node:20-alpine AS web-builder

WORKDIR /web

# Copy lockfile first for better layer caching
COPY web/package.json web/package-lock.json ./
RUN npm ci

# Copy the rest of the frontend sources and build
COPY web/ ./
RUN npm run build
# npm run build writes to /web/../app/static/dist per vite.config.ts,
# so the output lands at /app/static/dist inside the build container.


# --- Stage 2: Python runtime ---
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

# Overlay the frontend build output from stage 1
COPY --from=web-builder /app/static/dist /app/app/static/dist

VOLUME /data

EXPOSE 4844

CMD ["python", "-m", "app", "-c", "/data/printers.json"]
