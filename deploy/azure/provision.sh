#!/usr/bin/env bash
#
# Provisions the Conference Room Booking & Cost Recovery System on Azure:
#   - Azure Database for PostgreSQL Flexible Server
#   - Linux App Service Plan + Web App (Python 3.11)
#
# Run once, from the project root:   ./deploy/azure/provision.sh
# Requires: az CLI, logged in via `az login`, with Contributor on the subscription.
#
# Every value below can be overridden from the environment, e.g.
#   LOCATION=westeurope APP_NAME=crbs-sudan ./deploy/azure/provision.sh
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-crbs}"
LOCATION="${LOCATION:-westeurope}"
APP_NAME="${APP_NAME:-crbs-$RANDOM}"          # must be globally unique
PLAN_NAME="${PLAN_NAME:-plan-crbs}"
PLAN_SKU="${PLAN_SKU:-B1}"                    # B1 for pilot; P1v3 for production
PG_NAME="${PG_NAME:-pg-crbs-$RANDOM}"         # must be globally unique
PG_SKU="${PG_SKU:-Standard_B1ms}"
PG_TIER="${PG_TIER:-Burstable}"
PG_VERSION="${PG_VERSION:-16}"
PG_DB="${PG_DB:-crbs}"
PG_ADMIN="${PG_ADMIN:-crbsadmin}"

ORG_NAME="${ORG_NAME:-UNDP Country Office}"
DUTY_STATION="${DUTY_STATION:-Khartoum, Sudan}"
CURRENCY="${CURRENCY:-USD}"
TZ_LABEL="${TZ_LABEL:-EAT (UTC+2)}"

# Generated locally and never echoed to the terminal.
PG_PASSWORD="$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(32)))')"
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

echo "==> Resource group $RESOURCE_GROUP ($LOCATION)"
az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none

echo "==> PostgreSQL flexible server $PG_NAME (this takes several minutes)"
az postgres flexible-server create \
    -g "$RESOURCE_GROUP" -n "$PG_NAME" -l "$LOCATION" \
    --tier "$PG_TIER" --sku-name "$PG_SKU" --version "$PG_VERSION" \
    --admin-user "$PG_ADMIN" --admin-password "$PG_PASSWORD" \
    --storage-size 32 --public-access 0.0.0.0 --yes -o none
# --public-access 0.0.0.0 is the Azure shorthand for "allow other Azure services",
# NOT "allow the whole internet". It is what lets App Service reach the database.

az postgres flexible-server db create \
    -g "$RESOURCE_GROUP" -s "$PG_NAME" -d "$PG_DB" -o none

echo "==> App Service plan $PLAN_NAME ($PLAN_SKU, Linux)"
az appservice plan create \
    -g "$RESOURCE_GROUP" -n "$PLAN_NAME" -l "$LOCATION" \
    --is-linux --sku "$PLAN_SKU" -o none

echo "==> Web app $APP_NAME"
az webapp create \
    -g "$RESOURCE_GROUP" -p "$PLAN_NAME" -n "$APP_NAME" \
    --runtime "PYTHON:3.11" -o none

DATABASE_URL="postgresql+psycopg://${PG_ADMIN}:${PG_PASSWORD}@${PG_NAME}.postgres.database.azure.com:5432/${PG_DB}?sslmode=require"

echo "==> Application settings"
az webapp config appsettings set -g "$RESOURCE_GROUP" -n "$APP_NAME" -o none --settings \
    CRBS_ENV="production" \
    CRBS_SECRET_KEY="$SECRET_KEY" \
    CRBS_DATABASE_URL="$DATABASE_URL" \
    CRBS_ORG_NAME="$ORG_NAME" \
    CRBS_DUTY_STATION="$DUTY_STATION" \
    CRBS_CURRENCY="$CURRENCY" \
    CRBS_TZ_LABEL="$TZ_LABEL" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true" \
    WEBSITES_PORT="8000"

echo "==> Startup command and HTTPS-only"
az webapp config set -g "$RESOURCE_GROUP" -n "$APP_NAME" -o none \
    --startup-file "deploy/azure/startup.sh"
# Redirects any plain-http request to https. Without this the `secure` session
# cookie is dropped on the http request and sign-in appears to do nothing.
az webapp update -g "$RESOURCE_GROUP" -n "$APP_NAME" --https-only true -o none
az webapp config set -g "$RESOURCE_GROUP" -n "$APP_NAME" --min-tls-version 1.2 -o none

echo "==> Deploying application code"
az webapp up -g "$RESOURCE_GROUP" -n "$APP_NAME" -p "$PLAN_NAME" \
    --runtime "PYTHON:3.11" --os-type Linux

cat <<EOF

────────────────────────────────────────────────────────────────
Deployed:  https://${APP_NAME}.azurewebsites.net
Database:  ${PG_NAME}.postgres.database.azure.com / ${PG_DB}

The database password and CRBS_SECRET_KEY were generated here and stored only
in the web app's application settings. Read them back with:

  az webapp config appsettings list -g $RESOURCE_GROUP -n $APP_NAME \\
      --query "[?name=='CRBS_DATABASE_URL'].value" -o tsv

Next — create the first administrator (interactive, prompts for a password):

  az webapp ssh -g $RESOURCE_GROUP -n $APP_NAME
  cd /home/site/wwwroot && python createadmin.py

Then sign in and create the remaining users through Users & Roles.
Do NOT run seed.py against this database — it creates demo accounts with a
published password.
────────────────────────────────────────────────────────────────
EOF
