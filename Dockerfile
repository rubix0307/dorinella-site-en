FROM python:3.12-slim-bookworm

# Create a non-privileged user and group
RUN groupadd -g 10001 app && \
    useradd -u 10001 -g app -m -s /bin/bash app

# pg_dump of the daily backup (app/backup.py) must be as new as the server (PostgreSQL 18): from the PostgreSQL apt repo
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

# Ensure correct permissions for the application folder
RUN chown app:app /app

# Install dependencies using uv AS ROOT first, so it can write to /usr/local
COPY pyproject.toml ./
RUN uv pip install -r pyproject.toml


# Switch to non-root user for security
USER app

# Copy codebase and change owner to the non-root user
COPY --chown=app:app . .

# Expose app port
EXPOSE 8000

# Apply database migrations, then start the FastAPI app with Uvicorn
# (trust X-Forwarded-* from the reverse proxy so client IPs and https are right)
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 3 --proxy-headers --forwarded-allow-ips '*'"]
