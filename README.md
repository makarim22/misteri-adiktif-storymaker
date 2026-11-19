# Story Studio

A small web workspace for a YouTube storytelling team: a video board, a shared script
editor with comments and history, and an AI assistant that knows your channel's voice.

No installation needed beyond Python 3.8+ (standard library only).

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

## Fonts

`static/fonts/` contains Newsreader and Instrument Sans (from Google Fonts), both licensed under the
SIL Open Font License 1.1. See https://openfontlicense.org. They are bundled so the app works offline.
