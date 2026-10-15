#!/usr/bin/env python3
"""
Vigía Agent - Agente de Monitoreo Ligero y No Invasivo
Repositorio oficial: https://github.com/Lautaroscu/vigia-agent

Sin dependencias externas (solo stdlib). Recolecta inventario de solo lectura:
SO y paquetes instalados, sockets en escucha con su proceso y paquete dueño,
servicios systemd, configuración de seguridad del host, contenedores Docker y,
si `syft` está instalado, el SBOM de cada imagen (cacheado por image ID).
La detección de vulnerabilidades la hace la API central contra OSV.dev.
"""

import argparse
import gzip
import hashlib
import ipaddress
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

LOG = logging.getLogger("VigiaAgent")

COLLECTOR_VERSION = "3.0.0"
SBOM_CACHE_DIR = "/var/lib/vigia/sbom"
SBOM_CACHE_TTL = 7 * 24 * 3600
DEFAULT_SCAN_PATHS = ["/opt", "/srv", "/var/www"]

# Labels que pueden contener credenciales (p.ej. traefik ...basicauth.users=user:hash)
# no salen del servidor.
SENSITIVE_LABEL_TERMS = ("secret", "pass", "token", "key", "auth", "credential", "users")

def setup_logger(log_file_path=None):
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s UTC] [%(levelname)s] [PID %(process)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fmt.converter = time.gmtime  # el formato dice "UTC"

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    LOG.addHandler(ch)

    target_log = log_file_path
    if not target_log:
        if os.path.exists("/var/log") and os.access("/var/log", os.W_OK):
            log_dir = "/var/log/vigia"
            try:
                os.makedirs(log_dir, exist_ok=True)
                target_log = os.path.join(log_dir, "collector.log")
            except Exception:
                target_log = "vigia_collector.log"
        else:
            target_log = "vigia_collector.log"

    try:
        fh = logging.handlers.RotatingFileHandler(
            target_log, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        LOG.addHandler(fh)
        LOG.info(f"Vigía Agent inicializado. Log rotativo activo: {target_log}")
    except Exception as e:
        LOG.warning(f"No se pudo inicializar RotatingFileHandler en {target_log}: {e}")


def run_command(cmd, timeout=10, allow_fail=False):
    """stdout del comando, o None si falla. `allow_fail`: devolver stdout aunque el exit code != 0."""
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0 or (allow_fail and res.stdout):
            return res.stdout.strip()
        return None
    except Exception as e:
        LOG.debug(f"Error ejecutando {' '.join(cmd)}: {e}")
        return None


def get_os_info():
    info = {
        "hostname": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "distro": "unknown",
        "distro_version": "unknown",
        "id": None,
        "version_codename": None,
    }
    if os.path.exists("/etc/os-release"):
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                for line in f:
                    key, _, val = line.strip().partition("=")
                    val = val.strip().strip('"')
                    if key == "NAME":
                        info["distro"] = val
                    elif key == "VERSION_ID":
                        info["distro_version"] = val
                    elif key == "ID":
                        info["id"] = val.lower()
                    elif key == "VERSION_CODENAME":
                        info["version_codename"] = val
        except Exception as e:
            LOG.warning(f"Error leyendo /etc/os-release: {e}")
    return info


def get_docker_info():
    if not shutil.which("docker"):
        return {"installed": False}

    version_out = run_command(["docker", "version", "--format", "{{json .}}"])
    if version_out:
        try:
            v_data = json.loads(version_out)
            return {
                "installed": True,
                "server_version": v_data.get("Server", {}).get("Version"),
                "api_version": v_data.get("Server", {}).get("ApiVersion"),
            }
        except Exception:
            pass

    raw_v = run_command(["docker", "--version"])
    return {
        "installed": True,
        "raw_version": raw_v or "unknown"
    }


def get_docker_containers():
    if not shutil.which("docker"):
        return []

    ids_raw = run_command(["docker", "ps", "-q"])
    if not ids_raw:
        return []

    container_ids = [cid.strip() for cid in ids_raw.splitlines() if cid.strip()]
    inspect_raw = run_command(["docker", "inspect"] + container_ids, timeout=30)
    if not inspect_raw:
        return []

    try:
        raw_containers = json.loads(inspect_raw)
    except Exception as e:
        LOG.error(f"Error parseando docker inspect: {e}")
        return []

    containers = []
    for c in raw_containers:
        name = c.get("Name", "").lstrip("/")
        image_name = c.get("Config", {}).get("Image", "")
        image_id = c.get("Image", "")
        labels = c.get("Config", {}).get("Labels") or {}
        host_config = c.get("HostConfig") or {}

        # Mapeo de puertos
        port_bindings = []
        network_settings = c.get("NetworkSettings", {})
        ports = network_settings.get("Ports") or {}
        for container_port, host_bindings in ports.items():
            if host_bindings:
                for b in host_bindings:
                    host_ip = b.get("HostIp", "0.0.0.0")
                    host_port = b.get("HostPort")
                    is_public = host_ip in ["0.0.0.0", "::", ""]
                    port_bindings.append({
                        "container_port": container_port,
                        "host_ip": host_ip,
                        "host_port": host_port,
                        "is_public": is_public
                    })

        # Chequeos de Seguridad Host (Docker CIS Benchmarks)
        mounts = c.get("Mounts") or []
        docker_sock_mounted = any("/docker.sock" in m.get("Source", "") for m in mounts)
        is_privileged = host_config.get("Privileged", False)
        run_as_user = c.get("Config", {}).get("User", "")

        security_flags = {
            "docker_sock_mounted": docker_sock_mounted,
            "is_privileged": is_privileged,
            "run_as_root": run_as_user in ["", "0", "root"],
            "read_only_rootfs": host_config.get("ReadonlyRootfs", False),
            "network_mode": host_config.get("NetworkMode"),
            "cap_add": host_config.get("CapAdd") or [],
        }

        # Labels de Docker Compose
        compose_info = {
            "project": labels.get("com.docker.compose.project"),
            "service": labels.get("com.docker.compose.service"),
            "working_dir": labels.get("com.docker.compose.project.working_dir"),
            "config_files": labels.get("com.docker.compose.project.config_files"),
        }

        # Variables de versión (sin contraseñas ni secrets)
        env_vars = {}
        for env in c.get("Config", {}).get("Env") or []:
            parts = env.split("=", 1)
            k = parts[0]
            v = parts[1] if len(parts) > 1 else ""
            if any(term in k.upper() for term in ["VERSION", "TAG", "RELEASE", "EDITION"]):
                if not any(secret in k.upper() for secret in ["KEY", "SECRET", "PASS", "TOKEN"]):
                    env_vars[k] = v

        containers.append({
            "id": c.get("Id", "")[:12],
            "name": name,
            "image": image_name,
            "image_id": image_id,
            "created": c.get("Created"),
            "state": c.get("State", {}).get("Status"),
            "ports": port_bindings,
            "security": security_flags,
            "compose": compose_info,
            "version_envs": env_vars,
            "labels": {
                k: v for k, v in labels.items()
                if not any(sec in k.lower() for sec in SENSITIVE_LABEL_TERMS)
            }
        })

    return containers


def get_os_packages():
    """Paquetes del SO con su paquete fuente (OSV indexa Debian/Ubuntu/Alpine por fuente)."""
    pkgs = []
    if shutil.which("dpkg-query"):
        fmt = "${db:Status-Abbrev}\t${Package}\t${Version}\t${source:Package}\t${source:Version}\n"
        out = run_command(["dpkg-query", "-W", "-f", fmt], timeout=30)
        for line in (out or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 5 or not parts[0].startswith(("ii", "hi")):
                continue
            pkgs.append({"type": "deb", "name": parts[1], "version": parts[2],
                         "source": parts[3] or parts[1], "source_version": parts[4] or parts[2]})
    elif shutil.which("rpm"):
        fmt = "%{NAME}\t%{EPOCH}\t%{VERSION}\t%{RELEASE}\t%{SOURCERPM}\n"
        out = run_command(["rpm", "-qa", "--qf", fmt], timeout=60)
        for line in (out or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 5 or parts[0] == "gpg-pubkey":
                continue
            name, epoch, ver, rel, srpm = parts[:5]
            version = f"{ver}-{rel}" if epoch in ("(none)", "0", "") else f"{epoch}:{ver}-{rel}"
            src = _srpm_name(srpm) or name
            pkgs.append({"type": "rpm", "name": name, "version": version, "source": src})
    elif os.path.exists("/lib/apk/db/installed"):
        cur = {}
        try:
            with open("/lib/apk/db/installed", "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines() + [""]
            for line in lines:
                if not line:
                    if cur.get("name") and cur.get("version"):
                        pkgs.append({"type": "apk", "name": cur["name"], "version": cur["version"],
                                     "source": cur.get("source") or cur["name"]})
                    cur = {}
                elif line.startswith("P:"):
                    cur["name"] = line[2:]
                elif line.startswith("V:"):
                    cur["version"] = line[2:]
                elif line.startswith("o:"):
                    cur["source"] = line[2:]
        except Exception as e:
            LOG.warning(f"Error leyendo base apk: {e}")
    return pkgs


def _srpm_name(srpm):
    if not srpm or srpm == "(none)":
        return None
    return re.sub(r"-[^-]+-[^-]+\.src\.rpm$", "", srpm) or None


def _is_public_address(addr):
    if addr in ("0.0.0.0", "::", "*", ""):
        return True
    try:
        ip = ipaddress.ip_address(addr)
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        return not (ip.is_loopback or ip.is_private or ip.is_link_local)
    except ValueError:
        return False


def _split_addr(local):
    host, _, port = local.rpartition(":")
    host = host.strip("[]").split("%")[0]
    return host, int(port) if port.isdigit() else 0


def _container_of_pid(pid):
    try:
        with open(f"/proc/{pid}/cgroup", "r", encoding="utf-8") as f:
            m = re.search(r"(?:docker|containerd|kubepods)[^\n]*?([0-9a-f]{64})", f.read())
            return m.group(1)[:12] if m else None
    except Exception:
        return None


def _owner_packages(paths):
    """{path: paquete del SO dueño del binario} con una sola invocación del gestor."""
    owners = {}
    if not paths:
        return owners
    candidates = {}
    for p in paths:
        candidates[p] = p
        if p.startswith("/usr/"):
            candidates[p[4:]] = p  # usrmerge: dpkg puede tener registrado /sbin/x en vez de /usr/sbin/x
    if shutil.which("dpkg-query"):
        out = run_command(["dpkg-query", "-S"] + sorted(candidates), timeout=30, allow_fail=True)
        for line in (out or "").splitlines():
            pkg, sep, path = line.partition(": ")
            if sep and path.strip() in candidates and "diversion" not in pkg:
                owners[candidates[path.strip()]] = pkg.split(",")[0].split(":")[0].strip()
    elif shutil.which("rpm"):
        ordered = sorted(paths)
        out = run_command(["rpm", "-qf", "--qf", "%{NAME}\n"] + ordered, timeout=30, allow_fail=True)
        for path, line in zip(ordered, (out or "").splitlines()):  # noqa: B905 (py<3.10 en hosts)
            if line.strip() and " " not in line.strip():
                owners[path] = line.strip()
    elif shutil.which("apk"):
        for p in paths:
            out = run_command(["apk", "info", "--who-owns", p], allow_fail=True) or ""
            m = re.search(r"is owned by (\S+?)-\d", out)
            if m:
                owners[p] = m.group(1)
    return owners


def get_listening_sockets():
    """Sockets en escucha del namespace de red del host con proceso, binario y paquete dueño."""
    sockets = []
    if shutil.which("ss"):
        out = run_command(["ss", "-H", "-tulpn"]) or run_command(["ss", "-tulpn"])
        for line in (out or "").splitlines():
            parts = line.split()
            # Netid State Recv-Q Send-Q Local:Port Peer:Port [Process]
            if len(parts) < 5 or parts[0] == "Netid":
                continue
            host, port = _split_addr(parts[4])
            proc = " ".join(parts[6:]) if len(parts) > 6 else ""
            m = re.search(r'\("([^"]+)",pid=(\d+)', proc)
            sockets.append({"proto": parts[0], "address": host, "port": port,
                            "process": m.group(1) if m else None, "pid": int(m.group(2)) if m else None})
    elif shutil.which("netstat"):
        out = run_command(["netstat", "-tulpn"])
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) < 6 or not parts[0].startswith(("tcp", "udp")):
                continue
            if parts[0].startswith("tcp") and "LISTEN" not in parts:
                continue
            host, port = _split_addr(parts[3])
            pid, _, name = parts[-1].partition("/")
            sockets.append({"proto": parts[0], "address": host, "port": port,
                            "process": name or None, "pid": int(pid) if pid.isdigit() else None})

    seen = set()
    uniq = []
    for s in sockets:
        key = (s["proto"][:3], s["address"], s["port"], s["pid"])
        if key in seen or not s["port"]:
            continue
        seen.add(key)
        s["is_public"] = _is_public_address(s["address"])
        s["exe"] = None
        s["container_id"] = None
        if s["pid"]:
            try:
                s["exe"] = os.readlink(f"/proc/{s['pid']}/exe").replace(" (deleted)", "")
            except Exception:
                pass
            s["container_id"] = _container_of_pid(s["pid"])
        uniq.append(s)

    # docker-proxy es del paquete de Docker pero expone al contenedor, no a Docker en sí
    exes = sorted({s["exe"] for s in uniq if s["exe"] and not s["container_id"]
                   and s["process"] != "docker-proxy"})
    owners = _owner_packages(exes)
    for s in uniq:
        if s["exe"] in owners and not s["container_id"] and s["process"] != "docker-proxy":
            s["owner_package"] = owners[s["exe"]]
    return uniq[:300]


def get_services():
    if not shutil.which("systemctl"):
        return []
    out = run_command(["systemctl", "list-units", "--type=service", "--state=running",
                       "--no-legend", "--plain", "--no-pager"])
    return [l.split()[0] for l in (out or "").splitlines() if l.strip()][:300]


def get_host_config():
    """Configuración de seguridad del host (sin secretos)."""
    cfg = {"kernel": platform.release(),
           "reboot_required": os.path.exists("/var/run/reboot-required")}

    if shutil.which("sshd"):
        keep = ("port", "permitrootlogin", "passwordauthentication", "pubkeyauthentication",
                "kbdinteractiveauthentication", "permitemptypasswords", "x11forwarding",
                "maxauthtries", "allowusers", "allowgroups")
        ssh = {}
        for line in (run_command(["sshd", "-T"]) or "").splitlines():
            k, _, v = line.partition(" ")
            if k in keep:
                ssh[k] = v.strip()
        if ssh:
            cfg["sshd"] = ssh

    fw = {}
    if shutil.which("ufw"):
        out = run_command(["ufw", "status"]) or ""
        fw["ufw"] = out.splitlines()[0].replace("Status:", "").strip() if out else "unknown"
    if shutil.which("nft"):
        out = run_command(["nft", "list", "ruleset"]) or ""
        fw["nft_rules"] = sum(1 for l in out.splitlines()
                              if l.strip() and not l.strip().startswith(("table", "chain", "}", "type", "policy")))
    if shutil.which("iptables"):
        lines = (run_command(["iptables", "-S"]) or "").splitlines()
        fw["iptables_rules"] = sum(1 for l in lines if l.startswith("-A"))
        fw["iptables_input_policy"] = next((l.split()[-1] for l in lines if l.startswith("-P INPUT")), None)
    if fw:
        cfg["firewall"] = fw

    auto = None
    if os.path.exists("/etc/apt/apt.conf.d/20auto-upgrades"):
        try:
            with open("/etc/apt/apt.conf.d/20auto-upgrades", "r", encoding="utf-8") as f:
                auto = 'Unattended-Upgrade "1"' in f.read()
        except Exception:
            pass
    elif shutil.which("systemctl"):
        auto = run_command(["systemctl", "is-enabled", "dnf-automatic.timer"]) == "enabled"
    cfg["auto_security_updates"] = auto

    try:
        with open("/etc/docker/daemon.json", "r", encoding="utf-8") as f:
            daemon = json.load(f)
        cfg["docker_daemon"] = {k: daemon.get(k) for k in ("userns-remap", "live-restore", "icc",
                                                          "no-new-privileges", "iptables", "hosts")
                                if k in daemon}
    except Exception:
        pass
    return cfg


def _reduce_sbom(doc):
    """syft-json -> solo lo que necesita el matching (el SBOM completo pesa MBs)."""
    packages = []
    for a in doc.get("artifacts") or []:
        md = a.get("metadata") or {}
        pkg = {"type": a.get("type"), "name": a.get("name"), "version": a.get("version"),
               "purl": a.get("purl")}
        if a.get("type") == "deb":
            pkg["source"] = md.get("source") or None
            pkg["source_version"] = md.get("sourceVersion") or None
        elif a.get("type") == "apk":
            pkg["source"] = md.get("originPackage") or None
        elif a.get("type") == "rpm":
            pkg["source"] = _srpm_name(md.get("sourceRpm"))
            epoch = md.get("epoch")
            if epoch not in (None, 0, "0") and ":" not in (pkg["version"] or ""):
                pkg["version"] = f"{epoch}:{pkg['version']}"
        locs = a.get("locations") or []
        if locs:
            pkg["location"] = locs[0].get("path")
        if pkg["name"] and pkg["version"]:
            packages.append(pkg)
    distro = doc.get("distro") or {}
    src_md = (doc.get("source") or {}).get("metadata") or {}
    return {"distro": {"id": distro.get("id"), "version_id": distro.get("versionID")},
            "image_id": src_md.get("imageID"), "packages": packages}


def _run_syft(source, timeout=600):
    out = run_command(["syft", source, "-o", "syft-json", "-q"], timeout=timeout)
    if not out:
        return None
    try:
        return _reduce_sbom(json.loads(out))
    except Exception as e:
        LOG.warning(f"SBOM de {source} ilegible: {e}")
        return None


def get_sboms(containers, scan_paths):
    """SBOM por imagen en uso (cacheado por image ID) + directorios de apps del host."""
    if not shutil.which("syft"):
        LOG.info("syft no instalado: se omite el SBOM de imágenes (solo inventario del host).")
        return []
    try:
        os.makedirs(SBOM_CACHE_DIR, exist_ok=True)
    except Exception:
        pass

    sboms = []
    images = {}
    for c in containers:
        if c.get("image_id"):
            images.setdefault(c["image_id"], c.get("image"))
    for image_id, ref in images.items():
        cache = os.path.join(SBOM_CACHE_DIR, hashlib.sha256(image_id.encode()).hexdigest()[:32] + ".json")
        try:
            if time.time() - os.path.getmtime(cache) < SBOM_CACHE_TTL:
                with open(cache, "r", encoding="utf-8") as f:
                    sboms.append(json.load(f))
                continue
        except Exception:
            pass
        t0 = time.time()
        sb = _run_syft(f"docker:{ref}")
        if sb is None:
            sboms.append({"target": ref, "image_id": image_id, "packages": [], "error": "syft falló"})
            continue
        if sb.get("image_id") and sb["image_id"] != image_id:
            # El tag local apunta a otra imagen que la del contenedor: no atribuir paquetes ajenos
            sboms.append({"target": ref, "image_id": image_id, "packages": [],
                          "error": "el tag local apunta a otra imagen que la del contenedor (recrealo)"})
            continue
        sb.update({"target": ref, "image_id": image_id})
        LOG.info(f"SBOM {ref}: {len(sb['packages'])} paquetes en {time.time() - t0:.1f}s")
        try:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(sb, f)
        except Exception:
            pass
        sboms.append(sb)

    for path in scan_paths:
        if not os.path.isdir(path):
            continue
        sb = _run_syft(f"dir:{path}")
        if sb:
            # Los deb/rpm/apk ya están en el inventario del host
            sb["packages"] = [p for p in sb["packages"] if p["type"] not in ("deb", "rpm", "apk")]
            sb.update({"target": path, "image_id": None, "distro": None})
            if sb["packages"]:
                sboms.append(sb)
    return sboms


def collect_inventory(server_id, cfg=None, with_sbom=True):
    cfg = cfg or {}
    t0 = time.time()
    LOG.info(f"Recolectando inventario para '{server_id}'...")
    containers = get_docker_containers()
    listening = get_listening_sockets()
    payload = {
        "server_id": server_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "collector_version": COLLECTOR_VERSION,
        "os": get_os_info(),
        "docker": get_docker_info(),
        "containers": containers,
        "listening_ports_sample": sorted({f"{s['address']}:{s['port']}" for s in listening})[:50],
        "os_packages": get_os_packages(),
        "listening": listening,
        "services": get_services(),
        "host_config": get_host_config(),
        "sboms": get_sboms(containers, cfg.get("scan_paths", DEFAULT_SCAN_PATHS)) if with_sbom else [],
    }
    LOG.info(f"Inventario listo en {time.time() - t0:.2f}s ({len(containers)} contenedores, "
             f"{len(payload['os_packages'])} paquetes, {len(listening)} sockets, "
             f"{len(payload['sboms'])} SBOMs).")
    return payload


def send_report(api_url, token, payload, attempts=3):
    url = f"{api_url.rstrip('/')}/api/v1/report"
    data = gzip.compress(json.dumps(payload).encode("utf-8"))
    LOG.info(f"Payload: {len(data) / 1024:.0f} KiB comprimido")

    status, body = 0, ""
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Authorization": f"Bearer {token}",
                "User-Agent": f"VigiaAgent/{COLLECTOR_VERSION}",
            },
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                LOG.info(f"Reporte enviado con éxito en {time.time() - t0:.2f}s (HTTP {resp.status})")
                return resp.status, body
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read().decode("utf-8", errors="replace")
            LOG.error(f"Error HTTP {e.code} de la API (intento {attempt}/{attempts}): {body}")
            if e.code < 500:
                return status, body
        except Exception as e:
            status, body = 0, str(e)
            LOG.error(f"Fallo de conexión al enviar reporte (intento {attempt}/{attempts}): {e}")
        if attempt < attempts:
            time.sleep(10 * attempt)
    return status, body


def load_config(config_path):
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def main():
    parser = argparse.ArgumentParser(description="Vigía Agent - Recolector de Inventario Ligero")
    parser.add_argument("--config", default="/opt/vigia/config.json")
    parser.add_argument("--server-id")
    parser.add_argument("--api-url")
    parser.add_argument("--token")
    parser.add_argument("--log-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-sbom", action="store_true", help="No generar SBOMs con syft")
    args = parser.parse_args()

    setup_logger(args.log_file)
    cfg = load_config(args.config)
    server_id = args.server_id or cfg.get("server_id") or platform.node()
    api_url = args.api_url or cfg.get("api_url")
    token = args.token or cfg.get("token")

    payload = collect_inventory(server_id, cfg, with_sbom=not args.no_sbom and cfg.get("sbom", True))

    if args.dry_run:
        LOG.info("Modo --dry-run: imprimiendo JSON resultante.")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(0)

    if not api_url or not token:
        LOG.critical("Faltan parámetros: api_url o token (en config.json o por flags).")
        sys.exit(1)

    status, response = send_report(api_url, token, payload)
    if 200 <= status < 300:
        LOG.info("Ejecución finalizada con éxito.")
        sys.exit(0)
    else:
        LOG.critical(f"Fallo en el reporte: {response}")
        sys.exit(2)

if __name__ == "__main__":
    main()
