# Demo end-to-end en VirtualBox

Stack con imágenes viejas a propósito para mostrar Vigía de punta a punta: detección, reporte y remediación. **Solo para una VM descartable**: nunca levantarlo en un servidor real.

## Qué levanta

| Servicio | Imagen | Puerto | Para mostrar |
|---|---|---|---|
| `web` | `nginx:1.20` | `0.0.0.0:8080` | CVEs del SO en un servicio expuesto. |
| `db` | `postgres:9.6` | `0.0.0.0:5432` | Imagen fin de vida y base de datos expuesta. |
| `cache` | `redis:4.0` | `0.0.0.0:6379` | Imagen fin de vida y servicio expuesto. |
| `mongo` | `mongo:3.6` | `127.0.0.1:27017` | El mismo riesgo pero solo en loopback (mitigado). |
| `api` | `node:12-alpine` | `127.0.0.1:3000` | Runtime fin de vida. |
| `monitor` | `alpine:3.12` | — | `docker.sock` montado. |
| `debug-tools` | `busybox:1.31` | — | `--privileged`. |
| `whoami` | `traefik/whoami:v1.10.3` | `127.0.0.1:8081` | Servicio sano, para contraste. |

## 1. Crear la VM

- Ubuntu Server 24.04 o Debian 12, con 2 vCPU, 4 GB de RAM y 30 GB de disco.
- **Red: NAT** (la opción por defecto). No usar el modo puente (Bridged): la VM tiene que poder salir a internet sin que nadie pueda entrar.
- Red → Reenvío de puertos: `127.0.0.1:2222 → 22`, para entrar desde Windows con `ssh -p 2222 usuario@127.0.0.1`.

## 2. Preparar

En la consola de Vigía, crear un cliente "Demo" y hacer **Add Server** con `server_id` `demo01`. Después, en la VM:

```bash
git clone https://github.com/Lautaroscu/vigia-agent.git
sudo bash vigia-agent/demo/setup.sh --token vga_live_demo01_<hex>
```

Si el repo es privado, copiar la carpeta desde Windows: `scp -P 2222 -r demo usuario@127.0.0.1:~/`.

Esto instala Docker, levanta el stack en `/opt/vigia-demo` e instala el agente con `syft`. La primera corrida tarda varios minutos por el SBOM de las imágenes; las siguientes usan el caché.

## 3. Instantánea

Con todo levantado y el primer reporte en la consola, sacar una instantánea de la VM en VirtualBox. Antes de cada toma de remediación, volver a esa instantánea.

## 4. Correr el agente durante la grabación

```bash
sudo /usr/bin/python3 /opt/vigia/collector.py
```

## Limitaciones conocidas

- **Escaneo de puertos desde la API:** con NAT, la API no llega a la VM, así que su escaneo externo de puertos no encuentra nada. La exposición se ve igual, porque sale de los puertos publicados en `0.0.0.0` que reporta el agente.
- **`vigia-fix` sobre hallazgos de OSV:** hoy la API usa como tag de imagen la versión corregida del *paquete* (por ejemplo `3.0.15-1~deb12u1`). Eso da un 409 o un tag de imagen que no existe, y el script vuelve atrás. Hasta que se corrija en vigia-ops, en el video la remediación se puede mostrar como vuelta atrás segura, o dejarla para una segunda toma.

## Limpiar

```bash
sudo docker compose -f /opt/vigia-demo/docker-compose.yml down -v
```

O directamente borrar la VM.
