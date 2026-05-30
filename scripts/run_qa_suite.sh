#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

run_step() {
  printf '\n[QA] %s\n' "$1"
  shift
  "$@"
}

run_step "Python unit tests" python3 -m unittest discover -s tests
run_step "Python compile check" python3 -m compileall docformat tests
run_step "Frontend syntax check" node --check docformat/web/app.js
run_step "Docker compose config check" docker compose config

if [[ "${1:-}" == "--with-docker-build" ]]; then
  if docker version >/dev/null 2>&1; then
    run_step "Docker image build" docker build -t docformat:local .
  else
    printf '\n[QA] Skip docker build: docker daemon is unavailable.\n'
  fi
fi

printf '\n[QA] Completed.\n'
