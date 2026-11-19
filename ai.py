"""AI layer: provider-agnostic text generation plus the per-stage prompts.

Providers are called over plain HTTPS with urllib, so no extra packages are needed.
Switch provider/model in the app's Settings page (or via env vars).
"""
import json
import urllib.error
import urllib.request

PROVIDERS = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "default_model": "claude-sonnet-4-5",
        "env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "default_model": "gpt-4o",
        "env": "OPENAI_API_KEY",
    },
}

BASE_RULES = """You are the writing partner for a YouTube storytelling channel.
Rules:
- Follow the channel style guide below exactly. Match the voice of the example scripts.
- NEVER invent facts, names, dates, quotes or numbers. If a detail is not in the
  material you were given, either leave it out or mark it with [VERIFY].
- Mark every claim that needs a source check with [VERIFY].
- Write for the ear: spoken language, short sentences, clear rhythm.
- Put visual ideas in [VISUAL: ...] notes, not in the spoken text.
"""

# Each stage: label, instruction template. {input} is what the user typed,
# {script} is the current script text in the editor.
STAGES = {
    "ideas": {
        "label": "Brainstorm story angles",
        "needs": "input",
        "prompt": (
            "Brainstorm 10 story angles for a video about the topic below. For each: "
            "a working title, a one-line hook, and why a viewer would keep watching. "
            "Prefer angles with a clear tension or turning point.\n\nTOPIC / NOTES:\n{input}"
        ),
    },
    "research": {
        "label": "Research brief",
        "needs": "input",
        "prompt": (
            "Turn the raw notes below into a structured research brief: timeline, key "
            "people/characters, central conflict, turning points, open questions, and a "
            "list of claims that must be fact-checked. Use only the notes; do not add facts.\n\n"
            "RAW NOTES:\n{input}"
        ),
    },
    "outline": {
        "label": "Beat-by-beat outline",
        "needs": "input",
        "prompt": (
            "Write a beat-by-beat outline from the material below using this structure: "
            "Hook, Setup, Rising tension (several beats), Turning point, Payoff, Outro. "
            "For each beat give 1-2 sentences and the emotion it should create. Note where "
            "visuals could go.\n\nMATERIAL:\n{input}"
        ),
    },
    "draft": {
        "label": "First-draft script",
        "needs": "input",
        "prompt": (
            "Write a full first-draft narration script from the outline/material below, in "
            "the channel voice. Open with a strong hook. End with the outro. Mark unverified "
            "details with [VERIFY]. Add [VISUAL: ...] notes where useful.\n\n"
            "OUTLINE / MATERIAL:\n{input}"
        ),
    },
    "critique": {
        "label": "Critique this script",
        "needs": "script",
        "prompt": (
            "Critique the script below as a tough editor. Report: (1) where a viewer is most "
            "likely to drop off and why, (2) weak or slow transitions, (3) places where it "
            "drifts from the style guide, (4) unsupported claims, (5) the 3 highest-impact "
            "fixes. Quote the exact lines you refer to. Do not rewrite the whole script.\n\n"
            "SCRIPT:\n{script}"
        ),
    },
    "rewrite": {
        "label": "Rewrite a section",
        "needs": "both",
        "prompt": (
            "Rewrite the part of the script described in the instruction, keeping facts "
            "unchanged and matching the channel voice. Return only the rewritten text.\n\n"
            "INSTRUCTION / SECTION:\n{input}\n\nFULL SCRIPT FOR CONTEXT:\n{script}"
        ),
    },
    "package": {
        "label": "Titles, thumbnails, description",
        "needs": "script",
        "prompt": (
            "From the finished script below, produce: 10 title options (under 70 characters, "
            "honest, curiosity-driven), 5 thumbnail concepts (visual + max 4 words of text), "
            "a YouTube description (first 2 lines work as the hook), chapters with timestamps "
            "placeholders, and 15 tags.\n\nSCRIPT:\n{script}"
        ),
    },
    "repurpose": {
        "label": "Shorts + community post",
        "needs": "script",
        "prompt": (
            "From the script below write: 3 YouTube Shorts scripts (under 45 seconds each, "
            "strong first line), and 1 community-tab post that teases the story without "
            "spoiling the payoff.\n\nSCRIPT:\n{script}"
        ),
    },
}


class AIError(Exception):
    pass


def build_system(style_guide, examples):
    parts = [BASE_RULES]
    parts.append("=== CHANNEL STYLE GUIDE ===\n" + (style_guide.strip() or "(not written yet)"))
    if examples:
        parts.append("=== EXAMPLE SCRIPTS (match this voice) ===")
        for ex in examples:
            parts.append(f"--- {ex['title']} ---\n{ex['body']}")
    return "\n\n".join(parts)


def _post(url, headers, payload, timeout=180):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise AIError(f"Provider returned HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise AIError(f"Could not reach provider: {e.reason}")


def generate(provider, model, api_key, system, user, max_tokens=4096):
    if provider not in PROVIDERS:
        raise AIError(f"Unknown provider '{provider}'")
    if not api_key:
        raise AIError("No API key set. Add one in Settings or set the environment variable.")
    model = model or PROVIDERS[provider]["default_model"]

    if provider == "anthropic":
        data = _post(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        return "".join(b.get("text", "") for b in data.get("content", []))

    data = _post(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
    )
    return data["choices"][0]["message"]["content"]
