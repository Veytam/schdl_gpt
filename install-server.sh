#!/bin/sh
set -eu
umask 077

target=${1:-}
case "$target" in
    -100[0-9]*) ;;
    *) echo "Usage: sudo ./install-server.sh -100<telegram-group-id>" >&2; exit 2 ;;
esac
case "${target#-}" in
    *[!0-9]*) echo "Telegram group ID must be numeric" >&2; exit 2 ;;
esac

test "$(id -u)" -eq 0
command -v openssl >/dev/null
docker info >/dev/null
systemctl is-active --quiet docker.service
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
destination=/opt/study-schedule
if test -e "$destination"; then
    echo "Installation already exists at $destination" >&2
    exit 1
fi

install -d -m 700 "$destination" "$destination/drive-config"
install -d -m 700 -o 10001 -g 10001 "$destination/session"
cd "$project_dir"
install -m 600 collector.py drive_export.py expire.sql Dockerfile .dockerignore "$destination/"
install -m 700 start-collector telegram-login drive-login "$destination/"
install -m 755 init-db.sh "$destination/"
cd "$destination"

admin_password=$(openssl rand -hex 32)
collector_password=$(openssl rand -hex 32)
printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_DB=schedule\nCOLLECTOR_PASSWORD=%s\n' \
    "$admin_password" "$collector_password" > postgres.env
printf 'POSTGRES_PASSWORD=%s\nTARGET_CHAT_ID=%s\n' "$collector_password" "$target" > collector.env

docker build -t study-schedule-collector:local .
docker pull postgres:17-bookworm
docker pull rclone/rclone:1.76.0
docker tag rclone/rclone:1.76.0 study-schedule-drive:1.76.0
docker network create study-schedule
docker volume create study-schedule-pgdata
docker run -d --name study-schedule-db --restart unless-stopped \
    --network study-schedule --env-file postgres.env \
    --mount type=volume,src=study-schedule-pgdata,dst=/var/lib/postgresql/data \
    --mount type=bind,src="$destination/init-db.sh",dst=/docker-entrypoint-initdb.d/10-roles.sh,readonly \
    --log-opt max-size=5m --log-opt max-file=2 postgres:17-bookworm -c shared_buffers=64MB

cd "$project_dir"
install -m 644 study-schedule-*.service study-schedule-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now study-schedule-expire.timer
echo "PostgreSQL provisioned. Authorize Telegram using $destination/telegram-login."
