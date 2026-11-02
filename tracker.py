"""Matsu's Tracker (by MatsuHikaru): small always-on-top PokeMMO dashboard for swarm, kids and miniboss callouts.

Reads the newest chat_*.log in the PokeMMO log folder (needs client.gui.chat.log_chat=true)
and keeps following it, switching to a new file when the date rolls over.
"""
import glob
import json
import os
import re
import sys
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, simpledialog

# Where PokeMMO is usually installed; the user can pick another folder from the Settings menu.
GAME_DIR_CANDIDATES = [
    r"C:\Program Files\PokeMMO",
    r"C:\Program Files (x86)\PokeMMO",
    os.path.expandvars(r"%LOCALAPPDATA%\PokeMMO"),
    os.path.expandvars(r"%APPDATA%\PokeMMO"),
    os.path.expandvars(r"%USERPROFILE%\PokeMMO"),
    os.path.expandvars(r"%USERPROFILE%\Desktop\PokeMMO"),
    os.path.expandvars(r"%USERPROFILE%\Downloads\PokeMMO"),
    r"D:\PokeMMO",
    r"D:\Program Files\PokeMMO",
]


def is_game_dir(path):
    return bool(path) and os.path.isfile(os.path.join(path, "config", "main.properties"))


def find_game_dir(saved=None):
    for path in [saved] + GAME_DIR_CANDIDATES:
        if is_game_dir(path):
            return path
    return None


def chat_logging_enabled(game_dir):
    """True/False from main.properties, or None if it can't be read."""
    try:
        with open(os.path.join(game_dir, "config", "main.properties"), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("client.gui.chat.log_chat="):
                    return line.strip().endswith("true")
    except OSError:
        return None
    return False
POLL_MS = 500
RENDER_MS = 1000
STALE_SECONDS = 15 * 60

REGIONS = [
    ("K", "Kanto", "#e0533f"),
    ("J", "Johto", "#d9a520"),
    ("H", "Hoenn", "#2fa866"),
    ("S", "Sinnoh", "#3b82d6"),
    ("U", "Unova", "#8e5bd0"),
]

# ---------------------------------------------------------------- parsing

LINE_RE = re.compile(r"^\[(\d+/\d+/\d+ \d+:\d+:\d+ [AP]M)\] \[([^\]]+)\](?:\[([^\]]+)\])? ?(.*)$")
META_RE = re.compile(r"\s*\{M:[^}]*\}\s*$")
COLOR_RE = re.compile(r"\[#[0-9a-fA-F]*\]")
STATUS_RE = re.compile(r"\((\?|old)\)\s*$", re.I)

# Callers are random and each writes callouts their own way, so parsing is keyword based:
# find the section tags anywhere in the message, then read whatever sits between them.
# Users can add their own tags in Settings › Callout formats.
DEFAULT_TAGS = {
    "swarms": ["[SWARMS]", "[SWARM]", "Swarms:", "Swarm:"],
    "kids": ["[KIDS]", "[KK]", "Kids:", "Kid:", "KK:", "Kids", "KK"],
    "mb": ["MB:", "Miniboss:", "Mini boss:", "Mini-boss:", "MB", "Miniboss"],
}
# How much a tag is trusted: "[KIDS]" always counts; "KK:" needs a list; a bare "kk" (casual
# "kk r2 / 7 / 11") needs a list where every entry looks like a place, so chat like "kk where?" is ignored.
BRACKET, COLON, LOOSE = 2, 1, 0
SPOT_RE = re.compile(r"^[A-Za-z0-9][\w.'\-]*(?: [\w.'\-]+)?$")
TAG_LABELS = {"swarms": "Swarms", "kids": "Kids", "mb": "Miniboss"}
REGION_RE = re.compile(r"(?<![A-Za-z0-9])(Kanto|Johto|Hoenn|Sinnoh|Unova|[KJHSU])\s*:", re.I)
FLAG_RE = re.compile(r"^\s*:(\w*):")
# chatter that trails a callout; it ends the whole section
SECTION_END_RE = re.compile(r"\bwhisp|\bwisp\b|\bplease\b|\bplz\b|\bpls\b|<3|\{", re.I)
# per-value noise: "@11:26" times, "Wisp New/gone for update>" tails
VALUE_END_RE = re.compile(r"@\s*\d|\bwhisp|\bwisp\b|\bplease\b|\bplz\b|\bpls\b|<|>|\{", re.I)
SPLIT_RE = re.compile(r"[|/,]")
KIDS_MAX = 3


def tag_regex(tags):
    """Case-insensitive matcher for a list of tags; 'MB:' also matches 'mb :'."""
    alts = []
    for t in sorted({t.strip() for t in tags if t.strip()}, key=len, reverse=True):
        e = re.escape(t).replace(r"\ ", r"\s*").replace(":", r"\s*:")
        if t[0].isalnum():  # bare words must not be the tail of another word
            e = r"(?<![A-Za-z0-9])" + e
        if t[-1].isalnum():  # "KK" must not match the start of "kkk" or "kkdo"
            e += r"(?![A-Za-z0-9])"
        alts.append(e)
    return re.compile("|".join(alts), re.I) if alts else None


def tag_strength(tag):
    tag = tag.strip()
    return BRACKET if tag.startswith("[") else COLON if tag.endswith(":") else LOOSE


def build_tags(extra=None):
    extra = extra or {}
    return {k: tag_regex(v + list(extra.get(k, []))) for k, v in DEFAULT_TAGS.items()}


def clean_value(raw):
    """'Alpha _NidoK ' -> 'Alpha_NidoK', 'rt. 24' -> 'R24', 'R21 @11:16' -> 'R21'."""
    m = VALUE_END_RE.search(raw)
    if m:
        raw = raw[: m.start()]
    raw = raw.strip(" \t|/,;:-.")
    raw = re.sub(r"\s*_\s*", "_", raw)
    raw = re.sub(r"\b(?:rt|route)[.\-\s]*(\d+)", r"R\1", raw, flags=re.I)
    return raw[:1].upper() + raw[1:]


def clean_spot(raw):
    """Kid/MB spot: like clean_value, plus a bare number means a route ('Rt-24/14' -> R24, R14)."""
    raw = clean_value(raw)
    return "R" + raw if raw.isdigit() else raw


def parse_callout(msg, tags):
    """Pull swarms / kids / miniboss out of one chat message, whatever layout the caller used.
    Returns {"swarms": {code: (value, status)}, "kids": (flag, [(value, status)]), "mb": (value, status)}
    with only the parts that were found, or {} when it isn't a callout."""
    hits = []
    for kind, rx in tags.items():
        if rx:
            hits += [(m.start(), m.end(), kind, tag_strength(m.group(0))) for m in rx.finditer(msg)]
    hits.sort()
    sections = {}
    last_end = -1
    for i, (start, end, kind, strength) in enumerate(hits):
        if start < last_end:  # overlaps the previous tag
            continue
        last_end = end
        nxt = next((h[0] for h in hits[i + 1:] if h[0] >= end), len(msg))
        if kind not in sections or strength > sections[kind][1]:  # the most explicit tag wins
            sections[kind] = (msg[end:nxt], strength)

    out = {}
    if "swarms" in sections:
        text, strength = sections["swarms"]
        marks = list(REGION_RE.finditer(text))
        swarms = {}
        for j, m in enumerate(marks):
            code = m.group(1)[0].upper()
            if code in swarms:  # e.g. the "K:" in "...| K:Kids: ..." belongs to the kids part
                continue
            raw = text[m.end(): marks[j + 1].start() if j + 1 < len(marks) else len(text)]
            swarms[code] = parse_value(clean_value(raw))
        if swarms and (strength == BRACKET or len(swarms) >= 2):
            out["swarms"] = swarms

    if "kids" in sections:
        text, strength = sections["kids"]
        flag = ""
        m = FLAG_RE.match(text)
        if m:
            flag, text = m.group(1), text[m.end():]
        m = SECTION_END_RE.search(text)
        if m:
            text = text[: m.start()]
        slots = []
        for part in SPLIT_RE.split(text):
            value = clean_spot(part)
            if value:
                slots.append(parse_value(value))
        slots = slots[:KIDS_MAX]
        looks_like_spots = all(v is None or (len(v) <= 24 and SPOT_RE.match(v)) for v, _ in slots)
        if strength == BRACKET or (len(slots) >= 2 and (strength == COLON or looks_like_spots)):
            out["kids"] = (flag, slots)

    if "mb" in sections and ("swarms" in out or "kids" in out or sections["mb"][1] == BRACKET):
        text = SPLIT_RE.split(sections["mb"][0], 1)[0]
        out["mb"] = parse_value(clean_spot(text))
    return out


def describe_callout(found):
    """Human-readable preview of parse_callout() output, for the Settings dialog."""
    if not found:
        return "Nothing recognized.\nAdd the word the caller puts before the list (e.g. \"Swarms:\") as a tag above."
    def fmt(v, s):
        return (v or "?") + {"unconfirmed": " (?)", "old": " (old)"}.get(s, "")
    lines = []
    if "swarms" in found:
        lines.append("Swarms   " + "   ".join(f"{c}: {fmt(*found['swarms'].get(c, (None, 'unknown')))}"
                                             for c, _, _ in REGIONS))
    if "kids" in found:
        flag, slots = found["kids"]
        lines.append("Kids     " + (" | ".join(fmt(*s) for s in slots) or "?") + (f"   [{flag}]" if flag else ""))
    if "mb" in found:
        lines.append("MB       " + fmt(*found["mb"]))
    return "\n".join(lines)

# Your own Team messages never count as requests, and replying clears one. Your character names
# come from Settings, and are also auto-detected from "<you> sent out X!" at the start of wild battles.
REQUEST_TOPIC_RE = re.compile(r"\b(kids?|kk|mb|mini\s?boss|boss|swarms?|sw|locs?|locations?|status|spots?)\b", re.I)
REQUEST_ASK_RE = re.compile(r"\?|\b(where|any|anyone|pls|plz|please|status|update|locs?|wya)\b", re.I)
REQUEST_EXPIRE_SECONDS = 10 * 60
CHAT_LIMIT = 150  # in-game chat cuts longer messages


def norm(s):
    """Loose key so 'Lavender(?)', 'LavenderTown' and 'Lavender' compare equal."""
    k = re.sub(r"[^a-z0-9]", "", s.lower())
    return re.sub(r"(town|city)$", "", k) or k


def base_key(s):
    """KIDS/MB spots get refined over time (R6 -> R6_DOWN -> R6_Vermilion); key on the base."""
    return norm(s.split("_", 1)[0])


def parse_value(raw):
    """'R6(?)' -> ('R6', 'unconfirmed'); '?' -> (None, 'unknown')."""
    raw = raw.strip()
    if not raw or raw == "?":
        return None, "unknown"
    status = "confirmed"
    m = STATUS_RE.search(raw)
    if m:
        status = "unconfirmed" if m.group(1) == "?" else "old"
        raw = raw[: m.start()].strip()
    elif re.search(r"[_\s]old$", raw, re.I):  # some callers write "Saffron_OLD"
        status = "old"
        raw = raw[:-4]
    return (raw or None), status


class Tracked:
    """A value plus when it was first seen (resets when the value changes)."""

    def __init__(self, key_fn=norm):
        self.key_fn = key_fn
        self.value = None
        self.status = "unknown"
        self.key = None
        self.first = None

    def update(self, value, status, ts):
        key = self.key_fn(value) if value else None
        if key != self.key:
            self.key = key
            self.first = ts if key else None
        self.value, self.status = value, status


class Tracker:
    def __init__(self):
        # "(80%)"-style notes change between posts; ignore them so "since" doesn't reset
        self.swarms = {code: Tracked(lambda v: norm(re.sub(r"\(.*?\)", "", v))) for code, _, _ in REGIONS}
        self.swarm_last = None  # (ts, who)
        self.kids = []  # list of (name, status, first_ts)
        self.kids_first = {}
        self.mb = Tracked(base_key)
        self.kids_last = None
        self.me = set()
        self.request = None  # (ts, who, msg) of an unanswered Team request
        self.kids_acked = None  # kids_complete_key() at the last copy
        self._wild_pending = False
        self.tags = build_tags()

    def kids_complete_key(self):
        """Key of the current kid spots if all 3 are confirmed, else None."""
        slots = self.kids[:3]
        if len(slots) == 3 and all(name and status == "confirmed" for name, status, _ in slots):
            return tuple(sorted(base_key(name) for name, _, _ in slots))
        return None

    def kids_alert(self):
        """True when a fully confirmed set of kids hasn't been copied yet."""
        key = self.kids_complete_key()
        return key is not None and key != self.kids_acked

    def pending_request(self):
        if self.request and (datetime.now() - self.request[0]).total_seconds() < REQUEST_EXPIRE_SECONDS:
            return self.request
        return None

    def _battle(self, body):
        if body.startswith("A wild "):
            self._wild_pending = True
        elif self._wild_pending and " sent out " in body:
            self.me.add(body.split(" sent out ", 1)[0].strip())
            self._wild_pending = False

    def feed(self, line):
        m = LINE_RE.match(line)
        if not m:
            return
        try:
            ts = datetime.strptime(m.group(1), "%m/%d/%y %I:%M:%S %p")
        except ValueError:
            return
        channel, body = m.group(2), COLOR_RE.sub("", m.group(4))
        if channel == "Battle":
            self._battle(body)
            return
        if ": " not in body:
            return
        who, msg = body.split(": ", 1)
        msg = META_RE.sub("", msg).strip()

        if channel == "Team":
            if who in self.me:
                self.request = None
            elif REQUEST_TOPIC_RE.search(msg) and REQUEST_ASK_RE.search(msg):
                self.request = (ts, who, msg)

        found = parse_callout(msg, self.tags)
        if "swarms" in found:
            self._swarms(found["swarms"], ts, who)
        if "kids" in found:
            self._kids(*found["kids"], ts, who)
        if "mb" in found:
            self.mb.update(*found["mb"], ts)
            self.kids_last = (ts, who)

    def summary(self):
        """Compact copyable snapshot of swarms, kids and miniboss, fitted to the chat limit.
        Deliberately avoids the [SWARMS]/[KIDS] tags so pasting it doesn't feed back in."""
        def fmt(value, status, _first=None):
            if not value:
                return "?"
            return value + {"unconfirmed": "(?)", "old": "(old)"}.get(status, "")

        sw = " ".join(f"{code}:{fmt(s.value, s.status)}" for code, s in self.swarms.items())
        kk = " ".join(fmt(*k) for k in self.kids) or "?"
        mb = fmt(self.mb.value, self.mb.status)
        return f"SW {sw} | MB {mb} | KK {kk}"[:CHAT_LIMIT]

    def _swarms(self, swarms, ts, who):
        for code, (value, status) in swarms.items():
            self.swarms[code].update(value, status, ts)
        self.swarm_last = (ts, who)

    def _kids(self, flag, found, ts, who):
        if flag.upper() == "RESET":
            self.kids_first = {}
        slots, present = [], {}
        for name, status in found:
            first = None
            if name:
                k = base_key(name)
                first = present[k] = self.kids_first.get(k, ts)
            slots.append((name, status, first))
        self.kids_first = present
        self.kids = slots
        self.kids_last = (ts, who)
        if flag.upper() == "RESET":
            # A reset always turns the kids alert off; the next fully confirmed set alerts again.
            self.kids_acked = self.kids_complete_key()


class Tailer:
    """Follows the newest chat_*.log, handling partial lines and day rollover."""

    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.path = None
        self.pos = 0
        self.buf = b""

    def read_lines(self):
        if not self.log_dir:
            return []
        files = glob.glob(os.path.join(self.log_dir, "chat_*.log"))
        newest = max(files, key=os.path.getmtime) if files else None
        if newest != self.path:
            self.path, self.pos, self.buf = newest, 0, b""
        if not newest:
            return []
        try:
            size = os.path.getsize(newest)
            if size < self.pos:  # file truncated / recreated
                self.pos, self.buf = 0, b""
            if size == self.pos:
                return []
            with open(newest, "rb") as f:
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
        except OSError:
            return []
        lines = (self.buf + data).split(b"\n")
        self.buf = lines.pop()
        return [ln.decode("utf-8", "replace").rstrip("\r") for ln in lines]


# ---------------------------------------------------------------- UI

BG, CARD, FG, DIM, FAINT = "#14161b", "#1c1f26", "#e8e8ea", "#9aa0aa", "#555b66"
STATUS_STYLE = {
    "confirmed": ("#3fbf6f", "confirmed"),
    "unconfirmed": ("#e2b53a", "unconfirmed"),
    "old": ("#7d8590", "old"),
    "unknown": (FAINT, "—"),
}
FONT = ("Segoe UI", 9)
BOLD = ("Segoe UI Semibold", 9)
HEAD = ("Segoe UI Semibold", 10)


def age(ts):
    if not ts:
        return ""
    s = max(0, int((datetime.now() - ts).total_seconds()))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h {s % 3600 // 60}m"


def clock(ts):
    return ts.strftime("%H:%M") if ts else ""


APP_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = getattr(sys, "_MEIPASS", APP_DIR)  # bundled files when running as the .exe
ICON_PATH = os.path.join(RESOURCE_DIR, "tracker.ico")
APP_NAME = "Matsu's Tracker"
AUTHOR = "MatsuHikaru"
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA") or APP_DIR, APP_NAME)
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")
# settings from earlier versions, read once if the current file doesn't exist yet
LEGACY_SETTINGS = [os.path.join(os.environ.get("APPDATA") or APP_DIR, "PokeMMO Tracker", "settings.json"),
                   os.path.join(APP_DIR, "settings.json")]
MODES = ["Halloween", "Christmas", "Lunar New Year"]


def load_settings():
    for path in [SETTINGS_PATH] + LEGACY_SETTINGS:
        try:
            with open(path, encoding="utf-8-sig") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def save_settings(settings):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.tracker = Tracker()
        self.tracker.me.update(self.settings.get("my_names", []))
        self.tracker.tags = build_tags(self.settings.get("extra_tags"))
        self.game_dir = find_game_dir(self.settings.get("game_dir"))
        self.tailer = Tailer(os.path.join(self.game_dir, "log") if self.game_dir else "")
        mode = self.settings.get("mode")
        self.mode = tk.StringVar(value=mode if mode in MODES else MODES[0])
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        if os.path.exists(ICON_PATH):
            root.iconbitmap(ICON_PATH)

        # --- event menu
        menubar = tk.Menu(root)
        events = tk.Menu(menubar, tearoff=0)
        for m in MODES:
            events.add_radiobutton(label=m, variable=self.mode, value=m, command=self._apply_mode)
        menubar.add_cascade(label="Event", menu=events)

        # --- see-through window
        self.opacity = tk.IntVar(value=self.settings.get("opacity", 85))
        view = tk.Menu(menubar, tearoff=0)
        for pct in (100, 90, 85, 75, 65, 50):
            view.add_radiobutton(label=f"Opacity {pct}%", variable=self.opacity, value=pct,
                                 command=self._apply_opacity)
        menubar.add_cascade(label="View", menu=view)

        # --- setup
        setup = tk.Menu(menubar, tearoff=0)
        setup.add_command(label="Set PokeMMO folder…", command=self._pick_game_dir)
        setup.add_command(label="Set my character names…", command=self._set_names)
        setup.add_command(label="Callout formats…", command=self._callout_formats)
        menubar.add_cascade(label="Settings", menu=setup)
        root.config(menu=menubar)
        self._apply_opacity()

        # --- team request indicator
        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=10, pady=(8, 0))
        self.dot = tk.Canvas(bar, width=12, height=12, bg=BG, highlightthickness=0, cursor="hand2")
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill="#3fbf6f", outline="")
        self.dot.pack(side="left")
        self.dot_text = tk.Label(bar, text="", bg=BG, fg=FAINT, font=("Segoe UI", 8), anchor="w", cursor="hand2")
        self.dot_text.pack(side="left", padx=(6, 0), fill="x", expand=True)
        for w in (self.dot, self.dot_text):
            w.bind("<Button-1>", self._clear_request)
        self.pin = tk.Label(bar, text="📌 on top", bg=BG, fg=DIM, font=("Segoe UI", 8), cursor="hand2")
        self.pin.pack(side="right")
        self.pin.bind("<Button-1>", self._toggle_pin)
        self.copy_btn = tk.Label(bar, text="⧉ copy", bg="#2a2e37", fg=FG, font=("Segoe UI", 8),
                                 cursor="hand2", padx=6)
        self.copy_btn.pack(side="right", padx=(0, 8))
        self.copy_btn.bind("<Button-1>", self._copy)

        # --- footer (packed before the body so it stays at the bottom)
        foot = tk.Frame(root, bg=BG)
        foot.pack(fill="x", side="bottom", padx=10, pady=(2, 6))
        self.status = tk.Label(foot, text="", bg=BG, fg=FAINT, font=("Segoe UI", 8), anchor="w")
        self.status.pack(side="left")
        tk.Label(foot, text=f"by {AUTHOR}", bg=BG, fg="#3a3f4a", font=("Segoe UI", 7)).pack(side="right")

        # --- per-event panels
        self.panels = {m: tk.Frame(root, bg=BG) for m in MODES}
        for m in MODES[1:]:
            tk.Label(self.panels[m], text=f"{m} callouts aren't set up yet.\n"
                     "Paste a sample callout line from chat to add them.",
                     bg=BG, fg=FAINT, font=FONT, justify="left").pack(anchor="w", padx=10, pady=12)

        # --- Halloween: swarms
        hw = self.panels["Halloween"]
        self.swarm_meta = self._section(hw, "SWARMS")
        self.swarm_rows = {}
        for code, name, color in REGIONS:
            row = self._row(hw)
            tk.Label(row, text=code, bg=color, fg="white", font=BOLD, width=2).pack(side="left")
            mon = tk.Label(row, text="", bg=CARD, fg=FG, font=BOLD, anchor="w")
            mon.pack(side="left", padx=(8, 4))
            loc = tk.Label(row, text="", bg=CARD, fg=DIM, font=FONT, anchor="w")
            loc.pack(side="left")
            when = tk.Label(row, text="", bg=CARD, fg=DIM, font=FONT)
            when.pack(side="right", padx=(0, 6))
            self.swarm_rows[code] = (name, mon, loc, when)

        # --- Halloween: kids
        self.kids_meta = self._section(hw, "KIDS · Kanto")
        self.kid_rows = []
        for i in range(3):
            self.kid_rows.append(self._kid_row(hw, f"#{i + 1}", "#4b5263"))
        self.mb_row = self._kid_row(hw, "MB", "#d9772b")

        self.poll()
        self.render()
        self._apply_mode()
        if not self.game_dir:  # first run on a PC where PokeMMO isn't in a usual place
            root.after(300, self._pick_game_dir)

    def _pick_game_dir(self):
        path = filedialog.askdirectory(parent=self.root, title="Select your PokeMMO folder (contains PokeMMO.exe)")
        if not path:
            return
        path = os.path.normpath(path)
        if not is_game_dir(path):
            self.status.configure(text="That isn't the PokeMMO folder (no config\\main.properties)", fg="#d0574a")
            return
        self.game_dir = path
        self.tailer.log_dir = os.path.join(path, "log")
        self.settings["game_dir"] = path
        save_settings(self.settings)

    def _set_names(self):
        current = ", ".join(sorted(self.tracker.me))
        text = simpledialog.askstring("My character names",
                                      "Your in-game name(s), comma separated.\n"
                                      "Your own Team messages won't trigger the yellow dot.",
                                      initialvalue=current, parent=self.root)
        if text is None:
            return
        names = {n.strip() for n in text.split(",") if n.strip()}
        self.tracker.me = names
        self._save_names()

    def _callout_formats(self):
        """Extra tags for callers who label their lists differently, with a live paste-and-test preview."""
        win = tk.Toplevel(self.root, bg=BG, padx=12, pady=10)
        win.title("Callout formats")
        win.attributes("-topmost", True)
        win.transient(self.root)
        extra = self.settings.get("extra_tags", {})

        tk.Label(win, text="Extra tags (comma separated). Built in: " +
                 "  ".join(t for tags in DEFAULT_TAGS.values() for t in tags),
                 bg=BG, fg=DIM, font=("Segoe UI", 8), wraplength=460, justify="left").pack(anchor="w")
        entries = {}
        for kind in DEFAULT_TAGS:
            row = tk.Frame(win, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=TAG_LABELS[kind], bg=BG, fg=FG, font=BOLD, width=9, anchor="w").pack(side="left")
            e = tk.Entry(row, bg=CARD, fg=FG, insertbackground=FG, relief="flat", font=FONT)
            e.insert(0, ", ".join(extra.get(kind, [])))
            e.pack(side="left", fill="x", expand=True, ipady=2)
            entries[kind] = e

        tk.Label(win, text="Paste a callout from chat to test it:", bg=BG, fg=FG, font=BOLD).pack(anchor="w", pady=(10, 2))
        box = tk.Text(win, height=3, width=64, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                      font=FONT, wrap="word")
        box.pack(fill="x")
        preview = tk.Label(win, text="", bg=BG, fg=DIM, font=("Consolas", 9), justify="left", anchor="w")
        preview.pack(fill="x", pady=(6, 8))

        def current_extra():
            return {k: [t.strip() for t in e.get().split(",") if t.strip()] for k, e in entries.items()}

        def refresh(_e=None):
            text = box.get("1.0", "end").strip()
            if not text:
                preview.configure(text="", fg=DIM)
                return
            m = LINE_RE.match(text)  # full log line or just the message both work
            if m:
                text = m.group(4)
            text = META_RE.sub("", COLOR_RE.sub("", text))
            found = parse_callout(text, build_tags(current_extra()))
            preview.configure(text=describe_callout(found), fg="#3fbf6f" if found else "#e2b53a")

        box.bind("<KeyRelease>", refresh)
        box.bind("<<Paste>>", lambda e: win.after(10, refresh))
        for e in entries.values():
            e.bind("<KeyRelease>", refresh)

        def save():
            self.settings["extra_tags"] = {k: v for k, v in current_extra().items() if v}
            save_settings(self.settings)
            self._reload_tags()
            win.destroy()

        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x")
        tk.Button(btns, text="Save", command=save, bg="#2a2e37", fg=FG, relief="flat", padx=12).pack(side="right")
        tk.Button(btns, text="Cancel", command=win.destroy, bg=BG, fg=DIM, relief="flat", padx=8).pack(side="right", padx=6)

    def _reload_tags(self):
        """Apply new tags and re-read today's log so callouts that were missed show up right away."""
        old = self.tracker
        self.tracker = Tracker()
        self.tracker.me = old.me
        self.tracker.tags = build_tags(self.settings.get("extra_tags"))
        self.tracker.kids_acked = old.kids_acked
        self.tailer.path, self.tailer.pos, self.tailer.buf = None, 0, b""

    def _save_names(self):
        self.settings["my_names"] = sorted(self.tracker.me)
        save_settings(self.settings)

    def _apply_opacity(self):
        self.root.attributes("-alpha", self.opacity.get() / 100)
        self.settings["opacity"] = self.opacity.get()
        save_settings(self.settings)

    def _apply_mode(self):
        mode = self.mode.get()
        for m, panel in self.panels.items():
            if m == mode:
                panel.pack(fill="x")
            else:
                panel.pack_forget()
        self.root.title(f"{APP_NAME} — {mode}")
        self.settings["mode"] = mode
        save_settings(self.settings)
        self._render_dot()
        # Size the window to the filled-in UI, then lock it so it doesn't jump as text changes.
        self.root.update_idletasks()
        w, h = max(self.root.winfo_reqwidth(), 400), self.root.winfo_reqheight()
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(300, h)

    def _section(self, parent, title):
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=10, pady=(10, 3))
        tk.Label(head, text=title, bg=BG, fg=FG, font=HEAD).pack(side="left")
        meta = tk.Label(head, text="", bg=BG, fg=FAINT, font=("Segoe UI", 8))
        meta.pack(side="right")
        return meta

    def _row(self, parent):
        row = tk.Frame(parent, bg=CARD, pady=3)
        row.pack(fill="x", padx=10, pady=1)
        return row

    def _kid_row(self, parent, tag, color):
        row = self._row(parent)
        tk.Label(row, text=tag, bg=color, fg="white", font=BOLD, width=3).pack(side="left")
        loc = tk.Label(row, text="", bg=CARD, fg=FG, font=BOLD, anchor="w")
        loc.pack(side="left", padx=(8, 4))
        when = tk.Label(row, text="", bg=CARD, fg=DIM, font=FONT)
        when.pack(side="right", padx=(0, 6))
        stat = tk.Label(row, text="", bg=CARD, font=("Segoe UI", 8))
        stat.pack(side="right", padx=6)
        return loc, stat, when

    def _clear_request(self, _e=None):
        """Dismiss the yellow dot: clears a team request and marks the current kids as shared."""
        self.tracker.request = None
        self.tracker.kids_acked = self.tracker.kids_complete_key()
        self._render_dot()

    def _render_dot(self):
        req = self.tracker.pending_request()
        if req:
            ts, who, msg = req
            self.dot.itemconfigure(self.dot_id, fill="#e2b53a")
            self.dot_text.configure(text=f"{who} ({age(ts)}): {msg[:60]}", fg="#e2b53a")
        elif self.mode.get() == "Halloween" and self.tracker.kids_alert():
            self.dot.itemconfigure(self.dot_id, fill="#e2b53a")
            self.dot_text.configure(text="all Kanto kids confirmed — copy to share", fg="#e2b53a")
        else:
            self.dot.itemconfigure(self.dot_id, fill="#3fbf6f")
            self.dot_text.configure(text="team: ok", fg=FAINT)

    def _copy(self, _e):
        self._clear_request()
        if self.mode.get() != "Halloween":
            self.copy_btn.configure(text="nothing yet", fg=FAINT)
        else:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.tracker.summary())
            self.copy_btn.configure(text="✓ copied", fg="#3fbf6f")
        self.root.after(1500, lambda: self.copy_btn.configure(text="⧉ copy", fg=FG))

    def _toggle_pin(self, _e):
        on = not self.root.attributes("-topmost")
        self.root.attributes("-topmost", on)
        self.pin.configure(text="📌 on top" if on else "📌 off", fg=DIM if on else FAINT)

    def poll(self):
        known = len(self.tracker.me)
        for line in self.tailer.read_lines():
            self.tracker.feed(line)
        if len(self.tracker.me) != known:  # a name was auto-detected from a battle
            self._save_names()
        self.root.after(POLL_MS, self.poll)

    def _status_text(self):
        if not self.game_dir:
            return "PokeMMO folder not found — Settings › Set PokeMMO folder", "#d0574a"
        if chat_logging_enabled(self.game_dir) is False:
            return "Turn on PokeMMO Settings › Chat › \"Log chat to disk\"", "#e2b53a"
        if not self.tailer.path:
            return "waiting for chat log — say something or wait for chat", FAINT
        return f"watching {os.path.basename(self.tailer.path)}", FAINT

    def _meta(self, label, last):
        if not last:
            label.configure(text="no callout yet", fg=FAINT)
            return
        ts, who = last
        stale = (datetime.now() - ts).total_seconds() > STALE_SECONDS
        label.configure(text=f"updated {age(ts)} ago · {who}", fg="#d0574a" if stale else FAINT)

    def _set_kid(self, widgets, name, status, first):
        loc, stat, when = widgets
        color, text = STATUS_STYLE.get(status, STATUS_STYLE["unknown"])
        loc.configure(text=(name or "?").replace("_", " "), fg=FG if name else FAINT)
        stat.configure(text=text if name else "", fg=color)
        when.configure(text=f"since {clock(first)} · {age(first)}" if first else "")

    def render(self):
        t = self.tracker
        self._render_dot()
        self._meta(self.swarm_meta, t.swarm_last)
        for code, (region, mon, loc, when) in self.swarm_rows.items():
            s = t.swarms[code]
            if s.value:
                poke, _, place = s.value.partition("_")
                mark = {"old": " (old)", "unconfirmed": " (?)"}.get(s.status, "")
                mon.configure(text=poke, fg=FG if s.status == "confirmed" else DIM)
                loc.configure(text=place.replace("_", " ") + mark)
                when.configure(text=f"since {clock(s.first)} · {age(s.first)}")
            else:
                mon.configure(text=f"{region}: ?", fg=FAINT)
                loc.configure(text="")
                when.configure(text="")

        self._meta(self.kids_meta, t.kids_last)
        for i, widgets in enumerate(self.kid_rows):
            slot = t.kids[i] if i < len(t.kids) else (None, "unknown", None)
            self._set_kid(widgets, *slot)
        self._set_kid(self.mb_row, t.mb.value, t.mb.status, t.mb.first)

        text, color = self._status_text()
        self.status.configure(text=text, fg=color)
        self.root.after(RENDER_MS, self.render)


if __name__ == "__main__":
    try:  # own taskbar entry/icon instead of grouping under Python
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MatsuHikaru.MatsusTracker")
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
