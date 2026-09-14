#!/usr/bin/env bash
# Pack an existing dist/ into a .mcpb without re-running TypeScript or view builds.
# Requires a prior `npm run build` or `npm run mcpb:pack`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Parse --mode flag (cluster | app-index | combined); default: cluster
MODE="cluster"
for arg in "$@"; do
  case "$arg" in
    --mode=*) MODE="${arg#--mode=}" ;;
  esac
done

case "$MODE" in
  cluster|app-index|combined) ;;
  *) echo "Unknown --mode '$MODE'. Use cluster, app-index, or combined." >&2; exit 1 ;;
esac

if [ ! -f dist/main.js ]; then
  echo "Error: dist/main.js not found. Run 'npm run build' first." >&2
  exit 1
fi

# Determine output name
case "$MODE" in
  cluster)  OUT_NAME="elastic-cluster-triage-agent.mcpb" ;;
  app-index) OUT_NAME="elastic-app-index-triage-agent.mcpb" ;;
  combined)  OUT_NAME="elastic-triage-combined.mcpb" ;;
esac

echo "==> Bundling server with esbuild (BUILD_MODE=$MODE)..."
npx esbuild dist/main.js \
  --bundle \
  --platform=node \
  --format=esm \
  --target=node22 \
  --outfile=dist/main.bundle.mjs \
  "--define:BUILD_MODE=\"$MODE\"" \
  --banner:js="import{createRequire}from'module';const require=createRequire(import.meta.url);"

# Swap manifest for non-cluster modes
MANIFEST_SWAPPED=false
if [ "$MODE" != "cluster" ] && [ -f "manifest-${MODE}.json" ]; then
  cp manifest.json manifest.cluster.json.bak
  cp "manifest-${MODE}.json" manifest.json
  MANIFEST_SWAPPED=true
fi

echo "==> Packing MCPB bundle..."
npx @anthropic-ai/mcpb pack .

# Restore manifest if swapped
if $MANIFEST_SWAPPED; then
  mv manifest.cluster.json.bak manifest.json
fi

# Normalize output filename
PACKED_NAME="$(basename "$ROOT").mcpb"
if [ -f "elastic-cluster-triage-agent.mcpb" ] && [ "$OUT_NAME" != "elastic-cluster-triage-agent.mcpb" ]; then
  mv elastic-cluster-triage-agent.mcpb "$OUT_NAME"
elif [ -f "$PACKED_NAME" ]; then
  mv "$PACKED_NAME" "$OUT_NAME"
fi

VERSION=$(node -e "console.log(require('./package.json').version)")
echo ""
echo "==> Done! ${OUT_NAME} (v${VERSION}) is ready."
