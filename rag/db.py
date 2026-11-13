"""Connection to the runbooks database (Postgres + pgvector running in the cluster).

No passwords in files and no manual port-forward:
- the password is read from the Kubernetes Secret "runbooks-db" (same access as kubectl);
- a private `kubectl port-forward` on 127.0.0.1 is opened on first use and closed at exit.
Set RUNBOOKS_DATABASE_URL to connect somewhere else directly (e.g. when running in-cluster).
"""

from __future__ import annotations

import atexit
import base64
import os
import socket
import subprocess
import time
from functools import lru_cache

import psycopg
from pgvector.psycopg import register_vector

from mcp_server import k8s

NAMESPACE = os.environ.get("RUNBOOKS_DB_NAMESPACE", "kube-sentinel")
SERVICE = "runbooks-db"
DB, USER = "kube_sentinel", "sentinel"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _password() -> str:
    secret = k8s.core().read_namespaced_secret(SERVICE, NAMESPACE)
    return base64.b64decode(secret.data["password"]).decode()


@lru_cache(maxsize=1)
def _tunnel_port() -> int:
    port = _free_port()
    proc = subprocess.Popen(
        ["kubectl", "-n", NAMESPACE, "port-forward", f"svc/{SERVICE}", f"{port}:5432", "--address", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    atexit.register(proc.terminate)
    for _ in range(50):  # wait up to ~10s for the tunnel
        if proc.poll() is not None:
            raise ConnectionError(f"kubectl port-forward failed: {proc.stderr.read().decode()[:300]}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return port
        except OSError:
            time.sleep(0.2)
    proc.terminate()
    raise ConnectionError("Timed out opening a tunnel to the runbooks database")


@lru_cache(maxsize=1)
def _dsn() -> str:
    direct = os.environ.get("RUNBOOKS_DATABASE_URL")
    if direct:
        return direct
    return (f"host=127.0.0.1 port={_tunnel_port()} dbname={DB} user={USER} "
            f"password={_password()} connect_timeout=10")


def connect() -> psycopg.Connection:
    conn = psycopg.connect(_dsn(), autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn
