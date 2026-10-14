#!/usr/bin/env bash
# Build the DeepScout static demo bundle for Cloudflare Pages (or any static
# host). Run FROM THE REPO ROOT:
#
#     bash deploy/cloudflare-pages/build.sh
#
# Output: frontend/dist  (upload this as the Pages "build output directory").
#
# Demo mode is enabled by setting VITE_DEMO=1, which swaps the API client onto
# the scripted fixtures in frontend/src/demo/ at BUILD time. The result is a
# self-contained landing + docs + console bundle that needs NO backend.
#
# The two Cloudflare control files (_redirects, _headers) live in this folder
# and are copied into dist/ so a direct `wrangler pages deploy` and the
# "connect the repo" flow both pick them up.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND="$ROOT/frontend"
DEPLOY_DIR="$ROOT/deploy/cloudflare-pages"

echo "==> Building static demo bundle"
cd "$FRONTEND"

if [ ! -d node_modules ]; then
  echo "==> Installing frontend dependencies"
  npm ci
fi

# VITE_DEMO=1  -> fixtures replayed client-side, no /api calls leave the page.
# VITE_BASE=/  -> site served at the root (Cloudflare Pages default).
VITE_DEMO=1 VITE_BASE=/ npm run build

echo "==> Copying Cloudflare control files into dist/"
cp "$DEPLOY_DIR/_redirects" "$FRONTEND/dist/_redirects"
cp "$DEPLOY_DIR/_headers"   "$FRONTEND/dist/_headers"

echo "==> Done. Output: $FRONTEND/dist"
ls -la "$FRONTEND/dist"
