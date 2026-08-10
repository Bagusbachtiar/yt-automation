#!/usr/bin/env python3
"""
FaunaWorks Pipeline — CustomTkinter desktop wizard
Animal → Titles → Script → Images → Assembly → Preview → Upload

Requirements: pip install customtkinter pillow
"""
import concurrent.futures
import ctypes, ctypes.wintypes
import io, json, os, re, shutil, ssl, subprocess, sys, tempfile, threading, tkinter.messagebox, urllib.request
import cv2
from datetime import datetime, timedelta, timezone
from pathlib import Path

import customtkinter as ctk
from PIL import Image
from tkcalendar import DateEntry as _DateEntry

_CNW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma2:9b"
_HERE        = Path(__file__).parent
SCRIPT_JSON  = _HERE / "script.json"
CANDS_JSON   = _HERE / "image_candidates.json"
IMAGES_DIR   = _HERE / "images"
OUTPUT_MP4   = _HERE / "output.mp4"
VIDEOS_OUT   = _HERE / "videos_output"
_WIB         = timezone(timedelta(hours=7))   # Jakarta — fixed UTC+7, no DST

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode    = ssl.CERT_NONE

_SRC_ORDER = {
    "faunaworks": ["pexels_video","wiki_infobox","wikipedia","pexels","pixabay","inaturalist","gbif","commons","openverse","flickr","wiki_keyword"],
    "science":    ["pexels_video","nasa","wiki_infobox","wikipedia","commons","openverse","pexels","pixabay","wiki_keyword"],
}
SRC_ORDER = _SRC_ORDER["faunaworks"]   # default for any legacy callers
_VID      = {"pexels_video"}
MAX_POOL  = 30
TW, TH    = 160, 220    # thumbnail display size (portrait)

STEPS = ["Animal","Titles","Script","Images","Assembly","Preview","Upload"]

VOICES = [
    ("Emma (British F)",    "bf_emma"),
    ("Isabella (British F)","bf_isabella"),
    ("Heart (American F)",  "af_heart"),
    ("Bella (American F)",  "af_bella"),
    ("Nicole (American F)", "af_nicole"),
    ("Sarah (American F)",  "af_sarah"),
    ("George (British M)",  "bm_george"),
    ("Lewis (British M)",   "bm_lewis"),
    ("Adam (American M)",   "am_adam"),
    ("Michael (American M)","am_michael"),
]
PREVIEW_TEXT  = "This creature has one of the most surprising abilities in the entire animal kingdom."
SAMPLES_DIR   = _HERE / "voice_samples"
def _topics_path(channel: str = "faunaworks") -> Path:
    return _HERE / f"topics_{channel}.txt"

def _tiers_path(channel: str = "faunaworks") -> Path:
    return _HERE / f"topics_tiers_{channel}.json"


def _flash_taskbar(win) -> None:
    """Flash the taskbar button until the user focuses the window."""
    try:
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint)]
        FLASHW_ALL      = 3
        FLASHW_TIMERNOFG = 12
        fi = FLASHWINFO(cbSize=ctypes.sizeof(FLASHWINFO), hwnd=win.winfo_id(),
                        dwFlags=FLASHW_ALL | FLASHW_TIMERNOFG, uCount=0, dwTimeout=0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(fi))
    except Exception:
        pass


def _yt_video_id(url: str) -> str:
    if "/shorts/" in url:
        return url.split("/shorts/")[-1].split("?")[0]
    if "v=" in url:
        return url.split("v=")[-1].split("&")[0]
    return ""


def _load_tiers(channel: str = "faunaworks") -> dict:
    p = _tiers_path(channel)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_tiers(tiers: dict, channel: str = "faunaworks"):
    _tiers_path(channel).write_text(json.dumps(tiers, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_channels() -> dict:
    """Returns {key: display_name} from channels.json."""
    p = _HERE / "channels.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"faunaworks": "FaunaWorks"}


def _load_topics(channel: str = "faunaworks") -> list[tuple[str, bool]]:
    """Returns [(text, is_done), ...] — skips blank lines and comments."""
    p = _topics_path(channel)
    if not p.exists():
        return []
    result = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.upper().startswith("DONE:"):
            result.append((s[5:].strip(), True))
        else:
            result.append((s, False))
    return result


def _save_topics(topics: list[tuple[str, bool]], channel: str = "faunaworks"):
    lines = [f"# {channel} topic queue — managed by the app"]
    for text, done in topics:
        lines.append(f"DONE: {text}" if done else text)
    _topics_path(channel).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _ollama(prompt: str, tokens: int = 600) -> str:
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt,
                       "stream": False, "options": {"num_predict": tokens}}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(OLLAMA_URL, body, {"Content-Type": "application/json"}),
        timeout=120
    ) as r:
        return json.loads(r.read()).get("response", "")


def gen_titles(animal: str, status_cb=None, channel: str = "faunaworks") -> list:
    from wikipedia_fetch import fetch_wikipedia_text
    if status_cb:
        status_cb("Fetching Wikipedia…")
    wiki_snippet = ""
    try:
        wiki_title, wiki_text = fetch_wikipedia_text(animal)
        # Validate article is actually about this topic — fuzzy search can return wrong
        # matches (e.g. "How stars are born" → "How Angels Are Born", a crime film).
        # Require at least one non-trivial topic word to appear in the article title.
        _WIKI_STOP = {"what","when","where","does","have","will","been","they","this","that",
                      "from","with","into","some","born","form","forms","formed","make","made",
                      "happen","happens","work","works","exist","need","needs","live","lives",
                      "grow","grows","cause","causes","found","feel","move","turn","keep","come",
                      "came","goes","look","seem","show","take","much","more","most","also","only",
                      "just","even","well","actually","really","truly","actually","never","always"}
        topic_words = {w for w in animal.lower().split() if len(w) > 3 and w not in _WIKI_STOP}
        wiki_words  = set(wiki_title.lower().split())
        # prefix match handles plurals: "rainbows"/"rainbow", "earthquakes"/"earthquake"
        matched = any(tw.startswith(ww) or ww.startswith(tw) for tw in topic_words for ww in wiki_words)
        if matched:
            wiki_snippet = wiki_text[:2500]
    except SystemExit:
        pass

    grounding = (
        f"\n\nWikipedia reference — use ONLY facts from this text to inspire titles:\n{wiki_snippet}"
        if wiki_snippet else ""
    )
    if status_cb:
        status_cb("Generating titles…")

    if channel == "science":
        raw = _ollama(
            f"You are a YouTube Shorts title writer for CuriosityLab, a general science channel.\n"
            f"Topic: {animal}\n"
            f"Generate 5 title options. Each title MUST be based on a REAL, verifiable scientific fact.\n\n"
            f"STRICT RULES:\n"
            f"- Ground every title in a real fact from the Wikipedia text below\n"
            f"- NO pseudoscience, no invented scenarios, no clickbait exaggeration\n"
            f"- NO vague filler: no Amazing, Incredible, Shocking, Exposed\n"
            f"- Curiosity-gap format — tease something counterintuitive or surprising that's real:\n"
            f"  'Why [Thing] [Surprising Behavior]'\n"
            f"  'This [Object/Force/Element] Can [Surprising Real Fact]'\n"
            f"  'The [Phenomenon] That [Does Something Unexpected]'\n"
            f"  'How [Thing] Actually [Works/Happens]'\n"
            f"  'What Happens When [Surprising Condition]'\n"
            f"- 6-10 words max. No dashes, hyphens, colons, or em dashes\n"
            f"- Each title must cover a different angle{grounding}\n\n"
            f'Return ONLY a JSON array: ["Title 1","Title 2","Title 3","Title 4","Title 5"]', 600
        )
    else:
        raw = _ollama(
            f"You are a YouTube Shorts title writer for FaunaWorks, an animal facts channel.\n"
            f"Animal: {animal}\n"
            f"Generate 5 title options. Each title MUST be based on a REAL, verifiable biological trait, "
            f"ability, behavior, or ecological role this animal actually has.\n\n"
            f"STRICT RULES:\n"
            f"- Ground every title in a real fact from the Wikipedia text below\n"
            f"- NO invented scenarios: no pranks, parties, battles, drama, or human-framing\n"
            f"- NO vague filler: no Amazing, Incredible, Shocking, Exposed\n"
            f"- Curiosity-gap format — tease something real. Proven patterns:\n"
            f"  'This [Animal] Can [Real Ability]'\n"
            f"  'The [Animal] That [Real Behavior]'\n"
            f"  'Why [Animal] [Surprising Fact]'\n"
            f"  'How [Animal] [Protects/Saves/Feeds/Controls] [Ecosystem/Plants/Species]'\n"
            f"  'Why [Ecosystem/Garden/Ocean] Needs [Animal]'\n"
            f"- REQUIRED: at least 2 of the 5 titles must focus on the animal's ecological role or "
            f"benefit (pest control, pollination, food chain, seed dispersal, soil health, etc.)\n"
            f"- 6-10 words max. No dashes, hyphens, colons, or em dashes\n"
            f"- Each title must cover a different angle{grounding}\n\n"
            f'Return ONLY a JSON array: ["Title 1","Title 2","Title 3","Title 4","Title 5"]', 600
        )
    m = re.search(r'\[.*?\]', raw, re.DOTALL)
    if not m:
        raise ValueError(f"No array in response: {raw[:150]}")
    cleaned = re.sub(r',(\s*[\]}])', r'\1', m.group())
    return [t for t in json.loads(cleaned) if isinstance(t, str)][:5]


def brainstorm_topics(theme: str, count: int = 25) -> list[str]:
    raw = _ollama(
        f"Generate a list of {count} specific, engaging science video topics that fit this theme: {theme}\n\n"
        f"RULES:\n"
        f"- Return ONLY a JSON array of topic phrases, no explanation\n"
        f"- Each topic must be a specific question or phenomenon, not a broad subject\n"
        f"- Good examples: 'why ice floats', 'how black holes form', 'why Saturn has rings', 'how plants eat insects'\n"
        f"- Topics should be visual, surprising, or counterintuitive\n"
        f"- Mix space, physics, chemistry, and botany topics\n"
        f"- No pseudoscience, no made-up phenomena\n"
        f"Return ONLY a valid JSON array of strings.", 800
    )
    m = re.search(r'\[.*?\]', raw, re.DOTALL)
    if not m:
        return []
    try:
        cleaned = re.sub(r',(\s*[\]}])', r'\1', m.group())
        return [t for t in json.loads(cleaned) if isinstance(t, str)][:count]
    except Exception:
        return []


def brainstorm_animals(theme: str, count: int = 25) -> list[str]:
    raw = _ollama(
        f"Generate a list of {count} specific animal species that fit this theme: {theme}\n\n"
        f"RULES:\n"
        f"- Return ONLY a JSON array of animal common names, no explanation\n"
        f"- Use specific names, not vague groups: 'bluefin tuna' not 'fish', 'monarch butterfly' not 'insect'\n"
        f"- Include a mix of well-known and lesser-known real species\n"
        f"- Each must be a real species a wildlife photographer could photograph in the wild\n"
        f'Return ONLY: ["Animal 1", "Animal 2", ...]',
        tokens=900,
    )
    m = re.search(r'\[.*?\]', raw, re.DOTALL)
    if not m:
        return []
    try:
        cleaned = re.sub(r',(\s*[\]}])', r'\1', m.group())
        return [a for a in json.loads(cleaned) if isinstance(a, str)][:30]
    except Exception:
        return []


def _extract(src: str, entry) -> tuple:
    """Return (url, thumb) from a candidates entry."""
    if isinstance(entry, dict):
        url   = entry.get("url", "")
        thumb = entry.get("thumb")
    else:
        url, thumb = entry, None
    return url, thumb


def pool_from(candidates: dict, src_order: list | None = None) -> list:
    seen, pool = set(), []

    def _take_one(data: dict, sources: list) -> bool:
        for src in sources:
            for entry in data["sources"].get(src, []):
                url, thumb = _extract(src, entry)
                if src in _VID and not thumb:
                    continue
                if url and url not in seen:
                    seen.add(url); pool.append((src, url, thumb)); return True
        return False

    _order = src_order or SRC_ORDER

    # Phase 1 — per line: 1 nasa/wiki photo + 1 biodiversity photo + 1 video + 1 stock photo
    # wiki_keyword excluded from Phase 1 (returns wrong Wikipedia articles for animal names)
    for data in candidates.values():
        _take_one(data, ["nasa", "wiki_infobox", "wikipedia"])
        _take_one(data, ["inaturalist", "gbif"])
        _take_one(data, ["pexels_video"])
        _take_one(data, ["pexels", "pixabay"])

    # Phase 2 — fill remaining slots up to MAX_POOL
    for src in _order:
        if len(pool) >= MAX_POOL: break
        for data in candidates.values():
            for entry in data["sources"].get(src, []):
                url, thumb = _extract(src, entry)
                if src in _VID and not thumb: continue
                if url and url not in seen:
                    seen.add(url); pool.append((src, url, thumb))
                    if len(pool) >= MAX_POOL: break
            if len(pool) >= MAX_POOL: break

    pool.sort(key=lambda x: 0 if x[0] in _VID else 1)
    return pool


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "yt-auto/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
        return r.read()


def _slug(title: str) -> str:
    s = re.sub(r'[^\w\s-]', '', title.lower())
    return re.sub(r'\s+', '-', s).strip('-')[:60]


# ── Icon ──────────────────────────────────────────────────────────────────────
def _ensure_icon() -> Path | None:
    ico_path = _HERE / "faunaworks.ico"
    if ico_path.exists():
        return ico_path
    try:
        from PIL import Image, ImageDraw
        def _draw(s: int) -> Image.Image:
            img = Image.new("RGBA", (s, s), (34, 197, 94, 255))
            d = ImageDraw.Draw(img)
            # paw: palm + 4 toes
            cx, cy = s // 2, s * 58 // 100
            pr = s * 22 // 100
            d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill="white")
            tr = s * 11 // 100
            for ox, oy in [(-s*24//100, -s*32//100),
                           (-s*8//100,  -s*40//100),
                           ( s*8//100,  -s*40//100),
                           ( s*24//100, -s*32//100)]:
                d.ellipse([cx+ox-tr, cy+oy-tr, cx+ox+tr, cy+oy+tr], fill="white")
            return img
        base = _draw(256)
        base.save(str(ico_path), format="ICO",
                  sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
        return ico_path
    except Exception:
        return None


# ── App shell ─────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("FaunaWorks Pipeline")
        self.geometry("1040x780")
        self.resizable(True, True)
        # Windows: set AppUserModelID so taskbar shows our icon, not the generic Python one
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("faunaworks.pipeline.v1")
        except Exception:
            pass
        ico = _ensure_icon()
        if ico:
            # defer until after window is mapped — iconbitmap fails if called too early in CTk
            self.after(200, lambda: self._set_icon(str(ico)))

    def _set_icon(self, ico_path: str):
        try:
            self.iconbitmap(ico_path)
        except Exception:
            pass
        # shared pipeline state
        self.animal        = ""
        self.voice         = "bf_emma"
        self.channel       = next(iter(_load_channels()))
        self.titles        = []
        self.chosen_title  = ""
        self.script        = {}
        self.pool          = []   # [(src, url, thumb_url)]
        self.assigned      = {}   # {lid: pool_idx}
        self._kokoro       = None  # cached Kokoro instance for voice preview

        hdr = ctk.CTkFrame(self, height=50, fg_color="#0e0e20")
        hdr.pack(fill="x")
        self._hdrlbl = ctk.CTkLabel(hdr, text="FaunaWorks Pipeline",
                                    font=("Arial", 16, "bold"), text_color="white")
        self._hdrlbl.pack(side="left", padx=16)
        self._steplbl = ctk.CTkLabel(hdr, text="", font=("Arial", 12), text_color="#888888")
        self._steplbl.pack(side="right", padx=16)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=10)

        cls_list = (S1_Animal, S2_Titles, S3_Script, S4_Images,
                    S5_Assembly, S6_Preview, S7_Upload)
        self._frames = {Cls: Cls(body, self) for Cls in cls_list}
        self.goto(S1_Animal)

    def goto(self, cls):
        for f in self._frames.values():
            f.pack_forget()
        idx = list(self._frames).index(cls)
        self._steplbl.configure(text=f"Step {idx+1} / {len(STEPS)} — {STEPS[idx]}")
        f = self._frames[cls]
        f.pack(fill="both", expand=True)
        f.on_enter()


# ── Base step frame ───────────────────────────────────────────────────────────
class Base(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app  = app
        self._pane = None

    def _reset(self):
        if self._pane:
            self._pane.destroy()
        self._pane = ctk.CTkFrame(self, fg_color="transparent")
        self._pane.pack(fill="both", expand=True)
        return self._pane

    def on_enter(self):
        pass


def _h1(p, text):
    ctk.CTkLabel(p, text=text, font=("Arial", 20, "bold")).pack(pady=(22, 6))

def _sub(p, text):
    ctk.CTkLabel(p, text=text, text_color="#888888", font=("Arial", 12)).pack()

def _back_btn(row, app, cls):
    ctk.CTkButton(row, text="← Back", width=110, height=36,
                  fg_color="#3a3a3a",
                  command=lambda: app.goto(cls)).pack(side="left", padx=6)


_TIER_DOT = {"green": "#44ff66", "yellow": "#ffee44", "red": "#ff5544"}

# ── S1: Animal input ──────────────────────────────────────────────────────────
class S1_Animal(Base):
    def on_enter(self):
        p = self._reset()

        # Channel selector — top of step
        channels    = _load_channels()
        ch_names    = list(channels.values())
        cur_ch_name = channels.get(self.app.channel, ch_names[0] if ch_names else "FaunaWorks")
        self._ch_var = ctk.StringVar(value=cur_ch_name)
        crow = ctk.CTkFrame(p, fg_color="transparent")
        crow.pack(pady=(10, 0))
        ctk.CTkLabel(crow, text="Channel:", font=("Arial", 13)).pack(side="left", padx=(0, 8))
        ctk.CTkOptionMenu(crow, values=ch_names or ["FaunaWorks"], variable=self._ch_var,
                          width=200, command=self._on_channel_change).pack(side="left")

        # Channel avatar + email row
        self._ch_card = ctk.CTkFrame(p, fg_color="transparent")
        self._ch_card.pack(pady=(4, 0))
        self._ch_avatar_lbl = ctk.CTkLabel(self._ch_card, text="○", width=36, height=36,
                                            font=("Arial", 20), text_color="#7777aa")
        self._ch_avatar_lbl.pack(side="left", padx=(0, 6))
        self._ch_email_lbl = ctk.CTkLabel(self._ch_card, text="fetching account…",
                                           font=("Arial", 11), text_color="#8888aa")
        self._ch_email_lbl.pack(side="left")
        self._ch_avatar_img = None  # prevent GC

        channels = _load_channels()
        ch_key = next((k for k, v in channels.items() if v == cur_ch_name), self.app.channel)
        threading.Thread(target=self._fetch_s1_ch_info, args=(ch_key,), daemon=True).start()

        _is_sci = getattr(self.app, "channel", "faunaworks") == "science"
        _h1(p, "What topic?" if _is_sci else "What animal?")
        _sub(p, "Type any science topic — AI finds the best angle" if _is_sci
             else "Type any animal — AI finds the best angle")
        self._e = ctk.CTkEntry(p, placeholder_text="e.g. why ice floats" if _is_sci else "e.g. elephant",
                               width=320, font=("Arial", 15), height=42)
        self._e.pack(pady=(16, 8))
        if getattr(self.app, "animal", ""):
            self._e.insert(0, self.app.animal)
        self._e.focus()
        self._e.bind("<Return>", lambda _: self._go())

        # Topic queue
        self._build_queue(p)

        # Voice selector row
        vrow = ctk.CTkFrame(p, fg_color="transparent")
        vrow.pack(pady=(4, 4))
        ctk.CTkLabel(vrow, text="Voice:", font=("Arial", 13)).pack(side="left", padx=(0, 8))
        voice_names = [v[0] for v in VOICES]
        cur_name = next((v[0] for v in VOICES if v[1] == self.app.voice), voice_names[0])
        self._voice_var = ctk.StringVar(value=cur_name)
        ctk.CTkOptionMenu(vrow, values=voice_names, variable=self._voice_var,
                          width=230, command=self._on_voice_change).pack(side="left", padx=(0, 10))
        self._prev_btn = ctk.CTkButton(vrow, text="Play Sample", width=110, height=34,
                                       fg_color="#3a3a5a", command=self._preview_voice)
        self._prev_btn.pack(side="left")

        self._prev_lbl = ctk.CTkLabel(p, text="", text_color="#888888", font=("Arial", 11))
        self._prev_lbl.pack(pady=(0, 8))

        self._btn = ctk.CTkButton(p, text="Generate Titles →",
                                  width=210, height=42, command=self._go)
        self._btn.pack()
        self._lbl = ctk.CTkLabel(p, text="", text_color="#888888")
        self._lbl.pack(pady=8)

        self._build_recent_uploads(p)

        # Pre-generate all missing voice samples in background
        threading.Thread(target=self._prepare_samples, daemon=True).start()

    # ── Recent uploads ───────────────────────────────────────────────────────
    def _build_recent_uploads(self, p):
        hist_path = _HERE / "upload_history.json"
        if not hist_path.exists():
            return
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:
            return
        recent = [e for e in hist if e.get("channel") == getattr(self.app, "channel", "faunaworks")][:5]
        if not recent:
            return
        outer = ctk.CTkFrame(p, fg_color="#111122", corner_radius=8)
        outer.pack(fill="x", padx=60, pady=(0, 6))
        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(hdr, text="Recent Uploads", font=("Arial", 11, "bold"),
                     text_color="#aaaaaa").pack(side="left")
        for entry in recent:
            row = ctk.CTkFrame(outer, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=1)
            is_sched = entry.get("status") == "scheduled"
            if is_sched and entry.get("publish_at"):
                try:
                    from datetime import datetime as _dt
                    pub = _dt.strptime(entry["publish_at"], "%Y-%m-%d %H:%M")
                    if pub <= _dt.now():
                        is_sched = False
                except Exception:
                    pass
            time_str = entry.get("publish_at", entry.get("uploaded_at", "")) if is_sched else entry.get("uploaded_at", "")
            status_tag = " [Sched]" if is_sched else ""
            ctk.CTkLabel(row, text=time_str, font=("Arial", 10),
                         text_color="#556655" if is_sched else "#555566",
                         width=120, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=entry.get("title", "") + status_tag, font=("Arial", 11),
                         text_color="#aaccaa" if is_sched else "#cccccc",
                         anchor="w").pack(side="left", padx=(4, 0))
            voice_token = entry.get("voice", "")
            voice_name  = next((v[0].split(" (")[0] for v in VOICES if v[1] == voice_token), "")
            if voice_name:
                ctk.CTkLabel(row, text=voice_name, font=("Arial", 10),
                             text_color="#557799", anchor="e").pack(side="right", padx=(4, 0))
        ctk.CTkFrame(outer, fg_color="transparent", height=4).pack()

    # ── Topic queue ──────────────────────────────────────────────────────────
    def _build_queue(self, p):
        outer = ctk.CTkFrame(p, fg_color="#111122", corner_radius=8)
        outer.pack(fill="x", padx=60, pady=(0, 4))

        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(hdr, text="Topic Queue", font=("Arial", 11, "bold"),
                     text_color="#aaaaaa").pack(side="left")
        ctk.CTkLabel(hdr, text="click to use", font=("Arial", 10),
                     text_color="#555555").pack(side="left", padx=6)

        self._qframe = ctk.CTkScrollableFrame(outer, height=110, fg_color="transparent")
        self._qframe.pack(fill="x", padx=4)

        add_row = ctk.CTkFrame(outer, fg_color="transparent")
        add_row.pack(fill="x", padx=8, pady=(2, 2))
        _qadd_ph = "Add topic…" if getattr(self.app, "channel", "faunaworks") == "science" else "Add animal…"
        self._qadd = ctk.CTkEntry(add_row, placeholder_text=_qadd_ph,
                                  height=28, font=("Arial", 12))
        self._qadd.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._qadd.bind("<Return>", lambda _: self._add_topic())
        ctk.CTkButton(add_row, text="Add", width=54, height=28,
                      command=self._add_topic).pack(side="left")

        ctk.CTkFrame(outer, fg_color="#1a1a2a", height=1).pack(fill="x", padx=8, pady=(4, 0))

        disc_row = ctk.CTkFrame(outer, fg_color="transparent")
        disc_row.pack(fill="x", padx=8, pady=(4, 2))
        _disc_ph = "Discover theme… e.g. quantum physics" if getattr(self.app, "channel", "faunaworks") == "science" \
                   else "Discover theme… e.g. ocean creatures"
        self._disc_entry = ctk.CTkEntry(disc_row, placeholder_text=_disc_ph,
                                        height=28, font=("Arial", 12))
        self._disc_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._disc_entry.bind("<Return>", lambda _: self._discover_topics())
        self._disc_btn = ctk.CTkButton(disc_row, text="Discover ↺", width=90, height=28,
                                       command=self._discover_topics)
        self._disc_btn.pack(side="left")

        self._disc_lbl = ctk.CTkLabel(outer, text="", text_color="#666688", font=("Arial", 10))
        self._disc_lbl.pack(pady=(0, 4))

        self._refresh_queue()

    def _refresh_queue(self):
        for w in self._qframe.winfo_children():
            w.destroy()
        topics = _load_topics(self.app.channel)
        tiers  = _load_tiers(self.app.channel)
        pending = [(i, t) for i, (t, done) in enumerate(topics) if not done]
        if not pending:
            ctk.CTkLabel(self._qframe, text="No pending topics",
                         text_color="#444444", font=("Arial", 11)).pack(pady=4)
            return
        self._dot_labels = {}
        unchecked = []
        for orig_idx, text in pending:
            row = ctk.CTkFrame(self._qframe, fg_color="transparent")
            row.pack(fill="x", pady=1)
            tier = tiers.get(text)
            dot = ctk.CTkLabel(row, text="●", font=("Arial", 12),
                               text_color=_TIER_DOT.get(tier, "#333355"), width=18)
            dot.pack(side="left", padx=(2, 0))
            self._dot_labels[text] = dot
            if not tier:
                unchecked.append(text)
            ctk.CTkButton(row, text=text, anchor="w", height=26,
                          font=("Arial", 12), fg_color="#1e1e35",
                          hover_color="#2a2a50",
                          command=lambda t=text: self._pick_topic(t)
                          ).pack(side="left", fill="x", expand=True, padx=(0, 4))
            ctk.CTkButton(row, text="✕", width=26, height=26,
                          fg_color="#3a2020", hover_color="#5a3030",
                          font=("Arial", 11),
                          command=lambda i=orig_idx: self._delete_topic(i)
                          ).pack(side="left")
        if unchecked:
            threading.Thread(target=self._bg_check_tiers, args=(unchecked,), daemon=True).start()

    def _bg_check_tiers(self, topics: list):
        from coverage_check import check_coverage
        import concurrent.futures
        ch = self.app.channel
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(check_coverage, t, ch): t for t in topics}
            for fut in concurrent.futures.as_completed(futures):
                t = futures[fut]
                try:
                    cov  = fut.result()
                    tier = cov.get("video_tier", "red")
                    tiers = _load_tiers(ch)
                    tiers[t] = tier
                    _save_tiers(tiers, ch)
                    dot = self._dot_labels.get(t)
                    if dot:
                        color = _TIER_DOT.get(tier, "#333355")
                        self.after(0, lambda d=dot, c=color: d.configure(text_color=c))
                except Exception:
                    pass

    def _pick_topic(self, text: str):
        self._e.delete(0, "end")
        self._e.insert(0, text)
        self._e.focus()

    def _add_topic(self):
        text = self._qadd.get().strip()
        if not text:
            return
        topics = _load_topics(self.app.channel)
        if any(t == text for t, _ in topics):
            self._qadd.delete(0, "end")
            return
        topics.append((text, False))
        _save_topics(topics, self.app.channel)
        self._qadd.delete(0, "end")
        self._refresh_queue()

    def _delete_topic(self, idx: int):
        topics = _load_topics(self.app.channel)
        if 0 <= idx < len(topics):
            topics.pop(idx)
            _save_topics(topics, self.app.channel)
        self._refresh_queue()

    def _prepare_samples(self):
        """Generate and cache WAV samples for all voices. Runs once in background on S1 entry."""
        SAMPLES_DIR.mkdir(exist_ok=True)
        missing = [(name, vid) for name, vid in VOICES
                   if not (SAMPLES_DIR / f"{vid}.wav").exists()]
        if not missing:
            return
        try:
            import soundfile as sf
            from kokoro_onnx import Kokoro

            model_p  = _HERE / "kokoro-v1.0.int8.onnx"
            voices_p = _HERE / "voices-v1.0.bin"
            if not model_p.exists() or not voices_p.exists():
                self.after(0, lambda: self._prev_lbl.configure(
                    text="Model files missing — run generate_audio.py first",
                    text_color="#ff8855"))
                return

            if self.app._kokoro is None:
                n = len(missing)
                self.after(0, lambda: self._prev_lbl.configure(
                    text=f"Loading voice model… (preparing {n} sample(s))"))
                self.app._kokoro = Kokoro(str(model_p), str(voices_p))

            total = len(missing)
            for i, (name, vid) in enumerate(missing, 1):
                sample_path = SAMPLES_DIR / f"{vid}.wav"
                if sample_path.exists():
                    continue
                self.after(0, lambda i=i, t=total, nm=name: self._prev_lbl.configure(
                    text=f"Preparing sample {i}/{t}: {nm}…"))
                samples, sr = self.app._kokoro.create(
                    PREVIEW_TEXT, voice=vid, speed=0.85, lang="en-us")
                sf.write(str(sample_path), samples, sr)

            self.after(0, lambda: self._prev_lbl.configure(text=""))
        except Exception as e:
            self.after(0, lambda err=e: self._prev_lbl.configure(
                text=f"Sample prep error: {err}", text_color="#ff8855"))

    def _on_channel_change(self, display_name: str):
        channels = _load_channels()
        for k, v in channels.items():
            if v == display_name:
                self.app.channel = k
                pipeline_title = f"{display_name} Pipeline"
                self.app.title(pipeline_title)
                self.app._hdrlbl.configure(text=pipeline_title)
                self.app.goto(S1_Animal)   # rebuild so labels/placeholders match channel
                return

    def _fetch_s1_ch_info(self, ch_key: str):
        try:
            r = subprocess.run(
                [sys.executable, "upload.py", "--info", "--channel", ch_key],
                capture_output=True, text=True, encoding="utf-8",
                cwd=_HERE, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                timeout=30, creationflags=_CNW,
            )
            thumb_url = ""
            for line in r.stdout.splitlines():
                if line.startswith("THUMBNAIL:"):
                    thumb_url = line[len("THUMBNAIL:"):].strip()
            ctk_img = None
            if thumb_url:
                try:
                    img = Image.open(io.BytesIO(fetch_bytes(thumb_url))).convert("RGBA")
                    img = img.resize((36, 36), Image.LANCZOS)
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(36, 36))
                except Exception:
                    pass
            def update(ci=ctk_img):
                try:
                    if ci:
                        self._ch_avatar_img = ci
                        self._ch_avatar_lbl.configure(image=ci, text="")
                    self._ch_email_lbl.configure(text="")
                except Exception:
                    pass
            self.after(0, update)
        except Exception:
            pass

    def _on_voice_change(self, name: str):
        self.app.voice = next(v[1] for v in VOICES if v[0] == name)

    def _preview_voice(self):
        name = self._voice_var.get()
        voice_id = next(v[1] for v in VOICES if v[0] == name)
        sample_path = SAMPLES_DIR / f"{voice_id}.wav"
        self._prev_btn.configure(state="disabled", text="Playing…")

        def run():
            try:
                import winsound
                if not sample_path.exists():
                    self.after(0, lambda: self._prev_btn.configure(
                        state="normal", text="Play Sample"))
                    self.after(0, lambda: self._prev_lbl.configure(
                        text="Still preparing — wait a moment", text_color="#ff8855"))
                    return
                winsound.PlaySound(str(sample_path), winsound.SND_FILENAME)
                self.after(0, lambda: self._prev_btn.configure(state="normal", text="Play Sample"))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._prev_lbl.configure(text=f"Error: {err}", text_color="#ff5555"),
                    self._prev_btn.configure(state="normal", text="Play Sample"),
                ))

        threading.Thread(target=run, daemon=True).start()

    def _go(self):
        a = self._e.get().strip()
        if not a:
            return
        self.app.animal = a
        self.app.script = {}   # clear stale script from previous video
        self._on_voice_change(self._voice_var.get())   # ensure voice saved
        # Clear stale temp files so old media doesn't bleed into new video
        for d in (IMAGES_DIR, _HERE / "audio"):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(exist_ok=True)
        CANDS_JSON.unlink(missing_ok=True)
        self._btn.configure(state="disabled", text="Working…")
        self._lbl.configure(text="Fetching Wikipedia…", text_color="#888888")

        def _status(msg):
            self.after(0, lambda m=msg: self._lbl.configure(text=m, text_color="#888888"))

        def run():
            try:
                _status("Checking image/video coverage…")
                from coverage_check import check_coverage
                cov        = check_coverage(a, channel=self.app.channel)
                video_tier = cov.get("video_tier", "red")
                video      = cov.get("video", 0)
                # Cache tier dot for queue display (video is priority)
                tiers = _load_tiers(self.app.channel)
                tiers[a] = video_tier
                _save_tiers(tiers, self.app.channel)

                thin_video = video_tier != "green"
                if thin_video:
                    color      = "#ff5544" if video_tier == "red" else "#ffee44"
                    video_line = f"  Video: {video:,} Pexels clips  (tier: {video_tier})"
                    msg = (
                        f"Coverage for '{a}':\n{video_line}\n\n"
                        "thin video coverage = mostly static images in final video"
                        "\n\nProceed with script generation anyway?"
                    )
                    proceed = [None]
                    ev = threading.Event()
                    def _ask(msg=msg):
                        proceed[0] = tkinter.messagebox.askyesno("Coverage Warning", msg)
                        ev.set()
                    self.after(0, _ask)
                    ev.wait()
                    if not proceed[0]:
                        self.after(0, lambda c=color, v=video, vt=video_tier: (
                            self._lbl.configure(
                                text=f"Coverage: {vt} video ({v} clips) — cancelled", text_color=c),
                            self._btn.configure(state="normal", text="Generate Titles →"),
                        ))
                        return

                self.app.titles = gen_titles(a, status_cb=_status, channel=self.app.channel)
                self.after(0, lambda: (_flash_taskbar(self.app), self.app.goto(S2_Titles)))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._lbl.configure(text=f"Error: {err}", text_color="#ff5555"),
                    self._btn.configure(state="normal", text="Generate Titles →"),
                ))

        threading.Thread(target=run, daemon=True).start()

    def _discover_topics(self):
        theme = self._disc_entry.get().strip()
        if not theme:
            return
        self._disc_btn.configure(state="disabled", text="Working…")
        self._disc_lbl.configure(text="Asking Ollama…", text_color="#666688")

        def run():
            try:
                animals = (brainstorm_topics(theme, count=30) if self.app.channel == "science"
                           else brainstorm_animals(theme, count=30))
                if not animals:
                    self.after(0, lambda: (
                        self._disc_lbl.configure(text="No animals returned — try rephrasing", text_color="#ff5555"),
                        self._disc_btn.configure(state="normal", text="Discover ↺"),
                    ))
                    return
                self.after(0, lambda n=len(animals): self._disc_lbl.configure(
                    text=f"Got {n} candidates — checking coverage…", text_color="#666688"))

                from coverage_check import check_coverage
                results = []
                done = [0]
                total = len(animals)
                ch = self.app.channel
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                    futures = {ex.submit(check_coverage, a, ch): a for a in animals}
                    for fut in concurrent.futures.as_completed(futures):
                        results.append(fut.result())
                        done[0] += 1
                        self.after(0, lambda d=done[0], t=total: self._disc_lbl.configure(
                            text=f"Checking coverage… {d}/{t}", text_color="#666688"))

                # Science: filter on photo tier (NASA/Commons) — portrait Pexels videos are rare
                # for abstract topics and not the primary source. Fauna: filter on video tier.
                _qual = (lambda r: r["tier"]) if ch == "science" else (lambda r: r.get("video_tier", r["tier"]))
                good    = [r for r in results if _qual(r) != "red"]
                skipped = len(results) - len(good)
                good.sort(key=lambda r: ({"green": 0, "yellow": 1}[_qual(r)],
                                         -r.get("video", 0)))

                ch = self.app.channel
                topics = _load_topics(ch)
                existing = {t.lower() for t, _ in topics}
                tiers = _load_tiers(ch)
                added = 0
                for r in good:
                    if r["animal"].lower() not in existing:
                        topics.append((r["animal"], False))
                        existing.add(r["animal"].lower())
                        added += 1
                    tiers[r["animal"]] = r.get("video_tier", r["tier"])
                _save_topics(topics, ch)
                _save_tiers(tiers, ch)

                skip_txt = f" | {skipped} thin skipped" if skipped else ""
                self.after(0, lambda a=added, s=skip_txt: (
                    self._disc_lbl.configure(text=f"Added {a} new{s}", text_color="#44bb66"),
                    self._disc_btn.configure(state="normal", text="Discover ↺"),
                    self._disc_entry.delete(0, "end"),
                    self._refresh_queue(),
                ))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._disc_lbl.configure(text=f"Error: {err}", text_color="#ff5555"),
                    self._disc_btn.configure(state="normal", text="Discover ↺"),
                ))

        threading.Thread(target=run, daemon=True).start()


# ── S2: Title selection ───────────────────────────────────────────────────────
class S2_Titles(Base):
    def on_enter(self):
        p = self._reset()
        _h1(p, "Pick a title")
        _is_sci = getattr(self.app, "channel", "faunaworks") == "science"
        _sub(p, f"{'Topic' if _is_sci else 'Animal'}: {self.app.animal}")
        # Science: prepend raw topic as first option (already a good title as-is)
        display_titles = self.app.titles[:]
        if _is_sci:
            topic = self.app.animal
            if topic not in display_titles:
                display_titles.insert(0, topic)
            display_titles = display_titles[:5]
        var = ctk.StringVar(value=display_titles[0] if display_titles else "")
        ctk.CTkFrame(p, height=10, fg_color="transparent").pack()
        for t in display_titles:
            row = ctk.CTkFrame(p, fg_color="#1b1b2e", corner_radius=8)
            row.pack(fill="x", padx=50, pady=4)
            ctk.CTkRadioButton(row, text=t, variable=var, value=t,
                               font=("Arial", 13)).pack(anchor="w", padx=14, pady=9)
        ctk.CTkFrame(p, height=8, fg_color="transparent").pack()
        btn_row = ctk.CTkFrame(p, fg_color="transparent")
        btn_row.pack()
        _back_btn(btn_row, self.app, S1_Animal)
        self._regen_btn = ctk.CTkButton(btn_row, text="Regenerate ↺", width=140, height=36,
                                        fg_color="#2a2a1a", hover_color="#3a3a28",
                                        command=self._regen)
        self._regen_btn.pack(side="left", padx=6)
        self._btn = ctk.CTkButton(btn_row, text="Generate Script →",
                                  width=200, height=36,
                                  command=lambda: self._go(var.get()))
        self._btn.pack(side="left", padx=6)
        self._lbl = ctk.CTkLabel(p, text="", text_color="#888888")
        self._lbl.pack(pady=4)
        self._log = ctk.CTkTextbox(p, height=110, font=("Consolas", 11))
        self._log.pack(fill="x", padx=40, pady=4)
        self._log.configure(state="disabled")

    def _append(self, text: str):
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _regen(self):
        self._regen_btn.configure(state="disabled", text="Working…")
        self._lbl.configure(text="Generating fresh titles…", text_color="#888888")

        def run():
            try:
                self.app.titles = gen_titles(self.app.animal, channel=self.app.channel)
                self.after(0, lambda: (_flash_taskbar(self.app), self.app.goto(S2_Titles)))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._lbl.configure(text=f"Error: {err}", text_color="#ff5555"),
                    self._regen_btn.configure(state="normal", text="Regenerate ↺"),
                ))

        threading.Thread(target=run, daemon=True).start()

    def _go(self, title: str):
        if not title:
            return
        self.app.chosen_title = title
        # Science: title IS the topic — sync app.animal so grounding matches chosen direction
        if getattr(self.app, "channel", "faunaworks") == "science":
            self.app.animal = title
        self._btn.configure(state="disabled", text="Generating script…")
        self._lbl.configure(text="Running generate_script.py…", text_color="#888888")
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

        def run():
            try:
                proc = subprocess.Popen(
                    [sys.executable, "generate_script.py", self.app.animal,
                     "--title", title, "--channel", self.app.channel],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", cwd=_HERE,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    creationflags=_CNW,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.after(0, lambda l=line: self._append(l))
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError(f"generate_script.py exited {proc.returncode} — see log above")
                self.app.script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
                # Catch LLM returning wrong topic (uses "animal" for faunaworks, "topic" for science)
                got = (self.app.script.get("animal") or self.app.script.get("topic", "")).strip().lower()
                want = self.app.animal.strip().lower()
                if got and got != want:
                    raise RuntimeError(
                        f"Script is about '{got}', not '{self.app.animal}'. "
                        "Ollama returned wrong content — try generating again."
                    )
                self.app.script["title"] = title
                SCRIPT_JSON.write_text(json.dumps(self.app.script, indent=2, ensure_ascii=False), encoding="utf-8")
                self.after(0, lambda: (_flash_taskbar(self.app), self.app.goto(S3_Script)))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._lbl.configure(text=f"Error: {err}", text_color="#ff5555"),
                    self._btn.configure(state="normal", text="Generate Script →"),
                ))

        threading.Thread(target=run, daemon=True).start()


# ── S3: Script review ─────────────────────────────────────────────────────────
class S3_Script(Base):
    def on_enter(self):
        p = self._reset()
        sc = self.app.script
        # Guard: if script is stale or mismatched, send user back rather than silently showing wrong content
        got = (sc.get("animal") or sc.get("topic", "")).strip().lower()
        want = getattr(self.app, "animal", "").strip().lower()
        if not sc.get("lines") or (got and want and got != want):
            ctk.CTkLabel(p, text=f"Script mismatch (got '{got}', expected '{want}'). Go back.",
                         text_color="#ff5555").pack(pady=20)
            _back_btn(p, self.app, S2_Titles)
            return
        _h1(p, "Review Script")
        _sub(p, f"Title: {sc.get('title', '')}")
        scroll = ctk.CTkScrollableFrame(p, label_text="Narration lines — click to edit")
        scroll.pack(fill="both", expand=True, padx=16, pady=10)
        self._entries = []
        for line in sc.get("lines", []):
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=f"{line['id']}.", width=28,
                         font=("Arial", 12, "bold")).pack(side="left")
            e = ctk.CTkEntry(row, font=("Arial", 12), height=32)
            e.insert(0, line["text"])
            e.pack(side="left", fill="x", expand=True, padx=4)
            self._entries.append((line["id"], e))
        btn_row = ctk.CTkFrame(p, fg_color="transparent")
        btn_row.pack(pady=10)
        _back_btn(btn_row, self.app, S2_Titles)
        ctk.CTkButton(btn_row, text="Approve Script →", width=200, height=36,
                      command=self._approve).pack(side="left", padx=6)

    def _approve(self):
        sc = self.app.script
        for lid, e in self._entries:
            txt = e.get().strip()
            for ln in sc["lines"]:
                if ln["id"] == lid:
                    ln["text"] = txt
        SCRIPT_JSON.write_text(json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
        self.app.script = sc
        self.app.goto(S4_Images)


# ── S4: Image assignment ──────────────────────────────────────────────────────
class S4_Images(Base):
    def on_enter(self):
        p = self._reset()
        self.app.pool     = []
        self.app.assigned = {}
        self._thumbs      = []   # CTkImage refs — prevent GC
        self._thumb_imgs  = {}   # pool_idx → CTkImage (for preview)
        self._preview_imgs = []  # preview modal CTkImage refs
        self._cells       = {}   # pool_idx → (cell_frame, label)
        self._line_btns   = {}   # lid → button
        self._sel         = ""   # selected line id
        self._cur_cols    = 0
        self._grid_items  = []
        self._left        = None

        _h1(p, "Assign Images")
        self._stlbl = ctk.CTkLabel(p, text="Fetching candidates…", text_color="#888888")
        self._stlbl.pack()

        # pack footer BEFORE split so split gets remaining space
        bot = ctk.CTkFrame(p, fg_color="transparent")
        bot.pack(fill="x", pady=8, side="bottom")
        _back_btn(bot, self.app, S3_Script)
        self._appbtn = ctk.CTkButton(bot, text="Approve & Download →",
                                     width=220, height=36, state="disabled",
                                     command=self._approve)
        self._appbtn.pack(side="right", padx=8)

        split = ctk.CTkFrame(p, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=8, pady=6)

        # left: line list — full text, wrapping
        left = ctk.CTkScrollableFrame(split, width=340, label_text="Lines")
        self._left = left
        left.pack(side="left", fill="y", padx=(0, 6))
        for ln in self.app.script.get("lines", []):
            lid = str(ln["id"])
            kw  = ln.get("image_keyword", "")
            cell = ctk.CTkFrame(left, fg_color="#222233", corner_radius=6, cursor="hand2")
            cell.pack(fill="x", pady=2)
            lbl = ctk.CTkLabel(cell, text=f"{lid}. {ln['text']}",
                               anchor="w", justify="left", wraplength=295,
                               font=("Arial", 11))
            lbl.pack(fill="x", padx=8, pady=(8, 2))
            kw_lbl = ctk.CTkLabel(cell, text=f"  ↳ {kw}" if kw else "",
                                  anchor="w", font=("Arial", 10), text_color="#5566aa")
            kw_lbl.pack(fill="x", padx=8, pady=(0, 6))
            for w in (cell, lbl, kw_lbl):
                w.bind("<Button-1>", lambda e, l=lid: self._sel_line(l))
            self._line_btns[lid] = cell

        # right: image grid
        self._grid = ctk.CTkScrollableFrame(
            split, label_text="Candidates — select a line, then click an image")
        self._grid.pack(side="left", fill="both", expand=True)

        self._setup_scroll()

        threading.Thread(target=self._load, daemon=True).start()

    def _setup_scroll(self):
        """Route mouse wheel to whichever panel the cursor is over."""
        def on_wheel(event):
            x, y = event.x_root, event.y_root
            for sf in (self._left, self._grid):
                try:
                    if not sf.winfo_exists():
                        continue
                    sx, sy = sf.winfo_rootx(), sf.winfo_rooty()
                    if sx <= x <= sx + sf.winfo_width() and sy <= y <= sy + sf.winfo_height():
                        sf._parent_canvas.yview_scroll(int(-1 * (event.delta / 40)), "units")
                        return
                except Exception:
                    pass
        self.app.bind_all("<MouseWheel>", on_wheel)

    def _load(self):
        try:
            self.after(0, lambda: self._stlbl.configure(text="Running fetch_images.py…"))
            r = subprocess.run(
                [sys.executable, "fetch_images.py", "--channel", self.app.channel],
                capture_output=True, text=True, encoding="utf-8", cwd=Path(__file__).parent,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=_CNW,
            )
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout)[-400:])
            cands = json.loads(CANDS_JSON.read_text(encoding="utf-8"))
            pool  = pool_from(cands, _SRC_ORDER.get(self.app.channel, SRC_ORDER))
            self.app.pool = pool

            items = []
            for idx, (src, url, thumb) in enumerate(pool):
                preview = thumb if thumb else url
                self.after(0, lambda n=idx+1, t=len(pool):
                           self._stlbl.configure(text=f"Loading thumbnails {n}/{t}…"))
                try:
                    data = fetch_bytes(preview)
                    img  = Image.open(io.BytesIO(data)).convert("RGB")
                    img.thumbnail((TW, TH))
                    items.append((idx, src, img))
                except Exception:
                    pass  # skip bad thumbnails

            self.after(0, lambda: self._build_grid(items))
            self.after(0, lambda: _flash_taskbar(self.app))
        except Exception as e:
            self.after(0, lambda err=e:
                       self._stlbl.configure(text=f"Error: {err}", text_color="#ff5555"))

    def _cols_for_width(self) -> int:
        self._grid.update_idletasks()
        w = self._grid.winfo_width() - 24  # subtract scrollbar
        return max(2, w // (TW + 10))

    def _build_grid(self, items: list):
        self._grid_items = items  # keep for re-grid
        cols = self._cols_for_width()
        self._cur_cols = cols

        for idx, src, img in items:
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(TW, TH))
            self._thumbs.append(ctk_img)
            self._thumb_imgs[idx] = (ctk_img, img)  # img kept for preview rescale
            r, c = divmod(idx, cols)
            is_v  = src in _VID
            cell = ctk.CTkFrame(self._grid, fg_color="#1b1b2e", corner_radius=6)
            cell.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
            btn = ctk.CTkButton(cell, image=ctk_img, text="",
                               width=TW, height=TH,
                               fg_color="transparent", hover_color="#2a2a50",
                               command=lambda i=idx: self._assign(i))
            btn.pack(fill="both", expand=True, padx=2, pady=2)
            btn.bind("<Double-Button-1>", lambda e, i=idx: self._open_preview(i))
            lbl = ctk.CTkLabel(cell, text=f"#{idx+1}{'  [VID]' if is_v else ''}",
                               font=("Arial", 10), text_color="#aaaaaa")
            lbl.pack(pady=(0, 4))
            self._cells[idx] = (cell, lbl)

        self._set_col_weights(cols)
        self._grid.bind("<Configure>", self._on_resize)
        try:
            self._grid._scrollable_frame.bind("<Configure>", self._update_scrollregion)
        except Exception:
            pass
        self._grid.after(50, self._update_scrollregion)
        self._stlbl.configure(
            text=f"{len(items)}/{len(self.app.pool)} thumbnails ready. "
                 "Select a line (left), then click an image.")

    def _set_col_weights(self, cols: int):
        try:
            inner = self._grid._scrollable_frame
            for c in range(cols):
                inner.columnconfigure(c, weight=1)
        except AttributeError:
            pass

    def _update_scrollregion(self, *_):
        try:
            self._grid.update_idletasks()
            c = self._grid._parent_canvas
            c.configure(scrollregion=c.bbox("all"))
        except Exception:
            pass

    def _on_resize(self, event=None):
        if not self._cells:
            return
        cols = self._cols_for_width()
        if cols == self._cur_cols:
            return
        self._cur_cols = cols
        for idx, (cell, _) in self._cells.items():
            r, c = divmod(idx, cols)
            cell.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
        self._set_col_weights(cols)
        self._grid.after(20, self._update_scrollregion)

    def _sel_line(self, lid: str):
        self._sel = lid
        self._refresh_visuals()
        self._stlbl.configure(text=f"Line {lid} selected — click a thumbnail to assign.")

    def _assign(self, idx: int):
        if not self._sel:
            self._stlbl.configure(
                text="Select a line first (left panel), then click to assign. Double-click to preview.")
            return
        for other_lid, other_idx in self.app.assigned.items():
            if other_idx == idx and other_lid != self._sel:
                self._stlbl.configure(
                    text=f"#{idx+1} already used by line {other_lid}. Pick another.")
                return
        self.app.assigned[self._sel] = idx
        self._refresh_visuals()
        total = len(self.app.script.get("lines", []))
        done  = len(self.app.assigned)
        self._stlbl.configure(text=f"{done}/{total} lines assigned.")
        if done == total:
            self._appbtn.configure(state="normal")

    def _open_preview(self, idx: int):
        src, url, thumb = self.app.pool[idx]
        is_v = src in _VID

        win = ctk.CTkToplevel(self)
        win.title(f"Preview #{idx+1}  {'[VIDEO]' if is_v else '[IMAGE]'}")
        win.geometry("520x620")
        win.grab_set()

        self._stlbl.configure(text=f"Previewing #{idx+1} — select a line then click to assign.")

        img_lbl = ctk.CTkLabel(win, text="Loading…", text_color="#888888")
        img_lbl.pack(pady=(20, 8), padx=16, fill="both", expand=True)

        if is_v:
            _s = {"cap": None, "playing": False, "after_id": None, "ref": [None], "tmp": None}

            def _stop():
                if _s["after_id"]:
                    win.after_cancel(_s["after_id"])
                    _s["after_id"] = None
                if _s["cap"]:
                    _s["cap"].release()
                    _s["cap"] = None
                if _s["tmp"]:
                    try:
                        os.unlink(_s["tmp"])
                    except OSError:
                        pass
                    _s["tmp"] = None

            def _tick():
                if not _s["playing"] or not _s["cap"]:
                    return
                ret, frame = _s["cap"].read()
                if not ret:
                    _s["cap"].set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = _s["cap"].read()
                if ret:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil = Image.fromarray(rgb)
                    pil.thumbnail((480, 480))
                    ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil,
                                           size=(pil.width, pil.height))
                    _s["ref"][0] = ctk_img  # prevent GC
                    img_lbl.configure(image=ctk_img, text="")
                fps = _s["cap"].get(cv2.CAP_PROP_FPS) or 25
                _s["after_id"] = win.after(int(1000 / fps), _tick)

            play_btn = ctk.CTkButton(win, text="Loading…", state="disabled", width=130)
            play_btn.pack(pady=6)

            def toggle():
                _s["playing"] = not _s["playing"]
                play_btn.configure(text="⏸ Pause" if _s["playing"] else "▶ Play")
                if _s["playing"]:
                    _tick()

            play_btn.configure(command=toggle)
            win.protocol("WM_DELETE_WINDOW", lambda: (_stop(), win.destroy()))

            def _load_video():
                try:
                    win.after(0, lambda: img_lbl.configure(text="Downloading video…"))
                    data = fetch_bytes(url)
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                        f.write(data)
                        tmp = f.name
                    _s["tmp"] = tmp
                    cap = cv2.VideoCapture(tmp)
                    if not cap.isOpened():
                        win.after(0, lambda: img_lbl.configure(text="Cannot open video"))
                        return
                    _s["cap"] = cap
                    _s["playing"] = True
                    win.after(0, lambda: (
                        play_btn.configure(state="normal", text="⏸ Pause"),
                        _tick(),
                    ))
                except Exception as e:
                    try:
                        win.after(0, lambda err=e: img_lbl.configure(text=f"Error: {err}"))
                    except Exception:
                        pass

            threading.Thread(target=_load_video, daemon=True).start()

        else:
            def _open_full():
                def run():
                    try:
                        data = fetch_bytes(url)
                        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                            f.write(data)
                            tmp = f.name
                        os.startfile(tmp)
                    except Exception as e:
                        print(f"Image open error: {e}")
                threading.Thread(target=run, daemon=True).start()

            ctk.CTkButton(win, text="Open Full Size", command=_open_full).pack(pady=6)

            def _load_image():
                try:
                    if idx in self._thumb_imgs:
                        _, pil_img = self._thumb_imgs[idx]
                    else:
                        data = fetch_bytes(url)
                        pil_img = Image.open(io.BytesIO(data)).convert("RGB")
                    img = pil_img.copy()
                    img.thumbnail((460, 520))
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                                           size=(img.width, img.height))
                    self._preview_imgs.append(ctk_img)
                    win.after(0, lambda: img_lbl.configure(image=ctk_img, text=""))
                except Exception as e:
                    try:
                        win.after(0, lambda err=e: img_lbl.configure(text=f"Load failed: {err}"))
                    except Exception:
                        pass

            threading.Thread(target=_load_image, daemon=True).start()

        ctk.CTkButton(win, text="Close", fg_color="#444", command=win.destroy).pack(pady=6)

    def _refresh_visuals(self):
        used_rev = {v: k for k, v in self.app.assigned.items()}
        for idx, (cell, lbl) in self._cells.items():
            src  = self.app.pool[idx][0] if idx < len(self.app.pool) else ""
            base = f"#{idx+1}{'  [VID]' if src in _VID else ''}"
            if idx in used_rev:
                lbl.configure(text=f"{base} → L{used_rev[idx]}", text_color="#44ff66")
                cell.configure(fg_color="#1a3a1a")
            else:
                lbl.configure(text=base, text_color="#aaaaaa")
                cell.configure(fg_color="#1b1b2e")
        for lid, btn in self._line_btns.items():
            if lid == self._sel:
                btn.configure(fg_color="#2a2a6a")
            elif lid in self.app.assigned:
                btn.configure(fg_color="#1a4a1a")
            else:
                btn.configure(fg_color="#222233")

    def _approve(self):
        self._appbtn.configure(state="disabled", text="Downloading…")

        def run():
            try:
                IMAGES_DIR.mkdir(exist_ok=True)
                for f in IMAGES_DIR.iterdir():
                    f.unlink()
                for lid, idx in self.app.assigned.items():
                    src, url, _ = self.app.pool[idx]
                    ext  = ".mp4" if src in _VID else ".jpg"
                    data = fetch_bytes(url)
                    (IMAGES_DIR / f"{lid}{ext}").write_bytes(data)
                    self.after(0, lambda l=lid:
                               self._stlbl.configure(text=f"Downloaded line {l}…"))
                self.after(0, lambda: self.app.goto(S5_Assembly))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._stlbl.configure(text=f"Error: {err}", text_color="#ff5555"),
                    self._appbtn.configure(state="normal", text="Approve & Download →"),
                    tkinter.messagebox.showerror("Download Error", str(err)),
                ))

        threading.Thread(target=run, daemon=True).start()


# ── S5: Assembly ──────────────────────────────────────────────────────────────
class S5_Assembly(Base):
    def on_enter(self):
        p = self._reset()
        _h1(p, "Assembling Video")
        _sub(p, "Audio generation + ffmpeg assembly — no input needed")
        self._bar = ctk.CTkProgressBar(p, width=560)
        self._bar.pack(pady=10)
        self._bar.set(0)
        self._log = ctk.CTkTextbox(p, height=320, font=("Consolas", 11))
        self._log.pack(fill="both", expand=True, padx=16, pady=4)
        self._log.configure(state="disabled")
        self._stlbl = ctk.CTkLabel(p, text="Starting…", text_color="#888888")
        self._stlbl.pack(pady=6)
        threading.Thread(target=self._run, daemon=True).start()

    def _append(self, text: str):
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _run(self):
        voice = getattr(self.app, "voice", "bf_emma")
        pipeline = [
            (["generate_audio.py", "--voice", voice], "Generating audio…",  0.45),
            (["assemble.py"],                          "Assembling video…",  0.95),
        ]
        try:
            for script_args, label, end_pct in pipeline:
                start_pct = end_pct - 0.45
                self.after(0, lambda l=label, pv=start_pct: (
                    self._stlbl.configure(text=l),
                    self._bar.set(pv),
                    self._append(f"\n▶ {l}"),
                ))
                proc = subprocess.Popen(
                    [sys.executable] + script_args,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", cwd=Path(__file__).parent,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    creationflags=_CNW,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.after(0, lambda l=line: self._append(l))
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError(f"{script_args[0]} exited with code {proc.returncode}")
                self.after(0, lambda pv=end_pct: self._bar.set(pv))

            self.after(0, lambda: (
                self._bar.set(1.0),
                self._stlbl.configure(text="Done!", text_color="#44ff66"),
                _flash_taskbar(self.app),
            ))
            self.after(900, lambda: self.app.goto(S6_Preview))
        except Exception as e:
            self.after(0, lambda err=e:
                       self._stlbl.configure(text=f"Error: {err}", text_color="#ff5555"))


# ── S6: Preview ───────────────────────────────────────────────────────────────
class S6_Preview(Base):
    def on_enter(self):
        p = self._reset()
        _h1(p, "Preview Video")
        title = self.app.script.get("title", "")
        vid   = VIDEOS_OUT / _slug(title) / "output.mp4"
        if not vid.exists():
            vid = OUTPUT_MP4
        _sub(p, str(vid.resolve()))
        if vid.exists():
            mb = vid.stat().st_size / 1024 / 1024
            ctk.CTkLabel(p, text=f"Size: {mb:.1f} MB",
                         text_color="#888888").pack(pady=4)
        else:
            ctk.CTkLabel(p, text="output.mp4 not found",
                         text_color="#ff5555").pack(pady=4)

        ctk.CTkButton(p, text="Open in Player", width=200, height=40,
                      command=lambda: os.startfile(str(vid)) if vid.exists() else None
                      ).pack(pady=20)

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(pady=6)
        _back_btn(row, self.app, S5_Assembly)
        ctk.CTkButton(row, text="Upload to YouTube →", width=220, height=38,
                      command=lambda: self.app.goto(S7_Upload)).pack(side="left", padx=6)


# ── S7: Upload ────────────────────────────────────────────────────────────────
class S7_Upload(Base):
    def on_enter(self):
        p = self._reset()
        title = self.app.script.get("title", "")
        slug  = _slug(title)
        vid_p = VIDEOS_OUT / slug / "output.mp4"
        sc_p  = VIDEOS_OUT / slug / "script.json"
        if not vid_p.exists():
            vid_p, sc_p = OUTPUT_MP4, SCRIPT_JSON
        self._vid_p = vid_p
        self._sc_p  = sc_p

        _h1(p, "Upload to YouTube")

        # ── Channel info banner ───────────────────────────────────────────────
        channels = _load_channels()
        ch_key   = getattr(self.app, "channel", next(iter(channels)))
        ch_name  = channels.get(ch_key, ch_key)
        ch_bar = ctk.CTkFrame(p, fg_color="#0d1f0d", corner_radius=8)
        ch_bar.pack(fill="x", padx=60, pady=(0, 6))
        ch_inner = ctk.CTkFrame(ch_bar, fg_color="transparent")
        ch_inner.pack(fill="x", padx=14, pady=8)
        self._s7_avatar_lbl = ctk.CTkLabel(ch_inner, text="", width=36, height=36)
        self._s7_avatar_lbl.pack(side="left", padx=(0, 8))
        self._s7_avatar_img = None
        ctk.CTkLabel(ch_inner, text="Channel:", font=("Arial", 12),
                     text_color="#888888").pack(side="left")
        ctk.CTkLabel(ch_inner, text=f"  {ch_name}", font=("Arial", 13, "bold"),
                     text_color="#44ff88").pack(side="left")
        self._ch_stat_lbl = ctk.CTkLabel(ch_inner, text="",
                                          font=("Arial", 11), text_color="#556655")
        self._ch_stat_lbl.pack(side="left", padx=(10, 0))
        threading.Thread(target=self._fetch_ch_info, args=(ch_key,), daemon=True).start()

        _sub(p, f"Title: {title}")
        ctk.CTkLabel(p, text=str(vid_p), text_color="#555555",
                     font=("Arial", 11)).pack(pady=2)

        # ── Schedule options ──────────────────────────────────────────────────
        sframe = ctk.CTkFrame(p, fg_color="#111122", corner_radius=8)
        sframe.pack(fill="x", padx=60, pady=(10, 4))
        self._sched_var = ctk.StringVar(value="now")

        row_now = ctk.CTkFrame(sframe, fg_color="transparent")
        row_now.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkRadioButton(row_now, text="Publish immediately",
                           variable=self._sched_var, value="now",
                           command=self._on_sched_change).pack(side="left")

        row_later = ctk.CTkFrame(sframe, fg_color="transparent")
        row_later.pack(fill="x", padx=14, pady=(0, 2))
        ctk.CTkRadioButton(row_later, text="Schedule:",
                           variable=self._sched_var, value="later",
                           command=self._on_sched_change).pack(side="left", padx=(0, 10))

        # Date + time pickers (enabled only when "later" selected)
        row_pick = ctk.CTkFrame(sframe, fg_color="transparent")
        row_pick.pack(fill="x", padx=36, pady=(0, 4))

        self._date_entry = _DateEntry(row_pick, width=12, date_pattern="dd/MM/yyyy",
                                      background="#1a1a2e", foreground="white",
                                      borderwidth=2, state="disabled")
        self._date_entry.pack(side="left", padx=(0, 8))
        self._date_entry.bind("<<DateEntrySelected>>", self._update_preview)

        ctk.CTkLabel(row_pick, text="at", font=("Arial", 11),
                     text_color="#666688").pack(side="left", padx=(0, 6))

        self._hour_var = ctk.StringVar(value="13")
        self._hour_menu = ctk.CTkOptionMenu(
            row_pick, values=[f"{h:02d}" for h in range(0, 24)],
            variable=self._hour_var, width=72, state="disabled",
            command=self._update_preview)
        self._hour_menu.pack(side="left")

        ctk.CTkLabel(row_pick, text=":", font=("Arial", 13, "bold"),
                     text_color="#888888").pack(side="left", padx=2)

        self._min_var = ctk.StringVar(value="00")
        self._min_menu = ctk.CTkOptionMenu(
            row_pick, values=["00", "15", "30", "45"],
            variable=self._min_var, width=72, state="disabled",
            command=self._update_preview)
        self._min_menu.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(row_pick, text="WIB", font=("Arial", 11),
                     text_color="#555577").pack(side="left", padx=(0, 12))

        ctk.CTkButton(row_pick, text="⚡ Tomorrow 1 PM", width=148, height=28,
                      font=("Arial", 11), fg_color="#1a1a3a",
                      command=self._quick_pick).pack(side="left")

        self._preview_lbl = ctk.CTkLabel(sframe, text="",
                                          font=("Arial", 11, "italic"),
                                          text_color="#8888bb")
        self._preview_lbl.pack(padx=36, pady=(0, 10), anchor="w")

        # ── Upload button ─────────────────────────────────────────────────────
        self._btn = ctk.CTkButton(p, text="Upload Now", width=220, height=44,
                                  command=self._upload)
        self._btn.pack(pady=14)

        self._log = ctk.CTkTextbox(p, height=130, font=("Consolas", 11))
        self._log.pack(fill="x", padx=24, pady=4)
        self._log.configure(state="disabled")

        self._urllbl = ctk.CTkLabel(p, text="", font=("Arial", 13, "bold"),
                                    text_color="#44ff66")
        self._urllbl.pack(pady=4)

        nav = ctk.CTkFrame(p, fg_color="transparent")
        nav.pack(pady=4)
        _back_btn(nav, self.app, S6_Preview)
        self._new_btn = ctk.CTkButton(nav, text="New Video ↺", width=140, height=34,
                      fg_color="#1a4a1a", state="disabled",
                      command=lambda: self.app.goto(S1_Animal))
        self._new_btn.pack(side="left", padx=6)

    def _on_sched_change(self):
        later = self._sched_var.get() == "later"
        state = "normal" if later else "disabled"
        self._date_entry.configure(state=state)
        self._hour_menu.configure(state=state)
        self._min_menu.configure(state=state)
        self._btn.configure(text="Schedule Upload" if later else "Upload Now")
        self._update_preview()

    def _update_preview(self, *_):
        if self._sched_var.get() != "later":
            self._preview_lbl.configure(text="")
            return
        try:
            utc_str = self._local_to_utc_str()
            utc_dt  = datetime.strptime(utc_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            loc_dt  = utc_dt.astimezone(_WIB)
            h12     = loc_dt.hour % 12 or 12
            ampm    = "AM" if loc_dt.hour < 12 else "PM"
            self._preview_lbl.configure(
                text=f"Publishes: {loc_dt.strftime('%a, %b %d')} at {h12}:{loc_dt.minute:02d} {ampm} WIB"
                     f"  ({utc_dt.strftime('%H:%M UTC')})"
            )
        except Exception:
            self._preview_lbl.configure(text="")

    def _local_to_utc_str(self) -> str:
        d   = self._date_entry.get_date()          # datetime.date from DateEntry
        h   = int(self._hour_var.get())
        m   = int(self._min_var.get())
        loc = datetime(d.year, d.month, d.day, h, m, tzinfo=_WIB)
        return loc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")

    def _quick_pick(self):
        tomorrow = (datetime.now(tz=_WIB) + timedelta(days=1)).date()
        self._date_entry.set_date(tomorrow)
        self._hour_var.set("13")
        self._min_var.set("00")
        if self._sched_var.get() != "later":
            self._sched_var.set("later")
            self._on_sched_change()
        else:
            self._update_preview()

    def _fetch_ch_info(self, ch_key: str):
        """Query upload.py --info to get channel stats, avatar, email. Silent on failure."""
        try:
            r = subprocess.run(
                [sys.executable, "upload.py", "--info", "--channel", ch_key],
                capture_output=True, text=True, encoding="utf-8",
                cwd=Path(__file__).parent,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                timeout=30, creationflags=_CNW,
            )
            thumb_url = stat = ""
            for line in r.stdout.splitlines():
                if line.startswith("CHANNEL:"):
                    parts = line[len("CHANNEL:"):].strip().split("|")
                    stat = parts[1].strip() if len(parts) > 1 else ""
                elif line.startswith("THUMBNAIL:"):
                    thumb_url = line[len("THUMBNAIL:"):].strip()
            ctk_img = None
            if thumb_url:
                try:
                    img = Image.open(io.BytesIO(fetch_bytes(thumb_url))).convert("RGBA")
                    img = img.resize((36, 36), Image.LANCZOS)
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(36, 36))
                except Exception:
                    pass
            def update(s=stat, ci=ctk_img):
                try:
                    if s:
                        self._ch_stat_lbl.configure(text=f"— {s}")
                    if ci:
                        self._s7_avatar_img = ci
                        self._s7_avatar_lbl.configure(image=ci, text="")
                except Exception:
                    pass
            self.after(0, update)
        except Exception:
            pass

    def _append(self, t: str):
        self._log.configure(state="normal")
        self._log.insert("end", t + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _upload(self):
        publish_at = None
        if self._sched_var.get() == "later":
            try:
                publish_at = self._local_to_utc_str()
            except Exception as e:
                tkinter.messagebox.showerror("Schedule Error", str(e))
                return

        ch_key = getattr(self.app, "channel", next(iter(_load_channels())))
        label  = "Scheduling…" if publish_at else "Uploading…"
        self._btn.configure(state="disabled", text=label)

        def run():
            try:
                cmd = [sys.executable, "upload.py",
                       "--file",    str(self._vid_p),
                       "--script",  str(self._sc_p),
                       "--channel", ch_key]
                if publish_at:
                    cmd += ["--publish-at", publish_at]

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", cwd=Path(__file__).parent,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    creationflags=_CNW,
                )
                url = ""
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.after(0, lambda l=line: self._append(l))
                    if "youtube.com" in line:
                        url = line.split()[-1]
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError("upload.py failed — see log above")
                if url:
                    self.after(0, lambda u=url: self._urllbl.configure(text=u))
                # Mark animal done in queue only after successful upload
                animal = getattr(self.app, "animal", "")
                if animal:
                    topics = _load_topics(self.app.channel)
                    for i, (text, is_done) in enumerate(topics):
                        if not is_done and text.lower() == animal.lower():
                            topics[i] = (text, True)
                            _save_topics(topics, self.app.channel)
                            break
                # Persist upload history for S1 display
                try:
                    hist_path = Path(__file__).parent / "upload_history.json"
                    hist = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
                    sc = json.loads(self._sc_p.read_text(encoding="utf-8")) if self._sc_p.exists() else {}
                    entry = {
                        "title":       sc.get("title", animal),
                        "url":         url,
                        "channel":     ch_key,
                        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "status":      "scheduled" if publish_at else "uploaded",
                        "voice":       getattr(self.app, "voice", ""),
                    }
                    if publish_at:
                        entry["publish_at"] = publish_at
                    hist.insert(0, entry)
                    hist_path.write_text(json.dumps(hist[:50], indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
                done_lbl = "Scheduled ✓" if publish_at else "Uploaded ✓"
                self.after(0, lambda: (
                    self._btn.configure(state="disabled", text=done_lbl),
                    self._new_btn.configure(state="normal"),
                    _flash_taskbar(self.app),
                ))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._append(f"Error: {err}"),
                    self._btn.configure(state="normal", text="Retry"),
                ))

        threading.Thread(target=run, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
