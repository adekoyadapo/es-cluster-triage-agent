"""
javalogs.py — Spring/Log4j-style Java application log documents.
ECS-ish shape with weighted log levels, MDC as `flattened` (healthy),
and synthetic Java stacktraces on ERROR records.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator

from ..synth import SynthGen

NAME = "javalogs"
DATASTREAM = "sample-logs"
COMPONENT_TEMPLATE = "sample-logs-mappings"
INDEX_TEMPLATE_NAME = "sample-logs"
ILM_POLICY_NAME = "sample-logs-ilm"

# ── ILM policy ─────────────────────────────────────────────────────────────────
ILM_POLICY = {
    "policy": {
        "phases": {
            "hot": {
                "min_age": "0ms",
                "actions": {
                    "rollover": {
                        "max_primary_shard_size": "25gb",
                        "max_age": "1d",
                    },
                    "set_priority": {"priority": 100},
                },
            },
            "warm": {
                "min_age": "3d",
                "actions": {
                    "forcemerge": {"max_num_segments": 1},
                    "set_priority": {"priority": 50},
                },
            },
            "delete": {
                "min_age": "14d",
                "actions": {"delete": {}},
            },
        }
    }
}

# ── Mapping ────────────────────────────────────────────────────────────────────
COMPONENT_TEMPLATE_BODY = {
    "template": {
        "mappings": {
            "dynamic": "strict",
            "_source": {"enabled": True},
            "properties": {
                "@timestamp":   {"type": "date"},
                "log": {
                    "properties": {
                        "level": {"type": "keyword"},
                    }
                },
                "logger":       {"type": "keyword"},
                "thread": {
                    "properties": {
                        "name": {"type": "keyword"},
                        "id":   {"type": "long"},
                    }
                },
                "service": {
                    "properties": {
                        "name": {"type": "keyword"},
                    }
                },
                "host": {
                    "properties": {
                        "name": {"type": "keyword"},
                    }
                },
                "message": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}},
                },
                "error": {
                    "properties": {
                        "type":        {"type": "keyword"},
                        "message":     {"type": "text"},
                        # stack_trace is stored but not indexed in healthy mode
                        "stack_trace": {"type": "text", "index": False},
                    }
                },
                "trace": {
                    "properties": {
                        "id": {"type": "keyword"},
                    }
                },
                "span": {
                    "properties": {
                        "id": {"type": "keyword"},
                    }
                },
                # MDC fields as flattened (healthy) — prevents mapping explosion
                "labels": {"type": "flattened"},
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

# Weighted distribution: INFO 80%, WARN 12%, ERROR 6%, DEBUG 2%
_LEVEL_CHOICES = (
    ["INFO"] * 80 + ["WARN"] * 12 + ["ERROR"] * 6 + ["DEBUG"] * 2
)


class JavaLogsDataset:
    name = NAME
    datastream = DATASTREAM
    component_template = COMPONENT_TEMPLATE
    component_template_body = COMPONENT_TEMPLATE_BODY
    index_template = INDEX_TEMPLATE_NAME
    index_template_body = INDEX_TEMPLATE_BODY
    ilm_policy = ILM_POLICY_NAME
    ilm_policy_body = ILM_POLICY

    @staticmethod
    def generate_doc(synth: SynthGen, inject_dynamic_mdc: bool = False) -> dict:
        now = datetime.now(timezone.utc)
        ts = synth.ts_jitter_iso(now, max_jitter_s=5)
        level = synth._choice(_LEVEL_CHOICES)

        doc: dict = {
            "@timestamp": ts,
            "log": {"level": level},
            "logger": synth.logger_name(),
            "thread": {
                "name": synth.thread_name(),
                "id": synth._int(10, 9999),
            },
            "service": {"name": synth.service_name()},
            "host":    {"name": synth.host_name()},
            "message": synth.log_message(level),
            "trace": {"id": synth.uid()},
            "span":  {"id": synth._hex(16)},
        }

        # MDC context as flattened (healthy) or object (inject_dynamic_mdc → mapping explosion)
        mdc: dict = {
            "request_id": synth.uid(),
            "user_id":    f"user_{synth._int(1, 9999)}",
            "session_id": synth._hex(32),
        }
        if inject_dynamic_mdc:
            # Add randomly-named MDC keys → mapping explosion when dynamic:true
            for _ in range(synth._int(5, 15)):
                mdc[synth.random_field_name()] = synth.lorem_words(2)
        doc["labels"] = mdc

        if level == "ERROR":
            exc = synth._choice(synth._EXC_TYPES)
            doc["error"] = {
                "type": exc,
                "message": synth._choice([
                    "Unexpected null value", "Connection refused",
                    "Timeout after 5000ms", "Constraint violation",
                    "Read timed out", "Serialization error",
                ]),
                "stack_trace": synth.java_stacktrace(exc),
            }

        return doc

    @classmethod
    def generate_ndjson_lines(
        cls, synth: SynthGen, n: int, inject_dynamic_mdc: bool = False
    ) -> Iterator[str]:
        for _ in range(n):
            yield json.dumps(cls.generate_doc(synth, inject_dynamic_mdc))
