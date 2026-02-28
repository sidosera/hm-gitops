#!/usr/bin/env bash
set -euo pipefail

cd /srv/hackamonth

git fetch --prune origin
git reset --hard origin/main

docker compose pull
docker compose up -d --remove-orphans
