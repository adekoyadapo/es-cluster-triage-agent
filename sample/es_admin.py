"""
es_admin.py — Elasticsearch client factory + cluster setup/teardown.

Setup order (per cluster, idempotent):
  1. ILM policies × 3
  2. Component templates (shared slowlog + per-dataset mappings)
  3. Index templates (composing component templates, data_stream:{})
  4. Data stream creation
  5. Problem-variant index/setting changes
  6. Slowlog refresh on existing backing indices
"""
from __future__ import annotations

import logging
import warnings
from typing import Any

from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.exceptions import RequestError

from .config import ClusterConf
from .datasets import DATASETS

log = logging.getLogger(__name__)

# ── Shared slowlog component template ─────────────────────────────────────────
_COMMON_SLOWLOG_TEMPLATE = "sample-common-slowlog"
_COMMON_SLOWLOG_BODY = {
    "template": {
        "settings": {
            "index.search.slowlog.threshold.query.warn":   "2s",
            "index.search.slowlog.threshold.query.info":   "500ms",
            "index.search.slowlog.threshold.fetch.warn":   "1s",
            "index.indexing.slowlog.threshold.index.warn": "5s",
            "index.search.slowlog.include.user":           True,
        }
    }
}


# ── Client factory ─────────────────────────────────────────────────────────────
def make_client(conf: ClusterConf) -> Elasticsearch:
    """Create an elasticsearch-py client from a ClusterConf (TLS insecure)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = Elasticsearch(**conf.es_client_kwargs())
    return client


# ── Setup ─────────────────────────────────────────────────────────────────────
def setup_all(client: Elasticsearch) -> None:
    """
    Idempotent cluster setup: ILM → component templates → index templates →
    data streams → slowlog on existing backing indices.
    """
    _setup_ilm(client)
    _setup_component_templates(client)
    _setup_index_templates(client)
    _setup_datastreams(client)
    _refresh_slowlog(client)
    log.info("Cluster setup complete")


def _setup_ilm(client: Elasticsearch) -> None:
    for ds_cls in DATASETS:
        _put_ilm(client, ds_cls.ilm_policy, ds_cls.ilm_policy_body)


def _put_ilm(client: Elasticsearch, name: str, body: dict) -> None:
    try:
        client.ilm.put_lifecycle(name=name, policy=body["policy"])
        log.info("ILM policy upserted: %s", name)
    except Exception as e:
        log.warning("ILM %s: %s", name, e)


def _setup_component_templates(client: Elasticsearch) -> None:
    # Shared slowlog template first
    try:
        client.cluster.put_component_template(
            name=_COMMON_SLOWLOG_TEMPLATE,
            body=_COMMON_SLOWLOG_BODY,
        )
        log.info("Component template upserted: %s", _COMMON_SLOWLOG_TEMPLATE)
    except Exception as e:
        log.warning("Component template %s: %s", _COMMON_SLOWLOG_TEMPLATE, e)

    for ds_cls in DATASETS:
        try:
            client.cluster.put_component_template(
                name=ds_cls.component_template,
                body=ds_cls.component_template_body,
            )
            log.info("Component template upserted: %s", ds_cls.component_template)
        except Exception as e:
            log.warning("Component template %s: %s", ds_cls.component_template, e)


def _setup_index_templates(client: Elasticsearch) -> None:
    for ds_cls in DATASETS:
        try:
            client.indices.put_index_template(
                name=ds_cls.index_template,
                body=ds_cls.index_template_body,
            )
            log.info("Index template upserted: %s", ds_cls.index_template)
        except Exception as e:
            log.warning("Index template %s: %s", ds_cls.index_template, e)


def _setup_datastreams(client: Elasticsearch) -> None:
    for ds_cls in DATASETS:
        try:
            resp = client.indices.get_data_stream(name=ds_cls.datastream)
            if resp.get("data_streams"):
                log.info("Data stream already exists: %s", ds_cls.datastream)
                continue
        except NotFoundError:
            pass
        try:
            client.indices.create_data_stream(name=ds_cls.datastream)
            log.info("Data stream created: %s", ds_cls.datastream)
        except RequestError as e:
            if "already exists" in str(e).lower():
                log.info("Data stream already exists: %s", ds_cls.datastream)
            else:
                log.warning("Data stream %s: %s", ds_cls.datastream, e)
        except Exception as e:
            log.warning("Data stream %s: %s", ds_cls.datastream, e)


def _refresh_slowlog(client: Elasticsearch) -> None:
    """Apply slowlog settings to any existing backing indices."""
    from .scenarios.healthy import apply_healthy
    apply_healthy(client)


# ── Teardown ──────────────────────────────────────────────────────────────────
def teardown_all(client: Elasticsearch) -> None:
    """
    Delete ALL sample-* resources: data streams, plain indices, index templates,
    component templates, ILM policies.
    Prefix-scoped — cannot touch non-sample indices.
    """
    log.info("Starting teardown of sample-* resources...")

    # 1. Data streams
    try:
        resp = client.indices.get_data_stream(name="sample-*")
        for ds in resp.get("data_streams", []):
            name = ds["name"]
            client.indices.delete_data_stream(name=name)
            log.info("Deleted data stream: %s", name)
    except NotFoundError:
        pass
    except Exception as e:
        log.warning("Data stream teardown: %s", e)

    # 2. Plain indices (ES 9.x blocks wildcard deletes via destructive_requires_name)
    #    List first, then delete each by name.
    try:
        idx_resp = client.cat.indices(index="sample-*", h="index", format="json")
        for row in idx_resp:
            name = row.get("index") or row.get("i", "")
            if name.startswith("sample-"):
                try:
                    client.indices.delete(index=name)
                    log.info("Deleted index: %s", name)
                except Exception as e2:
                    log.warning("Delete index %s: %s", name, e2)
    except Exception as e:
        log.warning("Index listing for teardown: %s", e)

    # 3. Index templates
    try:
        resp = client.indices.get_index_template(name="sample-*")
        for t in resp.get("index_templates", []):
            client.indices.delete_index_template(name=t["name"])
            log.info("Deleted index template: %s", t["name"])
    except NotFoundError:
        pass
    except Exception as e:
        log.warning("Index template teardown: %s", e)

    # 4. Component templates
    try:
        resp = client.cluster.get_component_template(name="sample-*")
        for t in resp.get("component_templates", []):
            client.cluster.delete_component_template(name=t["name"])
            log.info("Deleted component template: %s", t["name"])
    except NotFoundError:
        pass
    except Exception as e:
        log.warning("Component template teardown: %s", e)

    # 5. ILM policies
    ilm_names = [
        "sample-transactions-ilm",
        "sample-logs-ilm",
        "sample-bulky-ilm",
        "sample-never-roll-ilm",
    ]
    for name in ilm_names:
        try:
            client.ilm.delete_lifecycle(name=name)
            log.info("Deleted ILM policy: %s", name)
        except NotFoundError:
            pass
        except Exception as e:
            log.warning("ILM teardown %s: %s", name, e)

    log.info("Teardown complete. Cluster should return GREEN shortly.")


# ── Query helpers used by run.py ──────────────────────────────────────────────

def get_cluster_health(client: Elasticsearch) -> dict:
    try:
        return client.cluster.health()
    except Exception as e:
        return {"status": "unknown", "error": str(e)}


def get_rejection_counts(client: Elasticsearch) -> dict[str, int]:
    """Return search + write thread-pool rejection counts."""
    try:
        stats = client.nodes.stats(metric="thread_pool")
        search_rej = write_rej = 0
        for node in stats.get("nodes", {}).values():
            tp = node.get("thread_pool", {})
            search_rej += tp.get("search", {}).get("rejected", 0)
            write_rej  += tp.get("write",  {}).get("rejected", 0)
        return {"search": search_rej, "write": write_rej}
    except Exception:
        return {"search": 0, "write": 0}
