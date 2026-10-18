#!/usr/bin/env bash
# Deploys origin/main on the server, by hand: ssh root@<server> 'cd ~/lolakrissss && ./deploy.sh'
# Blue-green, no downtime: the new version starts in the idle color (web_blue :8003 / web_green :8004) while the live
# one keeps serving; only once it is healthy nginx is pointed at it, then the old color is stopped. If it never gets
# healthy, nginx is never touched. Never run two deploys at once.
#
#   ./deploy.sh            deploy origin/main
#   ./deploy.sh rollback   bring the previous color (stopped, with its old image) back
#
# Migrations run before the new color starts while the old one is still serving: keep them backwards compatible
# (the old code must work with the new schema; a rollback doesn't undo them).
#
# Everything is inside main(): bash reads the whole function before running it, so the `git reset` below may
# safely replace this very file.
set -euo pipefail

UPSTREAM=/etc/nginx/conf.d/lolakrissss-upstream.conf
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

    check_env
    local branch="${DEPLOY_BRANCH:-main}" previous
    previous="$(git rev-parse HEAD)"
    git fetch --quiet origin "$branch"
    git reset --quiet --hard "origin/$branch"      # the server copy is never edited by hand
    echo "deploying $(git log -1 --format='%h %s') to $idle (live: $live)"
    export SENTRY_RELEASE="lolakrissss@$(git rev-parse --short HEAD)"   # errors in Sentry name the deploy

    bake_lastmod
    $COMPOSE build --quiet "web_$idle"
    $COMPOSE up -d --wait db
    if ! $COMPOSE run --rm --no-deps "web_$idle" alembic upgrade head; then
        echo "migrations failed — $live keeps serving" >&2
        git reset --quiet --hard "$previous"
        return 1
    fi
    $COMPOSE up -d --no-build --force-recreate "web_$idle"
    start_backup

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

check_env() {   # a missing or weak .env otherwise shows up only as a container that exits at import and a 2-minute "NOT healthy"
    [ -f .env ] || { echo ".env is missing: copy .env.example and fill it in (docs/ops/Deploy.md)" >&2; return 1; }
    local key
    key="$(sed -n 's/^SECRET_KEY=//p' .env | tail -n 1)"
    [ "${#key}" -ge 32 ] || { echo "SECRET_KEY in .env must be at least 32 characters (python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" >&2; return 1; }
    grep -q '^POSTGRES_PASSWORD=.\+' .env || { echo "POSTGRES_PASSWORD in .env is empty" >&2; return 1; }
    { grep -q '^GOOGLE_CLIENT_ID=.\+' .env && grep -q '^GOOGLE_CLIENT_SECRET=.\+' .env; } \
        || echo "warning: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set in .env: nobody can sign in and the Sign in links are hidden" >&2
}

bake_lastmod() {   # the sitemap's lastmod dates: the image has no git, so they are written to app/lastmod.json before the build; never fatal
    rm -f app/lastmod.json      # never leave the dates of an older deploy behind
    command -v python3 >/dev/null 2>&1 || { echo "lastmod: python3 not found — the sitemap has no lastmod for pages written in code" >&2; return 0; }
    python3 scripts/bake_lastmod.py || echo "lastmod: could not read the git history — the sitemap has no lastmod for pages written in code" >&2
}

start_backup() {   # the daily database backup: only when the server has a bucket for it (see .env.example)
    grep -qs '^BACKUP_S3_BUCKET=.' .env || return 0
    $COMPOSE --profile backup up -d --no-build --force-recreate backup || echo "backup: the worker did not start — the site is deployed anyway" >&2
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

live_color() {   # the color nginx points at; the very first deploy: blue
    if grep -qs '127.0.0.1:8004' "$UPSTREAM"; then echo green; else echo blue; fi
}

other() { [ "$1" = blue ] && echo green || echo blue; }

port() { [ "$1" = blue ] && echo 8003 || echo 8004; }

switch_to() {   # nginx reload is graceful: requests in flight finish on the old upstream
    local color="$1" backup
    backup="$(mktemp)"
    cp "$UPSTREAM" "$backup" 2>/dev/null || true
    printf '# written by /root/lolakrissss/deploy.sh — the live color (%s)\nupstream lolakrissss_web {\n    server 127.0.0.1:%s;\n}\n' \
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
