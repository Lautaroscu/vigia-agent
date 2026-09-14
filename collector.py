#!/usr/bin/env python3
"""
Vigía Agent - Agente de Monitoreo Ligero y No Invasivo
Repositorio oficial: https://github.com/Lautaroscu/vigia-agent

Diseñado para ejecutarse en servidores Linux/Docker en < 2 segundos sin dependencias externas.
"""

import argparse
import json
import logging
import logging.handlers
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

LOG = logging.getLogger("VigiaAgent")

def setup_logger(log_file_path=None):
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s UTC] [%(levelname)s] [PID %(process)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

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


def run_command(cmd, timeout=10):
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0:
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
    }
    if os.path.exists("/etc/os-release"):
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("NAME="):
                        info["distro"] = line.split("=", 1)[1].strip().strip('"')
                    elif line.startswith("VERSION_ID="):
                        info["distro_version"] = line.split("=", 1)[1].strip().strip('"')
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
            "read_only_rootfs": host_config.get("ReadonlyRootfs", False)
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
        for env in c.get("Config", {}).get("Env", []):
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
                if not any(sec in k.lower() for sec in ["secret", "pass", "token"])
            }
        })

    return containers


def get_listening_ports():
    listening = []
    out = None
    if shutil.which("ss"):
        out = run_command(["ss", "-tulpn"])
    elif shutil.which("netstat"):
        out = run_command(["netstat", "-tulpn"])

    if not out:
        return listening

    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("Netid") or line.startswith("Active"):
            continue
        parts = line.split()
        if "LISTEN" in parts:
            try:
                idx = parts.index("LISTEN")
                if len(parts) > idx + 1:
                    listening.append(parts[idx + 1])
            except Exception:
                pass
    return list(set(listening))[:50]


def collect_inventory(server_id):
    t0 = time.time()
    LOG.info(f"Recolectando inventario para '{server_id}'...")
    payload = {
        "server_id": server_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "collector_version": "2.0.0",
        "os": get_os_info(),
        "docker": get_docker_info(),
        "containers": get_docker_containers(),
        "listening_ports_sample": get_listening_ports(),
    }
    LOG.info(f"Inventario listo en {time.time() - t0:.2f}s ({len(payload['containers'])} contenedores).")
    return payload


def send_report(api_url, token, payload):
    url = f"{api_url.rstrip('/')}/api/v1/report"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "VigiaAgent/2.0.0",
        },
        method="POST",
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            LOG.info(f"Reporte enviado con éxito en {time.time() - t0:.2f}s (HTTP {resp.status})")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        LOG.error(f"Error HTTP {e.code} de la API: {body}")
        return e.code, body
    except Exception as e:
        LOG.error(f"Fallo de conexión al enviar reporte: {e}")
        return 0, str(e)


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
    args = parser.parse_args()

    setup_logger(args.log_file)
    cfg = load_config(args.config)
    server_id = args.server_id or cfg.get("server_id") or platform.node()
    api_url = args.api_url or cfg.get("api_url")
    token = args.token or cfg.get("token")

    payload = collect_inventory(server_id)

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
