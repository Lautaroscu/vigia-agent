#!/bin/sh
# claim.sh — reclama la TASK más vieja de inbox/ de forma atómica.
# Uso: sh .orchestrator/scripts/claim.sh [worker] [role]
# Imprime la ruta en doing/ o EMPTY si no hay. Solo uno gana ante carreras.
set -u
ORCH=".orchestrator"
INBOX="$ORCH/inbox"
DOING="$ORCH/doing"
WORKER="${1:-unknown}"
ROLE="${2:-IMPLEMENTER}"
found=""
for f in "$INBOX"/TASK-*.md; do
  [ -e "$f" ] || { echo "EMPTY"; exit 2; }
  found="$f"; break
done
base=$(basename "$found")
dest="$DOING/$base"
if mv -n "$found" "$dest" 2>/dev/null; then
  stamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  nnn=$(echo "$base" | sed -n 's/^TASK-\([0-9]*\).*/\1/p')
  printf '{"task":"%s","worker":"%s","role":"%s","claimed_at":"%s"}\n' "$base" "$WORKER" "$ROLE" "$stamp" > "$DOING/.claim-TASK-$nnn.json"
  echo "$stamp" > "$DOING/.heartbeat-TASK-$nnn"
  echo "$dest"
else
  echo "TAKEN"
  exit 3
fi
