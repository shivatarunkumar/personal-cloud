# Personal Cloud

Set-up and run steps. What the project is, how it works, and the roadmap are in
[project.md](project.md).

<!-- autodocs:start -->
## Overview

Personal Cloud is a self-hosted file drive, like a small private OneDrive. The web app and its
database run on your own machine, and every file is stored in **your own private Google Cloud
Storage bucket**, under readable names. Each person who uses it has their own account and sees only
their own files. It is meant for a home machine and your private network. It is not a hosted service
and has no public sharing.

- Upload files and whole folders, with a progress tray and Replace / Skip / Keep both when names clash
- View photos, play videos (streamed as they play), read PDFs and text files, download on demand
- Folders, rename, move, copy, search, Recent / Photos / Videos views, and a 30-day trash
- **Nothing is fetched from the bucket when you browse.** Bytes move only when you open, play or download a file
- Runs in the background (`./cloud.sh`), restarts itself, and can be reached at a name such as `http://mycloud.com`

## Architecture

![Architecture](docs/autodocs/diagrams/architecture.png)

[Interactive diagram](docs/autodocs/diagrams/architecture.html) · [draw.io](docs/autodocs/diagrams/architecture.drawio)

- **Browser → (Caddy) → Next.js web app → FastAPI.** The web app serves the pages and forwards `/api/*`
  to the API on the same address, so the session cookies never cross origins (`frontend/next.config.ts:29`).
- **FastAPI** checks the session cookie on every request. The **drive service** keeps folders, files and
  the trash in **PostgreSQL**, scoped to the signed-in user.
- **Uploads skip the server.** The API books the file and opens a GCS resumable session; the browser
  sends 8 MB chunks straight to the bucket (`frontend/lib/upload.ts:47`).
- **Downloads and playback stream through the API** with HTTP Range, so a video seeks without
  downloading the gap (`backend/app/api/v1/files.py:165`).
- **The bucket mirrors the drive**: `{username}/{folder}/{file}`, with trashed items under `_trash/`.
  After every change, objects are copied to their new names inside Google and the old ones are deleted
  only after the database commit (`backend/app/services/drive.py:170`).

The design, data model and roadmap are in [project.md](project.md).

## Quick start

You need **Python 3.12+**, **Node 20+**, **PostgreSQL 16+**, the **gcloud CLI**, and a Google Cloud
project with a bucket. Steps 1–7 above walk through each one; the short version is:

```bash
gcloud auth application-default login
cp .env.example .env        # fill in DATABASE_URL, POSTGRES_ADMIN_URL, JWT_SECRET, GCP_PROJECT_ID, GCS_BUCKET
make check && make setup    # packages, database, bucket settings, hosts entry, web build
make check-all              # every line should say ok
./cloud.sh start            # then open WEB_BASE_URL and create an account
```

For development, run `make api` and `make web` in two terminals instead of `./cloud.sh start`.

## Configuration

Settings live in `.env` (copied from `.env.example`, never committed). The main keys:

| Key | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://cloud:change-me@localhost:5432/personal_cloud` | App database; `make db-init` creates this role and database |
| `POSTGRES_ADMIN_URL` | yes | `postgresql://postgres@localhost:5432/postgres` | A superuser, used only by `make db-init` |
| `JWT_SECRET` | yes | *(empty: the API refuses to start)* | Signs session cookies; `openssl rand -hex 32` |
| `GCP_PROJECT_ID` | yes | *(empty)* | The project that owns the bucket |
| `GCS_BUCKET` | yes | *(empty)* | The bucket every file is stored in |
| `WEB_BASE_URL` | no | `http://localhost:3002` | The address people open; also decides which origins may upload |
| `EXTRA_ORIGINS` | no | *(empty)* | More addresses (LAN, Tailscale), comma-separated; then `make setup-gcp` |
| `APP_NAME` | no | `Personal Cloud` | Name shown in the app |
| `API_HOST_PORT` / `WEB_HOST_PORT` | no | `8002` / `3002` | Ports for the API and the web app |
| `ALLOW_SIGNUP` | no | `true` | `false` turns off new accounts |
| `GCS_PREFIX` | no | *(empty)* | Optional sub-path inside the bucket |
| `GOOGLE_APPLICATION_CREDENTIALS` | no | *(empty: your gcloud login)* | Path to a service-account key, kept outside the repo |
| `DEFAULT_QUOTA_BYTES` | no | *(empty: unlimited)* | Storage limit for new accounts |
| `MAX_UPLOAD_BYTES` | no | `53687091200` (50 GiB) | Largest single file |
| `UPLOAD_CHUNK_BYTES` | no | `8388608` (8 MiB) | Upload chunk size, a multiple of 256 KiB |
| `TRASH_RETENTION_DAYS` | no | `30` | Trash older than this is deleted by the housekeeping run |
| `LOG_LEVEL` / `LOG_FORMAT` | no | `INFO` / `text` | `DEBUG` traces each request; `json` for machine-read logs |

Upload origins are worked out from `WEB_BASE_URL`, localhost and `EXTRA_ORIGINS`
(`backend/app/core/config.py:94`); there are no separate CORS keys.

## Usage

| To | Do this | You get |
|---|---|---|
| Upload files | Drag them onto a folder, or **New → Upload files** | A tray with overall progress; files appear as they finish |
| Upload a folder | Drag it onto the page, or **New → Upload folder** | The same folders in the drive and in the bucket |
| Re-upload changed files | Upload again into the same folder | A window to Replace, Skip or Keep both; same-size files default to Skip |
| Open or play a file | Click it | The viewer loads only that file; videos stream as they play |
| Undo a delete | **Trash → Restore** | The item back where it was (renamed if the name is taken) |
| Check the setup | `make check-all` | One `ok` / `FAIL` line per check, with the fix for each failure |
| See the API | Open `http://localhost:8002/docs` | Interactive API reference (Swagger) |

The other commands are listed under *Everyday commands* above. Common problems and their fixes are
under *Troubleshooting* above.
<!-- autodocs:end -->

The steps are written for macOS with Homebrew. On Linux, install the same tools with your
package manager.

---

## 1. Install the tools

```bash
brew install git python node postgresql@17
brew install --cask gcloud-cli
brew services start postgresql@17
```

Check (Python 3.12+, Node 20+, PostgreSQL 16+):

```bash
python3 --version && node --version && psql --version && gcloud --version
```

## 2. Get the code

```bash
git clone <this-repository-url> personal-cloud
cd personal-cloud
```

Run every command below from this folder.

## 3. Prepare Google Cloud

Replace `<project-id>` and `<bucket-name>` with your own.

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project <project-id>
gcloud auth application-default set-quota-project <project-id>
gcloud services enable storage.googleapis.com
```

Create the bucket. Skip this if you already have one. Bucket names are globally unique.

```bash
gcloud storage buckets create gs://<bucket-name> \
  --location=<region, e.g. europe-west2> \
  --uniform-bucket-level-access \
  --public-access-prevention
```

Your Google account needs **Storage Admin** on the bucket for setup, and **Storage Object
Admin** day to day.

## 4. Create your settings file

```bash
cp .env.example .env
openssl rand -hex 32        # copy the output for JWT_SECRET
openssl rand -hex 16        # copy the output for the database password
```

Open `.env` and set:

| Key | Value |
|---|---|
| `APP_NAME` | what the UI calls your cloud, e.g. `My Cloud` |
| `WEB_BASE_URL` | the address you'll open, e.g. `http://mycloud.com:3002` (any name you like; keep `:3002`). Or leave `http://localhost:3002` |
| `DATABASE_URL` | `postgresql+asyncpg://cloud:<database-password>@localhost:5432/personal_cloud` |
| `POSTGRES_ADMIN_URL` | a Postgres superuser. Homebrew: `postgresql://<your-mac-username>@localhost:5432/postgres` |
| `JWT_SECRET` | the 64-character value from `openssl rand -hex 32` |
| `GCP_PROJECT_ID` | `<project-id>` |
| `GCS_BUCKET` | `<bucket-name>` |

Leave everything else as it is. Never commit `.env` (git ignores it).

## 5. Set everything up

```bash
make check          # every tool present?
make setup          # everything below, in one go
```

`make setup` runs these steps. Each one is safe to re-run on its own:

| Step | Does |
|---|---|
| `make install` | Python packages into `.venv`, npm packages for the web app |
| `make db-init` | the database role, database and tables |
| `make setup-gcp` | makes the bucket private and lets browsers on your address upload to it |
| `make setup-hosts` | adds `127.0.0.1 <your hostname>` to `/etc/hosts` (asks for your Mac password once; nothing to do for localhost) |
| `make build-web` | builds the web app |

Then confirm everything is `ok`:

```bash
make check-all
```

## 6. Start it

```bash
./cloud.sh start          # runs the app in the background
./cloud.sh status
./cloud.sh cron-install   # starts it at every boot and restarts it if it stops
```

> **macOS and cron:** if the project folder is inside Documents, Desktop or Downloads, macOS
> blocks cron from reading it. Open **System Settings → Privacy & Security → Full Disk
> Access**, press **+**, press ⌘⇧G, type `/usr/sbin/cron` and add it. Or keep the project
> somewhere else, e.g. `~/personal-cloud`.

For development instead, use two terminals: `make api` and `make web` (they reload on
change).

## 7. Create your account

Open your address (e.g. `http://mycloud.com:3002`), choose **Create an account**, and
you're in. Everyone who uses this cloud creates their own account the same way, and each
account sees only its own files.

To stop new sign-ups once everyone has an account, set `ALLOW_SIGNUP=false` in `.env`, then
run `./cloud.sh restart`.

## 8. Changing the address later

Edit `WEB_BASE_URL` in `.env`, then:

```bash
make setup-hosts REMOVE=1   # optional, BEFORE editing .env: removes the old name
make setup-hosts            # adds the new name
make setup-gcp              # lets the bucket accept uploads from it
make build-web
./cloud.sh restart
```

Sign in again, because cookies don't carry over between addresses.

## 8b. Optional: drop the `:3002`, even with other apps on this Mac

Only one program can use port 80 (the port a URL without `:number` uses). To give several
local apps their own names, let [Caddy](https://caddyserver.com) own port 80 and forward each
name to its app's port:

```bash
brew install caddy
```

Put this in `$(brew --prefix)/etc/Caddyfile` (one block per app; each name also needs its
`127.0.0.1 <name>` line in `/etc/hosts`):

```
{
	auto_https off
}

http://mycloud.com {
	reverse_proxy localhost:3002
}
```

```bash
brew services start caddy     # runs now and at every login; macOS needs no sudo for port 80
```

Then set `WEB_BASE_URL=http://mycloud.com` (no port) in `.env`, run `make setup-gcp`, and
restart the app. Any app that was on port 80 must move to its own port first.

## 9. Optional: use it from your phone or another computer

Add the other address to `EXTRA_ORIGINS` in `.env` (comma-separated), then run
`make setup-gcp` and `./cloud.sh restart`.

- **Same Wi-Fi:** `EXTRA_ORIGINS=http://<this-machine's-LAN-IP>:3002`, and open that address
  on the other device.
- **From anywhere:** install [Tailscale](https://tailscale.com) on this machine and your
  devices, then use `EXTRA_ORIGINS=http://<machine-name>:3002`.
- Don't expose the app to the internet with router port-forwarding.

---

## Everyday commands

| Command | Does |
|---|---|
| `make` | list every command |
| `./cloud.sh status` / `logs` / `restart` / `stop` | manage the always-on app |
| `make check-all` | verify the tools, the database and Google Cloud |
| `make reset-password LOGIN=…` | give someone who forgot their password a temporary one |
| `make maintenance` | empty old trash, forget abandoned uploads (cron does this) |
| `make reindex` | add files found in the bucket to the drive, e.g. ones uploaded in the Cloud console into `<username>/…` (`ARGS=--dry-run` to preview) |
| `make relayout` | check every object is at its readable path and fix it (`ARGS=--prune-missing` forgets files deleted from the bucket by hand) |
| `make db-status` / `make db-shell` | migrations / psql |
| `make test` / `make lint` | tests / lint |

## Updating to a new version

```bash
git pull
make install
make db-init
make build-web
./cloud.sh restart
```

## Moving to a new machine

Your files stay in the bucket. On the new machine, follow steps 1–7 with the same
`GCP_PROJECT_ID` and `GCS_BUCKET`, sign up with the **same usernames** as before, then:

```bash
make reindex
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make check-gcp` → credentials FAIL | `gcloud auth application-default login` |
| Uploads fail with a CORS error in the browser | the page's address isn't `WEB_BASE_URL` or in `EXTRA_ORIGINS`: add it, then `make setup-gcp` |
| The name doesn't open the app | `make setup-hosts`, and check the port is in the address (`:3002`) |
| `./cloud.sh start` says NOT STARTED | the next line says why and how to fix it; see also `logs/cloud.log` |
| API `/api/v1/health` returns 503 | `make check-db` and `make check-gcp` say which dependency is down and why |
| Nothing starts after a reboot | `./cloud.sh status`, then `logs/cloud.log`; see the cron note in step 6 |

## Uninstall

```bash
./cloud.sh cron-remove && ./cloud.sh stop
make setup-hosts REMOVE=1
make db-reset             # or drop the database yourself
```

Your files stay in the bucket until you delete them there.

## What's in the bucket

Files are stored under readable names, one folder per account:

```
<bucket>/<username>/<folder>/<sub-folder>/<file name>
<bucket>/_trash/<username>/<when deleted>/<original path>
```

Manage files through the app. If you delete objects in the Cloud console, run
`make relayout ARGS=--prune-missing` so the app forgets them too; if you add some under
`<username>/`, run `make reindex` to bring them in.
