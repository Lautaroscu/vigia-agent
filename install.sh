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
SERVER_ID_EXPLICIT=""
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
      SERVER_ID_EXPLICIT=1
      shift 2
      ;;
    --client-id)
      CLIENT_ID="$2"
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
        SERVER_ID_EXPLICIT=1
        API_URL="$2"
        TOKEN="${3:-}"
        break
      fi
      shift
      ;;
  esac
done

if [ -z "$TOKEN" ]; then
  echo "[!] Error: Falta el parámetro obligatorio --token <vga_live_...>"
  echo "    Para generar un token, ingresá a la consola de Vigía (Clientes -> Generar API Key)."
  echo "    Uso: sudo ./install.sh --token <vga_live_...> --api <https://vigia.serra.agency> [--server-id <id>]"
  exit 1
fi

if [ -z "$API_URL" ]; then
  API_URL="https://vigia.serra.agency"
fi

# Las keys `vga_live_{server_id}_{hex32}` quedan atadas a un server_id: la API
# rechaza fix/notify si no coincide con el de config.json. Se toma de la key.
if [[ "$TOKEN" =~ ^vga_live_(.+)_[0-9a-f]{32}$ ]]; then
  TOKEN_SERVER_ID="${BASH_REMATCH[1]}"
  if [ -n "$SERVER_ID_EXPLICIT" ] && [ "$SERVER_ID" != "$TOKEN_SERVER_ID" ]; then
    echo "[!] Error: --server-id '$SERVER_ID' no coincide con el de la API key ('$TOKEN_SERVER_ID')."
    echo "    Generá una key para '$SERVER_ID' o omití --server-id."
    exit 1
  fi
  SERVER_ID="$TOKEN_SERVER_ID"
fi

echo "=========================================================="
echo "🛡️  Instalando Vigía Agent para: $SERVER_ID"
echo "🌐 API Central: $API_URL"
echo "=========================================================="

INSTALL_DIR="/opt/vigia"
mkdir -p "$INSTALL_DIR"

# Copia local solo si install.sh se ejecuta como archivo desde el repo clonado.
# Con `curl ... | bash`, $0 es "bash" y dirname apuntaría al cwd: ahí no se copia nada.
LOCAL_SRC_DIR=""
if [ -f "$0" ]; then
  LOCAL_SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
fi

# Descargar o copiar collector.py
SCRIPT_SRC="$LOCAL_SRC_DIR/collector.py"
if [ -n "$LOCAL_SRC_DIR" ] && [ -f "$SCRIPT_SRC" ]; then
  cp "$SCRIPT_SRC" "$INSTALL_DIR/collector.py"
elif curl -sSLf "${API_URL%/}/collector.py" -o "$INSTALL_DIR/collector.py" 2>/dev/null; then
  echo "[*] Descargado collector.py desde API central..."
else
  echo "[*] Descargando collector.py desde repositorio oficial..."
  if ! curl -sSLf "https://raw.githubusercontent.com/Lautaroscu/vigia-agent/main/collector.py" -o "$INSTALL_DIR/collector.py"; then
    echo "[!] Error: no se pudo descargar collector.py (ni de la API ni del repositorio)."
    exit 1
  fi
fi
chmod +x "$INSTALL_DIR/collector.py"

# Descargar e instalar comando 'vigia-fix'
FIX_SRC="$LOCAL_SRC_DIR/fix.sh"
if [ -n "$LOCAL_SRC_DIR" ] && [ -f "$FIX_SRC" ]; then
  cp "$FIX_SRC" "$INSTALL_DIR/fix.sh"
elif curl -sSLf "${API_URL%/}/fix.sh" -o "$INSTALL_DIR/fix.sh" 2>/dev/null; then
  echo "[*] Descargado vigia-fix desde API central..."
else
  curl -sSLf "https://raw.githubusercontent.com/Lautaroscu/vigia-agent/main/fix.sh" -o "$INSTALL_DIR/fix.sh" 2>/dev/null || rm -f "$INSTALL_DIR/fix.sh"
fi
if [ -f "$INSTALL_DIR/fix.sh" ]; then
  chmod +x "$INSTALL_DIR/fix.sh"
  ln -sf "$INSTALL_DIR/fix.sh" /usr/local/bin/vigia-fix
fi

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
