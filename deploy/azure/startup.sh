#!/bin/bash
# Azure App Service startup command.
# Set with:
#   az webapp config set -g <rg> -n <app> --startup-file "deploy/azure/startup.sh"
#
# App Service terminates TLS at its front end and proxies to this process over
# plain HTTP on $PORT. CRBS_ENV=production marks the session cookie `secure`,
# which is correct because the browser only ever speaks https to the front end.
set -e

# App Service injects PORT; 8000 is the local fallback.
exec gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:"${PORT:-8000}" \
    --timeout 120 \
    --forwarded-allow-ips '*' \
    --access-logfile - \
    --error-logfile -
