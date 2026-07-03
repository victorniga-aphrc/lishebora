# WSL migration + GitHub branch guide

Use this document when asking an LLM (or following steps yourself) to finish moving
**lishebora_vic** off `/mnt/d/...` into native WSL (`~/projects/`) and hooking it up to
**https://github.com/damiancodes/lishebora-backend**.

---

## CURRENT STATUS (read this first) — migration DONE

The migration described below is **already complete**. Steps 1–3 are kept only as
reference for how it was done. **Do not re-run them.** Here is the live state:

- **WSL location (the only place to work):**
  `~/projects/lishebora_vic` (i.e. `/home/guest566/projects/lishebora_vic`).
  Do **not** edit, git, or `docker build` from `/mnt/d/aphrc/lishebora_vic` anymore.

- **The ONLY active branch going forward:**
  - Repo: **`damiancodes/lishebora-backend`** (Domian's mobile API backend)
  - Folder: `~/projects/lishebora_vic/local/lishebora-backend-domian/lishebora-backend-master`
  - Branch: **`feature/mobile-backend-updates`** ← all future commits go here, pushed, committed (`23e448b`).

- **Main pipeline repo is FROZEN for now.** The root `victorniga-aphrc/lishebora`
  branch **`feature/vic-pipeline-updates`** was pushed once and is **not** where ongoing
  work happens. Leave it alone unless explicitly told otherwise.

> **Go-forward rule for the LLM and the user:** work happens **only** on
> `feature/mobile-backend-updates` inside Domian's backend folder in WSL.
> No further changes to the main pipeline (`victorniga-aphrc/lishebora`) branch.

| What | Repo | Branch | Where (WSL) |
|------|------|--------|-------------|
| **Active work** | `damiancodes/lishebora-backend` | `feature/mobile-backend-updates` | `~/projects/lishebora_vic/local/lishebora-backend-domian/lishebora-backend-master` |
| Frozen (reference only) | `victorniga-aphrc/lishebora` | `feature/vic-pipeline-updates` | `~/projects/lishebora_vic` (root) |

---

## What failed last time

`cp -a /mnt/d/aphrc/lishebora_vic ~/projects/` ran for ~27 minutes then ended with:

```text
Bus error (core dumped)
```

**Why:** `cp -a` copied *everything* — 6.5 GB — including three things that should
never be copied. The real code is < 100 MB. Measured breakdown:

| Item | Size | Copy to WSL? |
|------|------|--------------|
| `.venv/` | 2.2 GB | **No** — recreate with `pip install` in WSL |
| `.git/` | 2.1 GB | Optional — large history (see note) |
| `supermarket_a.backup` | 2.2 GB | **No** — DB dump (gitignored) |
| `local/` (Domian backend) | ~36 MB | **Yes** — the actual work |
| `models/`, `data/`, `app/`, rest | ~32 MB | Yes |

The 2.2 GB `.venv` (thousands of tiny files) over the `/mnt` 9P boundary is what
triggered the bus error. The `rsync` excludes below skip all three monsters.

**Do not use `git clone` alone** as a replacement: Domian’s backend work lives under
`local/lishebora-backend-domian/` (gitignored here), plus you have uncommitted changes
in the main repo and `.env` files.

---

## Prerequisites (WSL Ubuntu terminal)

```bash
# Confirm WSL is healthy
wsl -l -v
# Should show Ubuntu-22.04 Running

cd ~
mkdir -p ~/projects
```

If `~/projects` already has a broken `lishebora_vic`, remove it first:

```bash
rm -rf ~/projects/lishebora_vic
```

---

## Step 1 — Safer copy (recommended: `rsync` in two passes)

### Pass A — Main repo without heavy/unneeded bulk

```bash
SRC=/mnt/d/aphrc/lishebora_vic
DST=~/projects/lishebora_vic

mkdir -p "$DST"

rsync -a --info=progress2 \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='env' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='supermarket_a.backup' \
  --exclude='*.backup' \
  --exclude='*.zip' \
  --exclude='data/huge_data.csv' \
  --exclude='data/all_categories_combined.csv' \
  "$SRC/" "$DST/"
```

This copies well under ~100 MB (code + `local/` + models + data), not 6.5 GB.

> **`.venv` is intentionally excluded.** Recreate it natively in WSL afterward:
> ```bash
> cd ~/projects/lishebora_vic
> python3 -m venv .venv && source .venv/bin/activate
> pip install -r requirements.txt
> ```
> (A venv copied from `/mnt` would be broken anyway — it hardcodes Windows paths.)

### About `.git` (2.1 GB)

The `rsync` above **includes `.git`** so your remote/branch tracking and uncommitted
state come along. If that copy is slow or also bus-errors, exclude it and re-link git
afterward instead:

```bash
# Add this exclude to Pass A if .git is too heavy to copy over 9P:
#   --exclude='.git'

# Then, after the copy, re-init and point at the same remote:
cd ~/projects/lishebora_vic
git init
git remote add origin git@github.com:victorniga-aphrc/lishebora.git
git fetch origin
git checkout -b feature/vic-pipeline-updates   # your new branch
# (your working files are already here; just add/commit/push)
```

Trade-off: excluding `.git` loses local commit history that isn't on the remote. If
history matters, keep `.git` in the copy. The 2.1 GB size suggests large blobs were
committed at some point — worth a cleanup later, but not now.

### Pass B — Ensure `local/` (Domian backend) is present

This folder is **critical** — all Domian backend edits are here.

```bash
rsync -a "$SRC/local/" "$DST/local/"
```

### Verify

```bash
cd ~/projects/lishebora_vic
ls -la local/lishebora-backend-domian/lishebora-backend-master/app/api/routes.py
git status -sb | head -30
```

You should see modified files (`app/`, `MOBILE_FRONTEND_UPDATES.md`, etc.) and
`local/` on disk.

### Git noise on ext4

```bash
git config core.fileMode false
```

---

## Step 2 — Two repos, two remotes (important)

You currently have **two different codebases**:

| Location | What it is | Git remote today |
|----------|------------|------------------|
| `~/projects/lishebora_vic` (root) | Your **main** Lishebora VIC pipeline (`app/`, `static/`, …) | `victorniga-aphrc/lishebora` |
| `~/projects/lishebora_vic/local/.../lishebora-backend-master` | **Domian’s** mobile API backend (what Docker runs on port 8000) | **Not** a git repo yet (no `.git`) |

Target for Domian’s work:

**https://github.com/damiancodes/lishebora-backend**

### Option A — Domian folder becomes its own repo (recommended)

```bash
cd ~/projects/lishebora_vic/local/lishebora-backend-domian/lishebora-backend-master

git init
git remote add origin git@github.com:damiancodes/lishebora-backend.git

# Fetch default branch name from GitHub (main or master)
git fetch origin

# Create your feature branch from current files (example name)
git checkout -b feature/mobile-backend-updates

git add -A
git status   # review: ensure .env is NOT staged (should be in .gitignore)

git commit -m "Backend: scan images, Google auth, KNPM octagons, LLM taxonomy, recommendations"

git push -u origin feature/mobile-backend-updates
```

Then open a PR on GitHub: `damiancodes/lishebora-backend` ← your branch.

### Option B — Clone damiancodes repo fresh, then copy our changes in

Use this if the GitHub repo already has history you must keep:

```bash
cd ~/projects
git clone git@github.com:damiancodes/lishebora-backend.git lishebora-backend
cd lishebora-backend
git checkout -b feature/mobile-backend-updates   # or checkout existing branch

rsync -a \
  ~/projects/lishebora_vic/local/lishebora-backend-domian/lishebora-backend-master/ \
  ./ \
  --exclude='.git'

git status
git add -A
git commit -m "Merge local backend changes from WSL migration"
git push -u origin feature/mobile-backend-updates
```

Work in `~/projects/lishebora-backend` for Domian/docker after this.

---

## Step 3 — Main repo (`victorniga-aphrc/lishebora`) — optional separate branch

If you also want your **root** pipeline changes on GitHub:

```bash
cd ~/projects/lishebora_vic

git fetch origin
git checkout -b feature/vic-pipeline-updates   # pick your branch name

git add -A
git status   # again: no secrets in .env

git commit -m "Pipeline: OCR confidence, image storage, substitute tier-1, docs"
git push -u origin feature/vic-pipeline-updates
```

Remote stays: `git@github.com:victorniga-aphrc/lishebora.git`

---

## Step 4 — Docker after migration

Always run Domian’s stack from **WSL paths**, not `/mnt/d`:

```bash
cd ~/projects/lishebora_vic/local/lishebora-backend-domian/lishebora-backend-master
# OR cd ~/projects/lishebora-backend   if you used Option B

docker compose up -d --build backend
```

Enable Docker Desktop → Settings → Resources → WSL Integration → Ubuntu-22.04.

Stop the old WSL-native duplicate docker daemon if you still use Desktop only:

```bash
sudo systemctl disable --now docker docker.socket 2>/dev/null || true
```

---

## Step 5 — Cursor / IDE

Open the WSL folder, not `D:\APHRC\...`:

- `\\wsl$\Ubuntu-22.04\home\<your-user>\projects\lishebora_vic`
- Or in Cursor: **Remote - WSL** → open `~/projects/lishebora_vic`

---

## Checklist for the LLM assistant

Migration is done. For any new work, the rules are:

1. [x] `~/projects/lishebora_vic` exists; Domian backend committed + pushed
2. [x] Domian folder is a git repo, `origin` → `damiancodes/lishebora-backend`
3. [x] Branch `feature/mobile-backend-updates` pushed to damiancodes (`23e448b`)
4. [ ] **All ongoing work stays on `feature/mobile-backend-updates`** in
       `~/projects/lishebora_vic/local/lishebora-backend-domian/lishebora-backend-master`
5. [ ] **Do NOT commit to the main pipeline** (`victorniga-aphrc/lishebora` /
       `feature/vic-pipeline-updates`) — it is frozen unless the user says otherwise
6. [ ] `.env` never committed; secrets only in local `.env`
7. [ ] Docker is rebuilt from the WSL path, never from `/mnt/d/aphrc/...`

---

## SSH / GitHub (WSL)

```bash
ssh -T git@github.com
# Hi victorniga-aphrc! or damiancodes — depending on key/account

git config --global user.name
git config --global user.email
```

If fetch fails, fix `~/.ssh/config` Host `github.com` → `IdentityFile ~/.ssh/id_ed25519_github`.
