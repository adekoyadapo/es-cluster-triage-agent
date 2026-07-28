"""
problems.py — inducer functions for each catalog scenario.

Each apply_* function is idempotent and scoped exclusively to sample-* indices.
They are called once during setup; the run loop feeds continuous data that exercises
the induced condition.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from elasticsearch import Elasticsearch, NotFoundError

log = logging.getLogger(__name__)


# ── P1: Mapping explosion — dedicated sample-field-explosion index ─────────────
def apply_mapping_explosion(client: Elasticsearch, ctx: dict) -> None:
    """
    Create sample-field-explosion plain index with dynamic:true and 100K field limit.
    The run loop injects random dyn_* field names each tick, growing the mapping.
    """
    if not client.indices.exists(index="sample-field-explosion"):
        client.indices.create(
            index="sample-field-explosion",
            body={
                "settings": {
                    "index.number_of_shards": 1,
                    "index.number_of_replicas": 1,
                    "index.mapping.total_fields.limit": 100_000,
                    "index.search.slowlog.threshold.query.debug": "0ms",
                    "index.search.slowlog.threshold.query.info": "0ms",
                    "index.search.slowlog.include.user": True,
                },
                "mappings": {
                    "dynamic": True,
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "message":   {"type": "text"},
                        "tag":       {"type": "keyword"},
                    },
                },
            },
        )
        log.info("[mapping_explosion] sample-field-explosion created (dynamic:true, limit 100k)")
    ctx["mapping_explosion"] = True


# ── P2: Oversharding ──────────────────────────────────────────────────────────
def apply_oversharding(client: Elasticsearch, ctx: dict) -> None:
    """
    Create sample-oversharded with 12 primary shards and an ILM policy that
    never rolls (500gb max, 365d max age). Simulates an over-partitioned index.
    """
    _ensure_ilm(client, "sample-never-roll-ilm", {
        "policy": {
            "phases": {
                "hot": {
                    "min_age": "0ms",
                    "actions": {
                        "rollover": {
                            "max_primary_shard_size": "500gb",
                            "max_age": "365d",
                        }
                    },
                }
            }
        }
    })
    if not client.indices.exists(index="sample-oversharded"):
        client.indices.create(
            index="sample-oversharded",
            body={
                "settings": {
                    "index.number_of_shards": 12,
                    "index.number_of_replicas": 1,
                    "index.lifecycle.name": "sample-never-roll-ilm",
                    "index.search.slowlog.threshold.query.warn": "2s",
                    "index.search.slowlog.include.user": True,
                },
                "mappings": {
                    "dynamic": True,
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "message":   {"type": "text"},
                        "value":     {"type": "float"},
                        "tag":       {"type": "keyword"},
                    },
                },
            },
        )
        log.info("[oversharding] sample-oversharded created (12 primaries, never-rolling ILM)")
    ctx["oversharding"] = True


# ── P3: Slow / expensive queries → CPU + slowlog ──────────────────────────────
def apply_slow_cpu(client: Elasticsearch, ctx: dict) -> None:
    """
    Lower slowlog threshold to 0ms on sample-search-stress so every query is
    recorded. The actual expensive queries are fired by the query worker thread pool.
    """
    for pattern in ("sample-search-stress", "sample-transactions*", ".ds-sample-transactions*"):
        try:
            client.indices.put_settings(
                index=pattern,
                settings={
                    "index.search.slowlog.threshold.query.warn":  "0ms",
                    "index.search.slowlog.threshold.query.info":  "0ms",
                    "index.search.slowlog.threshold.query.debug": "0ms",
                    "index.search.slowlog.include.user": True,
                },
                ignore_unavailable=True,
            )
        except Exception as e:
            log.debug("[slow_cpu] settings on %s: %s", pattern, e)
    log.info("[slow_cpu] slowlog thresholds set to 0ms on search-stress + transactions")
    ctx["slow_cpu"] = True


# ── P4: Thread-pool rejection (429s) ──────────────────────────────────────────
def apply_threadpool(client: Elasticsearch, ctx: dict) -> None:
    """No index setup — run.py's 64-worker thread pool triggers the rejection."""
    log.info("[threadpool] high-concurrency mode enabled (configured in run loop)")
    ctx["threadpool"] = True


# ── P5: Cluster YELLOW — unassigned replicas ──────────────────────────────────
def apply_unassigned(client: Elasticsearch, ctx: dict) -> None:
    """
    Create sample-unassigned with replicas:3 on a 3-node cluster.
    One replica shard cannot be placed → cluster goes YELLOW.
    """
    if not client.indices.exists(index="sample-unassigned"):
        client.indices.create(
            index="sample-unassigned",
            body={
                "settings": {
                    "index.number_of_shards": 1,
                    "index.number_of_replicas": 3,   # 3 replicas on 3 nodes → 1 unassignable
                    "index.search.slowlog.threshold.query.warn": "2s",
                    "index.search.slowlog.include.user": True,
                },
                "mappings": {
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "msg":        {"type": "text"},
                    }
                },
            },
        )
        from datetime import datetime, timezone
        client.index(index="sample-unassigned", document={
            "@timestamp": datetime.now(timezone.utc).isoformat(), "msg": "unassigned scenario seed"
        })
        log.info("[unassigned] sample-unassigned created with replicas:3 → YELLOW expected")
    ctx["unassigned"] = True


# ── P6: Heap / circuit-breaker ────────────────────────────────────────────────
def apply_heap(client: Elasticsearch, ctx: dict) -> None:
    """
    Enable fielddata on the message text field in sample-search-stress so that
    large-cardinality terms aggregations on it stress the fielddata cache.
    """
    try:
        client.indices.put_mapping(
            index="sample-search-stress",
            body={
                "properties": {
                    "message": {
                        "type": "text",
                        "fielddata": True,
                        "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                    }
                }
            },
            ignore_unavailable=True,
        )
        log.info("[heap] fielddata:true enabled on sample-search-stress.message")
    except Exception as e:
        log.warning("[heap] mapping update: %s", e)
    ctx["heap"] = True


# ── P7: Deep pagination / scroll ──────────────────────────────────────────────
def apply_scroll(client: Elasticsearch, ctx: dict) -> None:
    """No index setup — query workers issue from:10000+ and open scroll contexts."""
    log.info("[scroll] deep-pagination mode enabled (configured in run loop)")
    ctx["scroll"] = True


# ── P8: Shard write hotspot ────────────────────────────────────────────────────
def apply_hotspot(client: Elasticsearch, ctx: dict) -> None:
    """
    Create sample-hotspot with 3 primary shards. The run loop always ingests with
    routing='hot_partition' forcing all writes to shard 0, creating a visible
    write hotspot in hot_shards_analysis and shard indexing stats.
    """
    if not client.indices.exists(index="sample-hotspot"):
        client.indices.create(
            index="sample-hotspot",
            body={
                "settings": {
                    "index.number_of_shards": 3,
                    "index.number_of_replicas": 1,
                    "index.search.slowlog.threshold.query.warn": "2s",
                    "index.search.slowlog.include.user": True,
                },
                "mappings": {
                    "dynamic": True,
                    "properties": {
                        "@timestamp":    {"type": "date"},
                        "message":       {"type": "text"},
                        "value":         {"type": "float"},
                        "partition_key": {"type": "keyword"},
                        "tag":           {"type": "keyword"},
                    },
                },
            },
        )
        log.info("[hotspot] sample-hotspot created (3 primaries, all writes → shard 0 via routing)")
    ctx["hotspot"] = True


# ── P9: Cluster RED (gated) ───────────────────────────────────────────────────
def apply_red(client: Elasticsearch, ctx: dict) -> None:
    """
    Create sample-red with an impossible routing requirement so the primary
    shard can never be allocated → cluster goes RED.
    """
    if not client.indices.exists(index="sample-red"):
        client.indices.create(
            index="sample-red",
            body={
                "settings": {
                    "index.number_of_shards": 1,
                    "index.number_of_replicas": 0,
                    "index.routing.allocation.require._name": "node-does-not-exist-xxxxxxxx",
                },
            },
        )
        log.info("[red] sample-red created with impossible allocation → RED expected")
    ctx["red"] = True


# ── Registry ──────────────────────────────────────────────────────────────────
PROBLEM_APPLIERS: dict[str, Callable] = {
    "mapping_explosion": apply_mapping_explosion,
    "oversharding":      apply_oversharding,
    "slow_cpu":          apply_slow_cpu,
    "threadpool":        apply_threadpool,
    "unassigned":        apply_unassigned,
    "heap":              apply_heap,
    "scroll":            apply_scroll,
    "hotspot":           apply_hotspot,
    "red":               apply_red,
}


def apply_problems(client: Elasticsearch, selected: set[str]) -> dict:
    """Apply all selected problem scenarios. Returns context dict for run loop."""
    ctx: dict = {}
    for sid in selected:
        if sid in PROBLEM_APPLIERS:
            try:
                PROBLEM_APPLIERS[sid](client, ctx)
            except Exception as e:
                log.error("[%s] apply failed: %s", sid, e)
    return ctx


# ── Helpers ───────────────────────────────────────────────────────────────────
def _ensure_ilm(client: Elasticsearch, name: str, body: dict) -> None:
    try:
        client.ilm.get_lifecycle(name=name)
    except NotFoundError:
        client.ilm.put_lifecycle(name=name, policy=body["policy"])
        log.info("ILM policy created: %s", name)
