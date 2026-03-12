# ════════════════════════════════════════════════════════════════
# Orbit — Multi-stage Production Dockerfile
# Stage 1: Build Python dependencies
# Stage 2: Lean runtime image
# ════════════════════════════════════════════════════════════════

# ── Stage 1: Builder ───────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# ── Stage 2: Runtime ───────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN groupadd -r orbit && useradd -r -g orbit orbit

WORKDIR /app

# Runtime system dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages from wheels built in Stage 1
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* \
 && rm -rf /wheels

# Copy application source
COPY . .

# Make entrypoint executable
RUN chmod +x scripts/entrypoint.sh scripts/healthcheck.sh

# Set ownership
RUN chown -R orbit:orbit /app

USER orbit

# Expose app port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD /app/scripts/healthcheck.sh

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["web"]
