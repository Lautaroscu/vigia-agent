#!/usr/bin/env bash
# ==============================================================================
# Vigía Ops — Ejecutor Seguro de Parches Locales
# Uso: sudo vigia-fix <CVE-ID>
# ==============================================================================
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "[!] Por favor ejecutar como root o con sudo (ej: sudo vigia-fix $1)"
  exit 1
fi

if [ -z "${1:-}" ]; then
  echo "Uso: sudo vigia-fix <CVE-ID>"
  echo "Ejemplo: sudo vigia-fix CVE-2026-44791"
  exit 1
fi

CVE="$1"
CONFIG_FILE="/opt/vigia/config.json"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "[!] Error: No se encontró el archivo de configuración en $CONFIG_FILE"
  echo "    Asegurate de que Vigía Agent esté instalado en este servidor."
  exit 1
fi

SERVER_ID="$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('server_id', ''))")"
API_URL="$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('api_url', '').rstrip('/'))")"
TOKEN="$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('token', ''))")"

if [ -z "$SERVER_ID" ] || [ -z "$API_URL" ] || [ -z "$TOKEN" ]; then
  echo "[!] Error: Configuración incompleta en $CONFIG_FILE"
  exit 1
fi

echo "=========================================================="
echo "🛡️  Vigía Ops — Descargando parche de remediación"
echo "🖥️  Servidor: $SERVER_ID"
echo "🎯 Vulnerabilidad: $CVE"
echo "🌐 API Central: $API_URL"
echo "=========================================================="

FIX_URL="$API_URL/api/v1/fix/$SERVER_ID/$CVE.sh"
SCRIPT_CONTENT="$(curl -sSLf -H "Authorization: Bearer $TOKEN" "$FIX_URL" 2>/dev/null || true)"

if [ -z "$SCRIPT_CONTENT" ] || [[ "$SCRIPT_CONTENT" == *"detail"* ]] || [[ "$SCRIPT_CONTENT" == *"404"* ]]; then
  echo "[!] No se pudo obtener el script para $CVE en $SERVER_ID."
  echo "    Verificá que el CVE figure como activo en la consola central: $API_URL"
  exit 1
fi

echo "[*] Ejecutando remediación con guardrails de seguridad y rollback guarantee..."
echo "$SCRIPT_CONTENT" | bash
