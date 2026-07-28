"""
metrics.py — system/infrastructure metrics time-series dataset.
Small docs (~200-400 bytes), high write rate, ECS-compatible field names.
Simulates metricbeat-style system metrics: cpu, memory, filesystem, network, load.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator

from ..synth import SynthGen

NAME = "metrics"
DATASTREAM = "sample-metrics"
COMPONENT_TEMPLATE = "sample-metrics-mappings"
INDEX_TEMPLATE_NAME = "sample-metrics"
ILM_POLICY_NAME = "sample-metrics-ilm"

_METRICSETS = ["cpu", "memory", "filesystem", "network", "load"]
_HOSTS = [f"server-{i:02d}" for i in range(1, 9)]
_MOUNT_POINTS = ["/", "/data", "/var/log", "/tmp"]
_NET_IFACES = ["eth0", "eth1", "lo"]

ILM_POLICY = {
    "policy": {
        "phases": {
            "hot": {
                "min_age": "0ms",
                "actions": {
                    "rollover": {"max_primary_shard_size": "5gb", "max_age": "1h"},
                    "set_priority": {"priority": 100},
                },
            },
            "warm": {
                "min_age": "6h",
                "actions": {"forcemerge": {"max_num_segments": 1}},
            },
            "delete": {"min_age": "1d", "actions": {"delete": {}}},
        }
    }
}

COMPONENT_TEMPLATE_BODY = {
    "template": {
        "mappings": {
            "dynamic": "strict",
            "_source": {"enabled": True},
            "properties": {
                "@timestamp": {"type": "date"},
                "host": {
                    "properties": {
                        "name": {"type": "keyword"},
                        "id":   {"type": "keyword"},
                    }
                },
                "metricset": {"properties": {"name": {"type": "keyword"}}},
                "event":     {"properties": {"dataset": {"type": "keyword"}}},
                "system": {
                    "properties": {
                        "cpu": {
                            "properties": {
                                "user":   {"properties": {"pct": {"type": "float"}}},
                                "system": {"properties": {"pct": {"type": "float"}}},
                                "idle":   {"properties": {"pct": {"type": "float"}}},
                                "iowait": {"properties": {"pct": {"type": "float"}}},
                            }
                        },
                        "memory": {
                            "properties": {
                                "used":  {"properties": {"pct": {"type": "float"}, "bytes": {"type": "long"}}},
                                "free":  {"type": "long"},
                                "total": {"type": "long"},
                            }
                        },
                        "filesystem": {
                            "properties": {
                                "used":        {"properties": {"pct": {"type": "float"}, "bytes": {"type": "long"}}},
                                "free":        {"type": "long"},
                                "mount_point": {"type": "keyword"},
                            }
                        },
                        "network": {
                            "properties": {
                                "in":   {"properties": {"bytes": {"type": "long"}, "packets": {"type": "long"}}},
                                "out":  {"properties": {"bytes": {"type": "long"}, "packets": {"type": "long"}}},
                                "name": {"type": "keyword"},
                            }
                        },
                        "load": {
                            "properties": {
                                "1":  {"type": "float"},
                                "5":  {"type": "float"},
                                "15": {"type": "float"},
                            }
                        },
                    }
                },
            },
        },
        "settings": {"index.lifecycle.name": ILM_POLICY_NAME},
    }
}

INDEX_TEMPLATE_BODY = {
    "index_patterns": [f"{DATASTREAM}*"],
    "data_stream": {},
    "composed_of": [COMPONENT_TEMPLATE, "sample-common-slowlog"],
    "priority": 500,
}


class MetricsDataset:
    name = NAME
    datastream = DATASTREAM
    component_template = COMPONENT_TEMPLATE
    component_template_body = COMPONENT_TEMPLATE_BODY
    index_template = INDEX_TEMPLATE_NAME
    index_template_body = INDEX_TEMPLATE_BODY
    ilm_policy = ILM_POLICY_NAME
    ilm_policy_body = ILM_POLICY

    @staticmethod
    def generate_doc(synth: SynthGen) -> dict:
        now = datetime.now(timezone.utc)
        ts  = synth.ts_jitter_iso(now, max_jitter_s=30)
        host_idx = synth._int(0, len(_HOSTS) - 1)
        host     = _HOSTS[host_idx]
        ms       = synth._choice(_METRICSETS)

        doc: dict = {
            "@timestamp": ts,
            "host":     {"name": host, "id": f"host-{host_idx:04x}"},
            "metricset": {"name": ms},
            "event":    {"dataset": f"system.{ms}"},
            "system":   {},
        }

        if ms == "cpu":
            user = round(synth._rng.uniform(0.01, 0.85), 4)
            sys_ = round(synth._rng.uniform(0.01, 0.25), 4)
            iow  = round(synth._rng.uniform(0.0,  0.15), 4)
            idle = round(max(0.0, 1.0 - user - sys_ - iow), 4)
            doc["system"]["cpu"] = {
                "user":   {"pct": user},
                "system": {"pct": sys_},
                "idle":   {"pct": idle},
                "iowait": {"pct": iow},
            }
        elif ms == "memory":
            total     = synth._int(4, 64) * 1024 * 1024 * 1024
            used_pct  = round(synth._rng.uniform(0.3, 0.92), 4)
            used_bytes = int(total * used_pct)
            doc["system"]["memory"] = {
                "used":  {"pct": used_pct, "bytes": used_bytes},
                "free":  total - used_bytes,
                "total": total,
            }
        elif ms == "filesystem":
            total    = synth._int(50, 500) * 1024 * 1024 * 1024
            used_pct = round(synth._rng.uniform(0.2, 0.88), 4)
            doc["system"]["filesystem"] = {
                "used":        {"pct": used_pct, "bytes": int(total * used_pct)},
                "free":        int(total * (1 - used_pct)),
                "mount_point": synth._choice(_MOUNT_POINTS),
            }
        elif ms == "network":
            doc["system"]["network"] = {
                "in":   {"bytes": synth._int(0, 10_000_000), "packets": synth._int(0, 100_000)},
                "out":  {"bytes": synth._int(0,  5_000_000), "packets": synth._int(0,  50_000)},
                "name": synth._choice(_NET_IFACES),
            }
        else:  # load
            load1 = round(synth._rng.uniform(0.1, 4.0), 2)
            doc["system"]["load"] = {
                "1":  load1,
                "5":  round(load1 * synth._rng.uniform(0.8, 1.2), 2),
                "15": round(load1 * synth._rng.uniform(0.6, 1.4), 2),
            }

        return doc

    @classmethod
    def generate_ndjson_lines(cls, synth: SynthGen, n: int) -> Iterator[str]:
        for _ in range(n):
            yield json.dumps(cls.generate_doc(synth))
