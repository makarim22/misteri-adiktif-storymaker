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
