#!/usr/bin/env python3
"""
Vigía Agent - Agente de Monitoreo Ligero y No Invasivo
Repositorio oficial: https://github.com/Lautaroscu/vigia-agent

Sin dependencias externas (solo stdlib). Recolecta inventario de solo lectura:
SO y paquetes instalados, sockets en escucha con su proceso y paquete dueño,
servicios systemd, configuración de seguridad del host, contenedores Docker y,
si `syft` está instalado, el SBOM de cada imagen (cacheado por image ID).
La detección de vulnerabilidades la hace la API central contra OSV.dev.

Desde 3.2 también junta señales de integridad del host (procesos, sockets raw,
persistencia, cuentas, SUID, módulos de kernel, llaves sueltas) para detectar
un compromiso ya ocurrido. `--mode integrity` corre solo eso, pensado para cada
15 min. Nunca envía secretos: ver docs/HOST_INTRUSION_DETECTION.md §6.
"""

import argparse
import base64
import glob
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
import stat
import struct
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

LOG = logging.getLogger("VigiaAgent")

COLLECTOR_VERSION = "3.2.0"
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

    images = _image_index([c.get("Config", {}).get("Image", "") for c in raw_containers]
                          + [c.get("Image", "") for c in raw_containers])
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
            # collector >= 3.2 (CTR-001..006)
            "user": run_as_user,
            "pid_mode": host_config.get("PidMode") or "",
            "security_opt": host_config.get("SecurityOpt") or [],
            "sensitive_mounts": [
                {"source": m.get("Source"), "destination": m.get("Destination"), "rw": bool(m.get("RW"))}
                for m in mounts
                if m.get("Type") == "bind" and (m.get("Source") or "").rstrip("/") in SENSITIVE_MOUNTS
            ],
            "image_created": images["created"].get(image_id),
            # Imagen a la que apunta hoy el tag: si difiere de image_id, se bajó pero no se recreó
            "tag_image_id": images["tags"].get(_norm_ref(image_name)),
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


SENSITIVE_MOUNTS = {"", "/etc", "/root", "/proc", "/boot", "/var/run/docker.sock", "/run/docker.sock"}


def _norm_ref(ref):
    """`nginx` -> `nginx:latest` (como lo guarda RepoTags)."""
    if not ref or "@" in ref or ref.startswith("sha256:"):
        return ref
    return ref if ":" in ref.rsplit("/", 1)[-1] else f"{ref}:latest"


def _image_index(refs):
    """{"tags": {tag: image id}, "created": {image id: fecha}} con un solo `docker image inspect`."""
    index = {"tags": {}, "created": {}}
    refs = sorted({r for r in refs if r})
    if not refs:
        return index
    out = run_command(["docker", "image", "inspect"] + refs, timeout=30, allow_fail=True)
    try:
        data = json.loads(out or "[]")
    except Exception:
        return index
    for img in data:
        index["created"][img.get("Id")] = img.get("Created")
        for tag in img.get("RepoTags") or []:
            index["tags"][tag] = img.get("Id")
    return index


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


def _container_of_pid(pid, root="/"):
    try:
        with open(_j(root, f"/proc/{pid}/cgroup"), "r", encoding="utf-8") as f:
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


def _same_image(ref, container_image_id, syft_image_id):
    """¿El tag que escaneó syft es la imagen que corre el contenedor?

    Con el image store de containerd (Docker >= 29) el contenedor guarda el digest del
    índice multi-arch y syft reporta el de la config: no son comparables entre sí. La
    referencia es el ID que Docker le asigna hoy al tag local, que usa el mismo espacio
    de nombres que el contenedor.
    """
    if not syft_image_id or syft_image_id == container_image_id:
        return True
    local_id = (run_command(["docker", "image", "inspect", "--format", "{{.Id}}", ref]) or "").strip()
    return local_id == container_image_id


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
        if not _same_image(ref, image_id, sb.get("image_id")):
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


# --- Integridad del host (collector 3.2) ---------------------------------------------
# El collector solo junta evidencia; las reglas (PROC-*, NET-*, PERS-*...) y la baseline
# viven en la API. `root` permite testear contra un árbol falso.

SHELL_COMMS = {"sh", "bash", "dash", "zsh", "ksh", "ash", "fish", "csh", "tcsh", "busybox"}
NETTOOL_COMMS = {"nc", "ncat", "netcat", "nc.openbsd", "nc.traditional", "socat", "telnet"}
INTERPRETER_RE = re.compile(r"^(python[\d.]*|perl[\d.]*|ruby[\d.]*|php[\w.-]*|lua[\d.]*)$")
NOLOGIN_SHELLS = ("nologin", "false", "sync", "shutdown", "halt")
MAX_PROCS = 3000
MAX_FDS_PER_PROC = 2000

CAP_NAMES = (
    "cap_chown", "cap_dac_override", "cap_dac_read_search", "cap_fowner", "cap_fsetid", "cap_kill",
    "cap_setgid", "cap_setuid", "cap_setpcap", "cap_linux_immutable", "cap_net_bind_service",
    "cap_net_broadcast", "cap_net_admin", "cap_net_raw", "cap_ipc_lock", "cap_ipc_owner",
    "cap_sys_module", "cap_sys_rawio", "cap_sys_chroot", "cap_sys_ptrace", "cap_sys_pacct",
    "cap_sys_admin", "cap_sys_boot", "cap_sys_nice", "cap_sys_resource", "cap_sys_time",
    "cap_sys_tty_config", "cap_mknod", "cap_lease", "cap_audit_write", "cap_audit_control",
    "cap_setfcap", "cap_mac_override", "cap_mac_admin", "cap_syslog", "cap_wake_alarm",
    "cap_block_suspend", "cap_audit_read", "cap_perfmon", "cap_bpf", "cap_checkpoint_restore",
)

# Orden: primero donde suele aparecer lo plantado, así un corte por presupuesto no lo pierde.
SUID_WALK_ROOTS = ["/tmp", "/var/tmp", "/dev/shm", "/home", "/root", "/opt", "/srv", "/usr/local",
                   "/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/libexec", "/usr/lib", "/lib", "/lib64"]
WALK_SKIP = {"/proc", "/sys", "/var/lib/docker", "/var/lib/containerd"}
KEY_WALK_ROOTS = ["/root", "/home", "/opt", "/srv"]
KEY_SKIP_DIRS = {"node_modules", ".git", ".cache", ".npm", ".venv", "venv", "site-packages",
                 "dist-packages", "__pycache__", "_work"}
KEY_HEADERS = ((b"OPENSSH PRIVATE KEY", "openssh"), (b"RSA PRIVATE KEY", "rsa"),
               (b"EC PRIVATE KEY", "ec"), (b"DSA PRIVATE KEY", "dsa"),
               (b"ENCRYPTED PRIVATE KEY", "pkcs8-encrypted"), (b"BEGIN PRIVATE KEY", "pkcs8"),
               (b"PuTTY-User-Key-File", "putty"))

CRON_FILES = ["/etc/crontab", "/etc/anacrontab"]
CRON_TAB_DIRS = ["/etc/cron.d", "/var/spool/cron/crontabs", "/var/spool/cron"]
CRON_SCRIPT_DIRS = ["/etc/cron.hourly", "/etc/cron.daily", "/etc/cron.weekly", "/etc/cron.monthly"]
UNIT_SUFFIXES = (".service", ".timer", ".socket", ".path")
VENDOR_UNIT_DIRS = ["/usr/lib/systemd/system", "/lib/systemd/system"]
SHELL_RC_SYSTEM = ["/etc/profile", "/etc/bash.bashrc", "/etc/zsh/zshrc", "/etc/environment"]
SHELL_RC_USER = [".bashrc", ".profile", ".bash_profile", ".bash_login", ".bash_logout", ".zshrc"]
PAM_MODULE_GLOBS = ["/lib/security/*.so", "/lib64/security/*.so", "/usr/lib/security/*.so",
                    "/usr/lib64/security/*.so", "/lib/*/security/*.so", "/usr/lib/*/security/*.so"]


class _Truncated(list):
    """Resultado parcial: el check se cortó por presupuesto de tiempo o de archivos."""


def _j(root, path):
    """Path lógico del host dentro de `root` (en prod root es "/")."""
    return path if root == "/" else os.path.join(root, path.lstrip("/"))


def _read(path, limit=1 << 20, binary=False):
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
        return data if binary else data.decode("utf-8", errors="replace")
    except Exception:
        return None


def _readlink(path):
    try:
        return os.readlink(path)
    except OSError:
        return None


def _sha256_file(path, max_bytes=100 << 20):
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _ls(root, d, limit=500):
    """Paths lógicos de los archivos regulares de `d` (sin recursión)."""
    try:
        names = sorted(os.listdir(_j(root, d)))
    except Exception:
        return []
    out = [f"{d.rstrip('/')}/{n}" for n in names]
    return [p for p in out if os.path.isfile(_j(root, p))][:limit]


# Redacción (§6): valores de flags/variables con nombre sensible, credenciales en URLs
# y cualquier token largo con pinta de secreto.
_SECRET_NAME = re.compile(r"pass|token|secret|key|auth|credential", re.I)
_URL_CREDS = re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]*@", re.I)
_HEX_TOKEN = re.compile(r"^[A-Fa-f0-9]{32,}$")
_B64_TOKEN = re.compile(r"^[A-Za-z0-9+/_-]{32,}={0,2}$")


def _secretish(tok):
    if len(tok) < 32:
        return False
    if _HEX_TOKEN.match(tok):
        return True
    return bool(_B64_TOKEN.match(tok) and not tok.startswith("/") and re.search(r"[a-z]", tok)
                and re.search(r"[A-Z]", tok) and re.search(r"\d", tok))


def redact(text, limit=512):
    if not text:
        return ""
    text = _URL_CREDS.sub(r"\1***@", text)
    out, hide_next = [], False
    for tok in text.split():
        if hide_next and not tok.startswith("-"):
            out.append("***")
            hide_next = False
            continue
        hide_next = False
        name, eq, _ = tok.partition("=")
        if eq and _SECRET_NAME.search(name):
            out.append(f"{name}=***")
        elif tok.startswith("-") and _SECRET_NAME.search(tok):
            out.append(tok)
            hide_next = True
        else:
            parts = re.split(r"([\s'\":,;=@])", tok)
            out.append("".join("***" if _secretish(p) else p for p in parts))
    s = " ".join(out)
    return s if len(s) <= limit else s[:limit] + "…"


def _redact_lines(text, limit=1024):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip() and not l.strip().startswith("#")]
    s = "\n".join(redact(l, 256) for l in lines)
    return s if len(s) <= limit else s[:limit] + "…"


def _passwd(root="/"):
    users = []
    for line in (_read(_j(root, "/etc/passwd")) or "").splitlines():
        p = line.split(":")
        if len(p) >= 7 and p[2].isdigit():
            users.append({"user": p[0], "uid": int(p[2]), "home": p[5], "shell": p[6]})
    return users


def _login_shell(shell):
    return bool(shell) and not shell.rstrip().endswith(NOLOGIN_SHELLS)


def _homes(root="/"):
    """{home: usuario} de las cuentas con home real (incluye root)."""
    homes = {}
    for u in _passwd(root):
        h = u["home"]
        if h and h not in ("/", "/nonexistent") and os.path.isdir(_j(root, h)):
            homes.setdefault(h, u["user"])
    return homes


def is_shellish(comm):
    return comm in SHELL_COMMS or comm in NETTOOL_COMMS or bool(INTERPRETER_RE.match(comm or ""))


def _proc_status(text):
    st = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        st[k] = v.strip()
    return st


def scan_processes(root="/"):
    """Una pasada por /proc: procesos, mapa inode de socket -> pid, y un pid por netns."""
    users = {u["uid"]: u["user"] for u in _passwd(root)}
    try:
        pids = sorted(int(d) for d in os.listdir(_j(root, "/proc")) if d.isdigit())
    except Exception:
        return [], {}, {}
    procs, sock_inodes, netns = [], {}, {}
    for pid in pids[:MAX_PROCS]:
        base = _j(root, f"/proc/{pid}")
        st = _proc_status(_read(f"{base}/status", 8192))
        exe_raw = _readlink(f"{base}/exe")
        if not st or not exe_raw:
            continue  # hilo de kernel, proceso que terminó o sin permisos
        deleted = exe_raw.endswith(" (deleted)")
        exe = exe_raw[: -len(" (deleted)")] if deleted else exe_raw
        std = [_readlink(f"{base}/fd/{fd}") or "" for fd in (0, 1, 2)]
        try:
            fds = os.listdir(f"{base}/fd")[:MAX_FDS_PER_PROC]
        except Exception:
            fds = []
        for fd in fds:
            link = _readlink(f"{base}/fd/{fd}") or ""
            if link.startswith("socket:["):
                sock_inodes.setdefault(link[8:-1], pid)
        ns = _readlink(f"{base}/ns/net")
        if ns:
            netns.setdefault(ns, pid)
        # Del entorno solo sale LD_PRELOAD; el resto puede tener secretos.
        env = _read(f"{base}/environ", 1 << 16, binary=True) or b""
        ld = next((v[11:] for v in env.split(b"\0") if v.startswith(b"LD_PRELOAD=")), None)
        uid = int(((st.get("Uid") or "0").split() or ["0"])[0])
        cid = _container_of_pid(pid, root)
        procs.append({
            "pid": pid,
            "ppid": int(st.get("PPid") or 0),
            "comm": ((_read(f"{base}/comm", 64) or st.get("Name") or "").strip()),
            "exe": exe,
            "exe_deleted": deleted,
            "exe_sha256": None,
            "owner_package": None,
            "container_id": cid,
            "user": str(uid) if cid else users.get(uid, str(uid)),
            "cmdline": redact((_read(f"{base}/cmdline", 4096) or "").replace("\x00", " ").strip()),
            "ld_preload": ld.decode("utf-8", errors="replace")[:256] if ld else None,
            # stdin + stdout/stderr a un socket: forma de reverse shell (journald solo toma 1 y 2)
            "std_fds_socket": std[0].startswith("socket:") and (std[1].startswith("socket:")
                                                                 or std[2].startswith("socket:")),
        })
    return procs, sock_inodes, netns


def _hex_addr(s, ipv6):
    ip_hex, _, port_hex = s.partition(":")
    raw = bytes.fromhex(ip_hex)
    if ipv6:
        raw = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))
    else:
        raw = raw[::-1]
    return str(ipaddress.ip_address(raw)), int(port_hex, 16)


def _is_global(addr):
    try:
        ip = ipaddress.ip_address(addr)
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        return ip.is_global
    except ValueError:
        return False


def scan_network(procs, sock_inodes, netns, root="/"):
    """Conexiones salientes y sockets raw/packet de cada netns (host y contenedores)."""
    by_pid = {p["pid"]: p for p in procs}
    conns, outbound, raw = [], {}, {}
    for pid in netns.values():
        base = _j(root, f"/proc/{pid}/net")
        for name, v6 in (("tcp", False), ("tcp6", True)):
            for line in (_read(f"{base}/{name}") or "").splitlines()[1:]:
                f = line.split()
                if len(f) < 10 or f[3] != "01":  # 01 = ESTABLISHED
                    continue
                p = by_pid.get(sock_inodes.get(f[9]))
                try:
                    rip, rport = _hex_addr(f[2], v6)
                except ValueError:
                    continue
                if not p or not _is_global(rip):
                    continue
                key = (p["exe"], rport, p["container_id"])
                outbound[key] = outbound.get(key, 0) + 1
                if is_shellish(p["comm"]):  # IPs remotas solo para shells/intérpretes (§6)
                    conns.append({"pid": p["pid"], "comm": p["comm"], "exe": p["exe"],
                                  "remote": f"{rip}:{rport}", "container_id": p["container_id"]})
        for name, kind in (("packet", "packet"), ("raw", "raw"), ("raw6", "raw")):
            for line in (_read(f"{base}/{name}") or "").splitlines()[1:]:
                f = line.split()
                if not f:
                    continue
                inode = f[-1] if kind == "packet" else (f[9] if len(f) > 9 else "")
                p = by_pid.get(sock_inodes.get(inode)) or {}
                key = (kind, p.get("exe"), p.get("container_id"))
                raw.setdefault(key, {"kind": kind, "pid": p.get("pid"), "comm": p.get("comm"),
                                     "exe": p.get("exe"), "container_id": p.get("container_id"),
                                     "owner_package": None})
    return {
        "connections": conns[:200],
        "outbound": [{"exe": k[0], "port": k[1], "container_id": k[2], "count": v}
                     for k, v in sorted(outbound.items(), key=lambda kv: -kv[1])][:500],
        "raw_sockets": list(raw.values())[:200],
    }


def _systemd_items(root, homes):
    items, seen = [], set()

    def add(path, vendor):
        full = _j(root, path)
        real = os.path.realpath(full)
        if real in seen:
            return
        seen.add(real)
        item = {"kind": "systemd", "path": path, "sha256": _sha256_file(full), "owner_package": None,
                "entry": _redact_lines("\n".join(l for l in (_read(full, 65536) or "").splitlines()
                                                 if l.strip().startswith("Exec")))}
        if os.path.islink(full):
            item["target"] = _readlink(full)
        if vendor:
            item["_vendor"] = True
        items.append(item)

    admin_dirs = ["/etc/systemd/system"] + [f"{h.rstrip('/')}/.config/systemd/user" for h in homes]
    vendor_real = {os.path.realpath(_j(root, d)) for d in VENDOR_UNIT_DIRS}
    for top in admin_dirs:
        top_full = _j(root, top)
        for dirpath, dirnames, filenames in os.walk(top_full):
            if dirpath.count(os.sep) - top_full.count(os.sep) >= 2:
                dirnames[:] = []
            for n in sorted(filenames):
                full = os.path.join(dirpath, n)
                if not (n.endswith(UNIT_SUFFIXES) or (n.endswith(".conf") and dirpath.endswith(".d"))):
                    continue
                if os.path.islink(full):
                    real = os.path.realpath(full)
                    # Habilitaciones (*.wants) y units enmascaradas: el destino se cubre aparte
                    if real == "/dev/null" or os.path.dirname(real) in vendor_real:
                        continue
                add(top.rstrip("/") + "/" + os.path.relpath(full, top_full).replace(os.sep, "/"), False)
    for d in VENDOR_UNIT_DIRS:
        for path in _ls(root, d, limit=2000):
            if path.endswith(UNIT_SUFFIXES):
                add(path, True)
    return items


def get_persistence(root="/"):
    """Cron, systemd, rc de shells, rc.local/init.d, PAM y sudoers (§4.3)."""
    homes = _homes(root)
    items = []

    def item(kind, path, entry=None, lookup=False, **extra):
        full = _j(root, path)
        it = {"kind": kind, "path": path, "sha256": _sha256_file(full), "owner_package": None,
              "entry": entry}
        if lookup:
            it["_lookup"] = True
        it.update(extra)
        items.append(it)

    for path in CRON_FILES + [p for d in CRON_TAB_DIRS for p in _ls(root, d)]:
        if os.path.isfile(_j(root, path)):
            item("cron", path, _redact_lines(_read(_j(root, path), 65536)))
    for d in CRON_SCRIPT_DIRS:
        for path in _ls(root, d):
            item("cron", path, lookup=True)

    items.extend(_systemd_items(root, homes))

    for path in SHELL_RC_SYSTEM + _ls(root, "/etc/profile.d") + [
            f"{h.rstrip('/')}/{n}" for h in homes for n in SHELL_RC_USER]:
        if os.path.isfile(_j(root, path)):
            item("shell_rc", path)  # solo hash: suelen tener `export TOKEN=...`

    for path in ["/etc/rc.local"] + _ls(root, "/etc/init.d"):
        if os.path.isfile(_j(root, path)):
            item("rc_local", path, lookup=True)

    for path in _ls(root, "/etc/pam.d"):
        item("pam", path, _redact_lines(_read(_j(root, path), 65536)))
    mods = set()
    for pattern in PAM_MODULE_GLOBS:
        for full in glob.glob(_j(root, pattern)):
            real = os.path.realpath(full)
            if real not in mods:
                mods.add(real)
                item("pam_module", "/" + os.path.relpath(full, root).replace(os.sep, "/")
                     if root != "/" else full, lookup=True)

    for path in ["/etc/sudoers"] + _ls(root, "/etc/sudoers.d"):
        text = _read(_j(root, path), 65536)
        if text is not None:
            item("sudoers", path, _redact_lines(text),
                 nopasswd_all=bool(re.search(r"^[^#]*NOPASSWD:\s*ALL\b", text, re.M)))
    return items[:1500]


_AUTH_KEY_RE = re.compile(r"(?:^|\s)((?:sk-)?(?:ssh|ecdsa)-[\w@.-]+)\s+([A-Za-z0-9+/]+={0,3})(?:\s+(.*))?$")


def parse_authorized_key(line):
    m = _AUTH_KEY_RE.search(line.strip())
    if not m:
        return None
    try:
        blob = base64.b64decode(m.group(2), validate=True)
    except Exception:
        return None
    fp = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    opts_raw = line.strip()[:m.start(1)].strip()
    options = [redact(o, 200) for o in re.findall(r'(?:[^,"]|"[^"]*")+', opts_raw)]
    return {"fingerprint": f"SHA256:{fp}", "type": m.group(1),
            "comment": (m.group(3) or "").strip()[:200], "options": options}


def get_ssh_keys(root="/"):
    keys = []
    for home, user in _homes(root).items():
        for name in ("authorized_keys", "authorized_keys2"):
            path = f"{home.rstrip('/')}/.ssh/{name}"
            for line in (_read(_j(root, path), 1 << 20) or "").splitlines():
                if line.strip() and not line.strip().startswith("#"):
                    k = parse_authorized_key(line)
                    if k:
                        k.update({"user": user, "path": path})
                        keys.append(k)
    return keys[:1000]


def get_accounts(root="/"):
    """UID 0, cuentas con shell de login y contraseñas vacías. De shadow solo sale el booleano."""
    empty = set()
    for line in (_read(_j(root, "/etc/shadow")) or "").splitlines():
        p = line.split(":")
        if len(p) >= 2 and p[1] == "":
            empty.add(p[0])
    return [{"user": u["user"], "uid": u["uid"], "shell": u["shell"], "empty_password": u["user"] in empty}
            for u in _passwd(root) if u["uid"] == 0 or _login_shell(u["shell"]) or u["user"] in empty]


def get_ssh_logins(minutes):
    if not shutil.which("journalctl"):
        return None
    out = run_command(["journalctl", "-u", "ssh", "-u", "sshd", "--since", f"{minutes} min ago",
                       "--no-pager", "-q", "-o", "cat"], timeout=20, allow_fail=True)
    agg = {}
    for m in re.finditer(r"Accepted (\S+) for (\S+) from (\S+) port", out or ""):
        key = (m.group(2), m.group(3), m.group(1))
        agg[key] = agg.get(key, 0) + 1
    return [{"user": u, "ip": ip, "method": meth, "count": n} for (u, ip, meth), n in agg.items()][:500]


def _decode_caps(blob):
    """xattr security.capability (vfs_cap_data) -> nombres de las capabilities permitidas."""
    if not blob or len(blob) < 8:
        return None
    bits = struct.unpack_from("<I", blob, 4)[0]
    if len(blob) >= 16:
        bits |= struct.unpack_from("<I", blob, 12)[0] << 32
    return [CAP_NAMES[i] if i < len(CAP_NAMES) else f"cap_{i}" for i in range(64) if bits >> i & 1]


def _walk(root, tops, budget_s, max_files, state, max_depth=None, skip_names=()):
    """Genera (DirEntry, path lógico) de archivos regulares sin cruzar de filesystem.
    Si se corta por presupuesto deja state["truncated"] = True."""
    deadline = time.time() + budget_s
    root_real = os.path.realpath(root)
    skip = {os.path.realpath(_j(root, p)) for p in WALK_SKIP}
    seen, n = set(), 0
    for top in tops:
        real = os.path.realpath(_j(root, top))
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        dev = os.stat(real).st_dev
        stack = [(real, 0)]
        while stack:
            d, depth = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    n += 1
                    if n > max_files or (n % 512 == 0 and time.time() > deadline):
                        state["truncated"] = True
                        return
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if (e.path in skip or e.name in skip_names
                                    or (max_depth is not None and depth + 1 >= max_depth)
                                    or os.lstat(e.path).st_dev != dev):
                                continue
                            stack.append((e.path, depth + 1))
                        elif e.is_file(follow_symlinks=False):
                            logical = e.path if root == "/" else \
                                "/" + os.path.relpath(e.path, root_real).replace(os.sep, "/")
                            yield e, logical
                    except OSError:
                        continue


def scan_suid_caps(root="/", budget_s=3.0, max_files=200_000):
    """Binarios SUID/SGID y con file capabilities (§4.4, §4.8)."""
    state = {}
    out = []
    for e, logical in _walk(root, SUID_WALK_ROOTS, budget_s, max_files, state):
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        caps = None
        if st.st_mode & 0o111 and hasattr(os, "getxattr"):
            try:
                caps = _decode_caps(os.getxattr(e.path, "security.capability", follow_symlinks=False))
            except OSError:
                pass
        if st.st_mode & (stat.S_ISUID | stat.S_ISGID) or caps:
            out.append({"path": logical, "mode": format(stat.S_IMODE(st.st_mode), "04o"),
                        "uid": st.st_uid, "owner_package": None, "caps": caps})
    return _Truncated(out[:2000]) if state else out[:2000]


def find_loose_keys(root="/", budget_s=2.0, max_files=50_000):
    """Llaves privadas en disco (§4.7). Del archivo solo se leen los primeros 64 bytes."""
    users = {u["uid"]: u["user"] for u in _passwd(root)}
    state = {}
    out = []
    for e, logical in _walk(root, KEY_WALK_ROOTS, budget_s, max_files, state,
                            max_depth=4, skip_names=KEY_SKIP_DIRS):
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if st.st_size > 16384 or st.st_size == 0:
            continue
        head = _read(e.path, 64, binary=True) or b""
        ktype = next((t for marker, t in KEY_HEADERS if marker in head), None)
        if not ktype and not (e.name.startswith("id_") and "." not in e.name):
            continue
        out.append({"path": logical, "mode": format(stat.S_IMODE(st.st_mode), "04o"),
                    "owner": users.get(st.st_uid, str(st.st_uid)), "type": ktype or "unknown",
                    "in_ssh_dir": "/.ssh/" in logical,
                    "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return _Truncated(out[:200]) if state else out[:200]


def get_kernel_state(root="/"):
    mods = sorted(l.split()[0] for l in (_read(_j(root, "/proc/modules")) or "").splitlines() if l.strip())
    tainted = (_read(_j(root, "/proc/sys/kernel/tainted")) or "0").strip()
    preload = _read(_j(root, "/etc/ld.so.preload"), 4096)
    return {"modules": mods[:1000], "tainted": int(tainted) if tainted.isdigit() else 0,
            # None = no existe; "" = existe vacío
            "ld_so_preload": " ".join(preload.split())[:512] if preload is not None else None}


def get_pkg_verify(timeout=300):
    """Archivos de paquetes con el digest cambiado (§4.5). Solo en modo full."""
    if shutil.which("dpkg"):
        out = run_command(["dpkg", "--verify"], timeout=timeout, allow_fail=True)
    elif shutil.which("rpm"):
        out = run_command(["rpm", "-Va", "--nomtime"], timeout=timeout, allow_fail=True)
    else:
        return None
    if out is None:
        raise RuntimeError("el verificador de paquetes falló o superó el timeout")
    items = []
    for line in out.splitlines():
        m = re.match(r"^(\S{9})\s+(?:([cdglr])\s+)?(/.*)$", line)
        if m and m.group(1)[2] == "5":
            items.append({"path": m.group(3), "flags": m.group(1), "conffile": m.group(2) == "c",
                          "package": None})
    return items[:500]


def collect_integrity(mode="full", root="/"):
    checks = []

    def check(name, fn, default):
        t0 = time.time()
        try:
            res = fn()
            status = "skipped" if res is None else "truncated" if isinstance(res, _Truncated) else "ok"
            detail = None
        except Exception as e:
            res, status, detail = None, "error", f"{type(e).__name__}: {e}"[:200]
        checks.append({"check": name, "status": status, "detail": detail,
                       "ms": int((time.time() - t0) * 1000)})
        return default if res is None else list(res) if isinstance(res, _Truncated) else res

    integrity_mode = mode == "integrity"
    procs, sock_inodes, netns = check("processes", lambda: scan_processes(root), ([], {}, {}))
    net = check("network", lambda: scan_network(procs, sock_inodes, netns, root),
                {"connections": [], "outbound": [], "raw_sockets": []})
    persistence = check("persistence", lambda: get_persistence(root), [])
    ssh_keys = check("ssh_keys", lambda: get_ssh_keys(root), [])
    accounts = check("accounts", lambda: get_accounts(root), [])
    ssh_logins = check("ssh_logins", lambda: get_ssh_logins(16 if integrity_mode else 24 * 60)
                       if root == "/" else None, [])
    suid = check("suid", lambda: scan_suid_caps(root, budget_s=3.0 if integrity_mode else 60.0), [])
    kernel = check("kernel", lambda: get_kernel_state(root), {})
    loose_keys = check("loose_keys", lambda: find_loose_keys(root, budget_s=2.0 if integrity_mode else 30.0), [])
    if integrity_mode:
        pkg_verify = []
        checks.append({"check": "pkg_verify", "status": "skipped", "detail": "solo en modo full", "ms": 0})
    else:
        pkg_verify = check("pkg_verify", get_pkg_verify, [])

    # Paquete dueño de todo en una sola invocación del gestor (dpkg -S es caro).
    host_procs = [p for p in procs if not p["container_id"]]
    lookup = ({p["exe"] for p in host_procs}
              | {r["exe"] for r in net["raw_sockets"] if r["exe"] and not r["container_id"]}
              | {i["path"] for i in persistence if i.get("_lookup") or i.get("_vendor")}
              | {s["path"] for s in suid} | {v["path"] for v in pkg_verify})
    owners = check("owners", lambda: _owner_packages(sorted(lookup)), {})

    hashes = {}
    for p in host_procs:
        p["owner_package"] = owners.get(p["exe"])
        if not p["owner_package"]:
            # Por /proc/PID/exe: funciona aunque el binario se haya borrado
            if p["exe"] not in hashes:
                hashes[p["exe"]] = _sha256_file(_j(root, f"/proc/{p['pid']}/exe"))
            p["exe_sha256"] = hashes[p["exe"]]
    for r in net["raw_sockets"]:
        if r["exe"] and not r["container_id"]:
            r["owner_package"] = owners.get(r["exe"])
    for s in suid:
        s["owner_package"] = owners.get(s["path"])
    for v in pkg_verify:
        v["package"] = owners.get(v["path"])
    kept = []
    for i in persistence:
        if i.get("_lookup") or i.get("_vendor"):
            i["owner_package"] = owners.get(i["path"])
        if i.pop("_vendor", False) and i["owner_package"]:
            continue  # units de paquetes: su integridad la cubre pkg_verify
        i.pop("_lookup", None)
        kept.append(i)

    return {
        "processes": procs,
        "connections": net["connections"],
        "outbound": net["outbound"],
        "raw_sockets": net["raw_sockets"],
        "persistence": kept,
        "ssh_keys": ssh_keys,
        "accounts": accounts,
        "ssh_logins": ssh_logins,
        "suid": suid,
        "kernel": kernel,
        "pkg_verify": pkg_verify,
        "loose_keys": loose_keys,
        "checks": checks,
    }


def collect_inventory(server_id, cfg=None, with_sbom=True, mode="full"):
    cfg = cfg or {}
    t0 = time.time()
    LOG.info(f"Recolectando inventario para '{server_id}' (modo {mode})...")
    containers = get_docker_containers()
    payload = {
        "server_id": server_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "collector_version": COLLECTOR_VERSION,
        "mode": mode,
        "collector_sha256": _sha256_file(os.path.abspath(__file__)),
        "os": get_os_info(),
        "docker": get_docker_info(),
        "containers": containers,
    }
    if mode == "full":
        listening = get_listening_sockets()
        payload.update({
            "listening_ports_sample": sorted({f"{s['address']}:{s['port']}" for s in listening})[:50],
            "os_packages": get_os_packages(),
            "listening": listening,
            "services": get_services(),
            "host_config": get_host_config(),
            "sboms": get_sboms(containers, cfg.get("scan_paths", DEFAULT_SCAN_PATHS)) if with_sbom else [],
        })
    if cfg.get("integrity", True):
        payload["integrity"] = collect_integrity(mode)
    integ = payload.get("integrity") or {}
    LOG.info(f"Inventario listo en {time.time() - t0:.2f}s ({len(containers)} contenedores, "
             f"{len(payload.get('os_packages', []))} paquetes, {len(payload.get('listening', []))} sockets, "
             f"{len(payload.get('sboms', []))} SBOMs, {len(integ.get('processes', []))} procesos, "
             f"checks con problemas: {[c['check'] for c in integ.get('checks', []) if c['status'] != 'ok']}).")
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
    parser.add_argument("--mode", choices=["full", "integrity"], default="full",
                        help="full: inventario + SBOM + integridad (diario). "
                             "integrity: solo checks de integridad, liviano (cada 15 min)")
    args = parser.parse_args()

    setup_logger(args.log_file)
    cfg = load_config(args.config)
    server_id = args.server_id or cfg.get("server_id") or platform.node()
    api_url = args.api_url or cfg.get("api_url")
    token = args.token or cfg.get("token")

    payload = collect_inventory(server_id, cfg, mode=args.mode,
                                with_sbom=not args.no_sbom and cfg.get("sbom", True))

    if args.dry_run:
        LOG.info("Modo --dry-run: imprimiendo JSON resultante.")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(0)

    if not api_url or not token:
        LOG.critical("Faltan parámetros: api_url o token (en config.json o por flags).")
        sys.exit(1)

    # En modo integrity no tiene sentido reintentar por minutos: en 15 min corre otra vez.
    status, response = send_report(api_url, token, payload, attempts=3 if args.mode == "full" else 1)
    if 200 <= status < 300:
        LOG.info("Ejecución finalizada con éxito.")
        sys.exit(0)
    else:
        LOG.critical(f"Fallo en el reporte: {response}")
        sys.exit(2)

if __name__ == "__main__":
    main()
