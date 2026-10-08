# Vigía Agent

Agente liviano que corre por cron en cada servidor Linux con Docker monitoreado por [Vigía Ops](https://github.com/Lautaroscu/vigia-ops). Una vez por día junta el inventario de contenedores y lo envía por HTTPS a la API central, que detecta CVEs explotados (CISA KEV), arma el reporte diario y genera los scripts de remediación.

- **Python 3 de la librería estándar**: sin pip, sin daemons, sin puertos abiertos ni acceso SSH entrante.
- **Rápido**: ~0,2 s de recolección más el envío.
- **No lee datos ni secretos**: de las variables de entorno solo toma las de versión (`*_VERSION`, `*_TAG`…) que no contengan `KEY/SECRET/PASS/TOKEN`; descarta labels con `secret/pass/token/key/auth/credential/users`.

## Qué recopila

| Dato | Para qué |
|---|---|
| Distro, kernel, hostname | Contexto del servidor en el reporte. |
| Versión de Docker | Inventario. |
| Contenedores en ejecución: nombre, imagen y tag, estado | Matching de CVEs por producto y versión. |
| Puertos publicados (`0.0.0.0` vs `127.0.0.1`) | Severidad según exposición; alerta de bases de datos expuestas. |
| Labels de Docker Compose (proyecto, servicio, `working_dir`, archivos) | Que `fix.sh` actualice el servicio correcto. |
| Flags CIS: `--privileged`, `docker.sock` montado, usuario root, rootfs read-only | Recomendaciones de hardening. |

Para ver exactamente qué se enviaría, sin enviar nada:

```bash
python3 collector.py --dry-run
```

## Instalación

1. En la consola de Vigía: **Add Server** → genera una API key `vga_live_<server_id>_<hex>` atada a ese servidor.
2. En el servidor:

```bash
curl -sSL https://vigiaops.serra.agency/install.sh | sudo bash -s -- \
  --token vga_live_<server_id>_<hex> \
  --api https://vigiaops.serra.agency
```

Opciones: `--server-id` (por defecto se toma de la key), `--schedule "15 4 * * *"` (expresión cron, UTC en servidores en UTC).

El instalador deja:

| Archivo | Contenido |
|---|---|
| `/opt/vigia/collector.py` | El agente. |
| `/opt/vigia/config.json` | `server_id`, `api_url`, `token` (permisos `600`). |
| `/opt/vigia/fix.sh` → `/usr/local/bin/vigia-fix` | Ejecutor de remediaciones. |
| `/etc/cron.d/vigia-agent` | `15 4 * * * root /usr/bin/python3 /opt/vigia/collector.py > /var/log/vigia/cron.log 2>&1` |
| `/var/log/vigia/collector.log` | Log rotativo (5 MB × 3), timestamps UTC. |

Al terminar corre un escaneo de verificación; tiene que terminar con `Reporte enviado con éxito (HTTP 200)`.

La API central genera su reporte diario a las 05:00 UTC: si se cambia la hora del cron, conviene dejarlo antes de esa hora (o ajustar `VIGIA_DAILY_REPORT_TIME` en la API).

## Remediar una vulnerabilidad

Cuando el reporte o la consola marcan un CVE con versión verificada:

```bash
sudo vigia-fix CVE-2026-XXXXX
```

`vigia-fix` descarga el script de la API autenticándose con la key del servidor (solo HTTPS), valida su sintaxis y lo ejecuta. El script hace backup de la imagen actual, actualiza solo ese servicio de Compose al tag verificado, comprueba que el contenedor quede sano y, si no, vuelve atrás. Vigía nunca ejecuta nada sin que alguien corra este comando.

## Diagnóstico

```bash
sudo cat /etc/cron.d/vigia-agent
sudo tail -n 20 /var/log/vigia/collector.log
sudo /usr/bin/python3 /opt/vigia/collector.py      # corrida manual
```

Códigos de salida: `0` enviado, `1` falta configuración, `2` la API rechazó el reporte (ver el mensaje: key inválida, `server_id` que no coincide con la key, etc.).

## Desarrollo

```bash
python3 -m unittest discover -s tests -v      # tests sin Docker real
bash -n install.sh fix.sh
```

La API central sirve copias de `collector.py`, `install.sh` y `fix.sh` desde `vigia-ops/api/app/static/`; cualquier cambio acá hay que copiarlo también allá.
