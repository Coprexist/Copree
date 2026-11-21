#!/usr/bin/env bash
# 构建并发布 copree 站点到 Cloudflare Pages
# 依赖：/root/.cf-token（Cloudflare API Token，Pages:Edit）、/data_s001/coprexist-site/tools 下的 wrangler
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${SITE_OUT:-/data_s001/coprexist-site/dist}"
ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-7221c5c742d2eef6ba469d8b6886046a}"
PROJECT="${CF_PAGES_PROJECT:-copree}"
TOKEN_FILE="${CF_TOKEN_FILE:-/root/.cf-token}"
WRANGLER="${SITE_OUT:-/data_s001/coprexist-site}/tools/node_modules/.bin/wrangler"

[[ -r "$TOKEN_FILE" ]] || { echo "缺少 token：$TOKEN_FILE" >&2; exit 1; }
[[ -x "$WRANGLER" ]] || { echo "缺少 wrangler：$WRANGLER" >&2; exit 1; }

node "$ROOT/site/build.mjs"
chmod -R a+rX "$OUT"

CLOUDFLARE_API_TOKEN="$(cat "$TOKEN_FILE")" \
CLOUDFLARE_ACCOUNT_ID="$ACCOUNT_ID" \
CI=true "$WRANGLER" pages deploy "$OUT" \
  --project-name="$PROJECT" --branch=main --commit-dirty=true
