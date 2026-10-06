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

# El script se ejecuta como root: solo se acepta por HTTPS (http solo en loopback, para dev).
case "$API_URL" in
  https://*|http://localhost|http://localhost:*|http://127.0.0.1|http://127.0.0.1:*) ;;
  *)
    echo "[!] Error: api_url debe usar HTTPS ($API_URL). Corregí $CONFIG_FILE."
    exit 1
    ;;
esac

echo "=========================================================="
echo "🛡️  Vigía Ops — Descargando parche de remediación"
echo "🖥️  Servidor: $SERVER_ID"
echo "🎯 Vulnerabilidad: $CVE"
echo "🌐 API Central: $API_URL"
echo "=========================================================="

FIX_URL="$API_URL/api/v1/fix/$SERVER_ID/$CVE.sh"
SCRIPT_FILE="$(mktemp /tmp/vigia-fix.XXXXXX.sh)"
trap 'rm -f "$SCRIPT_FILE"' EXIT
chmod 700 "$SCRIPT_FILE"

# curl -f devuelve exit != 0 ante cualquier HTTP >= 400 (401/404/5xx): no hace
# falta inspeccionar el body, que puede contener "404"/"detail" legítimamente.
# --proto =https impide que un redirect baje a http.
PROTO_FLAGS=(--proto '=https' --proto-redir '=https')
case "$API_URL" in http://*) PROTO_FLAGS=() ;; esac
if ! curl -sSLf "${PROTO_FLAGS[@]}" -H "Authorization: Bearer $TOKEN" -o "$SCRIPT_FILE" "$FIX_URL" || [ ! -s "$SCRIPT_FILE" ]; then
  echo "[!] No se pudo obtener el script para $CVE en $SERVER_ID."
  echo "    Verificá que el CVE figure como activo en la consola central: $API_URL"
  exit 1
fi

# Un script truncado o corrupto no debe llegar a ejecutarse a medias como root.
if ! bash -n "$SCRIPT_FILE"; then
  echo "[!] El script recibido tiene errores de sintaxis; no se ejecuta."
  exit 1
fi

echo "[*] Script recibido (sha256 $(sha256sum "$SCRIPT_FILE" | cut -d' ' -f1))"
echo "[*] Ejecutando remediación con guardrails de seguridad y rollback guarantee..."
bash "$SCRIPT_FILE"
