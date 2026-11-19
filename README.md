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
| AI | Claude (Anthropic) or OpenAI over HTTPS | Called with `urllib`, no SDK; switchable in Settings |

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
- Local-first: for access outside your network, deploy behind HTTPS (e.g. a small VPS with a
  reverse proxy). Do not expose the plain HTTP server directly to the internet.
- API keys are stored unencrypted in `studio.db`; keep that file private.
- Default model names are in `ai.py`; change them in Settings if a newer model is available.

## Security notes

- All user text is inserted into the page with `textContent` and DOM calls, never `innerHTML`.
- API endpoints require a session cookie, except sign-in and first-run setup. Only JSON bodies
  are accepted, and request size is capped at 5 MB.
- Only admins can change AI settings, add or remove users.
- There is no HTTPS, rate limiting or CSRF token. Cookies use `SameSite=Lax`. Keep it on a trusted
  network, or put it behind an HTTPS reverse proxy.

## Fonts

`static/fonts/` contains Newsreader and Instrument Sans (from Google Fonts), both licensed under the
SIL Open Font License 1.1. See https://openfontlicense.org. They are bundled so the app works offline.
