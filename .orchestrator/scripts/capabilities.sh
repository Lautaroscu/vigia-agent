#!/bin/sh
# capabilities.sh — detecta qué CLIs de agentes hay en esta PC.
# Uso: sh .orchestrator/scripts/capabilities.sh
# El orquestador lo corre al arrancar y elige workers según disponibilidad.
for c in claude gemini opencode codex; do
  if command -v "$c" >/dev/null 2>&1; then
    ver=$("$c" --version 2>/dev/null | head -n 1)
    echo "OK $c :: $ver"
  else
    echo "MISSING $c"
  fi
done
