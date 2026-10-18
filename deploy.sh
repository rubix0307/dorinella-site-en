#!/usr/bin/env bash
# Deploys origin/main on the server: run by GitHub Actions over SSH (or by hand: ./deploy.sh).
# Blue-green, no downtime: the new version starts in the idle color (web_blue :8001 /
# web_green :8002) while the live one keeps serving; only once it is healthy nginx is pointed
# at it, then the old color is stopped. If it never gets healthy, nginx is never touched.
#
#   ./deploy.sh            deploy origin/main
#   ./deploy.sh rollback   bring the previous color (stopped, with its old image) back
#
# Migrations run before the new color starts while the old one is still serving: keep them
# backwards compatible (the old code must work with the new schema; a rollback doesn't undo them).
#
# Everything is inside main(): bash reads the whole function before running it, so the
# `git reset` below may safely replace this very file.
set -euo pipefail

UPSTREAM=/etc/nginx/conf.d/dorinella-upstream.conf
COMPOSE="docker compose -f docker-compose.prod.yml"
DRAIN=10   # seconds the old color keeps finishing its requests after the switch

main() {
    cd "$(dirname "$(readlink -f "$0")")"
    local live idle
    live="$(live_color)"
    idle="$(other "$live")"

    if [ "${1:-}" = rollback ]; then
        rollback "$live" "$idle"
        return
    fi

    local branch="${DEPLOY_BRANCH:-main}" previous
    previous="$(git rev-parse HEAD)"
    git fetch --quiet origin "$branch"
    git reset --quiet --hard "origin/$branch"      # the server copy is never edited by hand
    sync_bot_db_url
    echo "deploying $(git log -1 --format='%h %s') to $idle (live: $live)"
    export SENTRY_RELEASE="dorinella@$(git rev-parse --short HEAD)"   # errors in Sentry name the deploy

    # the database lives in volumes/postgres: never let Postgres init an empty one over a lost mount
    if [ ! -d volumes/postgres/18 ] && docker volume inspect dorinella_pgdata >/dev/null 2>&1; then
        echo "volumes/postgres is empty but the old dorinella_pgdata volume exists — copy it first" >&2
        git reset --quiet --hard "$previous"
        return 1
    fi

    bake_lastmod
    $COMPOSE build --quiet "web_$idle"
    $COMPOSE up -d --wait db
    if ! $COMPOSE run --rm --no-deps "web_$idle" alembic upgrade head; then
        echo "migrations failed — $live keeps serving" >&2
        git reset --quiet --hard "$previous"
        return 1
    fi
    install -d -o 10001 -g 10001 volumes/models    # the model's home; the container runs as uid 10001
    install -d -m 0777 volumes/telegram            # the files of Telegram posts (without a bucket): mounted into every web color and the sender
    # the embedder service (always on, the only container with the model); it is recreated with the new image and loads the model lazily
    if grep -qs '^EMBEDDINGS_TOKEN=.' .env; then
        $COMPOSE up -d --no-build --wait embedder || echo "embedder did not start — recommendations use the fallback" >&2
    else   # the embedder refuses to start without a token (an open one lets any container starve the nightly job)
        echo "EMBEDDINGS_TOKEN is empty in .env: the embedder is NOT started — set it (python -c 'import secrets; print(secrets.token_urlsafe(32))') and redeploy" >&2
    fi
    # recommendations: embed new / edited recipes and articles (a one-off web container: it only asks the embedder over HTTP);
    # not fatal — without fresh vectors the recipe pages fall back to same-category suggestions
    $COMPOSE run --rm --no-deps -e EMBEDDINGS_RETRIES=2 "web_$idle" timeout 600 sh -c 'python -m app.cli embed-recipes; r=$?; python -m app.cli embed-articles || r=$?; exit $r' \
        || echo "embed-recipes failed — recommendations use the fallback" >&2
    $COMPOSE up -d --no-build --force-recreate "web_$idle"
    start_calls
    start_backup
    start_telegram

    if ! wait_healthy "$idle"; then
        echo "$idle is NOT healthy — $live keeps serving, nothing switched" >&2
        $COMPOSE logs --tail 40 "web_$idle" >&2 || true
        $COMPOSE stop "web_$idle" >/dev/null
        git reset --quiet --hard "$previous"
        return 1
    fi

    switch_to "$idle"
    retire "$live"
    docker image prune -f >/dev/null   # images of stopped containers stay (that's the rollback)
    docker builder prune -af >/dev/null 2>&1 || true   # the build cache refills the small disk on every deploy
    echo "deployed: $idle is live"
}

sync_bot_db_url() {   # BOT_DATABASE_URL of the site is derived from the .env of the bot (the read-only role dori_ro, scripts/bot_db_roles.sql): one place for the password
    local bot_env="${BOT_ENV_FILE:-/root/bot2/.env}" password database url
    [ -r "$bot_env" ] || return 0
    password="$(sed -n 's/^DORI_RO_PASSWORD=//p' "$bot_env" | tail -n 1)"
    database="$(sed -n 's/^POSTGRES_DB=//p' "$bot_env" | tail -n 1)"
    [ -n "$password" ] && [ -n "$database" ] || return 0
    url="postgresql+asyncpg://dori_ro:${password}@dori-bot-db:5432/${database}"
    if grep -qs '^BOT_DATABASE_URL=' .env; then
        awk -v url="BOT_DATABASE_URL=$url" '/^BOT_DATABASE_URL=/ { print url; next } { print }' .env > .env.tmp && cat .env.tmp > .env && rm -f .env.tmp
    else
        printf '
BOT_DATABASE_URL=%s
' "$url" >> .env
    fi
}

bake_lastmod() {   # the sitemap's lastmod dates: the image has no git, so they are written to app/lastmod.json before the build; never fatal (without them the sitemap just has no lastmod)
    rm -f app/lastmod.json      # never leave the dates of an older deploy behind
    command -v python3 >/dev/null 2>&1 || { echo "lastmod: python3 not found — the sitemap has no lastmod for pages written in code" >&2; return 0; }
    python3 scripts/bake_lastmod.py || echo "lastmod: could not read the git history — the sitemap has no lastmod for pages written in code" >&2
}

start_calls() {   # video calls: only when the server has the keys (see .env.example); a failure here never stops a deploy
    grep -qs '^LIVEKIT_API_KEY=.' .env || return 0
    install -d -m 0777 volumes/recordings
    $COMPOSE --profile meet up -d --no-build livekit-redis livekit livekit-egress         || echo "calls: LiveKit did not start — the site is deployed anyway" >&2
    $COMPOSE --profile meet up -d --no-build --force-recreate meet-worker         || echo "calls: meet-worker did not start — the site is deployed anyway" >&2
    install -d -m 0777 volumes/models volumes/models/whisper
    $COMPOSE --profile meet up -d --no-build --force-recreate meet-transcriber    || echo "calls: meet-transcriber did not start — the site is deployed anyway" >&2
}

start_backup() {   # the daily database backup: only when the server has a bucket for it (see .env.example)
    grep -qs '^BACKUP_S3_BUCKET=.' .env || return 0
    $COMPOSE --profile backup up -d --no-build --force-recreate backup || echo "backup: the worker did not start — the site is deployed anyway" >&2
}

start_telegram() {   # the sender of the Telegram posts: only when the server has the token of the bot (see .env.example)
    grep -qs '^TELEGRAM_BOT_TOKEN=.' .env || return 0
    install -d -m 0777 volumes/telegram
    $COMPOSE --profile telegram up -d --no-build --force-recreate telegram-sender || echo "telegram: the sender did not start — the site is deployed anyway" >&2
    # the moderator of the chats needs the bot's database and the key of the moderation service as well
    if grep -qs '^BOT_DATABASE_URL=.' .env && grep -qs '^OPENAI_API_KEY=.' .env; then
        $COMPOSE --profile telegram up -d --no-build --force-recreate telegram-moderator || echo "telegram: the moderator did not start — the site is deployed anyway" >&2
    fi
}

rollback() {   # the stopped color still has its container and image from the previous deploy
    local live="$1" back="$2"
    echo "rolling back: $live -> $back"
    $COMPOSE start "web_$back"
    wait_healthy "$back" || { echo "$back did not come up — $live keeps serving" >&2; return 1; }
    switch_to "$back"
    retire "$live"
    echo "rolled back: $back is live"
}

live_color() {   # the color nginx points at; before the first blue-green deploy: the old single "web" on :8001
    if grep -qs '127.0.0.1:8002' "$UPSTREAM"; then echo green; else echo blue; fi
}

other() { [ "$1" = blue ] && echo green || echo blue; }

port() { [ "$1" = blue ] && echo 8001 || echo 8002; }

switch_to() {   # nginx reload is graceful: requests in flight finish on the old upstream
    local color="$1" backup
    backup="$(mktemp)"
    cp "$UPSTREAM" "$backup" 2>/dev/null || true
    printf '# written by /root/dorinella/deploy.sh — the live color (%s)\nupstream dorinella_web {\n    server 127.0.0.1:%s;\n}\n' \
        "$color" "$(port "$color")" > "$UPSTREAM"
    if ! nginx -t -q; then
        cp "$backup" "$UPSTREAM"
        echo "nginx config test failed — not switched" >&2
        return 1
    fi
    nginx -s reload
    rm -f "$backup"
}

retire() {   # stop (not remove) the old color: its container is the rollback target
    local color="$1"
    sleep "$DRAIN"
    $COMPOSE stop "web_$color" >/dev/null 2>&1 || true
    # the single "web" service from before blue-green, if it is still around
    docker ps -aq --filter label=com.docker.compose.project=dorinella \
        --filter label=com.docker.compose.service=web | xargs -r docker rm -f >/dev/null
}

wait_healthy() {   # up to 2 minutes for the color's healthcheck (it checks the database too)
    local color="$1" id status
    for _ in $(seq 1 60); do
        id="$($COMPOSE ps -q "web_$color")"
        status="$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo starting)"
        [ "$status" = healthy ] && return 0
        [ "$status" = unhealthy ] && return 1
        sleep 2
    done
    return 1
}

main "$@"
