#!/usr/bin/env python3
"""
run.py — Elasticsearch activity simulator and issue inducer.

Usage:
    python3 sample/run.py [options]

Options:
    --env FILE          Path to .env file (default: sample/.env)
    --duration DUR      Run duration: 30s / 5m / 2h  (default: 10m)
    --problems IDS      Comma-separated scenario ids, or 'all'/'safe'/'none'
    --interactive       Always show the scenario picker menu
    --teardown          Delete all sample-* resources and exit
    --seed INT          Random seed (default: 42)
    -v / --verbose      Enable DEBUG logging

Examples:
    python3 sample/run.py --duration 10m
    python3 sample/run.py --duration 2m --problems mapping_explosion,hotspot,unassigned
    python3 sample/run.py --teardown
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sample.config import ClusterConf, load_env, build_clusters, parse_duration, collect_interactive
from sample.synth import SynthGen
from sample.es_admin import make_client, setup_all, teardown_all, get_cluster_health, get_rejection_counts
from sample.espipe_runner import ensure_espipe, ingest_lines
from sample.datasets import DATASETS, TransactionsDataset, JavaLogsDataset, MetricsDataset
from sample.scenarios.catalog import CATALOG, DEFAULT_SAFE, interactive_picker, confirm_red
from sample.scenarios.problems import apply_problems

log = logging.getLogger("sample")

_TICK_INTERVAL = 10  # seconds between ingest ticks

# Docs generated per tick per dataset
_BATCH_SIZES = {
    "transactions": 150,
    "javalogs":     400,
    "metrics":      800,  # high write rate — small docs
}


# ── Ingest helpers ─────────────────────────────────────────────────────────────

def _ingest_with_routing(client, docs: list[dict], index_name: str, routing_key: str) -> int:
    """Bulk-index docs into a plain index with a fixed routing key."""
    from elasticsearch.helpers import bulk as es_bulk
    def _gen():
        for d in docs:
            yield {"_index": index_name, "_routing": routing_key, **d}
    try:
        ok, _ = es_bulk(client, _gen(), raise_on_error=False, max_retries=1)
        return ok
    except Exception as e:
        log.debug("routing ingest error: %s", e)
        return 0


def _generate_field_explosion_lines(synth: SynthGen, n: int):
    """Each doc includes 5-10 random dyn_* fields to grow the mapping."""
    for _ in range(n):
        doc = {
            "@timestamp": synth.ts_now_iso(),
            "message": synth.lorem_words(3),
            "tag": "explosion",
        }
        for _ in range(synth._int(5, 10)):
            doc[synth.random_field_name()] = synth._float(0, 100)
        yield json.dumps(doc)


def _generate_search_stress_lines(synth: SynthGen, n: int):
    """Transaction-like docs with longer text fields for search stress index."""
    for _ in range(n):
        doc = {
            "@timestamp": synth.ts_now_iso(),
            "message":  synth.lorem_words(synth._int(15, 40)),
            "category": synth.category(),
            "value":    synth._float(0, 10_000),
            "tag":      synth.merchant_name(),
            "user_ref": synth.party_ref(),
        }
        yield json.dumps(doc)


def _generate_oversharded_lines(synth: SynthGen, n: int):
    for _ in range(n):
        doc = {
            "@timestamp": synth.ts_now_iso(),
            "message": synth.lorem_words(5),
            "value":   synth._float(0, 100),
            "tag":     "overshard",
        }
        yield json.dumps(doc)


def _do_ingest_tick(confs: list[ClusterConf], clients: dict, synth: SynthGen,
                    problems: set[str], stats: dict, lock: threading.Lock) -> None:
    """Generate one batch per dataset and ingest into all clusters."""
    for ds_cls in DATASETS:
        n = _BATCH_SIZES.get(ds_cls.name, 100)

        if ds_cls.name == "transactions":
            lines = list(TransactionsDataset.generate_ndjson_lines(synth, n, False))
        elif ds_cls.name == "javalogs":
            lines = list(JavaLogsDataset.generate_ndjson_lines(synth, n, False))
        else:
            lines = list(MetricsDataset.generate_ndjson_lines(synth, n))

        for conf in confs:
            result = ingest_lines(iter(lines), conf, ds_cls.datastream)
            with lock:
                stats["docs_ingested"] += result.get("docs_sent", 0)

    # ── Problem-specific ingest ────────────────────────────────────────────────
    if "oversharding" in problems:
        ov_lines = list(_generate_oversharded_lines(synth, 200))
        for conf in confs:
            r = ingest_lines(iter(ov_lines), conf, "sample-oversharded", action="index")
            with lock:
                stats["docs_ingested"] += r.get("docs_sent", 0)

    if "mapping_explosion" in problems:
        ex_lines = list(_generate_field_explosion_lines(synth, 100))
        for conf in confs:
            r = ingest_lines(iter(ex_lines), conf, "sample-field-explosion", action="index")
            with lock:
                stats["docs_ingested"] += r.get("docs_sent", 0)

    if "hotspot" in problems:
        hotspot_docs = [
            {
                "@timestamp":    synth.ts_now_iso(),
                "message":       synth.lorem_words(5),
                "value":         synth._float(0, 100),
                "partition_key": "hot_partition",
                "tag":           "hotspot",
            }
            for _ in range(300)
        ]
        for conf in confs:
            client = clients[conf.name]
            ok = _ingest_with_routing(client, hotspot_docs, "sample-hotspot", "hot_partition")
            with lock:
                stats["docs_ingested"] += ok

    # Always feed search-stress if it may be targeted by queries
    if problems & {"slow_cpu", "heap", "scroll", "threadpool"}:
        ss_lines = list(_generate_search_stress_lines(synth, 50))
        for conf in confs:
            r = ingest_lines(iter(ss_lines), conf, "sample-search-stress", action="index")
            with lock:
                stats["docs_ingested"] += r.get("docs_sent", 0)


# ── Query workers ──────────────────────────────────────────────────────────────

def _query_worker(client, problems: set[str], stop_event: threading.Event,
                  counter: dict, lock: threading.Lock) -> None:
    import random
    rng = random.Random()
    rejections = 0

    def _search(body: dict, index: str = "sample-transactions*") -> None:
        nonlocal rejections
        try:
            client.search(index=index, body=body, request_timeout=30)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rejected" in msg.lower():
                rejections += 1

    while not stop_event.is_set():
        try:
            # Healthy baseline — run against all 3 data streams
            _search({"query": {"match_all": {}}, "size": 10})
            _search({"query": {"match_all": {}}, "size": 5}, index="sample-logs*")
            _search({"query": {"match_all": {}}, "size": 5}, index="sample-metrics*")

            # Reference index lookups (simulates app reading lookup data)
            _search({"query": {"term": {"active": True}}, "size": 20}, index="sample-reference")

            if "slow_cpu" in problems:
                # Expensive queries against search-stress
                _search({"query": {"query_string": {
                    "query": f"*{rng.choice(['lorem', 'ipsum', 'dolor', 'amet'])}*"
                }}, "size": 5}, index="sample-search-stress")
                _search({
                    "size": 0,
                    "aggs": {
                        "by_tag": {
                            "terms": {"field": "tag", "size": 10000},
                            "aggs": {"unique_users": {"cardinality": {"field": "user_ref"}}}
                        }
                    }
                }, index="sample-search-stress")
                _search({"query": {"regexp": {
                    "user_ref": f"PTY-[0-9]{{{rng.randint(4, 6)}}}"
                }}, "size": 5}, index="sample-search-stress")

            if "heap" in problems:
                # fielddata blowup: agg on text field with fielddata:true
                _search({
                    "size": 0,
                    "aggs": {"message_terms": {"terms": {"field": "message", "size": 10000}}}
                }, index="sample-search-stress")

            if "scroll" in problems:
                offset = rng.choice([5000, 10000, 20000, 50000])
                try:
                    client.search(
                        index="sample-search-stress",
                        body={"from": offset, "size": 10, "query": {"match_all": {}}},
                        request_timeout=30,
                    )
                except Exception as e:
                    if "429" in str(e) or "rejected" in str(e).lower():
                        rejections += 1
                try:
                    client.search(
                        index="sample-*", scroll="5m",
                        body={"size": 100, "query": {"match_all": {}}},
                        request_timeout=30,
                    )
                    # Intentionally NOT clearing the scroll — resource leak
                except Exception:
                    pass

            if "threadpool" in problems:
                _search({"query": {"match_all": {}}, "size": 100}, index="sample-*")

        except Exception:
            pass

        with lock:
            counter["rejections"] += rejections
        rejections = 0
        time.sleep(rng.uniform(0.05, 0.3))


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python3 sample/run.py",
        description="Elasticsearch activity simulator + issue inducer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env",         default=None, help="Path to .env file")
    parser.add_argument("--duration",    default=None, help="Run duration: 30s / 5m / 2h")
    parser.add_argument("--problems",    default=None, help="Scenario ids (csv) or all/safe/none")
    parser.add_argument("--interactive", action="store_true", help="Force interactive scenario picker")
    parser.add_argument("--teardown",    action="store_true", help="Delete all sample-* resources and exit")
    parser.add_argument("--seed",        type=int, default=42, help="Random seed")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Locate .env ───────────────────────────────────────────────────────────
    env_path: Path | None = None
    if args.env:
        env_path = Path(args.env)
    else:
        default = Path(__file__).parent / ".env"
        if default.exists():
            env_path = default

    # ── Load config ───────────────────────────────────────────────────────────
    confs: list[ClusterConf] = []
    duration = 600  # default 10m

    if env_path and env_path.exists():
        vals = load_env(env_path)
        try:
            confs = build_clusters(vals)
        except ValueError as e:
            print(f"  Config error: {e}")
        if "DURATION" in vals and not args.duration:
            duration = parse_duration(vals["DURATION"])
        if "SEED" in vals:
            args.seed = int(vals["SEED"])

    if args.duration:
        duration = parse_duration(args.duration)

    if not confs:
        print("\n  No clusters configured via .env — switching to interactive mode.\n")
        confs, duration = collect_interactive()

    print(f"\n  Clusters : {', '.join(c.name for c in confs)}")
    print(f"  Duration : {duration}s")

    # ── Build clients ─────────────────────────────────────────────────────────
    clients = {conf.name: make_client(conf) for conf in confs}
    synth = SynthGen(seed=args.seed)

    # ── Teardown mode ─────────────────────────────────────────────────────────
    if args.teardown:
        print("\n  Tearing down all sample-* resources...")
        for conf in confs:
            client = clients[conf.name]
            teardown_all(client)
            h = get_cluster_health(client)
            print(f"  [{conf.name}] cluster health after teardown: {h.get('status', '?')}")
        print("\n  Done.\n")
        return

    # ── Scenario selection ────────────────────────────────────────────────────
    selected: set[str] = set()

    if args.problems and not args.interactive:
        raw = args.problems.strip().lower()
        if raw == "all":
            selected = {s["id"] for s in CATALOG}
        elif raw in ("safe", "none"):
            selected = set(DEFAULT_SAFE) if raw == "safe" else set()
        else:
            selected = {p.strip() for p in raw.split(",") if p.strip()}
    elif args.interactive or not args.problems:
        initial = None
        if args.problems == "none":
            initial = set()
        selected = interactive_picker(initial)

    # RED gate
    if "red" in selected:
        if not confirm_red():
            selected.discard("red")
            print("  RED scenario skipped.\n")

    if not selected:
        print("  No scenarios selected — running healthy baseline only.\n")

    print(f"\n  Active scenarios : {', '.join(sorted(selected)) or '(healthy baseline)'}")
    print(f"  Seed             : {args.seed}\n")

    # ── Bootstrap espipe ──────────────────────────────────────────────────────
    print("  Checking espipe...")
    try:
        cmd = ensure_espipe()
        print(f"  espipe ready: {' '.join(cmd[:2])}\n")
    except RuntimeError as e:
        print(f"\n  ERROR: {e}\n")
        sys.exit(1)

    # ── Cluster setup ─────────────────────────────────────────────────────────
    print("  Setting up cluster resources...")
    for conf in confs:
        client = clients[conf.name]
        print(f"  [{conf.name}] configuring ILM, templates, datastreams, plain indices...")
        setup_all(client, synth)

        if selected:
            print(f"  [{conf.name}] applying problem scenarios...")
            ctx = apply_problems(client, selected)

        h = get_cluster_health(client)
        print(f"  [{conf.name}] cluster health: {h.get('status', '?')}  "
              f"(shards: {h.get('active_shards', 0)} active, "
              f"{h.get('unassigned_shards', 0)} unassigned)")

    print()

    # ── Stats counters ────────────────────────────────────────────────────────
    stats = {"docs_ingested": 0, "rejections": 0}
    lock = threading.Lock()

    # ── Query worker pool ─────────────────────────────────────────────────────
    n_query_workers = 64 if "threadpool" in selected else 6
    stop_event = threading.Event()
    executor = ThreadPoolExecutor(max_workers=n_query_workers, thread_name_prefix="qw")
    query_futures = []

    for conf in confs:
        client = clients[conf.name]
        for _ in range(n_query_workers):
            f = executor.submit(_query_worker, client, selected, stop_event, stats, lock)
            query_futures.append(f)

    # ── Main ingest loop ──────────────────────────────────────────────────────
    deadline = time.time() + duration
    tick = 0

    print(f"  Running for {duration}s  (Ctrl+C to stop early)")
    print(f"  {'─'*60}")

    try:
        while time.time() < deadline:
            tick += 1
            elapsed   = int(time.time() - (deadline - duration))
            remaining = max(0, int(deadline - time.time()))

            _do_ingest_tick(confs, clients, synth, selected, stats, lock)

            with lock:
                docs = stats["docs_ingested"]
                rejs = stats["rejections"]
            print(
                f"\r  [{elapsed:4d}s / {duration}s]  "
                f"docs: {docs:>9,}  "
                f"429s: {rejs:>5}  "
                f"remaining: {remaining}s   ",
                end="", flush=True,
            )

            time.sleep(_TICK_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n  Interrupted — stopping...")

    print()

    # ── Shutdown ──────────────────────────────────────────────────────────────
    stop_event.set()
    executor.shutdown(wait=False)

    # ── Final summary ─────────────────────────────────────────────────────────
    print("\n" + "═" * 62)
    print("  Run Summary")
    print("═" * 62)
    with lock:
        print(f"  Documents ingested : {stats['docs_ingested']:,}")
        print(f"  429 rejections     : {stats['rejections']:,}")

    for conf in confs:
        client = clients[conf.name]
        h  = get_cluster_health(client)
        rj = get_rejection_counts(client)
        print(f"\n  [{conf.name}]")
        print(f"    Cluster status     : {h.get('status', '?').upper()}")
        print(f"    Active shards      : {h.get('active_shards', '?')}")
        print(f"    Unassigned shards  : {h.get('unassigned_shards', '?')}")
        print(f"    Search rejections  : {rj['search']}")
        print(f"    Write rejections   : {rj['write']}")

    print()
    print("  Indices")
    print("  ─" * 30)
    print("  Stable data streams : sample-transactions, sample-logs, sample-metrics")
    print("  Plain reference     : sample-reference")
    if selected:
        print(f"\n  Induced scenarios (data left in place for agent inspection):")
        _problem_indices = {
            "mapping_explosion": "sample-field-explosion",
            "oversharding":      "sample-oversharded",
            "unassigned":        "sample-unassigned",
            "hotspot":           "sample-hotspot",
            "slow_cpu":          "sample-search-stress",
            "heap":              "sample-search-stress",
            "scroll":            "sample-search-stress",
            "red":               "sample-red",
        }
        for sid in sorted(selected):
            entry = next((s for s in CATALOG if s["id"] == sid), {"label": sid})
            idx   = _problem_indices.get(sid, "")
            print(f"    • {sid:<22} {entry.get('label',''):<28} → {idx}")
    print()
    print("  To clean up: python3 sample/run.py --teardown")
    print("═" * 62)


if __name__ == "__main__":
    main()
