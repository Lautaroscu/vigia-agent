#!/usr/bin/env bash
# Prepara una VM descartable (Ubuntu 24.04 / Debian 12) para la demo de Vigía:
# instala Docker, levanta demo/docker-compose.yml en /opt/vigia-demo y, si se pasa
# --token, instala el agente. SOLO para una VM de prueba en red NAT.
#
#   sudo bash setup.sh                                  # solo Docker + stack
#   sudo bash setup.sh --token vga_live_demo01_xxx      # + agente Vigía
set -euo pipefail

API_URL="https://vigiaops.serra.agency"
TOKEN=""
DEMO_DIR="/opt/vigia-demo"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --token) TOKEN="${2:-}"; shift 2 ;;
    --api) API_URL="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,8p "$0"; exit 0 ;;
    *) echo "[!] Opción desconocida: $1" >&2; exit 1 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "[!] Correr con sudo." >&2
  exit 1
fi

if [ ! -f "$SCRIPT_DIR/docker-compose.yml" ]; then
  echo "[!] No encuentro docker-compose.yml junto a este script." >&2
  exit 1
fi

# Aviso si la VM parece alcanzable desde la red (adaptador puente): la demo publica
# Postgres y Redis en 0.0.0.0 con imágenes vulnerables.
if ip -4 route show default 2>/dev/null | grep -qv '10\.0\.2\.'; then
  echo "[!] La ruta por defecto no es la NAT de VirtualBox (10.0.2.x)."
  echo "    Si la VM está en modo puente, los servicios vulnerables quedan accesibles en tu red."
  read -r -p "    ¿Seguir igual? [y/N] " ans
  [ "${ans:-}" = "y" ] || exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[*] Instalando Docker (get.docker.com)..."
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "[!] Falta el plugin 'docker compose'." >&2
  exit 1
fi
systemctl enable --now docker >/dev/null 2>&1 || true

echo "[*] Levantando el stack de demo en $DEMO_DIR..."
mkdir -p "$DEMO_DIR"
cp "$SCRIPT_DIR/docker-compose.yml" "$DEMO_DIR/docker-compose.yml"
docker compose -f "$DEMO_DIR/docker-compose.yml" pull -q
docker compose -f "$DEMO_DIR/docker-compose.yml" up -d
docker compose -f "$DEMO_DIR/docker-compose.yml" ps

if [ -n "$TOKEN" ]; then
  echo "[*] Instalando el agente Vigía..."
  curl -sSL "$API_URL/install.sh" | bash -s -- --token "$TOKEN" --api "$API_URL"
else
  echo
  echo "[i] Stack listo. Para instalar el agente (key de 'Add Server' en la consola):"
  echo "    curl -sSL $API_URL/install.sh | sudo bash -s -- --token vga_live_<server_id>_<hex> --api $API_URL"
fi

echo
echo "[i] Sacá ahora una instantánea de la VM en VirtualBox (estado 'todo vulnerable')."
