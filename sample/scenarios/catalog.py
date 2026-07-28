"""
catalog.py — scenario registry and interactive multi-select menu.

Each scenario entry:
    id       — used as CLI flag value and .env PROBLEMS entry
    label    — human-readable name
    desc     — one-line description
    risk     — severity hint shown in menu
    gated    — if True, requires explicit confirmation (RED cluster)
"""
from __future__ import annotations

CATALOG: list[dict] = [
    {
        "id":    "mapping_explosion",
        "label": "Mapping bomb",
        "desc":  "dynamic:true on sample-field-explosion + random dyn_ fields → thousands of mappings",
        "risk":  "medium",
        "gated": False,
    },
    {
        "id":    "oversharding",
        "label": "Oversharding",
        "desc":  "12 primary shards on sample-oversharded, ILM never rolls",
        "risk":  "low",
        "gated": False,
    },
    {
        "id":    "slow_cpu",
        "label": "Slow / expensive queries",
        "desc":  "Wildcards, high-cardinality aggs, regexp against sample-search-stress → slowlog hits",
        "risk":  "medium",
        "gated": False,
    },
    {
        "id":    "threadpool",
        "label": "Thread-pool rejection",
        "desc":  "64+ concurrent workers → search queue overflow → 429s",
        "risk":  "medium",
        "gated": False,
    },
    {
        "id":    "unassigned",
        "label": "Cluster YELLOW / unassigned shards",
        "desc":  "sample-unassigned: replicas:3 on 3-node cluster → 1 unassignable → YELLOW",
        "risk":  "low",
        "gated": False,
    },
    {
        "id":    "heap",
        "label": "Heap / circuit-breaker",
        "desc":  "fielddata:true on text field in sample-search-stress + large terms aggs",
        "risk":  "medium",
        "gated": False,
    },
    {
        "id":    "scroll",
        "label": "Deep pagination / scroll",
        "desc":  "from:10000+ deep pages + long-lived scroll contexts on sample-search-stress",
        "risk":  "low",
        "gated": False,
    },
    {
        "id":    "hotspot",
        "label": "Shard write hotspot",
        "desc":  "All writes routed to shard 0 of sample-hotspot via fixed routing key",
        "risk":  "low",
        "gated": False,
    },
    {
        "id":    "red",
        "label": "Cluster RED",
        "desc":  "sample-red: impossible allocation filter → primary unassignable → RED",
        "risk":  "high",
        "gated": True,
    },
]

# Default safe set (all except red)
DEFAULT_SAFE: set[str] = {s["id"] for s in CATALOG if not s["gated"]}

_ID_TO_ENTRY = {s["id"]: s for s in CATALOG}


def get(sid: str) -> dict:
    return _ID_TO_ENTRY[sid]


def interactive_picker(initial: set[str] | None = None) -> set[str]:
    """
    Interactive multi-select menu. Returns the set of selected scenario ids.
    Navigation: enter numbers to toggle, 'all'/'safe'/'none', or blank to confirm.
    """
    selected: set[str] = set(initial) if initial is not None else set(DEFAULT_SAFE)

    _RISK_COLOR = {"low": "🟢", "medium": "🟡", "high": "🔴"}

    while True:
        print("\n" + "─" * 62)
        print("  Scenario Selection  (space/comma-separated numbers to toggle)")
        print("─" * 62)
        for i, s in enumerate(CATALOG, 1):
            mark = "✓" if s["id"] in selected else " "
            icon = _RISK_COLOR.get(s["risk"], "")
            gate = "  ⚠ confirmation required" if s["gated"] else ""
            print(f"  [{mark}] {i:2d}. {s['label']:<26}  {icon} {s['desc']}{gate}")
        print("─" * 62)
        count = len(selected)
        print(f"  Selected: {count}  |  all=all-safe  safe=all-safe  none=clear  Enter=confirm")

        try:
            inp = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not inp:
            break

        if inp in ("all", "safe"):
            selected = set(DEFAULT_SAFE)
        elif inp == "none":
            selected.clear()
        else:
            for token in inp.replace(",", " ").split():
                try:
                    idx = int(token) - 1
                    if 0 <= idx < len(CATALOG):
                        sid = CATALOG[idx]["id"]
                        if sid in selected:
                            selected.discard(sid)
                        else:
                            selected.add(sid)
                    else:
                        print(f"  (no scenario #{token})")
                except ValueError:
                    print(f"  (unrecognised: {token!r})")

    return selected


def confirm_red() -> bool:
    """Gate for the RED scenario — returns True if user explicitly confirms."""
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║  ⚠  WARNING: 'red' scenario sets the entire cluster to  ║")
    print("  ║     RED status. This may interrupt monitoring shipping   ║")
    print("  ║     to your agent's cluster during the run.             ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    try:
        ans = input("  Type 'yes' to proceed with RED, or Enter to skip: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans == "yes"
