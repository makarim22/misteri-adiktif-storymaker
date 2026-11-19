"""AI layer: provider-agnostic text generation plus the per-stage prompts.

Providers are called over plain HTTPS with urllib, so no extra packages are needed.
Switch provider/model in the app's Settings page (or via env vars).
"""
import json
import os
import urllib.error
import urllib.request

PROVIDERS = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "default_model": "claude-sonnet-5-5",
        "env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "default_model": "gpt-4o",
        "env": "OPENAI_API_KEY",
    },
}

# ---- Claude model catalog -------------------------------------------------------------------
# Prices are USD per million tokens, copied from Anthropic's pricing page on PRICES_CHECKED.
# They are NOT fetched live: update this table when Anthropic changes prices.
# "min_cache" is the smallest prompt (in tokens) that can be cached; shorter prompts are processed
# normally and silently not cached. "long" = higher rates that apply when the prompt exceeds 100k tokens.
PRICES_CHECKED = "2026-11-19"
PRICES_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
CLAUDE_MODELS = [
    {"id": "claude-haiku-5-5", "label": "Haiku 5.5", "tier": "fast",
     "in": 0.10, "out": 0.50, "cache_write": 0.125, "cache_read": 0.01, "min_cache": 512,
     "long": {"in": 0.50, "out": 2.50, "cache_write": 0.625, "cache_read": 0.05}},
    {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5", "tier": "balanced",
     "in": 2.0, "out": 10.0, "cache_write": 2.5, "cache_read": 0.10, "min_cache": 512},
    {"id": "claude-opus-5-5", "label": "Opus 5.5", "tier": "strong",
     "in": 4.0, "out": 20.0, "cache_write": 5.0, "cache_read": 0.20, "min_cache": 512},
    {"id": "claude-fable-5-2", "label": "Fable 5.2", "tier": "strongest",
     "in": 8.0, "out": 40.0, "cache_write": 10.0, "cache_read": 0.20, "min_cache": 512},
    {"id": "claude-sonnet-4-5", "label": "Sonnet 4.5 (lama)", "tier": "legacy",
     "in": 3.0, "out": 15.0, "cache_write": 3.75, "cache_read": 0.30, "min_cache": 1024},
]
MODEL_BY_ID = {m["id"]: m for m in CLAUDE_MODELS}

# Which model each task uses under each preset. These are proposals, not measured quality rankings:
# there is no data here on how each model writes Indonesian. Use the compare feature to judge.
TASK_IDS = ["ideas", "research", "outline", "draft", "critique", "rewrite", "package", "repurpose"]
H, S_, O = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
PRESETS = {
    "hemat": {t: H for t in TASK_IDS},
    "seimbang": {"ideas": S_, "research": H, "outline": S_, "draft": S_, "critique": S_,
                 "rewrite": S_, "package": H, "repurpose": H},
    "terbaik": {"ideas": O, "research": S_, "outline": O, "draft": O, "critique": O,
                "rewrite": O, "package": S_, "repurpose": S_},
}
DEFAULT_PRESET = "seimbang"

# Rough output size per task (tokens) used only for cost ESTIMATES. These are guesses; the app
# replaces them with the average of your own past runs once it has some.
TYPICAL_OUT_TOKENS = {"ideas": 1500, "research": 1500, "outline": 1200, "draft": 3500,
                      "critique": 1200, "rewrite": 800, "package": 1500, "repurpose": 1500}
CHARS_PER_TOKEN = 3.2   # guess for Indonesian text; not measured
MAX_TOKENS = 12000      # newer models think adaptively and thinking tokens count toward this limit


def est_tokens(chars):
    return int(chars / CHARS_PER_TOKEN) + 1


def cost_usd(model_id, usage):
    """Cost in USD from real usage numbers, or None when the model has no known price."""
    m = MODEL_BY_ID.get(model_id)
    if not m or not usage:
        return None
    inp, out = usage.get("input", 0), usage.get("output", 0)
    cr, cw = usage.get("cache_read", 0), usage.get("cache_write", 0)
    rates = m
    if m.get("long") and (inp + cr + cw) > 100_000:
        rates = m["long"]
    return (inp * rates["in"] + out * rates["out"] + cw * rates["cache_write"] + cr * rates["cache_read"]) / 1e6


def estimate_cost(model_id, system_tokens, user_tokens, out_tokens):
    """(cold, warm) estimate in USD. Cold writes the system block to the cache; warm reads it."""
    m = MODEL_BY_ID.get(model_id)
    if not m:
        return None, None
    cacheable = system_tokens >= m["min_cache"]
    cold = cost_usd(model_id, {"input": user_tokens + (0 if cacheable else system_tokens), "output": out_tokens,
                               "cache_write": system_tokens if cacheable else 0})
    warm = cost_usd(model_id, {"input": user_tokens + (0 if cacheable else system_tokens), "output": out_tokens,
                               "cache_read": system_tokens if cacheable else 0})
    return cold, warm


# All prompts are in Indonesian and ask for Bahasa Indonesia output. Edit the text below to change
# tone or rules without touching the rest of the app.
BASE_RULES = """Kamu adalah rekan penulis untuk channel YouTube bergaya bercerita (storytelling).
Aturan:
- Tulis dalam Bahasa Indonesia yang baku dan enak didengar. Jangan pakai bahasa gaul (gue/lo).
- Untuk sapaan (aku/kamu atau saya/Anda) dan nada, ikuti panduan gaya dan contoh naskah di bawah.
  Jika tidak jelas, pilih satu dan pakai konsisten. Jangan mencampur dua gaya sapaan.
- Ikuti panduan gaya channel di bawah dengan tepat. Tiru suara contoh naskah.
- JANGAN mengarang fakta, nama, tanggal, kutipan, atau angka. Jika sebuah detail tidak ada
  di materi yang diberikan, hilangkan atau tandai dengan [VERIFY].
- Tandai setiap klaim yang perlu dicek sumbernya dengan [VERIFY].
- Tulis untuk didengar: bahasa lisan, kalimat pendek, ritme jelas.
- Taruh ide visual dalam catatan [VISUAL: ...], bukan di teks yang dibacakan.
- Jika diminta judul atau deskripsi, tulis dalam Bahasa Indonesia juga.
"""

# Each stage: label (English; the app translates it), instruction template.
# {input} is what the user typed, {script} is the current script text in the editor.
STAGES = {
    "ideas": {
        "label": "Brainstorm story angles",
        "needs": "input",
        "prompt": (
            "Cari 10 sudut cerita untuk video dengan topik di bawah. Untuk tiap sudut tulis: "
            "judul kerja, satu kalimat pembuka (hook), dan alasan penonton akan terus menonton. "
            "Utamakan sudut yang punya ketegangan atau titik balik yang jelas.\n\nTOPIK / CATATAN:\n{input}"
        ),
    },
    "research": {
        "label": "Research brief",
        "needs": "input",
        "prompt": (
            "Ubah catatan mentah di bawah menjadi ringkasan riset terstruktur: lini masa, tokoh utama, "
            "konflik inti, titik balik, pertanyaan yang belum terjawab, dan daftar klaim yang harus "
            "dicek faktanya. Pakai hanya isi catatan; jangan menambah fakta.\n\nCATATAN MENTAH:\n{input}"
        ),
    },
    "outline": {
        "label": "Beat-by-beat outline",
        "needs": "input",
        "prompt": (
            "Susun kerangka per bagian dari materi di bawah dengan struktur ini, memakai judul bagian "
            "berhuruf kapital: PEMBUKA, LATAR, KETEGANGAN (beberapa bagian), TITIK BALIK, PENUTUP. "
            "Untuk tiap bagian tulis 1-2 kalimat dan emosi yang ingin dibangun. Catat di mana visual bisa dipakai."
            "\n\nMATERI:\n{input}"
        ),
    },
    "draft": {
        "label": "First-draft script",
        "needs": "input",
        "prompt": (
            "Tulis draf pertama naskah narasi lengkap dari kerangka atau materi di bawah, dengan suara channel. "
            "Pakai judul bagian berhuruf kapital (PEMBUKA, LATAR, KETEGANGAN, TITIK BALIK, PENUTUP) di baris sendiri. "
            "Buka dengan pembuka yang kuat dan akhiri dengan penutup. Tandai detail yang belum terverifikasi "
            "dengan [VERIFY]. Tambahkan catatan [VISUAL: ...] bila berguna.\n\nKERANGKA / MATERI:\n{input}"
        ),
    },
    "critique": {
        "label": "Critique this script",
        "needs": "script",
        "prompt": (
            "Kritik naskah di bawah sebagai editor yang tegas. Laporkan: (1) bagian tempat penonton paling mungkin "
            "berhenti menonton dan alasannya, (2) transisi yang lemah atau lambat, (3) bagian yang menyimpang dari "
            "panduan gaya, (4) klaim tanpa dukungan, (5) tiga perbaikan paling berdampak. Kutip baris yang kamu "
            "maksud. Jangan menulis ulang seluruh naskah.\n\nNASKAH:\n{script}"
        ),
    },
    "rewrite": {
        "label": "Rewrite a section",
        "needs": "both",
        "prompt": (
            "Tulis ulang bagian naskah yang dijelaskan dalam instruksi, tanpa mengubah fakta dan sesuai suara "
            "channel. Kembalikan hanya teks hasil tulis ulang.\n\nINSTRUKSI / BAGIAN:\n{input}\n\n"
            "NASKAH LENGKAP SEBAGAI KONTEKS:\n{script}"
        ),
    },
    "package": {
        "label": "Titles, thumbnails, description",
        "needs": "script",
        "prompt": (
            "Dari naskah final di bawah, buat: 10 pilihan judul (di bawah 70 karakter, jujur, memancing rasa "
            "penasaran), 5 konsep thumbnail (visual + maksimal 4 kata teks), deskripsi YouTube (dua baris pertama "
            "berfungsi sebagai hook), bab dengan placeholder stempel waktu, dan 15 tag.\n\nNASKAH:\n{script}"
        ),
    },
    "repurpose": {
        "label": "Shorts + community post",
        "needs": "script",
        "prompt": (
            "Dari naskah di bawah, tulis: 3 naskah YouTube Shorts (masing-masing di bawah 45 detik, kalimat "
            "pertama harus kuat) dan 1 postingan tab komunitas yang memancing penasaran tanpa membocorkan "
            "akhir cerita.\n\nNASKAH:\n{script}"
        ),
    },
}


class AIError(Exception):
    pass


def build_system(style_guide, examples):
    parts = [BASE_RULES]
    parts.append("=== PANDUAN GAYA CHANNEL ===\n" + (style_guide.strip() or "(belum ditulis)"))
    if examples:
        parts.append("=== CONTOH NASKAH (tiru suara ini) ===")
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


def generate(provider, model, api_key, system, user, max_tokens=None):
    """Returns {"text", "model", "usage": {input, output, cache_read, cache_write}}."""
    if provider not in PROVIDERS:
        raise AIError(f"Unknown provider '{provider}'")
    if not api_key:
        raise AIError("No API key set. Add one in Settings or set the environment variable.")
    model = model or PROVIDERS[provider]["default_model"]

    if provider == "anthropic":
        # ANTHROPIC_BASE_URL exists so the request shape can be tested against a local fake server.
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        data = _post(
            base + "/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": model,
                "max_tokens": max_tokens or MAX_TOKENS,
                # The system block (rules + style guide + examples) is identical on every call, so it
                # is marked for prompt caching (5-minute lifetime). Cache reads are billed far lower.
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
            },
        )
        u = data.get("usage") or {}
        return {
            "text": "".join(b.get("text", "") for b in data.get("content", []) if b.get("type", "text") == "text"),
            "model": model,
            "usage": {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                      "cache_read": u.get("cache_read_input_tokens", 0) or 0,
                      "cache_write": u.get("cache_creation_input_tokens", 0) or 0},
        }

    data = _post(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        {
            "model": model,
            "max_tokens": max_tokens or 4096,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
    )
    u = data.get("usage") or {}
    return {"text": data["choices"][0]["message"]["content"], "model": model,
            "usage": {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
                      "cache_read": 0, "cache_write": 0}}
