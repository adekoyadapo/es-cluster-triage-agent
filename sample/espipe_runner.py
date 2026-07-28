"""
espipe_runner.py — espipe bootstrap and ingest wrapper.

espipe (https://github.com/vimcommando/espipe) is a Rust bulk-ingest CLI:
    espipe [OPTIONS] <INPUT> <OUTPUT>
where INPUT can be '-' (stdin NDJSON) and OUTPUT is an ES index/datastream URL.

Bootstrap order:
  1. shutil.which("espipe") — already installed
  2. cargo install espipe   — if cargo is available
  3. docker run vimcommando/espipe — fallback
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator

from .config import ClusterConf

log = logging.getLogger(__name__)

_espipe_cmd: list[str] | None = None  # cached after bootstrap


def ensure_espipe() -> list[str]:
    """
    Return the command prefix to invoke espipe.
    Installs it via cargo or falls back to docker if not found.
    Caches the result after first call.
    """
    global _espipe_cmd
    if _espipe_cmd is not None:
        return _espipe_cmd

    # 1. Already on PATH
    if shutil.which("espipe"):
        _espipe_cmd = ["espipe"]
        log.info("espipe found at: %s", shutil.which("espipe"))
        return _espipe_cmd

    # 2. cargo install
    cargo = shutil.which("cargo")
    if cargo:
        log.info("espipe not found — installing via cargo (this may take a minute)...")
        print("  Installing espipe via cargo (one-time, ~1 min)...")
        result = subprocess.run(
            ["cargo", "install", "espipe"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # cargo puts binaries in ~/.cargo/bin
            cargo_bin = Path.home() / ".cargo" / "bin" / "espipe"
            if cargo_bin.exists():
                _espipe_cmd = [str(cargo_bin)]
                log.info("espipe installed at: %s", cargo_bin)
                return _espipe_cmd
        log.warning("cargo install espipe failed:\n%s", result.stderr[-500:])

    # 3. Docker fallback
    if shutil.which("docker"):
        log.info("Using docker run vimcommando/espipe as fallback")
        _espipe_cmd = [
            "docker", "run", "--rm", "-i",
            "--network", "host",
            "vimcommando/espipe",
        ]
        return _espipe_cmd

    raise RuntimeError(
        "espipe not found and could not be installed.\n"
        "Install it manually: cargo install espipe\n"
        "Or via Docker: docker pull vimcommando/espipe"
    )


def ingest_lines(
    lines: Iterator[str],
    conf: ClusterConf,
    index_name: str,
    *,
    batch_size: int = 5000,
    max_requests: int = 16,
    action: str = "create",
) -> dict:
    """
    Stream NDJSON lines to espipe for bulk ingest into one index/datastream.

    Returns a summary dict: {docs_sent, exit_code, error}.
    """
    cmd = ensure_espipe()
    auth_flags = conf.espipe_auth_flags()
    output_url = f"{conf.url.rstrip('/')}/{index_name}"

    full_cmd = (
        cmd + auth_flags +
        [
            "--action", action,
            "--batch-size", str(batch_size),
            "--max-requests", str(max_requests),
            "-",          # read from stdin
            output_url,
        ]
    )

    docs_sent = 0
    try:
        proc = subprocess.Popen(
            full_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.stdin is not None

        for line in lines:
            line = line.strip()
            if line:
                proc.stdin.write(line + "\n")
                docs_sent += 1

        proc.stdin.close()
        stdout, stderr = proc.communicate(timeout=120)

        if proc.returncode != 0:
            log.warning(
                "espipe exited %d for %s:\nstdout: %s\nstderr: %s",
                proc.returncode, index_name, stdout[-300:], stderr[-300:],
            )
        else:
            log.debug("espipe ok → %s (%d docs)", index_name, docs_sent)

        return {"docs_sent": docs_sent, "exit_code": proc.returncode,
                "stdout": stdout, "error": stderr if proc.returncode != 0 else ""}

    except subprocess.TimeoutExpired:
        if "proc" in dir():
            proc.kill()  # type: ignore[possibly-undefined]
        log.error("espipe timed out ingesting into %s", index_name)
        return {"docs_sent": docs_sent, "exit_code": -1, "error": "timeout"}
    except Exception as e:
        log.error("espipe error for %s: %s", index_name, e)
        return {"docs_sent": docs_sent, "exit_code": -1, "error": str(e)}


def ingest_oversharded(
    lines: Iterator[str],
    conf: ClusterConf,
    *,
    batch_size: int = 5000,
    max_requests: int = 32,   # higher concurrency for the oversharding scenario
) -> dict:
    """Shortcut for the sample-oversharded plain index (uses 'index' action)."""
    return ingest_lines(
        lines, conf, "sample-oversharded",
        batch_size=batch_size, max_requests=max_requests, action="index",
    )
