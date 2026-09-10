#!/bin/bash
# Cloudflare Workers serves static files directly from ./public/
# (see wrangler.jsonc "assets.directory"), NOT from public_site/static
# or admin_site/static. Run this after any CSS/JS change, before
# committing, or those changes will silently never reach production.
set -e
cd "$(dirname "$0")"

cp -r public_site/static/. public/static/
cp -r admin_site/static/. public/admin/static/

echo "Synced public_site/static -> public/static"
echo "Synced admin_site/static -> public/admin/static"
