#!/bin/sh
# heartbeat.sh — actualiza el latido de una TASK en doing/.
# Uso: sh .orchestrator/scripts/heartbeat.sh TASK-001
set -u
NNN=$(echo "${1:-}" | sed -n 's/^TASK-\([0-9]*\).*/\1/p')
[ -n "$NNN" ] || { echo "uso: heartbeat.sh TASK-<nnn>"; exit 1; }
date -u +"%Y-%m-%dT%H:%M:%SZ" > ".orchestrator/doing/.heartbeat-TASK-$NNN"
echo "heartbeat TASK-$NNN ok"
