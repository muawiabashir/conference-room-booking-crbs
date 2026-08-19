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

## 4. (Optional) Microsoft sign-in for @undp.org accounts

Adds a "Sign in with Microsoft" option alongside the existing password login,
so UNDP staff authenticate with their normal Microsoft account instead of a
system-specific password. Purely additive — the app works exactly as before
if you skip this section, and the button only appears once all three env
vars below are set.

**How accounts work with SSO on:** signing in with Microsoft does not create
an account. The user must already exist — created ahead of time via
**Users & Roles**, same as today. SSO just replaces "prove who you are with a
password" with "prove who you are via UNDP's Microsoft login." First
successful sign-in binds the account to that Microsoft identity (its `oid`
claim); the identity, not just the email, is what's checked on every login
after that, so a mailbox reassigned to someone else in the tenant later can't
inherit the original owner's account.

1. **Register the app** in UNDP's Entra ID tenant (portal.azure.com → Entra ID
   → App registrations → New registration). Whoever administers that tenant
   needs to do this, or grant you Application Administrator to do it yourself:
   - Name: anything recognisable, e.g. "Conference Room Booking (Render)"
   - Supported account types: **Accounts in this organizational directory
     only** (single tenant) — this is what restricts sign-in to `@undp.org`,
     there's no separate domain check in the app
   - Redirect URI: platform **Web**, value
     `https://<service-name>.onrender.com/auth/microsoft/callback`
2. **Create a client secret**: the app registration → Certificates & secrets
   → New client secret. Copy the value immediately — Azure only shows it once.
3. **Collect three values**: Directory (tenant) ID and Application (client) ID
   from the app registration's Overview page, plus the client secret from
   step 2.
4. **Set them on Render** (dashboard → service → Environment, or ask me to set
   them via the API the same way the rest of this deploy was done):
   - `CRBS_MS_TENANT_ID`
   - `CRBS_MS_CLIENT_ID`
   - `CRBS_MS_CLIENT_SECRET`
5. Redeploy. The button appears on the sign-in page automatically once all
   three are present — no code change needed.

If the redirect URI is ever wrong, Microsoft's own consent screen says so
explicitly (`AADSTS50011`) — fix it on the app registration, not in this app.

## 5. (Optional) Email new accounts their temporary password

Without this, creating or resetting a user under **Users & Roles** shows the
temporary password on-screen to the admin, who relays it to the person
manually — that still works with nothing configured. With SMTP set, the
system emails it to the new user directly instead, and only falls back to
the on-screen password if the send fails for any reason (bad credentials,
network issue) — account creation itself never fails because of a mail
problem.

Any SMTP account works — a UNDP Office 365 mailbox, a transactional provider
(SendGrid, Mailgun, Postmark, SES), or a personal account for testing. Using
an Office 365 / Exchange Online mailbox from the same UNDP tenant as the SSO
setup:

- `CRBS_SMTP_HOST` = `smtp.office365.com`
- `CRBS_SMTP_PORT` = `587`
- `CRBS_SMTP_USERNAME` = the mailbox's address, e.g. `crbs-noreply@undp.org`
- `CRBS_SMTP_PASSWORD` = that mailbox's password (an app password if the
  account has MFA enforced — Exchange Online blocks plain password auth for
  MFA accounts)
- `CRBS_SMTP_FROM` = `Conference Room Booking <crbs-noreply@undp.org>`

All four must be set for emailing to activate. Redeploy after setting them.

## 6. (Optional) Sync approved bookings to a shared Outlook room calendar

Every booking that gets approved creates a matching event on a shared Outlook
mailbox's calendar (e.g. a room resource mailbox like
`undp-sd.conference.room@undp.org`), so anyone checking that calendar in
Outlook — including people who never touch this system — sees it as busy.
Cancelling an approved booking removes the event again. Rejected/pending
bookings never touch the calendar at all.

This reuses the **same Entra ID app registration** as SSO (step 4) — no new
app to register — but needs one more thing added to it, and it's a bigger
ask than SSO's sign-in scopes: an **Application permission**, which only a
tenant admin can consent to (unlike the delegated `openid`/`email`/`profile`
scopes SSO uses, which any user implicitly consents to just by signing in).

1. Same app registration → **API permissions** → **Add a permission** →
   **Microsoft Graph** → **Application permissions** (not Delegated) →
   search for and check **Calendars.ReadWrite** → **Add permissions**.
2. Still on that page: **Grant admin consent for [tenant]** → confirm. This
   button only works for a Global Administrator or Privileged Role
   Administrator — if it's greyed out, that's who needs to click it, or who
   needs to grant it via PowerShell/Graph Explorer instead.
3. **Recommended, not required**: an app holding `Calendars.ReadWrite` as an
   Application permission can, by default, read and write *every* mailbox's
   calendar in the tenant — not just the room's. Whoever administers Exchange
   Online can scope it down to just the room mailbox with an Application
   Access Policy:
   ```powershell
   New-ApplicationAccessPolicy -AppId <client-id> `
     -PolicyScopeGroupId undp-sd.conference.room@undp.org `
     -AccessRight RestrictAccess -Description "CRBS: room calendar only"
   ```
   Skipping this doesn't break anything — it's a defense-in-depth step, not
   a prerequisite.
4. Set on Render: `CRBS_ROOM_CALENDAR_EMAIL` = `undp-sd.conference.room@undp.org`.
   (`CRBS_MS_TENANT_ID`/`CLIENT_ID`/`CLIENT_SECRET` are already set from SSO —
   nothing new needed there.)
5. Redeploy.

A failed sync never blocks the approval or cancellation itself — the admin
sees "Calendar sync failed — check server logs" appended to the usual
success message, and the full error lands in Render's logs.

## What the settings do

| Setting | Why |
|---|---|
| `CRBS_ENV=production` | Requires `CRBS_SECRET_KEY`, marks the session cookie `secure`, hides the demo-credentials box on the sign-in page |
| `CRBS_SECRET_KEY` | Signs session cookies. Rotating it signs everyone out. Render generates and stores this for you |
| `CRBS_DATABASE_URL` | `postgresql+psycopg://…?sslmode=require` via Supabase's Session pooler — see step 1 for why the pooler, not the direct connection |
| `--forwarded-allow-ips '*'` | Tells uvicorn to trust `X-Forwarded-For` from Render's proxy, so the audit trail records the real client IP rather than Render's edge |
| `CRBS_MS_TENANT_ID` / `CRBS_MS_CLIENT_ID` / `CRBS_MS_CLIENT_SECRET` | Optional — see step 4. All three must be set for the Microsoft sign-in button to appear |
| `CRBS_SMTP_HOST` / `CRBS_SMTP_PORT` / `CRBS_SMTP_USERNAME` / `CRBS_SMTP_PASSWORD` / `CRBS_SMTP_FROM` | Optional — see step 5. All must be set for new-account emails to send; falls back to the on-screen password otherwise |
| `CRBS_ROOM_CALENDAR_EMAIL` | Optional — see step 6. The shared mailbox approved bookings sync to; reuses the SSO app registration's tenant/client credentials |
| `CRBS_TZ_NAME` | IANA time zone (default `Africa/Khartoum`) used only for the Outlook calendar event's time zone field — separate from `CRBS_TZ_LABEL`, which is just display text |

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
