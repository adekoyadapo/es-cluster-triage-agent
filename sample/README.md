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

# 3. Run (interactive scenario picker, 5m default)
python3 sample/run.py

# 4. Targeted 90s validation with specific scenarios
python3 sample/run.py --duration 90s --problems mapping_explosion,oversharding,slow_cpu,threadpool,yellow

# 5. Teardown (removes all sample-* indices, templates, ILM policies)
python3 sample/run.py --teardown
```

## Scenario catalog

| id | Anti-pattern | Risk | What it does |
|----|---|---|---|
| `mapping_explosion` | Mapping bomb | medium | `dynamic:true` + random field names → thousands of fields |
| `oversharding` | Too many shards | low | 12 primary shards, ILM never rolls |
| `slow_cpu` | Expensive queries | medium | Wildcards, high-cardinality aggs, scripts → CPU + slowlog |
| `threadpool` | 429 rejections | medium | 64 concurrent workers → search/write queue overflow |
| `yellow` | Cluster YELLOW | low | `replicas:3` on 3 nodes → 1 unassignable → YELLOW |
| `heap` | Heap / circuit-breaker | medium | `fielddata:true` on text → heap pressure |
| `scroll` | Deep pagination | low | `from:10000+` + long scroll contexts |
| `red` | Cluster RED ⚠ | high | Impossible allocation filter → primary unassignable → RED |

**Default:** all scenarios except `red` are active when run without `--problems`.  
**RED** requires an explicit confirmation prompt; it can disrupt monitoring shipping.

## CLI reference

```
python3 sample/run.py [options]

  --env FILE          .env file path (default: sample/.env)
  --duration DUR      30s / 5m / 2h  (default: 5m)
  --problems IDS      comma-separated ids, or 'all' / 'safe' / 'none'
  --interactive       Always show interactive picker
  --teardown          Delete all sample-* resources and exit
  --seed INT          Random seed for reproducible data (default: 42)
  -v / --verbose      Debug logging
```

## Three datasets

All datastreams are created with ILM rollover policies and slowlog enabled.

| Datastream | Shape | Notes |
|---|---|---|
| `sample-transactions` | Genericized transaction records (~1-2 KB/doc) | Revamp of customer mapping.json; text+keyword multifields, monetary sub-objects |
| `sample-logs` | Java/Spring app logs (~0.5-2 KB/doc) | Weighted levels (INFO 80%, WARN 12%, ERROR 6%); stacktraces on ERROR |
| `sample-bulky` | Random padded documents (4-10 KB/doc) | Fixed schema `dynamic:false`; large `blob` field for shard-size pressure |

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
DURATION=5m
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
- Never lower `cluster.routing.allocation.disk.watermark.*` globally — the bulky dataset
  simulates shard-size pressure via growth trends, not actual watermark breach (for short runs).
- `red` is gated; it creates a throwaway index and will turn the whole cluster RED.
  Only use it in a non-critical environment.
