# Personal Cloud — project plan

This file is the single source of truth for what Personal Cloud is, how it is built and
in what order. [README.md](README.md) only lists the steps to set it up and run it.

---

## 1. What it is

Personal Cloud is a private, self-hosted file drive — a small OneDrive/Google Drive you run
yourself. The web app and its database run on your own machine; **every file's bytes live in
your own Google Cloud Storage (GCS) bucket**. You sign in from a browser, and you can:

- create folders and sub-folders, and move around them like a file explorer
- upload files and whole folders (drag and drop, or pick), of any type and size
- view photos, play videos, and read PDFs and text files in the browser
- download a file only when you ask for it
- rename, move, copy and delete, with a trash to undo mistakes
- sign in from any device and see the same files, because the files are in GCS and the
  file index is keyed by your account, not by the device

Anyone can clone this repository and run their own instance against their own GCP project
and bucket. Nothing in the repository is tied to a particular person, project, bucket or
domain: all of that lives in `.env`, which is never committed.

### Goals

| # | Goal |
|---|---|
| G1 | All file bytes are stored in one private GCS bucket. The local disk holds only the database (metadata) and the app |
| G2 | **Nothing is downloaded from GCS when the UI opens.** Browsing shows names, sizes, dates and type icons from the database. Bytes move only when you open a photo, press play, or press Download, and only for that one file |
| G3 | Multi-user with proper login: each user sees only their own files |
| G4 | Large uploads (multi-GB videos) work reliably: chunked, resumable, and straight from the browser to GCS |
| G5 | Runs all the time on a local machine: starts at boot, restarts itself if it stops, reachable at a friendly name such as `http://mycloud.com:3002` |
| G6 | A new person can set up their own instance by following README.md alone |

### Non-goals (for now)

- Desktop sync clients or a mounted drive (no Finder/Explorer integration)
- Real-time collaborative editing of documents
- Public internet hosting with TLS certificates. It runs on your machine and your network,
  and remote access goes through a private network (see §11)
- End-to-end encryption (GCS encrypts at rest; client-side encryption is a later option)

---

## 2. Principles

1. **The bucket is private.** Uniform bucket-level access and public access prevention are
   on. No object is ever public. The browser only ever gets a short-lived upload session URL
   for one object, or bytes streamed through the API after an ownership check.
2. **Metadata in Postgres, bytes in GCS.** Listing, searching, sorting and folder
   navigation are pure database queries. They never touch GCS.
3. **The bucket reads like the drive.** A file is stored at
   `{username}/{folder path}/{file name}`, so the bucket in the Cloud console looks exactly
   like My files. Renaming or moving means copying the object to its new name inside GCS
   (server side, so no bytes pass through your machine) and deleting the old one, only
   after the database has saved the change.
4. **The bucket describes itself.** An object's path says whose it is and where it goes,
   so `make reindex` can rebuild the file index from the bucket if the database is ever
   lost. It also picks up files added straight to the bucket in the Cloud console.
5. **Upload bytes skip the API.** The browser PUTs chunks straight to a GCS resumable
   session, so there are no request-size limits and no memory pressure on the server, and
   a dropped connection resumes where it stopped.
6. **Downloads and streaming go through the API**, which checks ownership and supports HTTP
   `Range`, so video seeking works and nothing is fetched ahead of time. Signed URLs are a
   later option (§14).
7. **Idempotent, checkable setup.** Every setup command can be re-run safely. `make check-*`
   commands say what is wrong and print the command that fixes it.
8. **Logs explain why**, not just what: one line per request with a request id at `INFO`,
   and a full trace (SQL, GCS calls, timings) at `DEBUG`.

---

## 3. Architecture

```
                         your machine
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  Browser ──▶ Next.js web app (:3002) ──/api/*──▶ FastAPI (:8002) │
│     │          React UI, same origin              │        │     │
│     │                                             ▼        │     │
│     │                                    PostgreSQL (:5432)│     │
│     │                                    users, folders,   │     │
│     │                                    files, sessions   │     │
└─────┼──────────────────────────────────────────────────────┼─────┘
      │ upload: 8 MB chunks, PUT to a resumable session      │ create sessions, stat,
      ▼                                                      ▼ stream ranges, delete
┌──────────────────────────────────────────────────────────────────┐
│  Google Cloud Storage — one private bucket                       │
│    {username}/{folders}/{file}  every file's bytes, readable     │
└──────────────────────────────────────────────────────────────────┘
```

- **Web (Next.js + React + Tailwind)** serves the UI and proxies `/api/*` to FastAPI, so the
  browser sees one origin: auth cookies just work, and CORS is not needed for the API.
- **API (FastAPI)** handles auth, the folder tree and file index, upload sessions,
  ownership checks, and streaming. It talks to GCS with Application Default Credentials
  (your `gcloud` login), or a service-account key if you prefer.
- **PostgreSQL** holds all metadata. It is small: roughly 1 KB per file.
- **GCS** holds the bytes. The browser talks to GCS directly **only to upload**, and only
  through a session URL the API created for one object.

---

## 4. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Web | Next.js 16 (App Router), React 19, TypeScript, Tailwind v4 | React UI with server-side rendering for the first paint, and a built-in `/api` proxy to keep everything same-origin |
| API | Python 3.12+, FastAPI, Uvicorn, Pydantic Settings | Async, typed, and auto-documented at `/docs` |
| DB access | SQLAlchemy 2 (async) + asyncpg | Typed models and async queries |
| Database | PostgreSQL 16+ (17 recommended), `pg_trgm` extension | Fuzzy file-name search |
| Migrations | Plain versioned SQL files `V001__*.sql` plus a small Python runner | Readable, reviewable, and no ORM magic |
| Storage | `google-cloud-storage` | Resumable sessions, range reads, custom metadata |
| Auth | argon2id (`argon2-cffi`), PyJWT, httpOnly cookies | Current best practice (§8) |
| Tooling | GNU Make, `ruff`, `pytest`, `tsc --noEmit` | One command per task |
| Always-on | `cloud.sh` + cron | Starts at boot, watchdog every 10 min, log rotation |

---

## 5. Repository layout

```
personal-cloud/
├── README.md                 setup and run steps only
├── project.md                this plan
├── Makefile                  every developer command (`make` lists them)
├── cloud.sh                  start / stop / status / logs / cron-install (always-on mode)
├── .env.example              every setting, documented. Copied to .env (never committed)
├── ruff.toml
├── backend/
│   ├── requirements.txt, requirements-dev.txt, pyproject.toml
│   ├── app/
│   │   ├── main.py           app factory, middleware, routers
│   │   ├── core/             config.py, db.py, security.py, logging.py, middleware.py
│   │   ├── models/           user.py, drive.py (Folder, File, FileEvent), base.py
│   │   ├── schemas/          auth.py, drive.py
│   │   ├── adapters/         storage.py (GCS: sessions, stat, range download, metadata, delete)
│   │   ├── services/         auth.py, drive.py (folders, move/copy, trash), uploads.py, kinds.py, errors.py
│   │   └── api/
│   │       ├── deps.py       current_user, cookies
│   │       └── v1/           auth.py, account.py, drive.py, files.py (uploads + bytes), health.py
│   ├── scripts/              reset_password.py, reindex.py, maintenance.py
│   └── tests/
├── database/
│   ├── postgres/
│   │   ├── init_db.sql       role, database, privileges, extensions (as superuser)
│   │   └── migrations/       V001__helpers.sql, V002__users_auth.sql, V003__drive.sql
│   └── scripts/              setup_db.py, migrate.py, check_db.py, _db.py
├── infra/
│   └── scripts/              setup_gcp.py (bucket checks, CORS, hardening), check_gcp.py
├── scripts/
│   ├── localenv.sh           loads .env for host commands
│   └── setup_hosts.sh        adds the friendly name to /etc/hosts (idempotent, asks for sudo)
├── frontend/
│   ├── package.json, next.config.ts, tsconfig.json, postcss.config.mjs
│   ├── app/                  routes (§10)
│   ├── components/           UI pieces (§10)
│   └── lib/                  api.ts, upload.ts, format.ts, theme.ts, types.ts
└── logs/                     runtime logs (git-ignored)
```

---

## 6. Data model (PostgreSQL)

All ids are `uuid` (`gen_random_uuid()`), and all times are `timestamptz`. `updated_at` is kept
current by a shared `set_updated_at()` trigger (V001).

### 6.1 `users`

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| email | text unique | lowercase, validated |
| username | text unique | sign-in name, `^[a-z0-9][a-z0-9_.-]{1,49}$`. Sign in with email or username |
| display_name | text | shown in the top bar |
| password_hash | text | argon2id, never plaintext |
| password_changed_at | timestamptz | invalidates older sessions |
| quota_bytes | bigint null | from `DEFAULT_QUOTA_BYTES` at sign-up; null = unlimited. Checked when an upload starts |
| used_bytes | bigint | kept in step with `files`. Recomputable with `make reindex --usage-only` |
| failed_login_count, locked_until | int, timestamptz | lockout after 5 failures for 15 min |
| must_change_password | boolean | set by `make reset-password`; the next sign-in asks for a new password |
| last_login_at, created_at, updated_at, deleted_at | timestamptz | soft delete |

### 6.2 `refresh_tokens`

These are hashed opaque tokens, rotated on every use. Reusing a rotated token revokes the
whole chain, so a stolen token is detected.

`id, user_id → users, token_hash unique, expires_at, last_used_at, revoked_at,
revoked_reason (rotated|logout|reuse_detected|password_reset), replaced_by,
user_agent, ip, created_at`

This table also powers **Settings → Sessions**, which lists signed-in devices and signs one out.

### 6.3 Forgotten passwords

There is no mail server and no admin role, so there is no reset table. Whoever runs the
machine sets a temporary password with `make reset-password LOGIN=<email or username>`
(`must_change_password = true`, and every session of that user is signed out). The login
page's "Forgot password?" link says exactly that.

### 6.4 `folders`

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | uuid → users | owner |
| parent_id | uuid → folders null | null = the user's root |
| name | text | 1–255 chars, no `/`. Unique among siblings (`UNIQUE (user_id, parent_id, lower(name))` with `NULLS NOT DISTINCT`) |
| path | text | materialised `a/b/c`, used for breadcrumbs, search results and the zip/reindex metadata. Rewritten for a subtree on rename/move, in one transaction |
| trashed_at | timestamptz null | set on the folder that was deleted. Its contents are hidden through it |
| created_at, updated_at | | |

Indexes: `(user_id, parent_id)`, `(user_id, path text_pattern_ops)` for subtree queries.

### 6.5 `files`

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | also the object id in GCS |
| user_id | uuid → users | owner |
| folder_id | uuid → folders null | null = root |
| name | text | display name, 1–255 chars. Unique among live siblings, files and folders together (case-insensitive) |
| gcs_key | text unique | `{username}/{folder path}/{name}`, or its place under `_trash/`. Follows the file when it's renamed, moved or trashed |
| content_type | text | from the browser, corrected by extension when it is generic |
| kind | text | `image` \| `video` \| `audio` \| `pdf` \| `text` \| `archive` \| `document` \| `other`. Picks the icon and the viewer |
| size_bytes | bigint | confirmed from GCS when the upload completes |
| status | text | `uploading` \| `ready`. Only ready files are listed |
| upload_session_url | text null | the resumable session, so a reloaded page can resume. Cleared when ready |
| upload_expires_at | timestamptz null | sessions live about 1 week. Stale `uploading` rows are cleaned up |
| trashed_at, trash_root | timestamptz null, boolean | in the trash. `trash_root` marks what the person deleted (what Trash lists) |
| created_at, updated_at, last_opened_at | | |

Indexes: `(user_id, folder_id) WHERE trashed_at IS NULL AND status = 'ready'`,
`(user_id, updated_at DESC)` for Recent, a trigram index on `name` for search,
and `(user_id, kind)` for the Photos and Videos views.

### 6.6 `file_events` (audit)

Append-only: `id, user_id, file_id null, folder_id null, action (upload|rename|move|copy|
trash|restore|delete|download|view), detail jsonb, ip, user_agent, created_at`.
It powers an **Activity** panel later, and it answers "when did this disappear?".

### 6.7 Later tables

- `share_links (id, owner_id, file_id|folder_id, token_hash, expires_at, password_hash, max_downloads, …)`
- `thumbnails (file_id, size, gcs_key, …)` — only if thumbnails are switched on (§14)

---

## 7. GCS layout and bucket settings

```
gs://<GCS_BUCKET>/
  alex/                                   one folder per account (its username)
  alex/Trips/                             a folder: an empty "marker" object, so empty folders show too
  alex/Trips/Goa 2026/beach.jpg           a file, under its real name
  _trash/alex/2026-09-27T15-54-45-314299/Trips/old.jpg   in the trash: when it was deleted + where it was
  _probe/…                                written and deleted by `make check-gcp`
```

`GCS_PREFIX` (empty by default) puts everything under a sub-path, so an existing bucket can
be shared with other things. Usernames always start with a letter or digit, so `_trash/`
can never collide with someone's folder. Folders also have a `gcs_key`: their marker object.

**How the bucket follows the drive.** Every change (new folder, upload, rename, move, copy,
trash, restore) updates the database first. Then `sync_bucket` compares where each object
*is* with where it *should be*, copies whatever differs (16 at a time, server side), writes
missing folder markers, and records the new names. After the commit, the old objects are
deleted, but only ones nothing points at any more, under a per-account lock, so a quick
"rename back" can never delete the new copy. If a copy fails, the copies made so far are
removed and the change is rolled back. `make relayout` runs the same sync for every account
(and upgrades a bucket from the old id-based layout).

Object names can be at most 1024 bytes; a change that would make a longer path is refused.
`Content-Type` is set on upload, and `Content-Disposition` per response by the API. Each
object also carries `pc-file-id` metadata, so a reindex keeps the same ids.

**Bucket settings.** `make setup-gcp` checks and applies these, and it is idempotent:

| Setting | Value | Why |
|---|---|---|
| Uniform bucket-level access | on | no per-object ACLs to get wrong |
| Public access prevention | enforced | the bucket can never be made public by accident |
| CORS | origins = `WEB_BASE_URL`, `http://localhost:<WEB_HOST_PORT>`, `http://127.0.0.1:<WEB_HOST_PORT>` and `EXTRA_ORIGINS`; methods `PUT, POST, GET, HEAD, OPTIONS`; response headers `Content-Type, Content-Range, Range, x-goog-resumable, Location` | the browser PUTs upload chunks straight to GCS |
| Versioning | off (optional on) | trash covers undo. Turning it on adds cost |
| Storage class | Standard (optional Autoclass) | Autoclass moves rarely opened files to cheaper classes automatically |
| Soft delete | GCS default (7 days) | a second safety net under the app's trash |

**IAM.** The identity the API runs as (your user through ADC, or a service account) needs
`roles/storage.objectAdmin` on the bucket, plus `storage.buckets.get` and
`storage.buckets.update` for `make setup-gcp` (e.g. `roles/storage.admin` just for setup).

**Cost note.** GCS bills for storage and for **egress** (bytes downloaded). The
no-download-until-asked rule (G2) is also what keeps the bill small: opening the UI or
browsing costs nothing in GCS.

---

## 8. Authentication and accounts

- **Passwords:** argon2id, minimum 10 characters. They are rehashed on login if the parameters
  change.
- **Session:** a JWT access token (15 min) plus an opaque refresh token (30 days), both
  in **httpOnly, SameSite=Lax** cookies. `Secure` is set whenever the app is served over
  HTTPS. JavaScript never sees a token.
- **Refresh:** the web client calls `POST /auth/refresh` on a 401 and retries once. Rotation
  and reuse detection are covered in §6.2.
- **Lockout:** 5 wrong passwords lock the account for 15 minutes. The response stays the
  same whether the email exists or not.
- **No admin, no roles.** Every account is an ordinary user with its own private drive.
- **Sign-up:** anyone who can open the site creates an account on `/register` (email,
  username, display name, password), is signed in straight away, and can upload at once.
  `ALLOW_SIGNUP=true` is the default. Once everyone who should have an account has one,
  setting it to `false` hides `/register` and turns the endpoint off. That is a config
  switch, not an admin feature.
- **Account settings:** each user changes their own name, password and theme, sees and
  signs out their devices, and can delete their own account (which deletes their files
  from the bucket after a confirmation that asks for the password).
- **Isolation:** every drive query filters on `user_id = current_user.id`. A file id from
  another user returns 404, not 403, so ids can't be probed. Tests cover this for every
  endpoint.

---

## 9. Key flows

### 9.1 Opening the app (no downloads)

1. The browser loads the Next.js page. The server checks the access cookie. If it is
   missing, the browser goes to `/login`.
2. `GET /api/v1/drive/folders/{id}/children` returns folders and files (name, kind, size,
   dates) from Postgres.
3. The UI renders a list or grid with **type icons**, not thumbnails. **Zero GCS requests.**

### 9.2 Uploading files

1. The user drops files or picks them. Each file joins the **upload queue**, with up to 3
   uploads in parallel and a tray showing per-file progress, speed and ETA, with cancel
   and retry.
2. For each file: `POST /api/v1/uploads` `{folder_id, name, size, content_type, on_conflict}`.
   The API:
   - checks quota and the name, and applies the choice the person made for a clash (below)
   - inserts a `files` row with `status='uploading'`
   - creates a GCS resumable session for `u/{uid}/f/{file_id}` with the object metadata
     and `origin = WEB_BASE_URL`
   - returns `{file_id, upload_url, chunk_size}`
   Before any of this, the browser sends the whole batch's names to `POST /uploads/conflicts`
   (up to 20,000 per call). If some already exist, one window lists them with the new and
   existing sizes and dates. For each one the person picks **Replace** (the old version
   goes to the trash, so it can be undone), **Skip** or **Keep both** (`name (1).ext`),
   with Replace all / Skip all / Keep both for all. The suggested choice is Skip for files
   of the same size (probably unchanged) and Replace for the rest.
3. The browser PUTs 8 MB chunks with `Content-Range`. GCS replies `308` with the stored
   range, and the loop continues from that point. A failed chunk retries with backoff
   (4 attempts).
4. `POST /api/v1/uploads/{file_id}/complete`: the API stats the object in GCS, checks the
   size matches, sets `status='ready'`, `size_bytes`, adds to `used_bytes`, and writes a
   `file_events` row.
5. If the page reloads mid-upload, the tray offers **Resume** for the same file.
   `GET /uploads/{id}` returns the session URL, and a zero-length PUT asks GCS how much it
   already has.

### 9.3 Uploading a folder

1. The user drops a folder (`DataTransferItem.webkitGetAsEntry()`) or uses the **Upload
   folder** button (`<input webkitdirectory>`).
2. The client builds the set of relative paths and calls
   `POST /api/v1/drive/folders/ensure {parent_id, paths: ["Trip", "Trip/Day 1", …]}`.
   That creates any missing folders in one transaction and returns `path → folder_id`.
3. Each file is queued as in 9.2, with its own `folder_id`.
4. The tray groups the files under the folder name with a combined progress bar.

### 9.4 Viewing a photo

A click opens the **viewer** (a full-screen overlay with previous/next, zoom, info panel,
Download button). Only then is `<img src="/api/v1/files/{id}/content">` requested. The API
checks ownership and streams the object with `Content-Disposition: inline`. Previous/next
fetch only when you move to them (no prefetch).

HEIC photos show in Safari. Other browsers get a "can't preview this format — Download" card
until on-demand conversion arrives (§14).

### 9.5 Playing a video

The viewer shows a poster card with a ▶ button and the file size. **Nothing loads until you
press play.** Then `<video preload="none" src="/api/v1/files/{id}/content">` starts, and the
browser sends `Range` requests. The API answers `206 Partial Content` with just those bytes,
so seeking jumps without downloading the whole file.

Browsers play MP4 (H.264/AAC), WebM and, in Safari, HEVC `.mov`. Other formats
(`.mkv`, `.avi`) show "can't play in the browser — Download".

### 9.6 Downloading

The **Download** button opens `/api/v1/files/{id}/download`, which streams with
`Content-Disposition: attachment; filename*=UTF-8''…`. Multi-select Download and folder Download
will stream a **zip built on the fly**, without temp files (not built yet: for now each selected file downloads on its own).

### 9.7 Rename, move, copy, delete

- **Rename / move** (files or folders) updates the database, then moves the objects to their
  new names (§7). Renaming a folder moves everything under it. A folder can't move into itself.
- **Copy** is a GCS server-side copy to the new name, so no bytes pass through your machine.
- **Delete** moves the item to **Trash** (`trashed_at`). Trash lists the top-level trashed
  items; **Restore** puts them back (renaming if the name is now taken); **Delete forever**
  removes the rows, then the GCS objects. In the bucket, trashed items live under
  `_trash/{username}/…`, which frees their names straight away. Items in trash for more than `TRASH_RETENTION_DAYS`
  (30) are purged by the watchdog run of `cloud.sh`.

### 9.8 Another device, same files

Your files belong to your account in the database, and the bytes are in GCS. Any browser that
reaches the app and signs in as you sees the same drive. See §11 for how to reach it from
other devices, and for running the app on more than one machine.

---

## 10. Web UI

The layout is a left rail and a main pane, like OneDrive. Light, dark and auto themes are
stored in a cookie, so the first paint is right. The app name is `APP_NAME` from `.env`
(for example "My Cloud").

### Routes

| Route | Screen |
|---|---|
| `/login` | email-or-username + password, "Forgot password?" help text (§6.3) |
| `/register` | create an account (hidden when `ALLOW_SIGNUP=false`) |
| `/` → `/drive` | **My files**, the root folder |
| `/drive/[folderId]` | a folder |
| `/recent` | recently uploaded or opened |
| `/photos` | every image, newest first, in a grid of **icons, not thumbnails** (grouped by month) |
| `/videos` | every video |
| `/search?q=` | name search across all folders |
| `/trash` | trash with restore and delete forever |
| `/settings` | profile, change password, theme, signed-in sessions, storage used, delete account |

### Main pane

- **Top bar:** app name, search box, upload progress indicator, theme toggle, account menu
- **Command bar:** **+ New** (folder, upload files, upload folder), and, when something is
  selected, Download, Rename, Move to, Copy to, Delete
- **Breadcrumbs:** `My files › Trip › Day 1`. You can drop files onto a crumb to move them there
- **List view** (default): icon, name, modified, size, and a kebab menu. Sort by column.
  **Grid view** toggle: big type icons
- **Selection:** click, ⌘/Ctrl-click, Shift-click ranges, checkbox column, select all
- **Drag and drop:** drop from the desktop to upload into the current folder, or drag rows onto
  a folder to move them
- **Keyboard:** `Enter` open, `Delete` trash, `F2` rename, `⌘A` select all, `Esc` clear,
  `←/→` in the viewer
- **Right-click** context menu with the same actions
- **Empty states** that explain the next action ("Drop files here or press + New")
- **Storage meter** in the rail: `12.4 GB used` (of the quota, if set)

### Components (frontend/components)

- `AppShell`: checks the session in the browser (refreshing it if needed), then renders the top bar, sidebar and page
- `TopBar` (app name, search, theme, account menu), `Sidebar` (views and storage meter), `ThemeToggle`, `AccountMenu`
- `Browser`: one component for every file view (folder, Recent, Photos, Videos, search, trash) in list or grid,
  with the command bar, selection, context menu, keyboard shortcuts, drag and drop, and all dialogs
- `Dialogs`: new folder / rename, confirm, and the move/copy folder picker (a lazily loaded tree)
- `Viewer`: full-screen image, video/audio (play button first), PDF (the browser's viewer), text (first 1 MB),
  and a Download card for anything else
- `UploadProvider` (the queue: 3 at a time, cancel, retry, folder trees) and `UploadTray`
- `AuthForm` (sign in / create account), `ui` (buttons, fields, modal, toast), `icons`

### Client libraries (frontend/lib)

- `api.ts`: a fetch wrapper that refreshes the session on a 401 and retries once, with typed errors
- `upload.ts`: resumable chunk upload (the 308/Range loop, backoff, asking GCS where it got to after
  a dropped connection) and reading dropped folders
- `format.ts`, `theme.ts`, `types.ts`

---

## 11. Running always, the custom hostname, and other devices

### 11.1 Always on (`cloud.sh`)

`make api` and `make web` are for development: they run in a terminal, reload on every code
change, and stop when the terminal closes or the machine restarts. `cloud.sh` is how the
app runs **for real**: in the background, all the time, without a terminal open.

What `./cloud.sh start` does, step by step:

1. **Loads `.env`** through `scripts/localenv.sh`. If it's missing, it stops and says so.
2. **Pre-flight checks.** It refuses to start, writing the reason to `logs/cloud.log`, when:
   Postgres is unreachable, GCP credentials are missing, the web app hasn't been built
   (`make build-web`) or the build is older than the code, or the API/web port is
   held by a different program (it names that program).
3. **Starts only what isn't running.** The API runs as `uvicorn` (production mode, no reload)
   on `API_HOST_PORT`, and the web app as `next start` on `WEB_HOST_PORT`, both detached with
   `nohup`. Their PIDs go to `logs/api.pid` / `logs/web.pid`. A PID only counts as "running"
   if that process's command line really is our API/web app, so after a reboot a stale PID that
   now belongs to another program is not mistaken for the app.
4. **Waits for health.** It polls `/healthz` and the web page, then prints the URL.
5. **Housekeeping:** rotates any log over 10 MB (copy then truncate), and, when run by cron,
   purges trash older than `TRASH_RETENTION_DAYS` and abandoned `uploading` rows.

Because `start` only fills gaps, running it again and again is harmless. That is what makes
it a **watchdog**: `./cloud.sh cron-install` adds two cron lines, `@reboot` (start after the
machine boots) and `*/10 * * * *` (every 10 minutes, restart anything that crashed). Cron has
an almost empty `PATH`, so the script adds the Homebrew paths itself.

| Command | Does |
|---|---|
| `./cloud.sh start` | the steps above |
| `./cloud.sh stop` | stops both (TERM, then KILL after 10 s) and removes the PID files |
| `./cloud.sh restart` | stop, then start (after `git pull` or a `.env` change) |
| `./cloud.sh status` | running or not, PIDs, ports, health, URL, last cron run |
| `./cloud.sh logs` | follows `logs/api.log`, `logs/web.log` and `logs/cloud.log` |
| `./cloud.sh cron-install` / `cron-remove` | adds / removes the two cron lines (idempotent) |

### 11.2 A friendly name, e.g. `http://mycloud.com:3002`

The name comes from `WEB_BASE_URL` in `.env`, so each person picks their own and nothing
personal is in the repository.

- **`make setup-hosts`** (run by `make setup`) reads the hostname from `WEB_BASE_URL` and adds
  `127.0.0.1 <hostname>` to `/etc/hosts` if that line isn't already there, via
  `scripts/setup_hosts.sh`. It is idempotent and asks for your sudo password only when it
  needs to write. It also checks that the name resolves (`dscacheutil`), and
  `make setup-hosts REMOVE=1` takes the line out again.
- **Port 3002 stays in the URL.** Port 80 is often already taken by other local apps, and
  only one program can own it. The app does not try to use it. The address is
  `http://<hostname>:3002`.
- Allowed origins are worked out from `WEB_BASE_URL` (plus localhost and `EXTRA_ORIGINS`), so
  there is nothing else to configure. `make setup-gcp` applies them to the bucket.
- The name only works on machines whose hosts file (or local DNS) points at the server. If
  it is also a real internet domain, that machine no longer reaches the real site.
- **Later, if you want the bare name without `:3002`:** run one reverse proxy (Caddy) on port
  80 that routes by hostname to each app. Every app currently on 80 moves to its own port
  behind it. That is a machine-wide change, so it is documented but not done by setup.

### 11.3 Using it from other devices (phone, laptop)

The app runs on one machine (the **host**). Other devices open it in a browser:

- **Same Wi-Fi:** `http://<host-LAN-IP>:3002`, or the friendly name with a hosts/router DNS
  entry pointing at the host's LAN IP. Add the address to `EXTRA_ORIGINS`, then `make setup-gcp`.
- **Anywhere (recommended):** a private mesh VPN such as **Tailscale**. It is free for personal
  use, needs no open router ports, and can serve HTTPS with `tailscale serve`, which makes
  cookies `Secure` and enables the camera-roll-friendly upload APIs on phones.
- Do **not** port-forward the app to the internet as-is.

### 11.4 Running the app on more than one machine

For the same files everywhere, all instances must share **one database** as well as the
bucket. There are three options:

| Option | How | When |
|---|---|---|
| **A. One host (default)** | Run the app on one always-on machine and reach it from everywhere (11.3) | simplest, and one source of truth |
| **B. Shared Postgres** | Point every instance's `DATABASE_URL` at one Postgres (the host's over Tailscale, or a managed Postgres such as Cloud SQL, Neon or Supabase) | several machines each run the app |
| **C. Rebuild from the bucket** | New machine, fresh database: create the same usernames, then `make reindex`. It lists the bucket (names only) and recreates folders and files from their paths | disaster recovery, or moving to a new host |

---

## 12. Configuration (`.env.example`)

Every key is documented in `.env.example`. These are the main ones:

| Key | Default | Meaning |
|---|---|---|
| `APP_NAME` | `Personal Cloud` | shown in the UI and the browser tab |
| `APP_ENV` | `local` | `local` allows `make db-reset` |
| `LOG_LEVEL` / `LOG_FORMAT` / `SQL_ECHO` | `INFO` / `text` / `false` | `DEBUG` traces each request, `json` for machine logs |
| `API_HOST_PORT` / `WEB_HOST_PORT` | `8002` / `3002` | chosen so it can run next to other local apps on 8000/3000 and 80 |
| `API_BASE_URL` | `http://localhost:8002` | where the web server reaches the API. The browser never uses it |
| `WEB_BASE_URL` | `http://localhost:3002` | the address people open, e.g. `http://mycloud.com:3002`. `make setup-hosts` maps its hostname, and it decides which origins may upload |
| `EXTRA_ORIGINS` | *(empty)* | more addresses that may open the app (LAN or Tailscale), comma-separated |
| `DATABASE_URL` | `postgresql+asyncpg://cloud:change-me@localhost:5432/personal_cloud` | app connection. `make db-init` creates this role and database |
| `POSTGRES_ADMIN_URL` | `postgresql://postgres@localhost:5432/postgres` | superuser, used only by `make db-init` |
| `JWT_SECRET` | *(empty: API refuses to start)* | `openssl rand -hex 32`, per machine |
| `ACCESS_TOKEN_TTL_MIN` / `REFRESH_TOKEN_TTL_DAYS` | `15` / `30` | |
| `ALLOW_SIGNUP` | `true` | `false` turns off `/register` once your accounts exist |
| `GCP_PROJECT_ID` | *(required)* | project that owns the bucket |
| `GCS_BUCKET` | *(required)* | your bucket name |
| `GCS_PREFIX` | *(empty)* | optional sub-path inside the bucket |
| `GOOGLE_APPLICATION_CREDENTIALS` | *(empty = gcloud ADC)* | path to a service-account key, if you use one. Keep it outside the repo |
| `UPLOAD_CHUNK_BYTES` | `8388608` | a multiple of 256 KiB |
| `MAX_UPLOAD_BYTES` | `53687091200` (50 GiB) | per file |
| `DEFAULT_QUOTA_BYTES` | *(empty = unlimited)* | for new accounts |
| `TRASH_RETENTION_DAYS` | `30` | |

---

## 13. API (v1)

All endpoints are under `/api/v1`. Every drive endpoint needs a signed-in, active user and
touches only that user's rows. Errors are `{"detail": {"message", "field?"}}`.

| Method & path | Purpose |
|---|---|
| `POST /auth/login` · `/auth/logout` · `/auth/refresh` | session |
| `POST /auth/register` | create an account and sign in (403 when `ALLOW_SIGNUP=false`) |
| `GET /account/me` · `PATCH /account/me` · `POST /account/password` · `DELETE /account/me` | profile, password, delete account |
| `GET /account/sessions` · `DELETE /account/sessions/{id}` | signed-in devices |
| `GET /auth/config` · `GET /auth/me` | app name and sign-up switch (public) / the signed-in user |
| `GET /drive/usage` | used / quota bytes, bytes and counts by kind, trash size |
| `GET /drive/folders/{id\|root}` | a folder: its folders and ready files, with breadcrumbs |
| `GET /drive/tree?parent=` | one level of folders for the Move/Copy picker |
| `POST /drive/folders` | create `{parent_id, name}` |
| `POST /drive/folders/ensure` | create a dropped folder tree by relative paths |
| `PATCH /drive/folders/{id}` · `PATCH /drive/files/{id}` | rename |
| `POST /drive/move` · `POST /drive/copy` | bulk `{file_ids, folder_ids, target_folder_id}` |
| `POST /drive/trash` · `POST /drive/restore` · `POST /drive/delete` · `POST /drive/trash/empty` | trash / restore / delete forever |
| `GET /drive/trash` · `GET /drive/recent` · `GET /drive/kind/{kind}` · `GET /drive/search?q=` | views |
| `POST /uploads/conflicts` | which of a batch's names already exist (nothing changes) |
| `POST /uploads` · `GET /uploads/{id}` · `POST /uploads/{id}/complete` · `DELETE /uploads/{id}` | upload sessions (`on_conflict`: `replace` / `rename` / `error`) |
| `GET /files/{id}` | metadata |
| `GET /files/{id}/content` | inline bytes with `Range`/`206` support (viewer, player) |
| `GET /files/{id}/download` | attachment |
| `GET /health` · `GET /healthz` | dependency health (DB, GCS) / liveness |

---

## 14. Roadmap

Each phase ends with working software, tests, and updated docs.

Phases 0–3 are built, apart from the items still unticked.

### Phase 0 — Skeleton and setup tooling ✅
- [x] Repo layout (§5), `.gitignore`, `ruff.toml`, `.env.example`, `scripts/localenv.sh`
- [x] `Makefile`: `help, check, install, setup, db-init, db-reset, db-status, db-shell, setup-gcp,
      setup-hosts, check-db, check-gcp, check-all, api, web, build-web, reset-password, reindex,
      maintenance, test, lint, format, clean`
- [x] `database/`: `init_db.sql` (role, database, `pg_trgm`), migration runner, `setup_db.py`, `check_db.py`
- [x] `infra/scripts/setup_gcp.py` (bucket exists, UBLA, public access prevention, CORS) and
      `check_gcp.py` (config, network, credentials, privacy, CORS, write/read/delete probe)
- [x] FastAPI app: config, logging with request ids, `/health`, `/healthz`
- [x] Next.js app: shell, light/dark/auto theme
- [ ] CI workflow: ruff, pytest, tsc

### Phase 1 — Accounts ✅
- [x] Migrations `V001__helpers`, `V002__users_auth` (users, refresh_tokens)
- [x] Auth service and endpoints, lockout, refresh rotation with reuse detection, `must_change_password`
- [x] `make reset-password LOGIN=…`
- [x] UI: register, login, change password, account menu, signed-in devices, delete account

### Phase 2 — Drive core (the MVP) ✅
- [x] Migration `V003__drive` (folders, files, file_events)
- [x] Storage adapter: resumable session with metadata, stat, range read, delete, copy, patch metadata
- [x] Folder CRUD, children listing, breadcrumbs, name-clash handling
- [x] Upload start/complete/resume/cancel, quota check, usage tracking, empty files
- [x] `/files/{id}/content` with `Range`, `/download` with attachment
- [x] UI: My files (list and grid), New folder, upload files and folders, upload tray, drag and drop,
      viewer for image/video/audio/pdf/text, download, rename, trash/restore/delete forever
- [x] Tests: isolation between users, range responses, uploads (complete and incomplete), trash, moves
- [ ] UI: resume an upload after the page was reloaded (the API supports it)

### Phase 3 — Comfort
- [x] Move / copy (dialog and drag onto folders or breadcrumbs), multi-select, keyboard shortcuts, context menu
- [x] Search, Recent, Photos, Videos views
- [ ] Zip download of a selection or folder (streamed)
- [x] `cloud.sh` (start/stop/restart/status/logs/cron), trash purge and stale-upload cleanup
- [x] `make reindex` (rebuild from the bucket's paths) and `--usage-only`
- [x] Readable bucket paths (`{username}/{folders}/{file}`), kept in step on every change; `make relayout`

### Phase 4 — Nice to have
- [ ] Optional thumbnails: made once at upload time in the browser (canvas for images, a frame
      for videos), stored under `u/{uid}/t/`, and loaded **lazily** only in the grid view. Off by
      default because it means small GCS reads when browsing
- [ ] Capture date from EXIF/video metadata, then a Photos timeline by date taken
- [ ] HEIC → JPEG and non-web video → MP4 on demand (ffmpeg), cached in the bucket
- [ ] Share links (expiring, optional password, view or download)
- [ ] Signed-URL delivery mode through a service account, so large downloads skip the host machine
- [ ] Duplicate detection by sha256, and a "free up space" view
- [ ] PWA manifest so it installs on a phone home screen, with camera-roll upload

Not planned: Docker images. This runs directly on the machine.

---

## 15. Security checklist

- The bucket is private, with public access prevention enforced and UBLA on (checked by `make check-gcp`)
- No secrets in git: `.env`, `*.json` keys and `logs/` are git-ignored. `.env.example` holds placeholders only
- `JWT_SECRET` is required and random, and the API refuses to start without it
- httpOnly + SameSite cookies. `Secure` whenever served over HTTPS
- Ownership is checked on every file and folder id (404 for others). Tests enforce it
- Upload session URLs are created per object with the app's origin, are single-object, and expire
- `Content-Disposition` is always set, and `X-Content-Type-Options: nosniff`. HTML/SVG files are
  served as `attachment` (never rendered inline) so an uploaded file can't run script on the app's origin
- Rate limit and lockout on login
- File names are sanitised for headers (RFC 5987 `filename*`)
- No role can read another user's files: there are no admin endpoints at all

---

## 16. Decisions

| Decision | Choice |
|---|---|
| Accounts | No admin and no roles. Anyone who can reach the site signs up and gets a private drive. `ALLOW_SIGNUP=false` closes sign-up |
| Friendly name | Set per install in `WEB_BASE_URL`. `make setup-hosts` writes `/etc/hosts` during setup |
| Port | Web on 3002 and API on 8002. Port 80 is not used |

Still open:

1. **One host or several machines?** The default is **Option A** (§11.4): one always-on host,
   reached from other devices over Wi-Fi or Tailscale. Choose B if you really want to run the app
   on several machines at once.
2. **Credentials:** gcloud ADC (default, no key file) or a dedicated service account with a key
   (better for an always-on host that you don't want tied to a personal login).
3. **Thumbnails:** stay strictly "no bytes until you click" (default), or allow opt-in
   thumbnails (Phase 4).
