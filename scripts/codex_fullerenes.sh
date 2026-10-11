#!/usr/bin/env bash
# Project-only Codex configuration overrides; no user/global config writes.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
exec codex -c 'mcp_servers.fullerenes.command="bash"' \
    -c "mcp_servers.fullerenes.args=[\"$PROJECT_ROOT/scripts/fullerenes.sh\",\"mcp\",\"$PROJECT_ROOT\"]" \
    "$@"
