#!/usr/bin/env bash
# ==============================================================================
# Vigía Agent - Instalador Oficial One-Liner para Linux / Docker
# ==============================================================================
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "[!] Por favor, ejecutar como root (sudo bash install.sh ...)"
  exit 1
fi

SERVER_ID="$(hostname)"
API_URL=""
TOKEN=""
SCHEDULE="15 4 * * *" # 04:15 AM diario

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token)
      TOKEN="$2"
      shift 2
      ;;
    --api)
      API_URL="$2"
      shift 2
      ;;
    --server-id)
      SERVER_ID="$2"
      shift 2
      ;;
    --schedule)
      SCHEDULE="$2"
      shift 2
      ;;
    *)
      # Compatibilidad con argumentos posicionales: install.sh [server_id] [api_url] [token]
      if [ -z "$API_URL" ] && [ -n "${2:-}" ]; then
        SERVER_ID="$1"
        API_URL="$2"
        TOKEN="${3:-}"
        break
      fi
      shift
      ;;
  esac
done

if [ -z "$API_URL" ] || [ -z "$TOKEN" ]; then
  echo "Uso: sudo ./install.sh --token <vga_live_...> --api <https://vigia.serra.agency> [--server-id <id>]"
  echo "Ejemplo: sudo ./install.sh --token vga_live_9f81a7b... --api https://vigia.serra.agency --server-id serdeb13"
  exit 1
fi

echo "=========================================================="
echo "🛡️  Instalando Vigía Agent para: $SERVER_ID"
echo "🌐 API Central: $API_URL"
echo "=========================================================="

INSTALL_DIR="/opt/vigia"
mkdir -p "$INSTALL_DIR"

# Descargar o copiar collector.py
SCRIPT_SRC="$(dirname "$0")/collector.py"
if [ -f "$SCRIPT_SRC" ]; then
  cp "$SCRIPT_SRC" "$INSTALL_DIR/collector.py"
else
  echo "[*] Descargando collector.py desde repositorio oficial..."
  curl -sSL "https://raw.githubusercontent.com/Lautaroscu/vigia-agent/main/collector.py" -o "$INSTALL_DIR/collector.py"
fi
chmod +x "$INSTALL_DIR/collector.py"

# Guardar config con permisos protegidos
echo "[*] Configurando credenciales en $INSTALL_DIR/config.json..."
cat <<EOF > "$INSTALL_DIR/config.json"
{
  "server_id": "$SERVER_ID",
  "api_url": "$API_URL",
  "token": "$TOKEN"
}
EOF
chmod 600 "$INSTALL_DIR/config.json"

# Configurar cron
CRON_FILE="/etc/cron.d/vigia-agent"
echo "[*] Registrando tarea programada en $CRON_FILE..."
cat <<EOF > "$CRON_FILE"
# Vigía Agent - Escaneo programado de seguridad
$SCHEDULE root /usr/bin/python3 $INSTALL_DIR/collector.py > /var/log/vigia/cron.log 2>&1
EOF
chmod 644 "$CRON_FILE"

# Validar ejecución inmediata
echo "[*] Ejecutando escaneo inicial de verificación..."
/usr/bin/python3 "$INSTALL_DIR/collector.py"

echo "=========================================================="
echo "✅ ¡Vigía Agent instalado y verificado exitosamente!"
echo "📍 Configuración: $INSTALL_DIR/config.json"
echo "📑 Logs: /var/log/vigia/collector.log"
echo "=========================================================="
