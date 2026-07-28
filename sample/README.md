# ES Activity Simulator

Simulates realistic Elasticsearch cluster activity (ingestion + queries) and deliberately induces
notable anti-patterns that the **es-cluster-triage** agent should surface.

## Quick start

```bash
# 1. Install Python deps (elasticsearch-py is the only real dep; already installed)
pip install -r sample/requirements.txt

# 2. Copy and fill credentials
cp sample/.env.example sample/.env
# Edit sample/.env with your cluster URL + credentials

# 3. Run (interactive scenario picker, 10m default)
python3 sample/run.py

# 4. Targeted 2m validation with specific scenarios
python3 sample/run.py --duration 2m --problems mapping_explosion,hotspot,unassigned

# 5. All safe scenarios, 10m run
python3 sample/run.py --duration 10m --problems safe

# 6. Teardown (removes all sample-* indices, templates, ILM policies)
python3 sample/run.py --teardown
```

## Scenario catalog

| id | Anti-pattern | Risk | What it does |
|----|---|---|---|
| `mapping_explosion` | Mapping bomb | medium | `dynamic:true` on `sample-field-explosion` + random `dyn_*` fields → thousands of mappings |
| `oversharding` | Too many shards | low | 12 primary shards on `sample-oversharded`, ILM never rolls |
| `slow_cpu` | Expensive queries | medium | Wildcards, high-cardinality aggs, regexp against `sample-search-stress` → slowlog hits |
| `threadpool` | 429 rejections | medium | 64 concurrent workers → search queue overflow → 429s |
| `unassigned` | Cluster YELLOW | low | `sample-unassigned`: `replicas:3` on 3-node cluster → 1 unassignable → YELLOW |
| `heap` | Heap / circuit-breaker | medium | `fielddata:true` on text field in `sample-search-stress` + large terms aggs |
| `scroll` | Deep pagination | low | `from:10000+` + long-lived scroll contexts on `sample-search-stress` |
| `hotspot` | Shard write hotspot | low | All writes routed to shard 0 of `sample-hotspot` via fixed routing key |
| `red` | Cluster RED ⚠ | high | `sample-red`: impossible allocation filter → primary unassignable → RED |

**Default:** all scenarios except `red` are active when run without `--problems`.  
**RED** requires an explicit confirmation prompt; it can disrupt monitoring shipping.

## CLI reference

```
python3 sample/run.py [options]

  --env FILE          .env file path (default: sample/.env)
  --duration DUR      30s / 5m / 2h  (default: 10m)
  --problems IDS      comma-separated ids, or 'all' / 'safe' / 'none'
  --interactive       Always show interactive picker
  --teardown          Delete all sample-* resources and exit
  --seed INT          Random seed for reproducible data (default: 42)
  -v / --verbose      Debug logging
```

## Three stable data streams

All datastreams are created with ILM rollover policies and slowlog enabled.

| Datastream | Shape | Notes |
|---|---|---|
| `sample-transactions` | Genericized transaction records (~1-2 KB/doc) | Revamp of customer mapping.json; text+keyword multifields, monetary sub-objects |
| `sample-logs` | Java/Spring app logs (~0.5-2 KB/doc) | Weighted levels (INFO 80%, WARN 12%, ERROR 6%); stacktraces on ERROR |
| `sample-metrics` | System/infra metrics (~200-400 bytes/doc) | ECS-compatible; cpu, memory, filesystem, network, load metricsets; high write rate |

## Plain reference index

| Index | Purpose |
|---|---|
| `sample-reference` | Static merchant lookup table (~45 docs); seeded once at setup; simulates an app performing reference data lookups |

## Problem indices (created on demand)

Each scenario creates (or mutates) a dedicated `sample-*` plain index so the induced condition is
isolated and inspectable after the run.

| Scenario | Index |
|---|---|
| `mapping_explosion` | `sample-field-explosion` |
| `oversharding` | `sample-oversharded` |
| `unassigned` | `sample-unassigned` |
| `hotspot` | `sample-hotspot` |
| `slow_cpu` / `heap` / `scroll` | `sample-search-stress` |
| `red` | `sample-red` |

## espipe

The ingest leg uses [espipe](https://github.com/vimcommando/espipe) (Rust CLI).
If not installed, the tool will try `cargo install espipe`, then fall back to Docker.

```bash
# Manual install
cargo install espipe

# Test
espipe --help
```

## .env format

```
DURATION=10m
SEED=42

# Cluster 1 (basic auth)
CLUSTER_1_URL=https://es.example.com:9200
CLUSTER_1_USERNAME=elastic
CLUSTER_1_PASSWORD=changeme

# Cluster 2 (API key)
CLUSTER_2_URL=https://other.example.com:9200
CLUSTER_2_API_KEY=id:secret
```

Multiple `CLUSTER_<n>_*` blocks → simultaneous multi-cluster ingest.

## Safety notes

- All indices/datastreams/templates/ILM policies use the `sample-` prefix.
- `--teardown` is prefix-scoped: it cannot touch non-sample resources.
- `red` is gated; it creates a throwaway index and will turn the whole cluster RED.
  Only use it in a non-critical environment.
