#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/update-and-restart.sh [--allow-dirty] [--no-pull] [--no-cache] [--prune-build-cache] [--service NAME]

Pull the latest code and restart the Docker Compose deployment:
  git pull --ff-only
  docker-compose up -d --build

Options:
  --allow-dirty        Do not fail when the worktree has local changes.
  --no-pull            Skip git pull and only rebuild/restart containers.
  --no-cache           Build the app service without Docker layer cache first.
  --prune-build-cache  Run docker builder prune -f before building.
  --service NAME       Compose service to rebuild with --no-cache (default: revit-api-rag).
  -h, --help           Show this help text.
EOF
}

allow_dirty=0
do_pull=1
no_cache=0
prune_build_cache=0
service_name="revit-api-rag"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-dirty)
      allow_dirty=1
      shift
      ;;
    --no-pull)
      do_pull=0
      shift
      ;;
    --no-cache)
      no_cache=1
      shift
      ;;
    --prune-build-cache)
      prune_build_cache=1
      shift
      ;;
    --service)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "--service requires a service name." >&2
        usage >&2
        exit 2
      fi
      service_name="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if [[ ! -f docker-compose.yml ]]; then
  echo "docker-compose.yml not found in $repo_dir" >&2
  exit 1
fi

if [[ "$allow_dirty" != "1" ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Worktree has local changes. Commit/stash them first, or rerun with --allow-dirty." >&2
    git status --short >&2
    exit 1
  fi
fi

if [[ "$do_pull" == "1" ]]; then
  echo "Pulling latest code..."
  git pull --ff-only
fi

if command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose)
elif docker compose version >/dev/null 2>&1; then
  compose_cmd=(docker compose)
else
  echo "Neither docker-compose nor docker compose is available." >&2
  exit 1
fi

if [[ "$prune_build_cache" == "1" ]]; then
  echo "Pruning Docker build cache..."
  docker builder prune -f
fi

if [[ "$no_cache" == "1" ]]; then
  echo "Building $service_name without Docker layer cache..."
  "${compose_cmd[@]}" build --no-cache "$service_name"
  echo "Restarting containers..."
  "${compose_cmd[@]}" up -d
else
  echo "Rebuilding and restarting containers..."
  "${compose_cmd[@]}" up -d --build
fi

echo "Deployment updated."
