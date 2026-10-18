# Postgres 18 + pgvector. Built on the official postgres:18 image on purpose: the database files were
# created by it, and a base with another glibc (e.g. pgvector/pgvector, Debian 12) changes the text
# collation under existing indexes. The extension comes from the PostgreSQL apt repo already set up there.
FROM postgres:18
RUN apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-${PG_MAJOR}-pgvector" \
    && rm -rf /var/lib/apt/lists/*
