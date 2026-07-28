"""
bulky.py — random 4-10 KB documents for simulating large-shard / disk-pressure scenarios.
Each document has a fixed schema (dynamic:false) with a padded `blob` text field.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Iterator

from ..synth import SynthGen

NAME = "bulky"
DATASTREAM = "sample-bulky"
COMPONENT_TEMPLATE = "sample-bulky-mappings"
INDEX_TEMPLATE_NAME = "sample-bulky"
ILM_POLICY_NAME = "sample-bulky-ilm"

_TARGET_MIN_BYTES = 4_096
_TARGET_MAX_BYTES = 10_240

# ── ILM policy ─────────────────────────────────────────────────────────────────
ILM_POLICY = {
    "policy": {
        "phases": {
            "hot": {
                "min_age": "0ms",
                "actions": {
                    "rollover": {
                        "max_primary_shard_size": "20gb",
                        "max_age": "6h",
                    },
                    "set_priority": {"priority": 100},
                },
            },
            "delete": {
                "min_age": "2d",
                "actions": {"delete": {}},
            },
        }
    }
}

# ── Mapping ────────────────────────────────────────────────────────────────────
COMPONENT_TEMPLATE_BODY = {
    "template": {
        "mappings": {
            # dynamic:false → new fields silently ignored; no mapping explosion
            "dynamic": False,
            "_source": {"enabled": True},
            "properties": {
                "@timestamp":   {"type": "date"},
                "doc_id":       {"type": "keyword"},
                "category":     {"type": "keyword"},
                "sub_category": {"type": "keyword"},
                "priority":     {"type": "keyword"},
                "status":       {"type": "keyword"},
                "region":       {"type": "keyword"},
                "env":          {"type": "keyword"},
                "version":      {"type": "keyword"},
                "owner_ref":    {"type": "keyword"},
                "source_ref":   {"type": "keyword"},
                "tags":         {"type": "keyword"},
                "score":        {"type": "float"},
                "weight":       {"type": "float"},
                "count":        {"type": "long"},
                "size_bytes":   {"type": "long"},
                "duration_ms":  {"type": "long"},
                "retry_count":  {"type": "integer"},
                "flag_a":       {"type": "boolean"},
                "flag_b":       {"type": "boolean"},
                "created_at":   {"type": "date"},
                "updated_at":   {"type": "date"},
                "payload": {
                    "properties": {
                        "key_a": {"type": "keyword"},
                        "key_b": {"type": "keyword"},
                        "key_c": {"type": "keyword"},
                        "val_1": {"type": "float"},
                        "val_2": {"type": "float"},
                        "val_3": {"type": "long"},
                    }
                },
                # blob is stored but NOT indexed — saves heap, simulates large source
                "blob": {"type": "text", "index": False, "store": False},
            },
        },
        "settings": {
            "index.lifecycle.name": ILM_POLICY_NAME,
        },
    }
}

INDEX_TEMPLATE_BODY = {
    "index_patterns": [f"{DATASTREAM}*"],
    "data_stream": {},
    "composed_of": [COMPONENT_TEMPLATE, "sample-common-slowlog"],
    "priority": 500,
}

_CATEGORIES   = ["analytics", "audit", "archive", "telemetry", "snapshot", "batch", "export"]
_SUB_CATS     = ["daily", "weekly", "monthly", "realtime", "historical", "derived"]
_PRIORITIES   = ["low", "medium", "high", "critical"]
_REGIONS      = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1", "ap-northeast-1"]
_ENVS         = ["production", "staging", "development"]
_VERSIONS     = ["1.0.0", "1.1.0", "1.2.0", "2.0.0", "2.1.0"]


class BulkyDataset:
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
        ts = synth.ts_jitter_iso(now, max_jitter_s=120)

        doc: dict = {
            "@timestamp": ts,
            "doc_id":       synth.uid(),
            "category":     synth._choice(_CATEGORIES),
            "sub_category": synth._choice(_SUB_CATS),
            "priority":     synth._choice(_PRIORITIES),
            "status":       synth._choice(["active", "archived", "pending", "error"]),
            "region":       synth._choice(_REGIONS),
            "env":          synth._choice(_ENVS),
            "version":      synth._choice(_VERSIONS),
            "owner_ref":    f"OWN-{synth.short_id()}",
            "source_ref":   f"SRC-{synth.short_id()}",
            "tags":         synth.random_tags(),
            "score":        round(synth._rng.gauss(0.5, 0.15), 4),
            "weight":       synth._float(0.0, 1.0),
            "count":        synth._int(0, 100_000),
            "size_bytes":   synth._int(100, 10_000_000),
            "duration_ms":  synth._int(1, 60_000),
            "retry_count":  synth._int(0, 5),
            "flag_a":       synth._rng.random() > 0.7,
            "flag_b":       synth._rng.random() > 0.5,
            "created_at":   ts,
            "updated_at":   synth.ts_jitter_iso(now, max_jitter_s=300),
            "payload": {
                "key_a": synth.short_id("K"),
                "key_b": synth.short_id("K"),
                "key_c": synth.short_id("K"),
                "val_1": synth._float(0.0, 1000.0),
                "val_2": synth._float(0.0, 1000.0),
                "val_3": synth._int(0, 999_999),
            },
            "blob": "",  # placeholder; padded below
        }

        # Measure base size and pad blob to hit target
        base_json = json.dumps(doc)
        base_size = len(base_json.encode("utf-8"))
        target = synth._int(_TARGET_MIN_BYTES, _TARGET_MAX_BYTES)
        needed_chars = max(0, target - base_size - 10)  # -10 for key overhead
        if needed_chars > 0:
            # ~5 chars per word on average
            n_words = max(1, math.ceil(needed_chars / 5))
            doc["blob"] = synth.lorem_words(n_words)

        return doc

    @classmethod
    def generate_ndjson_lines(cls, synth: SynthGen, n: int) -> Iterator[str]:
        for _ in range(n):
            yield json.dumps(cls.generate_doc(synth))
