#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for pidfile in "$ROOT"/logs/*.pid; do
    [ -f "$pidfile" ] || continue
    name="$(basename "$pidfile" .pid)"
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        echo "🛑 Detenido $name (pid $pid)"
    fi
    rm -f "$pidfile"
done
