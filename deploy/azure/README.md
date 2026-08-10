# Deploying to Microsoft Azure

Target architecture:

```
Browser ──https──> Azure App Service (Linux, Python 3.11)
                     └── gunicorn + uvicorn workers
                            └── Azure Database for PostgreSQL Flexible Server
```

App Service terminates TLS and issues the certificate for
`*.azurewebsites.net`, so there is no nginx and no certbot to manage. The
systemd + nginx files in `../vm/` are for a plain virtual machine instead —
use one path or the other, not both.

## One-time provisioning

```bash
az login
az account set --subscription "<subscription name or id>"

./deploy/azure/provision.sh
```

Override any default from the environment:

```bash
RESOURCE_GROUP=rg-crbs-sudan \
LOCATION=westeurope \
APP_NAME=crbs-sudan \
ORG_NAME="UNDP Sudan Country Office" \
DUTY_STATION="Khartoum, Sudan" \
./deploy/azure/provision.sh
```

The script creates the resource group, a PostgreSQL flexible server, an App
Service plan and the web app; generates the database password and
`CRBS_SECRET_KEY` locally; stores both in the web app's application settings;
and deploys the code. It never prints either secret.

`APP_NAME` and the PostgreSQL server name must be globally unique across
Azure — the defaults append a random number for that reason.

## Create the first administrator

`seed.py` is for demos only: it creates accounts whose password is published in
this repository. Never run it against Azure. Instead:

```bash
az webapp ssh -g rg-crbs -n <app-name>
cd /home/site/wwwroot
python createadmin.py
```

Then sign in at `https://<app-name>.azurewebsites.net` and create the remaining
users through **Users & Roles**, which issues a temporary password and forces a
change at first sign-in.

## Redeploying after a code change

```bash
az webapp up -g rg-crbs -n <app-name> -p plan-crbs --runtime "PYTHON:3.11" --os-type Linux
```

`az webapp up` respects `.gitignore`, so `.venv/`, `crbs.db` and `.secret_key`
are excluded from the upload.

## What the settings do

| Setting | Why |
|---|---|
| `CRBS_ENV=production` | Requires `CRBS_SECRET_KEY`, marks the session cookie `secure`, hides the demo-credentials box on the sign-in page |
| `CRBS_SECRET_KEY` | Signs session cookies. Rotating it signs everyone out |
| `CRBS_DATABASE_URL` | `postgresql+psycopg://…?sslmode=require` — Azure PostgreSQL refuses non-TLS connections |
| `SCM_DO_BUILD_DURING_DEPLOYMENT=true` | Makes Oryx install `requirements.txt` on the server |
| `--https-only true` | Redirects http to https. Without it the `secure` cookie is dropped on a plain-http request and sign-in silently fails |

## Operational notes

- **Scaling.** The startup command runs 4 workers. On the B1 plan that is
  roughly the ceiling; move to P1v3 before scaling out. Keep total workers
  across instances below the PostgreSQL `max_connections`.
- **Schema.** Tables are created on first boot. Workers starting simultaneously
  can race on `CREATE TABLE`; `app/main.py` catches that and retries.
- **Backups.** Flexible Server takes automatic backups with 7-day retention by
  default. Raise it with `az postgres flexible-server update --backup-retention`.
  The audit trail is append-only and lives in the same database, so it is
  covered by the same backup.
- **Client IP in the audit trail.** `--forwarded-allow-ips '*'` tells uvicorn to
  trust `X-Forwarded-For`. That is required behind App Service, but it means the
  recorded IP is only as trustworthy as the front end — treat it as indicative,
  not as forensic evidence.
- **Logs.** `az webapp log tail -g rg-crbs -n <app-name>`.

## Custom domain

```bash
az webapp config hostname add -g rg-crbs --webapp-name <app-name> \
    --hostname booking.undp.org
az webapp config ssl create -g rg-crbs --name <app-name> --hostname booking.undp.org
az webapp config ssl bind -g rg-crbs --name <app-name> \
    --certificate-thumbprint <thumbprint> --ssl-type SNI
```

App Service issues and renews a free managed certificate for custom domains.
