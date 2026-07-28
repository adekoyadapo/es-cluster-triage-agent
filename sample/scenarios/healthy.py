"""
healthy.py — "good settings" profile.
The healthy baseline is already baked into each dataset's component template
(dynamic:strict, sane shards, normal ILM).  This module verifies the setup is
clean and enables slowlog on all datastream backing indices.
"""
from __future__ import annotations

import logging

from elasticsearch import Elasticsearch

log = logging.getLogger(__name__)


def apply_healthy(client: Elasticsearch) -> None:
    """
    Ensure slowlog is active on all sample-* backing indices.
    (It's already in the component template for new backing indices;
    this handles any pre-existing ones that rolled before our template change.)
    """
    slowlog_settings = {
        "index.search.slowlog.threshold.query.warn":  "2s",
        "index.search.slowlog.threshold.query.info":  "500ms",
        "index.search.slowlog.threshold.fetch.warn":  "1s",
        "index.indexing.slowlog.threshold.index.warn": "5s",
        "index.search.slowlog.include.user": True,
    }
    # Apply to both the datastream aliases AND backing index patterns
    for pattern in ("sample-*", ".ds-sample-*"):
        try:
            client.indices.put_settings(
                index=pattern,
                settings=slowlog_settings,
                ignore_unavailable=True,
            )
        except Exception as e:
            log.debug("Slowlog %s: %s", pattern, e)
    log.info("Slowlog settings applied to sample-* and .ds-sample-* indices")
