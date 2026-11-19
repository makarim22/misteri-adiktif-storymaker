# Story Studio

A small web workspace for a YouTube storytelling team: a video board, a shared script
editor with comments and history, a running-order strip, a deadline calendar, and an AI
assistant that knows your channel's voice.

## Tech stack

| Layer | Technology | Notes |
|---|---|---|
| Language (backend) | Python 3 | Developed and tested on 3.12. Older 3.x versions are untested. |
| Web server | `http.server` (`ThreadingHTTPServer`) | Standard library, no web framework |
| Database | SQLite via `sqlite3` | One file (`studio.db`), WAL mode, foreign keys on |
| API | JSON over HTTP, around 30 routes | Small regex-based router in `server.py` |
| Auth | PBKDF2-SHA256 (200,000 rounds, random salt) and cookie sessions | `HttpOnly`, `SameSite=Lax`, 30-day expiry |
| Frontend | Vanilla JavaScript, HTML and CSS | No framework, no build step, no npm packages |
| Routing (frontend) | Hash-based (`#/video/3`) | Handled in `static/app.js` |
| Language | Indonesian (default) and English | Dictionaries in `static/i18n.js`, switch in the top bar |
| Theme | Light, Dark or System | CSS variables, `data-theme` attribute, stored per browser |
| Fonts | Newsreader and Instrument Sans | Self-hosted WOFF2 files, SIL Open Font License 1.1 |
| AI | Claude (Anthropic) or OpenAI over HTTPS | Called with `urllib`, no SDK; switchable in Settings. Claude calls use prompt caching |

**Python dependencies:** none. Everything comes from the standard library.

**Why no framework:** the app targets a team of about three people on one machine. Skipping
a framework and a build step means nothing to install and nothing to keep updated. If it
grows (many users, public hosting), moving to a framework such as FastAPI or Flask, with
Postgres, would be the natural next step.

## Project structure

    story-studio/
      server.py        HTTP server, routes, auth, SQLite schema
      ai.py            AI providers (Claude, OpenAI) and the stage prompts
      static/
        index.html     Single page shell
        i18n.js        Language and theme preferences, id/en dictionaries
        app.js         All UI: board, editor, calendar, library, settings
        style.css      Design tokens and layout (light and dark)
        fonts.css      @font-face rules
        fonts/         Bundled WOFF2 fonts
      studio.db        Created on first run (not in git)

## Data model

Eight SQLite tables: `users`, `sessions`, `videos`, `versions`, `comments`, `examples`,
`ai_runs` and `settings`. The style guide, script template, provider and API keys are
stored as rows in `settings`. Each video keeps a `rev` counter that is used for conflict
detection on save.

## Language and theme

- **Indonesian first.** The interface, the starter style guide, the starter script template and the
  AI prompts are in Bahasa Indonesia. English is available from the **ID / EN** switch in the top bar.
- **Interface wording is neutral** (no aku/kamu or saya/Anda), so it suits any register.
- **AI-written scripts** are asked for formal, standard Indonesian (no gue/lo). The pronouns (aku/kamu
  or saya/Anda) are not fixed: set them in the **Style guide** (the `Sapaan:` line) or add example
  scripts, and the AI follows them. Until then it may mix the two.
- **Theme:** Light, Dark or System (follows the computer). The choice is stored in the browser, as is
  the language, so each person sets their own.
- Stored data is unchanged by the language switch. Stage names are translated only on screen.
- Server messages are translated in the browser; a message not in the dictionary is shown in English.
- Prompts are in `ai.py` (`BASE_RULES` and `STAGES`) if you want to change the tone.
- The `avoid-ai-writing` OpenCode skill is built around English wording, so it will not catch
  Indonesian AI-sounding phrasing.

## Choosing models, cost and budget (Claude)

- **Model picker.** Each AI run can use a different Claude model. Under **Tim dan AI** pick a preset
  (Hemat / Seimbang / Kualitas terbaik) that assigns a model to each task, adjust any task by hand, or
  override a single run from the **Model** dropdown in the AI tab.
- **Cost before and after.** The dropdown shows an estimated cost per model. After a run the real cost
  is shown, computed from the token counts the API returns, and logged per run.
- **Prompt caching.** The system prompt (rules, style guide, example scripts) is the same on every call,
  so it is marked for caching (5-minute lifetime). A cache hit bills that part at a small fraction of the
  normal input price; the first call after the cache expires pays about 25% extra to write it. Prompts
  under the model's minimum size (512 tokens on current models) are not cached. Changing the style guide
  or examples starts a new cache.
- **Compare.** The AI tab can run one task on two models and show both results side by side with their
  costs. It costs about double for that one run.
- **Budget.** An optional monthly budget (USD) shows spent versus budget, warns at 80% and asks for
  confirmation after 100%. It only counts spending made through this app, so also set a limit in the
  Anthropic console. Rupiah is shown if an admin enters an exchange rate (typed by hand, no live lookup).
- **Prices are hard-coded** in `ai.py` (`CLAUDE_MODELS`, with the date they were checked) and are not
  fetched live. Update them when Anthropic changes prices.
- **Estimates are approximate.** Tokens per character for Indonesian and typical output length are rough
  guesses until the app has a few of your own runs to average. Newer models also think before answering,
  and those tokens are billed as output.
- **Model quality is not measured here.** The tiers come from price and Anthropic's descriptions. Use
  Compare on a real task to judge whether a cheaper model is good enough.
- **Not yet tested against the real API.** The request format and cost math were checked against a local
  fake server (set `ANTHROPIC_BASE_URL` to point at one). The first real call may reveal differences.
- OpenAI keeps a free-text model field. No OpenAI prices are built in, so its costs show as unknown.

## Run

    cd /home/jaka/story-studio
    python3 server.py                  # only this computer: http://127.0.0.1:8000
    python3 server.py --host 0.0.0.0   # teammates on the same network: http://<your-ip>:8000

On first visit you create the admin account, then add teammates under **Team & AI**.
Data lives in `studio.db` (back this file up). Set `STUDIO_DB=/path/file.db` to move it.

## First-time setup (about 15 minutes)

1. **Team & AI**: choose a provider, paste an API key (or set `ANTHROPIC_API_KEY` /
   `OPENAI_API_KEY` before starting). Optionally set a model name.
2. **Library**: fill in the style guide and add 3-5 of your best scripts as examples.
   This is what makes the AI sound like you.
3. **Board**: add a video, open it, write, and use the AI tab.

## How it works

- **Board**: one card per video, drag between stages (idea, research, outline, script,
  review, filmed, published).
- **Editor**: autosaves. Each save checks a revision number, so two people can never silently
  overwrite each other; the second saver gets a conflict warning instead.
  History keeps versions; you can name versions and restore old ones.
- **Running order**: reads capitalised headings in the script (the starter template uses `PEMBUKA`,
  `LATAR`, `KETEGANGAN`, `TITIK BALIK`, `PENUTUP`; English headings work too) and shows each
  section's estimated spoken time. An opening (`PEMBUKA` or `HOOK`) over 30 seconds is flagged.
  Click a section to jump to it. `[bracketed notes]` are not counted.
- **Speaking rate**: durations use words per minute, set under **Tim dan AI** (admin). The default
  of 150 is a starting guess and has not been verified for Indonesian narration. Time one real
  narration and adjust it.
- **Calendar**: a monthly view of video deadlines. Overdue videos are marked in red.
- **AI tab**: stages for ideas, research brief, outline, first draft, critique, rewrite,
  titles/thumbnails/description, and Shorts. Results can be inserted into the script.
  Every generation is logged per video.
- AI is told never to invent facts and to mark uncertain claims `[VERIFY]`. Always fact-check.

## Limits (by design, for now)

- Not real-time co-editing; one person at a time per script is best.
- Plain-text editor (no bold/italics).
- Local-first by default. To put it online, follow `DEPLOY.md` (HTTPS reverse proxy, service, backups).
  Do not expose the plain HTTP server directly to the internet.
- API keys are stored unencrypted in `studio.db`; keep that file private.
- Default model names are in `ai.py`; change them in Settings if a newer model is available.

## Configuration (environment variables)

| Variable | Purpose |
|---|---|
| `STUDIO_HOST`, `STUDIO_PORT` | Address and port to listen on (default `127.0.0.1:8000`; flags `--host`/`--port` also work) |
| `STUDIO_DB` | Path of the SQLite file |
| `STUDIO_SETUP_TOKEN` | If set, creating the very first admin account requires this token |
| `STUDIO_TRUSTED_PROXY` | Comma-separated proxy addresses whose forwarded headers are believed (default `127.0.0.1,::1`) |
| `STUDIO_COOKIE_SECURE=1` | Always mark the session cookie `Secure` |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | AI keys (preferred over saving them in the database) |
| `ANTHROPIC_BASE_URL` | Testing only: point Claude calls at a local fake server |

## Backups

`python3 backup.py [--dir DIR] [--keep N]` writes a consistent, integrity-checked copy of the database (safe while
the server runs), keeps the newest N (default 14), and creates files readable only by you. Do not copy `studio.db`
with `cp` while the server is running. To restore, stop the server and copy a backup over `studio.db`.

## Security notes

- All user text is inserted into the page with `textContent` and DOM calls, never `innerHTML`.
- API endpoints require a session cookie, except sign-in and first-run setup. Only JSON bodies
  are accepted, and request size is capped at 5 MB.
- Only admins can change AI settings, add or remove users.
- Sign-in is rate limited: after 10 failed attempts from one address, or 20 for one username, within 15
  minutes, further attempts get "429 Too many attempts" until the window passes. Failures are logged to stderr.
- The session cookie is `HttpOnly` and `SameSite=Lax`, and `Secure` when the request came over HTTPS. Responses
  carry a Content-Security-Policy (scripts only from the app itself), `X-Frame-Options: DENY`, and
  `Strict-Transport-Security` over HTTPS.
- The server speaks plain HTTP. Anything reachable from outside must sit behind an HTTPS reverse proxy.
  `X-Forwarded-For` / `X-Forwarded-Proto` are only believed from addresses in `STUDIO_TRUSTED_PROXY`.
- There is still no CSRF token (the `SameSite=Lax` cookie and the JSON-only API are the protection).

## Fonts

`static/fonts/` contains Newsreader and Instrument Sans (from Google Fonts), both licensed under the
SIL Open Font License 1.1. See https://openfontlicense.org. They are bundled so the app works offline.
