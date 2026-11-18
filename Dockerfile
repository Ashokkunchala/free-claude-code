# Multi-stage Dockerfile for Free Claude Code
# Stage 1: Build
FROM python:3.14.7-slim AS builder

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies in editable mode
# We use the optional voice_local extra only if needed
RUN uv sync --frozen --no-dev --all-extras

# Copy source code
COPY src/ ./src/

# Build the package
RUN uv pip install --no-deps -e .

# Stage 2: Runtime
FROM python:3.14.7-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy built package from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy assets if needed (for Admin UI)
COPY assets/ /app/assets/

# Create non-root user
RUN useradd -m -u 1000 fcc && \
    chown -R fcc:fcc /app

USER fcc

# Expose ports
# 8000: Admin UI
# 8001: Proxy API
EXPOSE 8000 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the server
CMD ["fcc-server"]