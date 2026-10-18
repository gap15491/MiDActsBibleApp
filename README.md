# The KJV Rightly Divided — study Bible + notes app

A King James Bible reordered on the **right-division** principle (2 Timothy 2:15), with a
mid-Acts / Acts 9 framework, book introductions, a commentary layer, and — now — **user
accounts with private chapter- and verse-notes** stored in Postgres.

## What's here
- `app.py` — Flask backend: serves the site, handles registration/login/logout, and a notes API.
- `index.html` — the reordered study Bible (served at `/`).
- `chart.html` — the companion division chart (served at `/chart.html`).
- `requirements.txt`, `Dockerfile` — Python app, run with gunicorn.
- `src/` — the build system that generates the HTML (`build.sh`, `build_bible.py`, `content.py`, `books/`).

## How notes work
- Anyone can **read** the Bible. To **save notes** a visitor registers with an email + password.
- Each user's notes are **private to them**. Passwords are hashed (never stored in plain text);
  login uses a signed session cookie.
- **Verse notes:** in Verse view, tap a verse number → a "My note" box appears in the panel.
- **Chapter notes:** each chapter has a **✎ Note** button that opens a whole-chapter note.
- A small dot marks verses that have a note; the ✎ Note button fills in when a chapter note exists.

Notes are stored in a `notes` table keyed by `(user, book, chapter, verse)` — `verse` is NULL for
a chapter-level note.

---

## Deploy to Railway (app + Postgres)

1. **Push this repo to GitHub** (see below).
2. **Create the app service:** railway.com/new → Deploy from GitHub repo → select this repo.
   Railway builds the `Dockerfile` (Python + gunicorn).
3. **Add Postgres:** in the same Railway project, click **New → Database → PostgreSQL**.
4. **Give the app the database URL:** open the app service → **Variables** → add
   `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (Railway autocompletes the reference).
   The app rewrites the `postgres://` scheme automatically and creates its tables on first boot.
5. **Set a secret key:** in the app service **Variables**, add `SECRET_KEY` to a long random
   string (this signs login cookies — keep it private). e.g. generate one with
   `python -c "import secrets; print(secrets.token_hex(32))"`.
6. **Redeploy.** Open the generated `*.up.railway.app` URL. Register an account and start taking notes.

### Custom domain
App service → **Settings → Networking → Custom Domain** → add the CNAME at your DNS provider.
SSL is automatic.

### Push to GitHub
```bash
cd kjv-rightly-divided
git init && git add . && git commit -m "KJV Rightly Divided — study Bible + notes app"
git remote add origin https://github.com/<you>/kjv-rightly-divided.git
git branch -M main && git push -u origin main
# or: gh repo create kjv-rightly-divided --public --source=. --push
```

---

## Environment variables
| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes (prod) | Postgres connection. On Railway set to `${{Postgres.DATABASE_URL}}`. If unset, the app uses a local SQLite file (dev only). |
| `SECRET_KEY` | Yes (prod) | Signs session cookies. Use a long random value. |
| `PORT` | auto | Railway injects it; gunicorn binds it. |

## Run locally
```bash
pip install -r requirements.txt
SECRET_KEY=dev python3 app.py      # uses a local SQLite notes.db, serves on :8080
```

## Editing Bible content and redeploying
All authored content is in `src/content.py` (book intros, front/back matter, per-verse NOTES,
commentary source links). To rebuild and redeploy:
```bash
bash src/build.sh                       # writes ../index.html and ../chart.html
git add -A && git commit -m "update" && git push
```

---

## Strong's concordance
Each verse (in verse view) has a **Strong's** button that shows a word-by-word breakdown —
original Greek/Hebrew, transliteration, part of speech, Strong's definition, and outline of usage.
No login required (public-domain data). Data lives in `data/strongs_verses.json` (tagged KJV) and
`data/strongs_lex.json` (lexicon), loaded by the app at startup and served via `GET /api/strongs`.
Source: kaiserlik/kjv (Strong's-tagged KJV) — public domain.

## API (all JSON, same-origin cookies)
- `POST /api/register` `{email, password}` — create account + log in.
- `POST /api/login` `{email, password}` — log in.
- `POST /api/logout` — log out.
- `GET  /api/me` — `{email}` or `{email:null}`.
- `GET  /api/notes?book=&chapter=` — this user's notes for a chapter: `{chapter_note, verses:{}}`.
- `PUT  /api/notes` `{book, chapter, verse|null, text}` — create/update (empty text deletes).
- `DELETE /api/notes` `{book, chapter, verse|null}` — delete.
- `GET  /api/strongs?book=&chapter=&verse=` — word-by-word Strong's breakdown (no login needed).

## Security notes
Passwords are hashed with PBKDF2 (Werkzeug). Sessions are signed cookies (HttpOnly, SameSite=Lax,
Secure in production). This is a solid baseline, not a hardened identity system — there is no email
verification or password-reset flow yet, and a strong `SECRET_KEY` is essential. Consider adding
rate-limiting and password reset if you open it to the public.

## Troubleshooting deploys
- Confirm the version loaded via the **"Updated … UTC"** stamp near the top of the page.
- If notes fail to save with a 401, you're logged out — log in again.
- If the app boots but can't connect to the DB, check that `DATABASE_URL` is set on the app service.
- Hard-refresh (Ctrl/Cmd+Shift+R) if the page looks stale; the server sends no-cache for HTML.

## Credits
KJV text: public domain (aruljohn/Bible-kjv), verified 1189 chapters / 31,102 verses.
Commentary sources are linked, not reproduced. Inline per-verse notes are original.
