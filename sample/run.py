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
import sys
import time
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ── Silence noisy libraries before anything else imports them ──────────────────
import urllib3
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

_TICK_INTERVAL = 10      # seconds between ingest ticks
_HEALTH_EVERY  = 3       # check cluster health every N ticks

# Docs generated per tick per dataset
_BATCH_SIZES = {
    "transactions": 150,
    "javalogs":     400,
    "metrics":      800,
}

# Maps scenario id → display label for dashboard
_SCENARIO_TARGETS = {
    "mapping_explosion": "sample-field-explosion      [dynamic:true, 100K fields]",
    "oversharding":      "sample-oversharded          [12 primaries, no rollover]",
    "unassigned":        "sample-unassigned           [replicas:3 → YELLOW]",
    "hotspot":           "sample-hotspot              [all writes → shard 0]",
    "slow_cpu":          "sample-search-stress        [0ms slowlog, expensive qs]",
    "heap":              "sample-search-stress        [fielddata:true on text]",
    "scroll":            "sample-search-stress        [from:10k+ / scroll leak]",
    "threadpool":        "64 concurrent query workers [→ 429 rejections]",
    "red":               "sample-red                  [impossible alloc → RED]",
}


# ── Dashboard ──────────────────────────────────────────────────────────────────

_BW = 60          # box inner width (between ║ characters)
_PFX = "  "       # left indent

def _top():    return f"{_PFX}╔{'═' * _BW}╗"
def _bot():    return f"{_PFX}╚{'═' * _BW}╝"
def _div():    return f"{_PFX}╠{'═' * _BW}╣"
def _row(s=""):
    content_w = _BW - 2
    if len(s) > content_w:
        s = s[:content_w - 1] + "…"
    return f"{_PFX}║ {s:<{content_w}} ║"


def _progress_bar(pct: float, width: int = 28) -> str:
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


def _render_dashboard(stats: dict, scenarios: set, cluster_names: list,
                      duration: int, start_time: float) -> list[str]:
    elapsed   = time.time() - start_time
    remaining = max(0, duration - elapsed)
    pct       = min(1.0, elapsed / max(1, duration))

    def fmt_t(s): return f"{int(s // 60)}m {int(s % 60):02d}s"

    lines = [_top()]
    lines.append(_row(f" ES Activity Simulator            {fmt_t(elapsed)} / {fmt_t(duration)}"))
    lines.append(_row(f" Clusters: {', '.join(cluster_names)}"))
    lines.append(_div())

    # Progress bar
    bar = _progress_bar(pct)
    lines.append(_row(f" [{bar}]  {int(pct * 100):3d}%  —  {fmt_t(remaining)} left"))
    lines.append(_div())

    # Ingest stats
    lines.append(_row(" INGEST"))
    by_ds = stats.get("by_dataset", {})
    labels = [
        ("transactions", "transactions"),
        ("javalogs",     "logs (Java)"),
        ("metrics",      "metrics"),
        ("problems",     "problem indices"),
    ]
    for key, label in labels:
        n = by_ds.get(key, 0)
        if n > 0 or key != "problems":
            lines.append(_row(f"   {label:<20}  {n:>9,} docs"))
    total = stats.get("docs_ingested", 0)
    rejs  = stats.get("rejections", 0)
    lines.append(_row(f"   {'─' * 38}"))
    rej_str = f"  429s: {rejs:,}" if rejs > 0 else ""
    lines.append(_row(f"   {'Total':<20}  {total:>9,} docs{rej_str}"))
    lines.append(_div())

    # Cluster health  (use narrow ASCII markers — emoji are 2 display cols but
    # Python counts them as 1, which throws off the ANSI cursor-up line math)
    lines.append(_row(" CLUSTER HEALTH"))
    health = stats.get("cluster_health", {})
    if health:
        for cname, h in health.items():
            status = str(h.get("status", "?")).upper()
            mark   = {"GREEN": "+", "YELLOW": "!", "RED": "X"}.get(status, "?")
            active   = h.get("active_shards", "?")
            unassign = h.get("unassigned_shards", 0)
            search_r = h.get("search_rejections", 0)
            write_r  = h.get("write_rejections", 0)
            lines.append(_row(f"   [{mark}] {cname:<16} {status}"))
            lines.append(_row(f"       shards: {active} active  /  {unassign} unassigned"))
            if search_r or write_r:
                lines.append(_row(f"       rejections: search={search_r}  write={write_r}"))
    else:
        lines.append(_row("   (checking…)"))
    lines.append(_div())

    # Scenarios
    lines.append(_row(f" SCENARIOS  ({len(scenarios)} active)"))
    for sid in sorted(scenarios):
        target = _SCENARIO_TARGETS.get(sid, sid)
        lines.append(_row(f"   ✓ {sid:<20}  {target}"))
    if not scenarios:
        lines.append(_row("   (healthy baseline — no problem scenarios)"))

    lines.append(_bot())
    lines.append(f"{_PFX}  Press Ctrl+C to stop early")
    return lines


_dashboard_height = 0


def _draw_dashboard(lines: list[str]) -> None:
    global _dashboard_height
    if _dashboard_height:
        # Move cursor up to start of previous draw, clear each line as we go
        sys.stdout.write(f"\033[{_dashboard_height}A")
    for line in lines:
        sys.stdout.write(f"\033[2K{line}\n")
    sys.stdout.flush()
    _dashboard_height = len(lines)


# ── Ingest helpers ─────────────────────────────────────────────────────────────

def _ingest_with_routing(client, docs: list[dict], index_name: str, routing_key: str) -> int:
    """Bulk-index docs with a fixed routing key (forces all to one shard)."""
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
        yield json.dumps({
            "@timestamp": synth.ts_now_iso(),
            "message": synth.lorem_words(5),
            "value":   synth._float(0, 100),
            "tag":     "overshard",
        })


def _do_ingest_tick(confs: list[ClusterConf], clients: dict, synth: SynthGen,
                    problems: set[str], stats: dict, lock: threading.Lock) -> None:
    """Generate one batch per dataset and ingest into all clusters."""
    for ds_cls in DATASETS:
        n = _BATCH_SIZES.get(ds_cls.name, 100)

        if ds_cls.name == "transactions":
            lines = list(TransactionsDataset.generate_ndjson_lines(synth, n, False))
            ds_key = "transactions"
        elif ds_cls.name == "javalogs":
            lines = list(JavaLogsDataset.generate_ndjson_lines(synth, n, False))
            ds_key = "javalogs"
        else:
            lines = list(MetricsDataset.generate_ndjson_lines(synth, n))
            ds_key = "metrics"

        for conf in confs:
            result = ingest_lines(iter(lines), conf, ds_cls.datastream)
            sent = result.get("docs_sent", 0)
            with lock:
                stats["docs_ingested"] += sent
                stats["by_dataset"][ds_key] += sent

    # Problem-index ingest
    problem_docs = 0

    if "oversharding" in problems:
        ov_lines = list(_generate_oversharded_lines(synth, 200))
        for conf in confs:
            r = ingest_lines(iter(ov_lines), conf, "sample-oversharded", action="index")
            problem_docs += r.get("docs_sent", 0)

    if "mapping_explosion" in problems:
        ex_lines = list(_generate_field_explosion_lines(synth, 100))
        for conf in confs:
            r = ingest_lines(iter(ex_lines), conf, "sample-field-explosion", action="index")
            problem_docs += r.get("docs_sent", 0)

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
            ok = _ingest_with_routing(clients[conf.name], hotspot_docs, "sample-hotspot", "hot_partition")
            problem_docs += ok

    if problems & {"slow_cpu", "heap", "scroll", "threadpool"}:
        ss_lines = list(_generate_search_stress_lines(synth, 50))
        for conf in confs:
            r = ingest_lines(iter(ss_lines), conf, "sample-search-stress", action="index")
            problem_docs += r.get("docs_sent", 0)

    if problem_docs:
        with lock:
            stats["docs_ingested"] += problem_docs
            stats["by_dataset"]["problems"] += problem_docs


# ── Query workers ──────────────────────────────────────────────────────────────

def _query_worker(client, problems: set[str], stop_event: threading.Event,
                  stats: dict, lock: threading.Lock) -> None:
    import random
    rng = random.Random()
    local_rej = 0

    def _search(body: dict, index: str = "sample-transactions*") -> None:
        nonlocal local_rej
        if stop_event.is_set():
            return
        try:
            client.search(index=index, body=body, request_timeout=30)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rejected" in msg.lower():
                local_rej += 1

    while not stop_event.is_set():
        try:
            # Healthy baseline — mixed queries across all streams + reference lookup
            _search({"query": {"match_all": {}}, "size": 10})
            _search({"query": {"match_all": {}}, "size": 5}, index="sample-logs*")
            _search({"query": {"match_all": {}}, "size": 5}, index="sample-metrics*")
            _search({"query": {"term": {"active": True}}, "size": 20}, index="sample-reference")

            if "slow_cpu" in problems:
                word = rng.choice(["lorem", "ipsum", "dolor", "amet"])
                _search({"query": {"query_string": {
                    "default_field": "message",
                    "query": f"message:*{word}*",
                }}, "size": 5}, index="sample-search-stress")
                _search({
                    "size": 0,
                    "aggs": {
                        "by_tag": {
                            "terms": {"field": "tag", "size": 10000},
                            "aggs": {"unique_users": {"cardinality": {"field": "user_ref"}}},
                        }
                    },
                }, index="sample-search-stress")
                _search({"query": {"regexp": {
                    "user_ref": f"PTY-[0-9]{{{rng.randint(4, 6)}}}"
                }}, "size": 5}, index="sample-search-stress")

            if "heap" in problems:
                _search({
                    "size": 0,
                    "aggs": {"message_terms": {"terms": {"field": "message", "size": 10000}}},
                }, index="sample-search-stress")

            if "scroll" in problems:
                offset = rng.choice([5000, 10000, 20000])
                try:
                    client.search(
                        index="sample-search-stress",
                        body={"from": offset, "size": 10, "query": {"match_all": {}}},
                        request_timeout=30,
                    )
                except Exception as e:
                    if "429" in str(e) or "rejected" in str(e).lower():
                        local_rej += 1
                try:
                    # Intentionally leak scroll context
                    client.search(
                        index="sample-search-stress", scroll="5m",
                        body={"size": 50, "query": {"match_all": {}}},
                        request_timeout=30,
                    )
                except Exception:
                    pass

            if "threadpool" in problems:
                _search({"query": {"match_all": {}}, "size": 100}, index="sample-*")

        except Exception:
            pass

        with lock:
            stats["rejections"] += local_rej
        local_rej = 0
        stop_event.wait(rng.uniform(0.05, 0.3))


# ── Setup helpers ──────────────────────────────────────────────────────────────

def _setup_step(cluster: str, label: str, ok: bool = True) -> None:
    icon = "✓" if ok else "✗"
    print(f"  [{cluster}]  {icon}  {label}")


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
    parser.add_argument("--interactive", action="store_true", help="Force interactive picker")
    parser.add_argument("--teardown",    action="store_true", help="Delete all sample-* and exit")
    parser.add_argument("--seed",        type=int, default=42, help="Random seed")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    # ── Logging ───────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress HTTP-level noise from elastic_transport, urllib3, and espipe bootstrap
    _noisy = [
        "elastic_transport",
        "elastic_transport.transport",
        "elastic_transport.node_pool",
        "urllib3",
        "urllib3.connectionpool",
        "sample.espipe_runner",   # logs INFO during bootstrap; hidden in banner
    ]
    for _name in _noisy:
        logging.getLogger(_name).setLevel(logging.ERROR)

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

    # ── Suppress es_admin INFO during setup (re-enable after) ─────────────────
    _admin_log = logging.getLogger("sample.es_admin")
    _admin_level_saved = _admin_log.level

    clients = {conf.name: make_client(conf) for conf in confs}
    synth   = SynthGen(seed=args.seed)

    # ── Teardown mode ─────────────────────────────────────────────────────────
    if args.teardown:
        print("\n  Tearing down all sample-* resources…")
        for conf in confs:
            client = clients[conf.name]
            teardown_all(client)
            h = get_cluster_health(client)
            status = h.get("status", "?").upper()
            print(f"  [{conf.name}]  ✓  Cluster: {status}")
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

    if "red" in selected:
        if not confirm_red():
            selected.discard("red")
            print("  RED scenario skipped.\n")

    # ── Interactive duration prompt (if not already set via CLI or .env) ───────
    _interactive_mode = args.interactive or not args.problems
    if _interactive_mode and not args.duration:
        dur_default = "10m"
        print()
        try:
            dur_inp = input(f"  Duration [{dur_default}]: ").strip()
            if dur_inp:
                try:
                    duration = parse_duration(dur_inp)
                except (ValueError, TypeError):
                    print(f"  (invalid format — using {dur_default})")
        except (EOFError, KeyboardInterrupt):
            print()

    # ── Print banner ──────────────────────────────────────────────────────────
    cluster_names = [c.name for c in confs]
    dur_str = f"{duration // 60}m" if duration % 60 == 0 else f"{duration}s"
    print()
    print(_top())
    print(_row(f" ES Activity Simulator"))
    print(_row(f" Clusters : {', '.join(cluster_names)}"))
    print(_row(f" Duration : {dur_str}   Seed: {args.seed}"))
    scenario_list = ", ".join(sorted(selected)) or "healthy baseline"
    # Truncate if too long for box
    max_len = _BW - 16
    if len(scenario_list) > max_len:
        scenario_list = scenario_list[:max_len - 1] + "…"
    print(_row(f" Scenarios: {len(selected)} active — {scenario_list}"))
    print(_div())

    # ── Bootstrap espipe ──────────────────────────────────────────────────────
    print(_row(" Checking espipe…"))
    try:
        cmd = ensure_espipe()
        print(_row(f"  ✓  espipe: {cmd[0]}"))
    except RuntimeError as e:
        print(_row(f"  ✗  espipe: {e}"))
        print(_bot())
        sys.exit(1)
    print(_div())

    # ── Cluster setup (suppress HTTP logs, print step bullets) ────────────────
    _problems_log = logging.getLogger("sample.scenarios.problems")
    _healthy_log  = logging.getLogger("sample.scenarios.healthy")
    _admin_log.setLevel(logging.WARNING)
    _problems_log.setLevel(logging.WARNING)
    _healthy_log.setLevel(logging.WARNING)

    print(_row(" Setting up cluster resources…"))
    for conf in confs:
        client = clients[conf.name]
        print(_row(f"  [{conf.name}]  ILM policies + component/index templates…"))
        sys.stdout.flush()
        setup_all(client, synth)
        print(_row(f"  [{conf.name}]  ✓  ILM, templates, data streams, plain indices"))

        if selected:
            print(_row(f"  [{conf.name}]  Applying {len(selected)} problem scenarios…"))
            sys.stdout.flush()
            apply_problems(client, selected)
            print(_row(f"  [{conf.name}]  ✓  Scenarios applied"))

        h = get_cluster_health(client)
        status = h.get("status", "?").upper()
        active = h.get("active_shards", "?")
        unassign = h.get("unassigned_shards", 0)
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(status, "⚪")
        print(_row(f"  [{conf.name}]  {icon}  {status}  —  {active} shards active, {unassign} unassigned"))

    _admin_log.setLevel(_admin_level_saved)
    _problems_log.setLevel(_admin_level_saved)
    _healthy_log.setLevel(_admin_level_saved)
    print(_bot())
    print()

    # ── Stats and shared state ────────────────────────────────────────────────
    stats: dict = {
        "docs_ingested": 0,
        "rejections": 0,
        "by_dataset": {"transactions": 0, "javalogs": 0, "metrics": 0, "problems": 0},
        "cluster_health": {},
    }
    lock = threading.Lock()

    # Initial health snapshot
    for conf in confs:
        h  = dict(get_cluster_health(clients[conf.name]))
        rj = get_rejection_counts(clients[conf.name])
        h["search_rejections"] = rj.get("search", 0)
        h["write_rejections"]  = rj.get("write", 0)
        with lock:
            stats["cluster_health"][conf.name] = h

    # ── Query workers ─────────────────────────────────────────────────────────
    n_workers = 64 if "threadpool" in selected else 6
    stop_event = threading.Event()
    executor = ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="qw")

    for conf in confs:
        for _ in range(n_workers):
            executor.submit(_query_worker, clients[conf.name], selected, stop_event, stats, lock)

    # ── Main ingest loop ──────────────────────────────────────────────────────
    start_time    = time.time()
    deadline      = start_time + duration
    tick          = 0
    interrupted   = False

    try:
        while time.time() < deadline and not stop_event.is_set():
            tick += 1

            # Ingest
            _do_ingest_tick(confs, clients, synth, selected, stats, lock)

            if stop_event.is_set():
                break

            # Refresh cluster health every N ticks
            if tick % _HEALTH_EVERY == 0:
                for conf in confs:
                    try:
                        h  = dict(get_cluster_health(clients[conf.name]))
                        rj = get_rejection_counts(clients[conf.name])
                        h["search_rejections"] = rj.get("search", 0)
                        h["write_rejections"]  = rj.get("write", 0)
                        with lock:
                            stats["cluster_health"][conf.name] = h
                    except Exception:
                        pass

            # Redraw dashboard
            with lock:
                snap = {
                    "docs_ingested": stats["docs_ingested"],
                    "rejections":    stats["rejections"],
                    "by_dataset":    dict(stats["by_dataset"]),
                    "cluster_health": dict(stats["cluster_health"]),
                }
            dash_lines = _render_dashboard(snap, selected, cluster_names, duration, start_time)
            _draw_dashboard(dash_lines)

            # Interruptible sleep — wakes immediately when stop_event is set
            stop_event.wait(_TICK_INTERVAL)

    except KeyboardInterrupt:
        interrupted = True
        stop_event.set()
        # Move below the dashboard and print a clear stop notice
        global _dashboard_height
        if _dashboard_height:
            sys.stdout.write("\n")
        sys.stdout.write(f"\033[2K{_PFX}  Ctrl+C received — stopping…\n\n")
        sys.stdout.flush()
        _dashboard_height = 0  # prevent final draw from cursor-upping over this message

    # ── Shutdown ──────────────────────────────────────────────────────────────
    stop_event.set()
    # Allow in-flight worker requests to drain before hard-stopping
    time.sleep(1.5)
    executor.shutdown(wait=False)

    # Final dashboard draw (only if not interrupted mid-draw)
    if not interrupted:
        with lock:
            snap = {
                "docs_ingested": stats["docs_ingested"],
                "rejections":    stats["rejections"],
                "by_dataset":    dict(stats["by_dataset"]),
                "cluster_health": dict(stats["cluster_health"]),
            }
        _draw_dashboard(_render_dashboard(snap, selected, cluster_names, duration, start_time))
        print()

    # ── Final summary ─────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    def _fmt_t(s): return f"{int(s // 60)}m {int(s % 60):02d}s"

    print(_top())
    if interrupted:
        print(_row(f" Stopped early after {_fmt_t(elapsed)}"))
    else:
        print(_row(f" Run Complete  ({_fmt_t(elapsed)})"))
    print(_div())
    with lock:
        print(_row(f"  Documents ingested : {stats['docs_ingested']:,}"))
        print(_row(f"  429 rejections     : {stats['rejections']:,}"))
    print(_div())

    for conf in confs:
        client = clients[conf.name]
        h  = dict(get_cluster_health(client))
        rj = get_rejection_counts(client)
        status = h.get("status", "?").upper()
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(status, "⚪")
        print(_row(f"  [{conf.name}]  {icon} {status}"))
        print(_row(f"    Active shards   : {h.get('active_shards', '?')}"))
        print(_row(f"    Unassigned      : {h.get('unassigned_shards', '?')}"))
        print(_row(f"    Search 429s     : {rj['search']}"))
        print(_row(f"    Write 429s      : {rj['write']}"))

    if selected:
        print(_div())
        print(_row("  Data left in place (use --teardown to clean up)"))
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
        seen = set()
        for sid in sorted(selected):
            idx = _problem_indices.get(sid, "")
            entry = next((s for s in CATALOG if s["id"] == sid), {"label": sid})
            key = (sid, idx)
            if key not in seen:
                print(_row(f"    ✓ {sid:<22}  → {idx}"))
                seen.add(key)

    print(_bot())
    print()


if __name__ == "__main__":
    main()
