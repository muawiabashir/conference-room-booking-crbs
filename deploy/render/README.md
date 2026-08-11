# Deploying to Render + Supabase

Target architecture:

```
Browser ──https──> Render (Linux, Python)
                     └── gunicorn + uvicorn workers
                            └── Supabase Postgres (via Supavisor pooler)
```

Render terminates TLS and issues the certificate for `*.onrender.com`, so
there's no nginx and no certbot to manage — same idea as the Azure path in
`../azure/`, different host. Use one deployment path or the other, not both
against the same database.

## 1. Create the Supabase project

1. [database.new](https://database.new) → create a project. Pick a region
   close to your users (e.g. `eu-central-1` for UNDP HQ / Europe traffic).
   Save the database password you set here — Supabase only shows it once.
2. In the dashboard: **Connect** (top bar) → **Connection string**.
   Copy the **Session pooler** URI, not the direct connection.

   Why the pooler and not the direct connection: Supabase's direct connection
   is IPv6-only unless you pay for the IPv4 add-on, and Render's network is
   IPv4. The Session pooler (Supavisor) is IPv4-reachable and — unlike the
   Transaction pooler on port 6543 — behaves like a normal long-lived
   connection, which is what SQLAlchemy's connection pool expects. It looks
   like:

   ```
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

3. Adapt it for this app — SQLAlchemy needs the `+psycopg` driver marker and
   an explicit `sslmode`:

   ```
   postgresql+psycopg://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   ```

   This is the value for `CRBS_DATABASE_URL` in step 3.

## 2. Create the Render web service

1. Push this repo to GitHub (Render deploys from a git remote, not a local
   upload).
2. In the Render dashboard: **New** → **Blueprint** → pick the repo. Render
   reads [`render.yaml`](../../render.yaml) at the repo root and creates the
   web service from it — build command, start command and most environment
   variables are already defined there.
3. `CRBS_DATABASE_URL` is marked `sync: false` in the blueprint (a secret,
   deliberately not committed to git). Render will prompt for it during
   blueprint creation — paste the connection string from step 1. `CRBS_SECRET_KEY`
   is generated automatically by Render (`generateValue: true`); you don't need
   to set it.
4. Deploy. First boot creates the schema automatically (`app/main.py`'s
   startup hook calls `Base.metadata.create_all`) — no separate migration
   step.

Without a blueprint: **New** → **Web Service** → connect the repo → set
**Build Command** `pip install -r requirements.txt` and **Start Command** to
the `gunicorn …` line in `render.yaml`, then add the env vars from the
blueprint's `envVars` list by hand.

## 3. Create the first administrator

`seed.py` is for demos only — it creates accounts whose password is published
in this repository. Never run it against Supabase. Instead, use Render's
**Shell** tab on the service (or `render ssh <service>` with the Render CLI):

```bash
python createadmin.py
```

Then sign in at `https://<service-name>.onrender.com` and create the
remaining users through **Users & Roles**, which issues a temporary password
and forces a change at first sign-in.

## What the settings do

| Setting | Why |
|---|---|
| `CRBS_ENV=production` | Requires `CRBS_SECRET_KEY`, marks the session cookie `secure`, hides the demo-credentials box on the sign-in page |
| `CRBS_SECRET_KEY` | Signs session cookies. Rotating it signs everyone out. Render generates and stores this for you |
| `CRBS_DATABASE_URL` | `postgresql+psycopg://…?sslmode=require` via Supabase's Session pooler — see step 1 for why the pooler, not the direct connection |
| `--forwarded-allow-ips '*'` | Tells uvicorn to trust `X-Forwarded-For` from Render's proxy, so the audit trail records the real client IP rather than Render's edge |

## Operational notes

- **Workers vs. pool limits.** `render.yaml` starts 2 gunicorn workers, each
  opening its own SQLAlchemy pool (default 5 + overflow). Supabase's free-tier
  pooler has a limited connection budget — raise worker count only alongside
  the Supabase plan's pool size, not independently.
- **Cold starts.** Render's free/starter plans spin the service down after
  inactivity; the next request pays a cold-start cost. Fine for a pilot,
  not for something colleagues rely on being instantly responsive — upgrade
  the plan if that matters.
- **Backups.** Supabase takes daily backups on paid plans (none on the free
  tier). The audit trail lives in the same database, so it's covered by
  whatever backup policy you set on the project.
- **Logs.** Render dashboard → service → **Logs** tab, or `render logs -s <service> -f` with the CLI.

## Custom domain

Render dashboard → service → **Settings** → **Custom Domains** → add
`booking.undp.org` and follow the DNS instructions shown there. Render issues
and renews a free managed certificate for it, same as App Service does for
Azure.
