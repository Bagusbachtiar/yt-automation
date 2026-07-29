#!/usr/bin/env python3
"""
FaunaWorks Pipeline — CustomTkinter desktop wizard
Animal → Titles → Script → Images → Assembly → Preview → Upload

Requirements: pip install customtkinter pillow
"""
import io, json, os, re, ssl, subprocess, sys, tempfile, threading, tkinter.messagebox, urllib.request
import cv2
from pathlib import Path

import customtkinter as ctk
from PIL import Image

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma2:9b"
_HERE        = Path(__file__).parent
SCRIPT_JSON  = _HERE / "script.json"
CANDS_JSON   = _HERE / "image_candidates.json"
IMAGES_DIR   = _HERE / "images"
OUTPUT_MP4   = _HERE / "output.mp4"
VIDEOS_OUT   = _HERE / "videos_output"

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode    = ssl.CERT_NONE

SRC_ORDER = ["wiki_infobox","wikipedia","wiki_keyword","pexels","pixabay","pexels_video","commons","flickr"]
_VID      = {"pexels_video"}
MAX_POOL  = 30
TW, TH    = 160, 220    # thumbnail display size (portrait)

STEPS = ["Animal","Titles","Script","Images","Assembly","Preview","Upload"]


# ── Helpers ───────────────────────────────────────────────────────────────────
def _ollama(prompt: str, tokens: int = 600) -> str:
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt,
                       "stream": False, "options": {"num_predict": tokens}}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(OLLAMA_URL, body, {"Content-Type": "application/json"}),
        timeout=120
    ) as r:
        return json.loads(r.read()).get("response", "")


def gen_titles(animal: str) -> list:
    raw = _ollama(
        f"YouTube Shorts title writer for FaunaWorks (animal channel).\n"
        f"Animal: {animal}\nGenerate 5 title options. Rules:\n"
        f"- Different surprising angle each\n- 6-10 words, no dashes, hyphens, or colons\n"
        f"- Curiosity-gap (make someone stop scrolling)\n"
        f'Return ONLY a JSON array: ["Title 1","Title 2","Title 3","Title 4","Title 5"]', 500
    )
    m = re.search(r'\[.*?\]', raw, re.DOTALL)
    if not m:
        raise ValueError(f"No array in response: {raw[:150]}")
    cleaned = re.sub(r',(\s*[\]}])', r'\1', m.group())
    return [t for t in json.loads(cleaned) if isinstance(t, str)][:5]


def _extract(src: str, entry) -> tuple:
    """Return (url, thumb) from a candidates entry."""
    if src in _VID:
        url   = entry.get("url", "") if isinstance(entry, dict) else entry
        thumb = entry.get("thumb")   if isinstance(entry, dict) else None
    else:
        url, thumb = entry, None
    return url, thumb


def pool_from(candidates: dict) -> list:
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

    # Phase 1 — per line: 1 wikipedia photo + 1 video + 1 pexels photo
    for data in candidates.values():
        _take_one(data, ["wiki_infobox", "wikipedia", "wiki_keyword"])
        _take_one(data, ["pexels_video"])
        _take_one(data, ["pexels", "pixabay"])

    # Phase 2 — fill remaining slots up to MAX_POOL
    for src in SRC_ORDER:
        if len(pool) >= MAX_POOL: break
        for data in candidates.values():
            for entry in data["sources"].get(src, []):
                url, thumb = _extract(src, entry)
                if src in _VID and not thumb: continue
                if url and url not in seen:
                    seen.add(url); pool.append((src, url, thumb))
                    if len(pool) >= MAX_POOL: break

    return pool


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "yt-auto/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
        return r.read()


def _slug(title: str) -> str:
    s = re.sub(r'[^\w\s-]', '', title.lower())
    return re.sub(r'\s+', '-', s).strip('-')[:60]


# ── App shell ─────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("FaunaWorks Pipeline")
        self.geometry("1040x780")
        self.resizable(True, True)
        # shared pipeline state
        self.animal        = ""
        self.titles        = []
        self.chosen_title  = ""
        self.script        = {}
        self.pool          = []   # [(src, url, thumb_url)]
        self.assigned      = {}   # {lid: pool_idx}

        hdr = ctk.CTkFrame(self, height=50, fg_color="#0e0e20")
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="FaunaWorks Pipeline",
                     font=("Arial", 16, "bold"), text_color="white").pack(side="left", padx=16)
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


# ── S1: Animal input ──────────────────────────────────────────────────────────
class S1_Animal(Base):
    def on_enter(self):
        p = self._reset()
        _h1(p, "What animal?")
        _sub(p, "Type any animal — AI finds the best angle")
        self._e = ctk.CTkEntry(p, placeholder_text="e.g. elephant",
                               width=320, font=("Arial", 15), height=42)
        self._e.pack(pady=22)
        self._e.focus()
        self._e.bind("<Return>", lambda _: self._go())
        self._btn = ctk.CTkButton(p, text="Generate Titles →",
                                  width=210, height=42, command=self._go)
        self._btn.pack()
        self._lbl = ctk.CTkLabel(p, text="", text_color="#888888")
        self._lbl.pack(pady=12)

    def _go(self):
        a = self._e.get().strip()
        if not a:
            return
        self.app.animal = a
        self._btn.configure(state="disabled", text="Calling Ollama…")
        self._lbl.configure(text="Generating titles…", text_color="#888888")

        def run():
            try:
                self.app.titles = gen_titles(a)
                self.after(0, lambda: self.app.goto(S2_Titles))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._lbl.configure(text=f"Error: {err}", text_color="#ff5555"),
                    self._btn.configure(state="normal", text="Generate Titles →"),
                ))

        threading.Thread(target=run, daemon=True).start()


# ── S2: Title selection ───────────────────────────────────────────────────────
class S2_Titles(Base):
    def on_enter(self):
        p = self._reset()
        _h1(p, "Pick a title")
        _sub(p, f"Animal: {self.app.animal}")
        var = ctk.StringVar(value=self.app.titles[0] if self.app.titles else "")
        ctk.CTkFrame(p, height=10, fg_color="transparent").pack()
        for t in self.app.titles:
            row = ctk.CTkFrame(p, fg_color="#1b1b2e", corner_radius=8)
            row.pack(fill="x", padx=50, pady=4)
            ctk.CTkRadioButton(row, text=t, variable=var, value=t,
                               font=("Arial", 13)).pack(anchor="w", padx=14, pady=9)
        ctk.CTkFrame(p, height=8, fg_color="transparent").pack()
        btn_row = ctk.CTkFrame(p, fg_color="transparent")
        btn_row.pack()
        _back_btn(btn_row, self.app, S1_Animal)
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

    def _go(self, title: str):
        if not title:
            return
        self.app.chosen_title = title
        self._btn.configure(state="disabled", text="Generating script…")
        self._lbl.configure(text="Running generate_script.py…", text_color="#888888")
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

        def run():
            try:
                proc = subprocess.Popen(
                    [sys.executable, "generate_script.py", self.app.animal, "--title", title],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, cwd=_HERE
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.after(0, lambda l=line: self._append(l))
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError(f"generate_script.py exited {proc.returncode} — see log above")
                self.app.script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
                self.app.script["title"] = title
                SCRIPT_JSON.write_text(json.dumps(self.app.script, indent=2, ensure_ascii=False), encoding="utf-8")
                self.after(0, lambda: self.app.goto(S3_Script))
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
                        sf._parent_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                        return
                except Exception:
                    pass
        self.app.bind_all("<MouseWheel>", on_wheel)

    def _load(self):
        try:
            self.after(0, lambda: self._stlbl.configure(text="Running fetch_images.py…"))
            r = subprocess.run(
                [sys.executable, "fetch_images.py"],
                capture_output=True, text=True, cwd=Path(__file__).parent
            )
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout)[-400:])
            cands = json.loads(CANDS_JSON.read_text(encoding="utf-8"))
            pool  = pool_from(cands)
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
        pipeline = [
            ("generate_audio.py", "Generating audio…",  0.45),
            ("assemble.py",       "Assembling video…",  0.95),
        ]
        try:
            for name, label, end_pct in pipeline:
                start_pct = end_pct - 0.45
                self.after(0, lambda l=label, pv=start_pct: (
                    self._stlbl.configure(text=l),
                    self._bar.set(pv),
                    self._append(f"\n▶ {l}"),
                ))
                proc = subprocess.Popen(
                    [sys.executable, name],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, cwd=Path(__file__).parent
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.after(0, lambda l=line: self._append(l))
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError(f"{name} exited with code {proc.returncode}")
                self.after(0, lambda pv=end_pct: self._bar.set(pv))

            self.after(0, lambda: (
                self._bar.set(1.0),
                self._stlbl.configure(text="Done!", text_color="#44ff66"),
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
        _sub(p, f"Title: {title}")
        ctk.CTkLabel(p, text=str(vid_p), text_color="#555555",
                     font=("Arial", 11)).pack(pady=2)

        self._btn = ctk.CTkButton(p, text="Upload Now", width=220, height=44,
                                  command=self._upload)
        self._btn.pack(pady=20)

        self._log = ctk.CTkTextbox(p, height=180, font=("Consolas", 11))
        self._log.pack(fill="x", padx=24, pady=6)
        self._log.configure(state="disabled")

        self._urllbl = ctk.CTkLabel(p, text="", font=("Arial", 13, "bold"),
                                    text_color="#44ff66")
        self._urllbl.pack(pady=6)

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(pady=4)
        _back_btn(row, self.app, S6_Preview)
        ctk.CTkButton(row, text="New Video ↺", width=140, height=34,
                      fg_color="#1a4a1a",
                      command=lambda: self.app.goto(S1_Animal)).pack(side="left", padx=6)

    def _append(self, t: str):
        self._log.configure(state="normal")
        self._log.insert("end", t + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _upload(self):
        self._btn.configure(state="disabled", text="Uploading…")

        def run():
            try:
                proc = subprocess.Popen(
                    [sys.executable, "upload.py",
                     "--file", str(self._vid_p),
                     "--script", str(self._sc_p)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, cwd=Path(__file__).parent
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
                    raise RuntimeError("upload.py failed")
                if url:
                    self.after(0, lambda u=url: self._urllbl.configure(text=u))
                self.after(0, lambda: self._btn.configure(state="disabled", text="Uploaded ✓"))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._append(f"Error: {err}"),
                    self._btn.configure(state="normal", text="Retry Upload"),
                ))

        threading.Thread(target=run, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
