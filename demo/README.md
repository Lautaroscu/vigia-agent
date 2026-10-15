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

## Con Vagrant (recomendado)

El [`Vagrantfile`](Vagrantfile) crea la VM en VirtualBox: Ubuntu Server 24.04 (`cloud-image/ubuntu-24.04`), 2 vCPU, 4 GB, disco de 30 GB, **solo red NAT** y SSH en `127.0.0.1:2222`. Al crearla corre `setup.sh` adentro. Requiere [VirtualBox](https://www.virtualbox.org/) y [Vagrant](https://developer.hashicorp.com/vagrant/install). Todos los comandos se corren desde `demo/`.

El token y la API van por variables de entorno, nunca en archivos del repo:

| Variable | Valor | Si falta |
|---|---|---|
| `VIGIA_TOKEN` | key de **Add Server** (`vga_live_demo01_<hex>`) | Solo Docker + stack, sin agente. |
| `VIGIA_API` | `https://vigiaops.serra.agency` o `http://127.0.0.1:8100` | Producción. |
| `VIGIA_BOX` | otro box de Vagrant | `cloud-image/ubuntu-24.04`. |

`VIGIA_API` solo acepta `https://…` o `http://127.0.0.1:<puerto>`, igual que `vigia-fix`.

### 1. Crear

En la consola de Vigía, crear un cliente "Demo" y hacer **Add Server** con `server_id` `demo01`.

**Contra producción** (PowerShell):

```powershell
$env:VIGIA_TOKEN = "vga_live_demo01_<hex>"
vagrant up
```

**Contra la API local de la PC** (`127.0.0.1:8100` en Windows). La VM la alcanza por un túnel SSH inverso, que solo se puede abrir cuando la VM ya existe; por eso son dos pasos:

```powershell
$env:VIGIA_TOKEN = "vga_live_demo01_<hex>"
$env:VIGIA_API = "http://127.0.0.1:8100"
vagrant up                                  # Docker + stack; avisa que todavía no llega a la API
```

En otra terminal, también en `demo/`, abrir el túnel y dejarlo abierto mientras dure la demo (el agente lo necesita en cada corrida):

```powershell
vagrant ssh -- -N -R 8100:127.0.0.1:8100
```

Y en la primera terminal (con las mismas variables), instalar el agente:

```powershell
vagrant provision --provision-with demo
```

Sin `VIGIA_TOKEN` se levanta solo Docker y el stack; el agente se puede agregar después definiendo las variables y corriendo `vagrant provision --provision-with demo`.

Para entrar: `vagrant ssh` (o `ssh -p 2222 vagrant@127.0.0.1` con la clave de `.vagrant/machines/default/virtualbox/private_key`).

### 2. Instantánea

Con todo levantado y el primer reporte en la consola:

```powershell
vagrant snapshot save todo-vulnerable
```

### 3. Restaurar

Antes de cada toma de remediación:

```powershell
vagrant snapshot restore todo-vulnerable --no-provision
```

`--no-provision` evita que vuelva a correr `setup.sh`. Con la API local, el túnel se corta al restaurar: volver a abrirlo con `vagrant ssh -- -N -R 8100:127.0.0.1:8100`. `vagrant snapshot list` muestra las instantáneas guardadas.

### 4. Destruir

```powershell
vagrant destroy -f
```

Borra la VM con sus instantáneas. El servidor `demo01` sigue en la consola de Vigía hasta borrarlo ahí.

## A mano en VirtualBox

### 1. Crear la VM

- Ubuntu Server 24.04 o Debian 12, con 2 vCPU, 4 GB de RAM y 30 GB de disco.
- **Red: NAT** (la opción por defecto). No usar el modo puente (Bridged): la VM tiene que poder salir a internet sin que nadie pueda entrar.
- Red → Reenvío de puertos: `127.0.0.1:2222 → 22`, para entrar desde Windows con `ssh -p 2222 usuario@127.0.0.1`.

### 2. Preparar

En la consola de Vigía, crear un cliente "Demo" y hacer **Add Server** con `server_id` `demo01`. Después, en la VM:

```bash
git clone https://github.com/Lautaroscu/vigia-agent.git
sudo bash vigia-agent/demo/setup.sh --token vga_live_demo01_<hex>
```

Si el repo es privado, copiar la carpeta desde Windows: `scp -P 2222 -r demo usuario@127.0.0.1:~/`.

Esto instala Docker, levanta el stack en `/opt/vigia-demo` e instala el agente con `syft`. La primera corrida tarda varios minutos por el SBOM de las imágenes; las siguientes usan el caché.

Para usar la API local, agregar `--api http://127.0.0.1:8100` y entrar con el túnel abierto: `ssh -p 2222 -R 8100:127.0.0.1:8100 usuario@127.0.0.1`.

### 3. Instantánea

Con todo levantado y el primer reporte en la consola, sacar una instantánea de la VM en VirtualBox. Antes de cada toma de remediación, volver a esa instantánea.

## Correr el agente durante la grabación

```bash
sudo /usr/bin/python3 /opt/vigia/collector.py
```

## Limitaciones conocidas

- **Escaneo de puertos desde la API:** con NAT, la API no llega a la VM, así que su escaneo externo de puertos no encuentra nada. La exposición se ve igual, porque sale de los puertos publicados en `0.0.0.0` que reporta el agente.
- **`vigia-fix` sobre hallazgos de OSV:** actualiza la imagen al tag más nuevo de su misma línea en Docker Hub: misma variante y mismo major (en bases de datos, mismo major.minor). La mejor toma es `web` (`nginx:1.20` → la última `1.x`). En `db`, `cache` y `mongo` solo sube al último parche de la misma versión, así que muchos CVEs del SO siguen ahí. Los hallazgos de paquetes del host se corrigen con `apt`/`dnf`, no con `vigia-fix`.

## Limpiar

```bash
sudo docker compose -f /opt/vigia-demo/docker-compose.yml down -v
```

O directamente borrar la VM (`vagrant destroy -f` si se creó con Vagrant).
