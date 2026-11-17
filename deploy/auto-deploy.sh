#!/usr/bin/env bash
# Deploy origin/main to this VM whenever it moves. Run from cron every five
# minutes — see "Automatic deploys" in DEPLOY.md for the crontab line, which
# runs the copy of this file on origin/main, so a change to it is picked up by
# the next tick without anyone logging in.
#
# Pull, not push: the VM fetches a public repo, so no deploy key or SSH secret
# lives on GitHub, and the NSG may keep port 22 closed to everyone but the
# maintainer.
#
# What it guarantees:
# - One run at a time (flock). A build that outlasts five minutes is not
#   joined by a second one.
# - Only fast-forwards, and never over tracked files edited on the VM.
# - Build before restart: a failed build leaves the running container alone.
# - A new container that is not healthy within HEALTH_WAIT_S is rolled back to
#   the previous commit.
# - A commit that failed is not retried every five minutes; the next commit on
#   main is tried as normal.
# - Nothing deploys while the nightly seed runs (02:00 UTC, bounded at 30 min
#   by SEED_TIME_BUDGET_S): a restart would kill it half-way.
#
# Everything is wrapped in main() and the script only runs once bash has read
# the whole file, so `git merge` replacing this file mid-run cannot change
# what is executing.

set -uo pipefail

main() {
  local repo="${THERMALSENSE_REPO:-$HOME/thermalsense}"
  local state="${THERMALSENSE_DEPLOY_STATE:-$HOME/.thermalsense-deploy}"
  local health_wait="${HEALTH_WAIT_S:-300}"
  mkdir -p "$state"

  # Keep the log bounded: trim to its last 256 KB once it passes 1 MB.
  local logf="$state/deploy.log"
  if [ -f "$logf" ] && [ "$(stat -c %s "$logf")" -gt 1048576 ]; then
    tail -c 262144 "$logf" > "$logf.tmp" && mv "$logf.tmp" "$logf"
  fi
  exec >>"$logf" 2>&1 </dev/null

  exec 9>"$state/lock"
  flock -n 9 || return 0

  log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

  # 01:55–02:40 UTC: the nightly seed's window, with margin.
  local hm; hm=$(date -u +%H%M)
  if [ "$hm" -ge 0155 ] && [ "$hm" -lt 0240 ]; then return 0; fi

  cd "$repo" || { log "no repo at $repo"; return 1; }
  git fetch -q origin main || { log "git fetch failed"; return 1; }

  local old new
  old=$(git rev-parse HEAD)
  new=$(git rev-parse origin/main)
  [ "$old" = "$new" ] && return 0
  [ "$(cat "$state/failed" 2>/dev/null)" = "$new" ] && return 0

  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    log "tracked files are modified on the VM; not deploying ${new:0:7}"
    git status --short --untracked-files=no
    echo "$new" > "$state/failed"
    return 1
  fi
  if ! git merge-base --is-ancestor "$old" "$new"; then
    log "origin/main ${new:0:7} is not a fast-forward of ${old:0:7}; not deploying"
    echo "$new" > "$state/failed"
    return 1
  fi

  log "deploying ${old:0:7} -> ${new:0:7}: $(git log -1 --format=%s "$new")"
  git merge -q --ff-only "$new" || { log "fast-forward failed"; return 1; }

  if ! docker compose build app; then
    log "build failed; staying on ${old:0:7}, the running container is untouched"
    git reset -q --hard "$old"
    echo "$new" > "$state/failed"
    return 1
  fi

  docker compose up -d
  if ! git diff --quiet "$old" "$new" -- Caddyfile; then
    # A bind-mounted single file keeps its old inode across git's
    # rename-over; reload would re-read the stale copy. See DEPLOY.md.
    log "Caddyfile changed; recreating caddy"
    docker compose up -d --force-recreate caddy
  fi

  if wait_healthy "$health_wait"; then
    log "deployed ${new:0:7}; app healthy"
    rm -f "$state/failed"
    docker image prune -f >/dev/null
    docker builder prune -f --filter until=168h >/dev/null
    return 0
  fi

  log "app not healthy after ${health_wait}s; rolling back to ${old:0:7}"
  docker compose logs --tail 60 app
  git reset -q --hard "$old"
  docker compose build app && docker compose up -d
  if ! git diff --quiet "$old" "$new" -- Caddyfile; then
    docker compose up -d --force-recreate caddy
  fi
  echo "$new" > "$state/failed"
  if wait_healthy "$health_wait"; then
    log "rolled back to ${old:0:7}; app healthy"
  else
    log "ROLLBACK ALSO UNHEALTHY; needs a human"
  fi
  return 1
}

wait_healthy() {
  local deadline=$((SECONDS + $1)) id status
  while [ "$SECONDS" -lt "$deadline" ]; do
    id=$(docker compose ps -q app)
    status=$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null)
    [ "$status" = "healthy" ] && return 0
    sleep 10
  done
  return 1
}

main "$@"
exit $?
