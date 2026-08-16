#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPO_DIR="${CHUDITE_REPO_DIR:-/home/rhys/chudite}"
readonly REMOTE="${CHUDITE_GIT_REMOTE:-origin}"
readonly BRANCH="${CHUDITE_GIT_BRANCH:-main}"
readonly LOCK_FILE="${CHUDITE_UPDATE_LOCK:-/home/rhys/.update-chudite.lock}"
readonly FORCE_REBUILD="${CHUDITE_FORCE_REBUILD:-false}"
readonly PULL_BASE_IMAGE="${CHUDITE_PULL_BASE_IMAGE:-false}"
readonly STABILITY_SECONDS="${CHUDITE_STABILITY_SECONDS:-5}"

readonly -a RUNTIME_FILES=(
  audit_log_channels.json
  coal_board_channels.json
  gem_board_channels.json
  reaction_roles.json
  voice_log_channels.json
  warns.json
)

is_runtime_path() {
  local path=$1
  local filename
  for filename in "${RUNTIME_FILES[@]}"; do
    if [[ "$path" == "$filename" || "$path" == "data/$filename" ]]; then
      return 0
    fi
  done
  return 1
}

cd "$REPO_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  printf 'Another Chudite update is already running.\n' >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf '%s is not a Git worktree.\n' "$REPO_DIR" >&2
  exit 1
fi

current_branch=$(git branch --show-current)
if [[ "$current_branch" != "$BRANCH" ]]; then
  printf 'Expected branch %s, but %s is checked out.\n' "$BRANCH" "$current_branch" >&2
  exit 1
fi

old_commit=$(git rev-parse HEAD)
printf 'Fetching %s/%s...\n' "$REMOTE" "$BRANCH"
git fetch --prune "$REMOTE" "$BRANCH"
remote_commit=$(git rev-parse "$REMOTE/$BRANCH")

if ! git merge-base --is-ancestor HEAD "$REMOTE/$BRANCH"; then
  printf 'Local history has diverged from %s/%s; refusing a non-fast-forward update.\n' \
    "$REMOTE" "$BRANCH" >&2
  exit 1
fi

if [[ "$old_commit" == "$remote_commit" ]]; then
  printf 'Git checkout is already current at %s.\n' "$old_commit"
  if [[ "$FORCE_REBUILD" != "true" ]]; then
    printf 'No new commit; skipping the Docker build and restart.\n'
    exit 0
  fi
  printf 'Forced rebuild requested.\n'
else
  # --no-renames exposes both sides of an upstream rename. Without it, a local
  # edit to the old path can evade this check and fail later during the merge.
  conflicts=$(
    comm -12 \
      <({ git diff --name-only; git diff --cached --name-only; } | sort -u) \
      <(git diff --name-only --no-renames HEAD.."$REMOTE/$BRANCH" | sort -u)
  )

  code_conflicts=""
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if ! is_runtime_path "$path"; then
      code_conflicts+="${path}"$'\n'
    fi
  done <<<"$conflicts"
  if [[ -n "$code_conflicts" ]]; then
    printf 'Upstream overlaps local code changes; commit, stash, or discard these first:\n%s' \
      "$code_conflicts" >&2
    exit 1
  fi

  state_backup=$(mktemp -d)
  declare -a saved_names=()
  declare -a saved_sources=()
  update_complete=false

  cleanup_state_backup() {
    local status=$?
    local index
    if [[ "$update_complete" != "true" ]]; then
      for index in "${!saved_names[@]}"; do
        mkdir -p "$(dirname "${saved_sources[$index]}")"
        cp -- "$state_backup/${saved_names[$index]}" "${saved_sources[$index]}"
      done
    fi
    rm -rf -- "${state_backup:?}"
    return "$status"
  }
  trap cleanup_state_backup EXIT

  for filename in "${RUNTIME_FILES[@]}"; do
    source_path=""
    if [[ -f "data/$filename" ]]; then
      source_path="data/$filename"
    elif [[ -f "$filename" ]]; then
      source_path="$filename"
    fi
    if [[ -n "$source_path" ]]; then
      cp -- "$source_path" "$state_backup/$filename"
      saved_names+=("$filename")
      saved_sources+=("$source_path")
    fi

    for candidate in "$filename" "data/$filename"; do
      if git ls-files --error-unmatch -- "$candidate" >/dev/null 2>&1; then
        git restore --source=HEAD --staged --worktree -- "$candidate"
      elif [[ -f "$candidate" ]]; then
        rm -f -- "$candidate"
      fi
    done
  done

  git merge --ff-only "$REMOTE/$BRANCH"
  mkdir -p data
  for filename in "${saved_names[@]}"; do
    cp -- "$state_backup/$filename" "data/$filename"
  done
  update_complete=true
  trap - EXIT
  rm -rf -- "${state_backup:?}"
  printf 'Updated Git checkout: %s -> %s\n' "$old_commit" "$remote_commit"
fi

docker compose config --quiet
printf 'Building the Chudite image...\n'
build_options=()
if [[ "$PULL_BASE_IMAGE" == "true" ]]; then
  build_options+=(--pull)
fi
docker compose build "${build_options[@]}" chudite

printf 'Replacing the Chudite service...\n'
docker compose up -d --no-deps chudite

container_id=$(docker compose ps -q chudite)
if [[ -z "$container_id" ]]; then
  printf 'Compose did not return a Chudite container ID.\n' >&2
  exit 1
fi

initial_restarts=$(docker inspect --format '{{.RestartCount}}' "$container_id")
printf 'Checking container stability for %s seconds...\n' "$STABILITY_SECONDS"
sleep "$STABILITY_SECONDS"
state=$(docker inspect --format '{{.State.Status}}' "$container_id")
final_restarts=$(docker inspect --format '{{.RestartCount}}' "$container_id")
if [[ "$state" != running || "$final_restarts" != "$initial_restarts" ]]; then
  printf 'Chudite failed its stability check (state: %s, restarts: %s -> %s). Recent logs:\n' \
    "$state" "$initial_restarts" "$final_restarts" >&2
  docker compose logs --tail=80 chudite >&2
  exit 1
fi

printf 'Chudite is running at commit %s in container %s.\n' \
  "$(git rev-parse HEAD)" "$container_id"
docker compose ps chudite
