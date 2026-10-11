#!/usr/bin/env bash
# Use the pinned project-local tool, never an implicit npx network install.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$PROJECT_ROOT/.runtime/agent-tools/node_modules/fullerenes/dist/cli.js"
if [[ ! -f "$CLI" ]]; then
    echo 'Install Fullerenes as described in docs/fullerenes.md first.' >&2
    exit 1
fi
cd "$PROJECT_ROOT"
case "${1:-}" in
    init|index)
        # Upstream 0.1.4 can erase handwritten instructions on repeated refresh.
        # Keep exact snapshots of any existing agent files and restore on EXIT.
        BACKUP_DIR="$(mktemp -d "$PROJECT_ROOT/.runtime/fullerenes-instructions-XXXXXX")"
        PRESERVED_FILES=()
        for AGENT_FILE in AGENTS.md CLAUDE.md .cursor/rules/fullerenes.mdc; do
            if [[ -f "$AGENT_FILE" ]]; then
                mkdir -p "$BACKUP_DIR/$(dirname "$AGENT_FILE")"
                cp -p -- "$AGENT_FILE" "$BACKUP_DIR/$AGENT_FILE"
                PRESERVED_FILES+=("$AGENT_FILE")
            fi
        done
        restore_instructions() {
            for AGENT_FILE in "${PRESERVED_FILES[@]}"; do
                cp -p -- "$BACKUP_DIR/$AGENT_FILE" "$PROJECT_ROOT/$AGENT_FILE"
            done
        }
        trap restore_instructions EXIT
        node "$CLI" "$@"
        exit $? ;;
    watch)
        echo 'Use index via this wrapper. Upstream watch rewrites instruction files while running.' >&2
        exit 1 ;;
esac
exec node "$CLI" "$@"
