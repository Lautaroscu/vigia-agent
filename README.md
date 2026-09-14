# Vigía Agent 🛡️

Agente de monitoreo ligero, transparente y no invasivo para servidores Linux con Docker.

## Características de Seguridad

1. **Cero invasión:** No abre puertos entrantes, no requiere accesos SSH, ni instala daemons pesados de fondo.
2. **Sin dependencias externas:** Desarrollado en Python 3 puro (compatible con librerías estándar).
3. **Protección de Privacidad:** No lee código fuente, bases de datos ni archivos confidenciales. Filtra y descarta automáticamente variables que contengan credenciales o secretos.
4. **Bajo consumo:** Ejecución en menos de 2 segundos mediante Cron diario. Memoria RAM liberada inmediatamente tras finalizar.

---

## Instalación Rápida (One-Liner)

Para instalar el agente en tu servidor Linux/Docker:

```bash
curl -sSL https://raw.githubusercontent.com/Lautaroscu/vigia-agent/main/install.sh | sudo bash -s -- \
  --api https://vigia.serra.agency \
  --token vga_live_tu_token_aqui \
  --server-id mi-servidor-prod
```

### Probar en modo Dry-Run (sin enviar nada)

Podés auditar el JSON exacto que recopila el agente antes de instalar:

```bash
python3 collector.py --dry-run
```

---

## ¿Qué información recopila el agente?

- **Sistema:** Distribución Linux, versión de kernel y hostname.
- **Docker Engine:** Versión del daemon Docker instalada.
- **Contenedores:** Nombres de imágenes y tags, puertos expuestos (`0.0.0.0` vs `127.0.0.1`), directorios de Docker Compose (`com.docker.compose.project.working_dir`) y flags de seguridad CIS (ej. `--privileged` o montaje de `/var/run/docker.sock`).
