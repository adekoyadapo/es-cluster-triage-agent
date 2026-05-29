#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Cleaning stale dist/views..."
rm -rf dist/views

echo "==> Building TypeScript + views..."
npm run build

echo "==> Bundling server with esbuild..."
npx esbuild dist/main.js \
  --bundle \
  --platform=node \
  --format=esm \
  --target=node22 \
  --outfile=dist/main.bundle.mjs \
  --banner:js="import{createRequire}from'module';const require=createRequire(import.meta.url);"

echo "==> Packing MCPB bundle..."
npx @anthropic-ai/mcpb pack .

# Normalize output filename to match manifest name
if [ -f elastic-cluster-triage-agent.mcpb ]; then
  echo ""
elif [ -f "$(basename "$ROOT").mcpb" ]; then
  mv "$(basename "$ROOT").mcpb" elastic-cluster-triage-agent.mcpb
fi

VERSION=$(node -e "console.log(require('./package.json').version)")
echo ""
echo "==> Done! elastic-cluster-triage-agent.mcpb (v${VERSION}) is ready."
echo ""
echo "Install in Claude Desktop:"
echo "  Double-click elastic-cluster-triage-agent.mcpb"
echo ""
echo "Or distribute via GitHub release:"
echo "  gh release create v${VERSION} elastic-cluster-triage-agent.mcpb"
