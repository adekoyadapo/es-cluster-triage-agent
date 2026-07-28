"""
config.py — credential loading and cluster configuration.

Mirrors the KEY=VALUE parsing from install/deploy_from_env.py and the
build_auth_header logic from install/install.py (lines 458-468).
"""
from __future__ import annotations

import base64
import getpass
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ClusterConf:
    name: str        # e.g. "cluster_1" or "local"
    url: str         # e.g. "https://es.10-0-10-52.sslip.io"
    auth_type: str   # "basic" or "apikey"
    username: str = ""
    password: str = ""
    api_key: str = ""  # raw id:secret or pre-encoded

    def espipe_auth_flags(self) -> list[str]:
        """Return espipe CLI auth flags (always includes -k for insecure TLS)."""
        flags = ["-k"]
        if self.auth_type == "apikey":
            # espipe -a accepts base64-encoded key
            key = self.api_key
            if ":" in key:
                key = base64.b64encode(key.encode()).decode()
            flags += ["-a", key]
        else:
            flags += ["-u", self.username, "-p", self.password]
        return flags

    def es_client_kwargs(self) -> dict:
        """Return kwargs for elasticsearch.Elasticsearch()."""
        kwargs: dict = {
            "hosts": [self.url],
            "verify_certs": False,
            "ssl_show_warn": False,
        }
        if self.auth_type == "apikey":
            key = self.api_key
            if ":" in key:
                kid, _, secret = key.partition(":")
                kwargs["api_key"] = (kid, secret)
            else:
                kwargs["api_key"] = key
        else:
            kwargs["basic_auth"] = (self.username, self.password)
        return kwargs


# ── .env file parsing ──────────────────────────────────────────────────────────

def load_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE / KEY="VALUE" lines; skip # comments and blanks."""
    vals: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def build_clusters(vals: dict[str, str]) -> list[ClusterConf]:
    """
    Scan for CLUSTER_<n>_URL keys and build a ClusterConf per cluster.
    Supports either CLUSTER_<n>_API_KEY or CLUSTER_<n>_USERNAME+PASSWORD.
    """
    clusters: list[ClusterConf] = []
    indices: list[int] = []

    for k in vals:
        m = re.match(r"^CLUSTER_(\d+)_URL$", k)
        if m:
            indices.append(int(m.group(1)))
    indices.sort()

    if not indices:
        # Fallback: single-cluster from flat keys (ES_URL / KB_URL style)
        url = vals.get("ES_URL") or vals.get("CLUSTER_URL", "")
        if url:
            conf = _build_one_cluster("cluster_1", url, vals,
                                       prefix="CLUSTER_", fallback_prefix="")
            clusters.append(conf)
    else:
        for n in indices:
            url = vals[f"CLUSTER_{n}_URL"]
            conf = _build_one_cluster(
                f"cluster_{n}", url, vals, prefix=f"CLUSTER_{n}_"
            )
            clusters.append(conf)

    return clusters


def _build_one_cluster(name: str, url: str, vals: dict,
                        prefix: str, fallback_prefix: str = "") -> ClusterConf:
    def get(key: str) -> str:
        return vals.get(f"{prefix}{key}", vals.get(f"{fallback_prefix}{key}", ""))

    api_key = get("API_KEY")
    username = get("USERNAME")
    password = get("PASSWORD")

    if api_key:
        return ClusterConf(name=name, url=url, auth_type="apikey", api_key=api_key)
    elif username and password:
        return ClusterConf(name=name, url=url, auth_type="basic",
                           username=username, password=password)
    else:
        raise ValueError(
            f"Cluster '{name}' ({url}): provide {prefix}API_KEY "
            f"or {prefix}USERNAME + {prefix}PASSWORD"
        )


# ── Duration parsing ───────────────────────────────────────────────────────────

def parse_duration(s: str) -> int:
    """Parse '30s', '5m', '2h', '90' (bare seconds) → int seconds."""
    s = s.strip().lower()
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("s"):
        return int(s[:-1])
    return int(s)


# ── Interactive credential collector ──────────────────────────────────────────

def collect_interactive() -> tuple[list[ClusterConf], int]:
    """Prompt for at least one cluster and duration; return (clusters, duration_secs)."""
    print("\n  ── Cluster Configuration ──────────────────────────────")
    clusters: list[ClusterConf] = []
    n = 1
    while True:
        print(f"\n  Cluster {n}:")
        url = input("    Elasticsearch URL (blank to finish): ").strip()
        if not url:
            if not clusters:
                print("  At least one cluster is required.")
                continue
            break
        auth_choice = input("    Auth: (1) API key  (2) username/password  [1]: ").strip() or "1"
        if auth_choice == "2":
            username = input("    Username: ").strip()
            password = getpass.getpass("    Password: ")
            clusters.append(ClusterConf(
                name=f"cluster_{n}", url=url, auth_type="basic",
                username=username, password=password
            ))
        else:
            api_key = getpass.getpass("    API key (id:secret or encoded): ")
            clusters.append(ClusterConf(
                name=f"cluster_{n}", url=url, auth_type="apikey", api_key=api_key
            ))
        n += 1

    raw_dur = input("\n  Run duration [5m]: ").strip() or "5m"
    duration = parse_duration(raw_dur)
    return clusters, duration
