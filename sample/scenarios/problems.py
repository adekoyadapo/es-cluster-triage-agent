"""
problems.py — inducer functions for each scenario in the catalog.

Each apply_* function is idempotent and scoped exclusively to sample-* indices.
They are called once during setup; the run loop then continuously feeds data
that exercises the induced condition.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from elasticsearch import Elasticsearch, NotFoundError

log = logging.getLogger(__name__)

# ── P1: Mapping explosion ──────────────────────────────────────────────────────
def apply_mapping_explosion(client: Elasticsearch, ctx: dict) -> None:
    """
    Create a broken transactions template variant with dynamic:true and an
    extremely high field-limit.  The generator will inject random field names
    each tick, pushing the field count into the thousands.
    """
    from ..datasets.transactions import MAPPING_PROPERTIES, ILM_POLICY_NAME
    try:
        # PUT component template directly — avoids round-tripping system metadata
        # (ES 9.x includes created_date_millis / modified_date_millis in GET
        # responses which cannot be written back via PUT).
        client.cluster.put_component_template(
            name="sample-transactions-mappings",
            body={
                "template": {
                    "mappings": {
                        "dynamic": True,       # <-- the key change; allows new fields
                        "_source": {"enabled": True},
                        "properties": MAPPING_PROPERTIES,
                    },
                    "settings": {
                        "index.lifecycle.name": ILM_POLICY_NAME,
                        "index.mapping.total_fields.limit": 100_000,
                    },
                }
            },
        )
        # Also update the dynamic setting on EXISTING backing indices
        # (ES 9.x allows changing dynamic from strict → true via update mapping API)
        for pattern in ("sample-transactions*", ".ds-sample-transactions*"):
            try:
                client.indices.put_mapping(
                    index=pattern,
                    body={"dynamic": True},
                    ignore_unavailable=True,
                )
            except Exception:
                pass
        # Raise field limit + lower slowlog on existing backing indices
        for pattern in ("sample-transactions*", ".ds-sample-transactions*"):
            try:
                client.indices.put_settings(
                    index=pattern,
                    settings={
                        "index.mapping.total_fields.limit": 100_000,
                        "index.search.slowlog.threshold.query.debug": "0ms",
                        "index.search.slowlog.threshold.query.info":  "0ms",
                    },
                    ignore_unavailable=True,
                )
            except Exception:
                pass
        log.info("[mapping_explosion] dynamic:true + field limit 100k applied")
    except Exception as e:
        log.warning("[mapping_explosion] setup error: %s", e)
    ctx["mapping_explosion"] = True


# ── P2: Oversharding ──────────────────────────────────────────────────────────
def apply_oversharding(client: Elasticsearch, ctx: dict) -> None:
    """
    Create a dedicated 'sample-oversharded' plain index with 12 primary shards
    and an ILM policy that never rolls over (500gb max, 365d max age).
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
                        "message":    {"type": "text"},
                        "value":      {"type": "float"},
                        "tag":        {"type": "keyword"},
                    },
                },
            },
        )
        log.info("[oversharding] sample-oversharded index created (12 primaries)")
    ctx["oversharding"] = True


# ── P3: Slow / expensive queries → CPU ────────────────────────────────────────
def apply_slow_cpu(client: Elasticsearch, ctx: dict) -> None:
    """
    Lower slowlog threshold to 0ms on transactions so every query lands in logs.
    The actual expensive queries are fired by the query worker thread pool in run.py.
    """
    try:
        client.indices.put_settings(
            index="sample-transactions*",
            settings={
                "index.search.slowlog.threshold.query.warn":  "0ms",
                "index.search.slowlog.threshold.query.info":  "0ms",
                "index.search.slowlog.threshold.query.debug": "0ms",
                "index.search.slowlog.include.user": True,
            },
            ignore_unavailable=True,
        )
        log.info("[slow_cpu] slowlog thresholds set to 0ms on sample-transactions*")
    except Exception as e:
        log.warning("[slow_cpu] setup error: %s", e)
    ctx["slow_cpu"] = True


# ── P4: Thread-pool rejection (429s) ──────────────────────────────────────────
def apply_threadpool(client: Elasticsearch, ctx: dict) -> None:
    """
    No index-level setup needed.  run.py's query thread pool will be configured
    to 64 concurrent workers to overwhelm the search thread pool (default queue=1000,
    size=# CPU cores).  A concurrent bulk storm is added to hit the write pool.
    """
    log.info("[threadpool] high-concurrency mode enabled (configured in run loop)")
    ctx["threadpool"] = True


# ── P5: Cluster YELLOW ────────────────────────────────────────────────────────
def apply_yellow(client: Elasticsearch, ctx: dict) -> None:
    """
    Create 'sample-yellow' with replicas:3 on a 3-node cluster.
    One replica shard is unassignable → cluster goes YELLOW.
    """
    if not client.indices.exists(index="sample-yellow"):
        client.indices.create(
            index="sample-yellow",
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
        # Seed a few docs to make the index real
        client.index(index="sample-yellow", document={"@timestamp": "now", "msg": "yellow scenario seed"})
        log.info("[yellow] sample-yellow created with replicas:3 → YELLOW expected")
    ctx["yellow"] = True


# ── P6: Heap / circuit-breaker ────────────────────────────────────────────────
def apply_heap(client: Elasticsearch, ctx: dict) -> None:
    """
    Enable fielddata on the transactions.transaction.merchant_name text field
    so that large-cardinality terms aggregations on it blow up the fielddata cache.
    The actual agg queries are fired by the query worker in run.py.
    """
    try:
        client.indices.put_mapping(
            index="sample-transactions*",
            body={
                "properties": {
                    "transaction": {
                        "properties": {
                            "merchant_name": {
                                "type": "text",
                                "fielddata": True,
                                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                            }
                        }
                    }
                }
            },
            ignore_unavailable=True,
        )
        log.info("[heap] fielddata enabled on transaction.merchant_name for fielddata pressure")
    except Exception as e:
        log.warning("[heap] mapping update error: %s", e)
    ctx["heap"] = True


# ── P7: Deep pagination / scroll ──────────────────────────────────────────────
def apply_scroll(client: Elasticsearch, ctx: dict) -> None:
    """
    No index setup needed.  Query workers in run.py will issue from:10000 deep
    pages and open long-lived scroll contexts.
    """
    log.info("[scroll] deep-pagination mode enabled (configured in run loop)")
    ctx["scroll"] = True


# ── P8: Cluster RED (gated) ───────────────────────────────────────────────────
def apply_red(client: Elasticsearch, ctx: dict) -> None:
    """
    Create 'sample-red' with an impossible routing requirement so the primary
    never allocates → cluster goes RED.  Scoped to one throwaway index.
    """
    if not client.indices.exists(index="sample-red"):
        client.indices.create(
            index="sample-red",
            body={
                "settings": {
                    "index.number_of_shards": 1,
                    "index.number_of_replicas": 0,
                    # Route to a node that does not exist → primary unassignable → RED
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
    "yellow":            apply_yellow,
    "heap":              apply_heap,
    "scroll":            apply_scroll,
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
