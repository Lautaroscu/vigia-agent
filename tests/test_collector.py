"""Tests del collector sin Docker real (solo stdlib: python3 -m unittest)."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collector  # noqa: E402

INSPECT = [{
    "Id": "0123456789abcdef",
    "Name": "/traefik",
    "Image": "sha256:abc",
    "Created": "2026-10-01T00:00:00Z",
    "State": {"Status": "running"},
    "Config": {
        "Image": "traefik:v3.1",
        "User": "",
        "Env": ["TRAEFIK_VERSION=3.1.2", "API_TOKEN_VERSION=x", "PATH=/usr/bin"],
        "Labels": {
            "com.docker.compose.project": "edge",
            "com.docker.compose.service": "traefik",
            "com.docker.compose.project.working_dir": "/opt/stacks/edge",
            "traefik.http.middlewares.admin.basicauth.users": "admin:$apr1$hash",
            "app.api_key": "should-not-leak",
        },
    },
    "HostConfig": {"Privileged": True, "ReadonlyRootfs": False},
    "Mounts": [{"Source": "/var/run/docker.sock"}],
    "NetworkSettings": {"Ports": {
        "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}],
        "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}],
        "9000/tcp": None,
    }},
}]


def fake_run(cmd, timeout=10):
    if cmd[:2] == ["docker", "ps"]:
        return "0123456789ab"
    if cmd[:2] == ["docker", "inspect"]:
        return json.dumps(INSPECT)
    return None


class CollectorTest(unittest.TestCase):
    def setUp(self):
        p1 = mock.patch.object(collector, "run_command", side_effect=fake_run)
        p2 = mock.patch.object(collector.shutil, "which", return_value="/usr/bin/docker")
        p1.start(), p2.start()
        self.addCleanup(mock.patch.stopall)
        self.c = collector.get_docker_containers()[0]

    def test_ports_public_vs_loopback(self):
        ports = {p["container_port"]: p["is_public"] for p in self.c["ports"]}
        self.assertEqual(ports, {"443/tcp": True, "8080/tcp": False})

    def test_security_flags(self):
        sec = self.c["security"]
        self.assertTrue(sec["is_privileged"])
        self.assertTrue(sec["docker_sock_mounted"])
        self.assertTrue(sec["run_as_root"])

    def test_no_secrets_leave_the_server(self):
        self.assertNotIn("traefik.http.middlewares.admin.basicauth.users", self.c["labels"])
        self.assertNotIn("app.api_key", self.c["labels"])
        self.assertIn("com.docker.compose.project", self.c["labels"])
        self.assertEqual(self.c["version_envs"], {"TRAEFIK_VERSION": "3.1.2"})

    def test_compose_info(self):
        self.assertEqual(self.c["compose"]["service"], "traefik")
        self.assertEqual(self.c["compose"]["working_dir"], "/opt/stacks/edge")


if __name__ == "__main__":
    unittest.main()
