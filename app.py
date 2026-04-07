"""
Field Notes — personal knowledge base
Run with: python app.py
No installs needed — uses only Python built-ins.
Optional: pip install Pillow  (enables clipboard screenshots + JPEG/PNG images)
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3
from datetime import datetime
from pathlib import Path
import json
import base64
import uuid
import io

try:
    from PIL import Image, ImageTk, ImageGrab
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "notes.db"

FONTS = {
    "title":    ("Georgia", 13, "bold"),
    "body":     ("Courier New", 11),
    "meta":     ("Courier New", 9),
    "heading":  ("Georgia", 15, "bold"),
    "ui":       ("Segoe UI", 10),
    "ui_sm":    ("Segoe UI", 9),
    "ui_bold":  ("Segoe UI", 10, "bold"),
    "toolbar":  ("Segoe UI", 10, "bold"),
}

COLORS = {
    "bg":                "#F5F2EC",
    "surface":           "#FEFCF8",
    "border":            "#DDD8CC",
    "text":              "#1C1A16",
    "muted":             "#7A7568",
    "faint":             "#B0AA9E",
    "accent":            "#2F5C3E",
    "accent_light":      "#E8F0EB",
    "white":             "#FFFFFF",
    "note_bg":           "#FEFCF8",
    "pin_stripe":        "#2F5C3E",
    "separator":         "#E8E4DC",
    "hover":             "#F0EDE5",
    "sidebar":           "#FEFCF8",
    "filter_active":     "#2F5C3E",
    "filter_text_active":"#FFFFFF",
    "toolbar_bg":        "#F0EDE5",
    "toolbar_active":    "#DDD8CC",
}

DEFAULT_CATS = [
    ("Religion",           "#7c6fad", "personal"),
    ("RCT Project",        "#2f5c3e", "professional"),
    ("Scaling for Impact", "#1a5276", "professional"),
    ("Meeting Notes",      "#7d5a0a", "other"),
    ("Brainstorming",      "#7a2020", "other"),
]

# ── Database ──────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS categories (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#2f5c3e',
                type  TEXT NOT NULL DEFAULT 'other'
            );
            CREATE TABLE IF NOT EXISTS notes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT,
                content      TEXT,
                content_rich TEXT,
                category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                pinned       INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS tags (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
                tag_id  INTEGER REFERENCES tags(id)  ON DELETE CASCADE,
                PRIMARY KEY (note_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS note_images (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                img_key TEXT NOT NULL,
                data    BLOB NOT NULL
            );
        """)
        # Non-destructive migration: add content_rich column to existing DBs
        try:
            conn.execute("ALTER TABLE notes ADD COLUMN content_rich TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists

        cur = conn.execute("SELECT COUNT(*) FROM categories")
        if cur.fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO categories (name, color, type) VALUES (?,?,?)",
                DEFAULT_CATS
            )

def get_categories():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM categories ORDER BY type, name"
        ).fetchall()]

def add_category(name, color, cat_type):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO categories (name, color, type) VALUES (?,?,?)",
            (name.strip(), color, cat_type)
        )

def delete_category(cat_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))

def get_notes(search="", cat_type=None, category_id=None, tag=None,
              sort="newest", pinned_first=True):
    sql = """
        SELECT n.*, c.name AS cat_name, c.color AS cat_color, c.type AS cat_type,
               GROUP_CONCAT(DISTINCT t.name) AS tags,
               (SELECT COUNT(*) FROM note_images WHERE note_id = n.id) AS image_count
        FROM notes n
        LEFT JOIN categories c ON n.category_id = c.id
        LEFT JOIN note_tags nt ON n.id = nt.note_id
        LEFT JOIN tags t       ON nt.tag_id = t.id
        WHERE 1=1
    """
    params = []
    if search.strip():
        sql += " AND (LOWER(n.title) LIKE ? OR LOWER(n.content) LIKE ?)"
        s = f"%{search.lower().strip()}%"
        params += [s, s]
    if cat_type:
        sql += " AND c.type = ?"
        params.append(cat_type)
    if category_id:
        sql += " AND n.category_id = ?"
        params.append(category_id)
    if tag:
        sql += " AND t.name = ?"
        params.append(tag)

    sql += " GROUP BY n.id"
    order = {
        "newest":   "n.created_at DESC",
        "oldest":   "n.created_at ASC",
        "updated":  "n.updated_at DESC",
        "alpha":    "LOWER(COALESCE(n.title,'')) ASC",
        "category": "c.name ASC, n.created_at DESC",
    }.get(sort, "n.created_at DESC")

    pin_part = "n.pinned DESC, " if pinned_first else ""
    sql += f" ORDER BY {pin_part}{order}"

    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

def save_note(title, content_plain, content_rich, category_id, tags_str, note_id=None):
    """
    content_plain: plain text (used for search)
    content_rich:  JSON with formatting events (used by editor)
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tags = [t.strip().lower() for t in tags_str.split(",") if t.strip()]
    with get_conn() as conn:
        if note_id:
            conn.execute(
                "UPDATE notes SET title=?,content=?,content_rich=?,category_id=?,updated_at=? WHERE id=?",
                (title or None, content_plain, content_rich, category_id or None, now, note_id)
            )
        else:
            cur = conn.execute(
                "INSERT INTO notes (title,content,content_rich,category_id,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (title or None, content_plain, content_rich, category_id or None, now, now)
            )
            note_id = cur.lastrowid
        conn.execute("DELETE FROM note_tags WHERE note_id=?", (note_id,))
        for tag in tags:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            tid = conn.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO note_tags VALUES (?,?)", (note_id, tid))
    return note_id

def save_note_images(note_id, images):
    """Save images: dict of {img_key: bytes}."""
    with get_conn() as conn:
        conn.execute("DELETE FROM note_images WHERE note_id=?", (note_id,))
        for key, data in images.items():
            conn.execute(
                "INSERT INTO note_images (note_id, img_key, data) VALUES (?,?,?)",
                (note_id, key, sqlite3.Binary(data))
            )

def get_note_images(note_id):
    """Returns dict of {img_key: bytes}."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT img_key, data FROM note_images WHERE note_id=?", (note_id,)
        ).fetchall()
        return {r[0]: bytes(r[1]) for r in rows}

def delete_note(note_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM notes WHERE id=?", (note_id,))

def toggle_pin(note_id, current):
    with get_conn() as conn:
        conn.execute("UPDATE notes SET pinned=? WHERE id=?", (0 if current else 1, note_id))

def get_all_tags():
    with get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT name FROM tags ORDER BY name")]

def export_json():
    notes = get_notes(sort="newest", pinned_first=False)
    # Strip binary image data from export (images stay in DB only)
    for n in notes:
        if n.get("image_count", 0):
            n["_note"] = f"{n['image_count']} embedded image(s) not exported"
    return json.dumps({
        "categories": get_categories(),
        "notes": notes
    }, indent=2, default=str)

def hex_tint(hex_color, factor=0.85):
    """Blend a hex color toward white — Tkinter-safe light background."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "#F0EDE5"
    r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"

def fmt_dt(iso):
    try:
        dt = datetime.strptime(iso[:19], "%Y-%m-%d %H:%M:%S")
        try:
            return dt.strftime("%-d %b %Y")   # Linux/Mac
        except ValueError:
            return dt.strftime("%d %b %Y").lstrip("0")  # Windows fallback
    except Exception:
        return iso or ""

# ── Main App Window ───────────────────────────────────────────────────────────

class FieldNotesApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Field Notes")
        self.geometry("1200x800")
        self.minsize(900, 600)
        self.configure(bg=COLORS["bg"])

        # Filter state
        self.search_var    = tk.StringVar()
        self.sort_var      = tk.StringVar(value="newest")
        self.active_type   = None
        self.active_cat_id = None
        self.active_tag    = None

        self._build_ui()
        self._refresh()

        # Search on type
        self.search_var.trace_add("write", lambda *_: self._refresh())

    # ── UI construction ───────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()

    def _build_sidebar(self):
        sb = tk.Frame(self, bg=COLORS["sidebar"], width=220,
                      highlightbackground=COLORS["border"], highlightthickness=1)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.columnconfigure(0, weight=1)

        tk.Label(sb, text="Field Notes", font=("Georgia", 16, "bold italic"),
                 bg=COLORS["sidebar"], fg=COLORS["text"],
                 anchor="w", padx=16, pady=14).grid(row=0, column=0, sticky="ew")

        tk.Frame(sb, height=1, bg=COLORS["border"]).grid(row=1, column=0, sticky="ew")

        tk.Button(sb, text="＋  New note",
                  font=FONTS["ui_bold"], bg=COLORS["accent"], fg="#fff",
                  activebackground="#234a30", activeforeground="#fff",
                  relief="flat", cursor="hand2", pady=8,
                  command=self._open_new_note
                  ).grid(row=2, column=0, sticky="ew", padx=14, pady=(14, 4))

        tk.Frame(sb, height=1, bg=COLORS["border"]).grid(row=3, column=0, sticky="ew", pady=(10, 0))

        tk.Label(sb, text="SEARCH", font=("Courier New", 8, "bold"),
                 bg=COLORS["sidebar"], fg=COLORS["faint"],
                 anchor="w", padx=16).grid(row=4, column=0, sticky="ew", pady=(10, 2))

        search_frame = tk.Frame(sb, bg=COLORS["sidebar"])
        search_frame.grid(row=5, column=0, sticky="ew", padx=14)
        search_frame.columnconfigure(0, weight=1)

        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var,
                                     font=FONTS["body"], bg=COLORS["bg"],
                                     fg=COLORS["text"], relief="flat",
                                     highlightbackground=COLORS["border"],
                                     highlightthickness=1, insertbackground=COLORS["text"])
        self.search_entry.grid(row=0, column=0, sticky="ew", ipady=5)

        tk.Frame(sb, height=1, bg=COLORS["border"]).grid(row=6, column=0, sticky="ew", pady=(12, 0))

        tk.Label(sb, text="AREA", font=("Courier New", 8, "bold"),
                 bg=COLORS["sidebar"], fg=COLORS["faint"],
                 anchor="w", padx=16).grid(row=7, column=0, sticky="ew", pady=(10, 4))

        self.type_frame = tk.Frame(sb, bg=COLORS["sidebar"])
        self.type_frame.grid(row=8, column=0, sticky="ew", padx=10)
        self.type_frame.columnconfigure(0, weight=1)
        self._build_type_filters()

        tk.Frame(sb, height=1, bg=COLORS["border"]).grid(row=9, column=0, sticky="ew", pady=(12, 0))

        tk.Label(sb, text="CATEGORY", font=("Courier New", 8, "bold"),
                 bg=COLORS["sidebar"], fg=COLORS["faint"],
                 anchor="w", padx=16).grid(row=10, column=0, sticky="ew", pady=(10, 4))

        self.cat_frame = tk.Frame(sb, bg=COLORS["sidebar"])
        self.cat_frame.grid(row=11, column=0, sticky="ew", padx=10)
        self.cat_frame.columnconfigure(0, weight=1)
        self._build_cat_filters()

        tk.Frame(sb, height=1, bg=COLORS["border"]).grid(row=12, column=0, sticky="ew", pady=(12, 0))

        tk.Label(sb, text="TAG", font=("Courier New", 8, "bold"),
                 bg=COLORS["sidebar"], fg=COLORS["faint"],
                 anchor="w", padx=16).grid(row=13, column=0, sticky="ew", pady=(10, 4))

        self.tag_frame = tk.Frame(sb, bg=COLORS["sidebar"])
        self.tag_frame.grid(row=14, column=0, sticky="ew", padx=10)
        self.tag_frame.columnconfigure(0, weight=1)
        self._build_tag_filters()

        tk.Frame(sb, height=1, bg=COLORS["border"]).grid(row=15, column=0, sticky="ew", pady=(12, 0))

        tk.Label(sb, text="SORT", font=("Courier New", 8, "bold"),
                 bg=COLORS["sidebar"], fg=COLORS["faint"],
                 anchor="w", padx=16).grid(row=16, column=0, sticky="ew", pady=(10, 2))

        sort_opts   = ["newest", "oldest", "updated", "alpha", "category"]
        sort_labels = ["Newest first", "Oldest first", "Last updated", "Alphabetical", "By category"]
        self.sort_var.set("newest")

        for i, (val, label) in enumerate(zip(sort_opts, sort_labels)):
            rb = tk.Radiobutton(sb, text=label, variable=self.sort_var, value=val,
                                font=FONTS["ui_sm"], bg=COLORS["sidebar"],
                                fg=COLORS["muted"], selectcolor=COLORS["sidebar"],
                                activebackground=COLORS["sidebar"],
                                command=self._refresh, anchor="w", padx=14, cursor="hand2")
            rb.grid(row=17 + i, column=0, sticky="ew")

        btm = tk.Frame(sb, bg=COLORS["sidebar"])
        btm.grid(row=25, column=0, sticky="sew", padx=14, pady=14)
        btm.columnconfigure(0, weight=1)
        sb.rowconfigure(25, weight=1)

        tk.Button(btm, text="Manage categories", font=FONTS["ui_sm"],
                  bg=COLORS["bg"], fg=COLORS["muted"], relief="flat",
                  activebackground=COLORS["border"], cursor="hand2",
                  command=self._open_manage_cats
                  ).grid(row=0, column=0, sticky="ew", pady=(0, 4), ipady=4)

        tk.Button(btm, text="Export JSON backup", font=FONTS["ui_sm"],
                  bg=COLORS["bg"], fg=COLORS["muted"], relief="flat",
                  activebackground=COLORS["border"], cursor="hand2",
                  command=self._export
                  ).grid(row=1, column=0, sticky="ew", ipady=4)

    def _build_type_filters(self):
        for w in self.type_frame.winfo_children():
            w.destroy()

        types = [("All areas", None), ("Personal", "personal"),
                 ("Professional", "professional"), ("Other", "other")]
        for i, (label, val) in enumerate(types):
            active = self.active_type == val
            btn = tk.Button(
                self.type_frame, text=label,
                font=FONTS["ui_sm"],
                bg=COLORS["accent"] if active else COLORS["bg"],
                fg="#fff" if active else COLORS["muted"],
                activebackground=COLORS["accent"], activeforeground="#fff",
                relief="flat", anchor="w", padx=10, cursor="hand2",
                command=lambda v=val: self._set_type(v)
            )
            btn.grid(row=i, column=0, sticky="ew", pady=1, ipady=3)

    def _build_cat_filters(self):
        for w in self.cat_frame.winfo_children():
            w.destroy()

        cats = get_categories()
        if self.active_type:
            cats = [c for c in cats if c["type"] == self.active_type]

        active_all = self.active_cat_id is None
        btn = tk.Button(
            self.cat_frame, text="All categories",
            font=FONTS["ui_sm"],
            bg=COLORS["accent"] if active_all else COLORS["bg"],
            fg="#fff" if active_all else COLORS["muted"],
            activebackground=COLORS["accent"], activeforeground="#fff",
            relief="flat", anchor="w", padx=10, cursor="hand2",
            command=lambda: self._set_cat(None)
        )
        btn.grid(row=0, column=0, sticky="ew", pady=1, ipady=3)

        for i, c in enumerate(cats):
            active = self.active_cat_id == c["id"]
            btn = tk.Button(
                self.cat_frame,
                text=f"  {c['name']}",
                font=FONTS["ui_sm"],
                bg=COLORS["accent_light"] if active else COLORS["bg"],
                fg=c["color"] if active else COLORS["muted"],
                activebackground=COLORS["accent_light"],
                relief="flat", anchor="w", padx=10, cursor="hand2",
                command=lambda cid=c["id"]: self._set_cat(cid)
            )
            btn.grid(row=i + 1, column=0, sticky="ew", pady=1, ipady=3)

    def _build_tag_filters(self):
        for w in self.tag_frame.winfo_children():
            w.destroy()

        tags = get_all_tags()
        active_all = self.active_tag is None

        btn = tk.Button(
            self.tag_frame, text="All tags",
            font=FONTS["ui_sm"],
            bg=COLORS["accent"] if active_all else COLORS["bg"],
            fg="#fff" if active_all else COLORS["muted"],
            activebackground=COLORS["accent"], activeforeground="#fff",
            relief="flat", anchor="w", padx=10, cursor="hand2",
            command=lambda: self._set_tag(None)
        )
        btn.grid(row=0, column=0, sticky="ew", pady=1, ipady=3)

        for i, tag in enumerate(tags):
            active = self.active_tag == tag
            btn = tk.Button(
                self.tag_frame, text=f"  #{tag}",
                font=FONTS["ui_sm"],
                bg=COLORS["accent_light"] if active else COLORS["bg"],
                fg=COLORS["accent"] if active else COLORS["muted"],
                activebackground=COLORS["accent_light"],
                relief="flat", anchor="w", padx=10, cursor="hand2",
                command=lambda t=tag: self._set_tag(t)
            )
            btn.grid(row=i + 1, column=0, sticky="ew", pady=1, ipady=3)

    def _build_main(self):
        main = tk.Frame(self, bg=COLORS["bg"])
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        topbar = tk.Frame(main, bg=COLORS["surface"],
                          highlightbackground=COLORS["border"], highlightthickness=1)
        topbar.grid(row=0, column=0, sticky="ew")
        topbar.columnconfigure(1, weight=1)

        tk.Label(topbar, text="All Notes", font=("Georgia", 14, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"],
                 padx=20, pady=10).grid(row=0, column=0)

        self.count_label = tk.Label(topbar, text="", font=FONTS["ui_sm"],
                                    bg=COLORS["surface"], fg=COLORS["faint"])
        self.count_label.grid(row=0, column=1, sticky="w", padx=4)

        scroll_container = tk.Frame(main, bg=COLORS["bg"])
        scroll_container.grid(row=1, column=0, sticky="nsew")
        scroll_container.columnconfigure(0, weight=1)
        scroll_container.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(scroll_container, bg=COLORS["bg"],
                                highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.vscroll = ttk.Scrollbar(scroll_container, orient="vertical",
                                     command=self.canvas.yview)
        self.vscroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.vscroll.set)

        self.notes_frame = tk.Frame(self.canvas, bg=COLORS["bg"])
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.notes_frame, anchor="nw"
        )

        self.notes_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>",   self._on_mousewheel)
        self.canvas.bind_all("<Button-5>",   self._on_mousewheel)

    # ── Events ────────────────────────────────────────────────────

    def _on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _set_type(self, val):
        self.active_type = val
        self.active_cat_id = None
        self._build_type_filters()
        self._build_cat_filters()
        self._refresh()

    def _set_cat(self, cat_id):
        self.active_cat_id = cat_id
        self._build_cat_filters()
        self._refresh()

    def _set_tag(self, tag):
        self.active_tag = tag
        self._build_tag_filters()
        self._refresh()

    # ── Refresh / render ──────────────────────────────────────────

    def _refresh(self):
        for w in self.notes_frame.winfo_children():
            w.destroy()

        notes = get_notes(
            search=self.search_var.get(),
            cat_type=self.active_type,
            category_id=self.active_cat_id,
            tag=self.active_tag,
            sort=self.sort_var.get(),
        )

        total = len(get_notes())
        self.count_label.config(
            text=f"{len(notes)} note{'s' if len(notes) != 1 else ''}"
                 + (" filtered" if len(notes) < total else " total")
        )

        if not notes:
            tk.Label(
                self.notes_frame,
                text="No notes yet. Click '＋ New note' to begin.",
                font=FONTS["ui"], fg=COLORS["faint"], bg=COLORS["bg"],
                pady=60
            ).pack(fill="x", padx=40)
            return

        for i, note in enumerate(notes):
            self._render_note(note, i)

        self.canvas.yview_moveto(0)

    def _render_note(self, note, index):
        """Render one note as a block in the continuous scroll."""
        is_pinned = bool(note["pinned"])

        outer = tk.Frame(self.notes_frame, bg=COLORS["bg"])
        outer.pack(fill="x", padx=24, pady=(0, 0))
        outer.columnconfigure(0, weight=1)

        card = tk.Frame(
            outer,
            bg=COLORS["note_bg"],
            highlightbackground=COLORS["pin_stripe"] if is_pinned else COLORS["border"],
            highlightthickness=2 if is_pinned else 1,
        )
        card.pack(fill="x", pady=(10, 0))
        card.columnconfigure(0, weight=1)

        if is_pinned:
            stripe = tk.Frame(card, bg=COLORS["pin_stripe"], width=4)
            stripe.pack(side="left", fill="y")

        content_area = tk.Frame(card, bg=COLORS["note_bg"])
        content_area.pack(side="left", fill="both", expand=True, padx=16, pady=12)
        content_area.columnconfigure(0, weight=1)

        # Top row: category pill + date + pin indicator
        top_row = tk.Frame(content_area, bg=COLORS["note_bg"])
        top_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        top_row.columnconfigure(1, weight=1)

        if note.get("cat_name"):
            cat_color = note.get("cat_color") or COLORS["muted"]
            tk.Label(
                top_row,
                text=f"  {note['cat_name']}  ",
                font=("Courier New", 8, "bold"),
                bg=hex_tint(cat_color),
                fg=cat_color,
                relief="flat", padx=2, pady=1
            ).grid(row=0, column=0, sticky="w")

        if is_pinned:
            tk.Label(top_row, text="📌", font=("Segoe UI", 9),
                     bg=COLORS["note_bg"], fg=COLORS["faint"]
                     ).grid(row=0, column=1, sticky="w", padx=6)

        # Image badge
        img_count = note.get("image_count", 0)
        if img_count:
            tk.Label(top_row,
                     text=f"📷 {img_count} image{'s' if img_count > 1 else ''}",
                     font=("Segoe UI", 8),
                     bg=COLORS["note_bg"], fg=COLORS["faint"]
                     ).grid(row=0, column=1, sticky="e" if not is_pinned else "w",
                            padx=(20 if is_pinned else 0, 0))

        date_str = fmt_dt(note["created_at"])
        if note["updated_at"] != note["created_at"]:
            date_str += f"  ·  updated {fmt_dt(note['updated_at'])}"
        tk.Label(top_row, text=date_str, font=FONTS["meta"],
                 bg=COLORS["note_bg"], fg=COLORS["faint"]
                 ).grid(row=0, column=2, sticky="e")

        # Title
        if note.get("title"):
            tk.Label(
                content_area, text=note["title"],
                font=FONTS["title"],
                bg=COLORS["note_bg"], fg=COLORS["text"],
                anchor="w", wraplength=700, justify="left"
            ).grid(row=1, column=0, sticky="ew", pady=(2, 4))

        # Body — always display plain text in card view
        display_text = note.get("content") or ""
        if display_text:
            body = tk.Text(
                content_area,
                font=FONTS["body"],
                bg=COLORS["note_bg"], fg=COLORS["text"],
                relief="flat", bd=0,
                wrap="word",
                cursor="arrow",
                state="normal",
                padx=0, pady=0,
            )
            body.insert("1.0", display_text)
            body.config(state="disabled")

            lines = display_text.count("\n") + 1
            chars = len(display_text)
            estimated_lines = max(lines, chars // 80 + 1)
            body.config(height=min(estimated_lines + 1, 30))

            body.grid(row=2, column=0, sticky="ew", pady=(0, 4))

            body.bind("<MouseWheel>", self._on_mousewheel)
            body.bind("<Button-4>",   self._on_mousewheel)
            body.bind("<Button-5>",   self._on_mousewheel)

        # Tags row
        if note.get("tags"):
            tag_row = tk.Frame(content_area, bg=COLORS["note_bg"])
            tag_row.grid(row=3, column=0, sticky="ew", pady=(2, 4))
            for tag in note["tags"].split(","):
                tag = tag.strip()
                if tag:
                    tk.Label(
                        tag_row, text=f"#{tag}",
                        font=("Courier New", 8),
                        bg=COLORS["separator"], fg=COLORS["muted"],
                        relief="flat", padx=6, pady=1
                    ).pack(side="left", padx=(0, 4))

        # Action buttons row
        btn_row = tk.Frame(content_area, bg=COLORS["note_bg"])
        btn_row.grid(row=4, column=0, sticky="e", pady=(4, 0))

        def make_btn(parent, text, cmd, danger=False):
            return tk.Button(
                parent, text=text, font=FONTS["ui_sm"],
                bg=COLORS["bg"],
                fg="#8b2e2e" if danger else COLORS["muted"],
                activebackground=COLORS["border"],
                relief="flat", cursor="hand2", padx=8, pady=2,
                command=cmd
            )

        make_btn(btn_row, "Edit",   lambda n=note: self._open_edit_note(n)).pack(side="left", padx=2)
        pin_text = "Unpin" if is_pinned else "Pin"
        make_btn(btn_row, pin_text, lambda n=note: self._do_pin(n)).pack(side="left", padx=2)
        make_btn(btn_row, "Delete", lambda n=note: self._do_delete(n), danger=True).pack(side="left", padx=2)

        tk.Frame(self.notes_frame, height=1, bg=COLORS["separator"]).pack(fill="x", padx=24, pady=(10, 0))

    # ── Actions ───────────────────────────────────────────────────

    def _do_pin(self, note):
        toggle_pin(note["id"], note["pinned"])
        self._refresh()

    def _do_delete(self, note):
        title = note.get("title") or "this note"
        if messagebox.askyesno("Delete note",
                               f"Delete \"{title}\"?\nThis cannot be undone.",
                               icon="warning"):
            delete_note(note["id"])
            self._refresh()

    def _open_new_note(self):
        NoteEditor(self, note=None, on_save=self._on_note_saved)

    def _open_edit_note(self, note):
        NoteEditor(self, note=note, on_save=self._on_note_saved)

    def _on_note_saved(self):
        self._build_tag_filters()
        self._refresh()

    def _open_manage_cats(self):
        ManageCats(self, on_change=self._on_cats_changed)

    def _on_cats_changed(self):
        self._build_type_filters()
        self._build_cat_filters()
        self._refresh()

    def _export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"field_notes_{datetime.now().strftime('%Y%m%d')}.json"
        )
        if path:
            Path(path).write_text(export_json(), encoding="utf-8")
            messagebox.showinfo("Exported", f"Saved to:\n{path}")


# ── Note Editor Dialog ────────────────────────────────────────────────────────

RICH_TAGS = ("bold", "italic", "underline", "bullet")

class NoteEditor(tk.Toplevel):
    def __init__(self, parent, note=None, on_save=None):
        super().__init__(parent)
        self.note    = note
        self.on_save = on_save
        self.cats    = get_categories()
        self._photos     = {}   # img_key -> PhotoImage (prevents GC)
        self._image_data = {}   # img_key -> bytes (for saving)

        self.title("Edit note" if note else "New note")
        self.geometry("760x640")
        self.configure(bg=COLORS["surface"])
        self.resizable(True, True)
        self.grab_set()
        self.focus_set()
        self.transient(parent)

        self._build()
        if note:
            self._load(note)
        else:
            self.content_box.focus_set()

    # ── Build ─────────────────────────────────────────────────────

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # Title
        tk.Label(self, text="Title (optional)", font=FONTS["ui_sm"],
                 bg=COLORS["surface"], fg=COLORS["muted"],
                 anchor="w").grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 2))

        self.title_var = tk.StringVar()
        tk.Entry(self, textvariable=self.title_var,
                 font=("Georgia", 13, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"], relief="flat",
                 highlightbackground=COLORS["border"], highlightthickness=1,
                 insertbackground=COLORS["text"]
                 ).grid(row=1, column=0, sticky="ew", padx=20, ipady=6)

        # Content label
        tk.Label(self, text="Content", font=FONTS["ui_sm"],
                 bg=COLORS["surface"], fg=COLORS["muted"],
                 anchor="w").grid(row=2, column=0, sticky="nw", padx=20, pady=(10, 2))

        # Formatting toolbar
        toolbar = self._build_toolbar()
        toolbar.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 2))

        # Content text area
        content_frame = tk.Frame(self, bg=COLORS["surface"])
        content_frame.grid(row=4, column=0, sticky="nsew", padx=20)
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        self.content_box = tk.Text(
            content_frame,
            font=FONTS["body"],
            bg=COLORS["bg"], fg=COLORS["text"],
            relief="flat",
            highlightbackground=COLORS["border"], highlightthickness=1,
            insertbackground=COLORS["text"],
            wrap="word", padx=10, pady=8,
            undo=True,
        )
        self.content_box.grid(row=0, column=0, sticky="nsew")

        cscroll = ttk.Scrollbar(content_frame, command=self.content_box.yview)
        cscroll.grid(row=0, column=1, sticky="ns")
        self.content_box.config(yscrollcommand=cscroll.set)

        self._configure_tags()

        # Keyboard shortcuts
        self.content_box.bind("<Control-b>", lambda e: (self._toggle_format("bold"),   "break")[1])
        self.content_box.bind("<Control-i>", lambda e: (self._toggle_format("italic"), "break")[1])
        self.content_box.bind("<Control-u>", lambda e: (self._toggle_format("underline"), "break")[1])
        self.content_box.bind("<Control-v>", self._on_paste)

        # Bottom row: category + tags + buttons
        bottom = tk.Frame(self, bg=COLORS["surface"])
        bottom.grid(row=5, column=0, sticky="ew", padx=20, pady=14)
        bottom.columnconfigure(1, weight=1)

        tk.Label(bottom, text="Category:", font=FONTS["ui_sm"],
                 bg=COLORS["surface"], fg=COLORS["muted"]
                 ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.cat_var = tk.StringVar(value="— none —")
        cat_names = ["— none —"] + [c["name"] for c in self.cats]
        ttk.Combobox(bottom, textvariable=self.cat_var,
                     values=cat_names, state="readonly",
                     font=FONTS["ui_sm"], width=22
                     ).grid(row=0, column=1, sticky="w", padx=(6, 0), pady=(0, 4))

        tk.Label(bottom, text="Tags:", font=FONTS["ui_sm"],
                 bg=COLORS["surface"], fg=COLORS["muted"]
                 ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        self.tags_var = tk.StringVar()
        tk.Entry(bottom, textvariable=self.tags_var,
                 font=FONTS["ui_sm"],
                 bg=COLORS["bg"], fg=COLORS["text"], relief="flat",
                 highlightbackground=COLORS["border"], highlightthickness=1,
                 insertbackground=COLORS["text"]
                 ).grid(row=1, column=1, sticky="ew", padx=(6, 0), ipady=4, pady=(0, 10))

        tk.Label(bottom, text="comma-separated, e.g. theory-of-change, land-tenure",
                 font=("Courier New", 8), bg=COLORS["surface"], fg=COLORS["faint"]
                 ).grid(row=2, column=1, sticky="w", padx=(6, 0))

        btn_frame = tk.Frame(bottom, bg=COLORS["surface"])
        btn_frame.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))

        tk.Button(btn_frame, text="Cancel", font=FONTS["ui"],
                  bg=COLORS["bg"], fg=COLORS["muted"], relief="flat",
                  activebackground=COLORS["border"], cursor="hand2",
                  padx=14, pady=6, command=self.destroy
                  ).pack(side="left", padx=(0, 8))

        tk.Button(btn_frame, text="Save note", font=FONTS["ui_bold"],
                  bg=COLORS["accent"], fg="#fff", relief="flat",
                  activebackground="#234a30", cursor="hand2",
                  padx=14, pady=6, command=self._save
                  ).pack(side="left")

        self.bind("<Control-Return>", lambda e: self._save())

    def _build_toolbar(self):
        """Build the formatting toolbar above the content area."""
        bar = tk.Frame(self, bg=COLORS["toolbar_bg"],
                       highlightbackground=COLORS["border"], highlightthickness=1)

        def tbtn(text, cmd, tooltip_text=""):
            b = tk.Button(bar, text=text, font=FONTS["toolbar"],
                          bg=COLORS["toolbar_bg"], fg=COLORS["text"],
                          relief="flat", cursor="hand2",
                          padx=10, pady=3,
                          activebackground=COLORS["toolbar_active"],
                          command=cmd)
            b.pack(side="left", padx=1, pady=2)
            return b

        def sep():
            tk.Frame(bar, width=1, bg=COLORS["border"]).pack(
                side="left", fill="y", padx=5, pady=4)

        tbtn("B",  lambda: self._toggle_format("bold"))
        tbtn("I",  lambda: self._toggle_format("italic"))
        tbtn("U",  lambda: self._toggle_format("underline"))
        sep()
        tbtn("•  Bullet", self._insert_bullet)
        sep()
        tbtn("🖼  Image", self._insert_image_from_file)
        tbtn("📋  Paste image", self._paste_image)

        if not PIL_AVAILABLE:
            tk.Label(bar,
                     text="  tip: pip install Pillow for clipboard + JPEG support",
                     font=("Segoe UI", 8), bg=COLORS["toolbar_bg"], fg=COLORS["faint"]
                     ).pack(side="right", padx=8)

        return bar

    def _configure_tags(self):
        """Configure visual appearance of rich-text tags."""
        base_font = FONTS["body"]
        family, size = base_font[0], base_font[1]
        self.content_box.tag_configure("bold",      font=(family, size, "bold"))
        self.content_box.tag_configure("italic",    font=(family, size, "italic"))
        self.content_box.tag_configure("underline", underline=True)
        self.content_box.tag_configure("bullet",    lmargin1=20, lmargin2=30)

    # ── Formatting actions ────────────────────────────────────────

    def _toggle_format(self, tag):
        """Toggle a formatting tag on the current selection."""
        try:
            sel_start = self.content_box.index("sel.first")
            sel_end   = self.content_box.index("sel.last")
        except tk.TclError:
            return  # No selection — nothing to do

        # Check if the entire selection is already fully covered by this tag
        ranges = self.content_box.tag_ranges(tag)
        covered = False
        for i in range(0, len(ranges), 2):
            if (self.content_box.compare(ranges[i],     "<=", sel_start) and
                    self.content_box.compare(ranges[i + 1], ">=", sel_end)):
                covered = True
                break

        if covered:
            self.content_box.tag_remove(tag, sel_start, sel_end)
        else:
            self.content_box.tag_add(tag, sel_start, sel_end)

    def _insert_bullet(self):
        """Toggle a bullet point at the start of the current line."""
        insert_pos = self.content_box.index(tk.INSERT)
        line_start = self.content_box.index(f"{insert_pos} linestart")
        line_text  = self.content_box.get(line_start, f"{insert_pos} lineend")

        if line_text.startswith("• "):
            self.content_box.delete(line_start, f"{line_start}+2c")
            line_end = self.content_box.index(f"{line_start} lineend")
            self.content_box.tag_remove("bullet", line_start, f"{line_end}+1c")
        else:
            self.content_box.insert(line_start, "• ")
            line_end = self.content_box.index(f"{line_start} lineend")
            self.content_box.tag_add("bullet", line_start, f"{line_end}+1c")

    # ── Image handling ────────────────────────────────────────────

    def _bytes_to_photo(self, data):
        """Convert raw image bytes to a tkinter PhotoImage, resizing if needed."""
        try:
            if PIL_AVAILABLE:
                img = Image.open(io.BytesIO(data))
                img.thumbnail((640, 480), Image.LANCZOS)
                return ImageTk.PhotoImage(img)
            else:
                # Native tkinter: supports PNG and GIF only
                b64 = base64.b64encode(data).decode()
                return tk.PhotoImage(data=b64)
        except Exception as exc:
            print(f"[Field Notes] Image load error: {exc}")
            return None

    def _insert_image_bytes(self, data):
        """Insert image bytes into the content box at the current cursor."""
        photo = self._bytes_to_photo(data)
        if not photo:
            messagebox.showerror(
                "Image error",
                "Could not load this image.\n"
                "Install Pillow for JPEG/BMP/WebP support:\n  pip install Pillow"
            )
            return
        img_key = f"img_{uuid.uuid4().hex[:12]}"
        self._photos[img_key]     = photo
        self._image_data[img_key] = data
        self.content_box.image_create(tk.INSERT, image=photo, name=img_key)
        self.content_box.insert(tk.INSERT, "\n")   # newline after image

    def _insert_image_from_file(self):
        """Open a file dialog and insert the chosen image."""
        if PIL_AVAILABLE:
            ftypes = [("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tiff"), ("All files", "*.*")]
        else:
            ftypes = [("Images (PNG/GIF)", "*.png *.gif"), ("All files", "*.*")]

        path = filedialog.askopenfilename(filetypes=ftypes, title="Insert image")
        if not path:
            return
        with open(path, "rb") as f:
            self._insert_image_bytes(f.read())

    def _paste_image(self, event=None):
        """Paste an image from the clipboard (requires Pillow)."""
        if not PIL_AVAILABLE:
            messagebox.showinfo(
                "Pillow required",
                "To paste images from the clipboard, install Pillow:\n  pip install Pillow"
            )
            return "break"
        try:
            img = ImageGrab.grabclipboard()
            if img is not None and hasattr(img, "size"):
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                self._insert_image_bytes(buf.getvalue())
                return "break"   # Stop default text paste
        except Exception as exc:
            print(f"[Field Notes] Clipboard paste error: {exc}")
        return None  # Fall through to normal text paste

    def _on_paste(self, event):
        """Ctrl+V handler: try image paste first, fall back to text paste."""
        result = self._paste_image(event)
        if result == "break":
            return "break"
        # Default text paste behaviour
        return None

    # ── Serialisation ─────────────────────────────────────────────

    def _get_rich_content(self):
        """
        Serialise the Text widget content.
        Returns (plain_text: str, rich_json: str).
        plain_text is stored in notes.content for full-text search.
        rich_json is stored in notes.content_rich for editor round-trips.
        """
        events = []
        for key, value, index in self.content_box.dump("1.0", "end-1c", all=True):
            if key == "text":
                events.append({"k": "t", "v": value})
            elif key == "tagon"  and value in RICH_TAGS:
                events.append({"k": "on", "v": value})
            elif key == "tagoff" and value in RICH_TAGS:
                events.append({"k": "off", "v": value})
            elif key == "image":
                events.append({"k": "img", "v": value})   # value = img_key name

        plain = "".join(e["v"] for e in events if e["k"] == "t").strip()
        rich  = json.dumps({"v": 2, "events": events})
        return plain, rich

    def _load_rich_content(self, content_rich, note_id=None):
        """
        Deserialise content_rich JSON into the Text widget.
        Returns True on success, False if fallback to plain text is needed.
        """
        try:
            data = json.loads(content_rich)
            if data.get("v") != 2:
                return False

            images      = get_note_images(note_id) if note_id else {}
            char_count  = 0
            tag_starts  = {}    # tag_name -> char offset when opened

            for event in data.get("events", []):
                k, v = event["k"], event["v"]

                if k == "t":
                    self.content_box.insert("end", v)
                    char_count += len(v)

                elif k == "on":
                    tag_starts[v] = char_count

                elif k == "off":
                    start = tag_starts.pop(v, None)
                    if start is not None:
                        self.content_box.tag_add(
                            v,
                            f"1.0 + {start} chars",
                            f"1.0 + {char_count} chars"
                        )

                elif k == "img":
                    img_key = v
                    if img_key in images:
                        photo = self._bytes_to_photo(images[img_key])
                        if photo:
                            self._photos[img_key]     = photo
                            self._image_data[img_key] = images[img_key]
                            self.content_box.image_create("end", image=photo, name=img_key)
                            char_count += 1   # embedded image = 1 char in tkinter index

            return True
        except Exception as exc:
            print(f"[Field Notes] Rich content load error: {exc}")
            return False

    # ── Load / Save ───────────────────────────────────────────────

    def _load(self, note):
        self.title_var.set(note.get("title") or "")

        # Try rich content first, fall back to plain
        loaded = False
        if note.get("content_rich"):
            loaded = self._load_rich_content(note["content_rich"], note.get("id"))
        if not loaded:
            self.content_box.insert("1.0", note.get("content") or "")

        if note.get("cat_name"):
            self.cat_var.set(note["cat_name"])

        with get_conn() as conn:
            rows = conn.execute("""
                SELECT t.name FROM tags t
                JOIN note_tags nt ON t.id = nt.tag_id
                WHERE nt.note_id = ?
            """, (note["id"],)).fetchall()
            self.tags_var.set(", ".join(r[0] for r in rows))

    def _save(self):
        title = self.title_var.get().strip()
        plain, rich = self._get_rich_content()

        if not title and not plain and not self._image_data:
            messagebox.showwarning("Empty note", "Add a title or some content first.")
            return

        cat_name = self.cat_var.get()
        cat_id   = next((c["id"] for c in self.cats if c["name"] == cat_name), None)

        note_id = save_note(
            title, plain, rich, cat_id, self.tags_var.get(),
            note_id=self.note["id"] if self.note else None
        )
        save_note_images(note_id, self._image_data)

        if self.on_save:
            self.on_save()
        self.destroy()


# ── Manage Categories Dialog ──────────────────────────────────────────────────

class ManageCats(tk.Toplevel):
    def __init__(self, parent, on_change=None):
        super().__init__(parent)
        self.on_change = on_change
        self.title("Manage categories")
        self.geometry("420x520")
        self.configure(bg=COLORS["surface"])
        self.resizable(False, True)
        self.grab_set()
        self.transient(parent)
        self._build()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        tk.Label(self, text="Categories", font=("Georgia", 14, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"],
                 anchor="w", padx=20, pady=12).grid(row=0, column=0, sticky="ew")

        list_frame = tk.Frame(self, bg=COLORS["surface"])
        list_frame.grid(row=1, column=0, sticky="nsew", padx=20)
        list_frame.columnconfigure(0, weight=1)
        self.list_inner = list_frame
        self._populate_list()

        tk.Frame(self, height=1, bg=COLORS["border"]).grid(row=2, column=0, sticky="ew", pady=(10, 0))

        add_frame = tk.Frame(self, bg=COLORS["surface"])
        add_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=14)
        add_frame.columnconfigure(1, weight=1)

        tk.Label(add_frame, text="Add category", font=FONTS["ui_bold"],
                 bg=COLORS["surface"], fg=COLORS["text"]
                 ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        tk.Label(add_frame, text="Name", font=FONTS["ui_sm"],
                 bg=COLORS["surface"], fg=COLORS["muted"]
                 ).grid(row=1, column=0, sticky="w")
        self.new_name = tk.StringVar()
        tk.Entry(add_frame, textvariable=self.new_name, font=FONTS["ui"],
                 bg=COLORS["bg"], relief="flat",
                 highlightbackground=COLORS["border"], highlightthickness=1
                 ).grid(row=1, column=1, sticky="ew", padx=(8, 0), ipady=4)

        tk.Label(add_frame, text="Type", font=FONTS["ui_sm"],
                 bg=COLORS["surface"], fg=COLORS["muted"]
                 ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.new_type = tk.StringVar(value="other")
        ttk.Combobox(add_frame, textvariable=self.new_type,
                     values=["personal", "professional", "other"],
                     state="readonly", font=FONTS["ui_sm"], width=16
                     ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        tk.Label(add_frame, text="Color", font=FONTS["ui_sm"],
                 bg=COLORS["surface"], fg=COLORS["muted"]
                 ).grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.new_color = tk.StringVar(value="#2f5c3e")
        color_row = tk.Frame(add_frame, bg=COLORS["surface"])
        color_row.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        self.color_preview = tk.Label(color_row, bg="#2f5c3e", width=3,
                                      relief="flat",
                                      highlightbackground=COLORS["border"],
                                      highlightthickness=1)
        self.color_preview.pack(side="left", padx=(0, 6), ipady=8)
        tk.Button(color_row, text="Pick color", font=FONTS["ui_sm"],
                  bg=COLORS["bg"], fg=COLORS["muted"], relief="flat",
                  activebackground=COLORS["border"], cursor="hand2",
                  padx=8, command=self._pick_color
                  ).pack(side="left")

        tk.Button(add_frame, text="Add category", font=FONTS["ui_bold"],
                  bg=COLORS["accent"], fg="#fff", relief="flat",
                  activebackground="#234a30", cursor="hand2",
                  padx=12, pady=6, command=self._add
                  ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _populate_list(self):
        for w in self.list_inner.winfo_children():
            w.destroy()

        for c in get_categories():
            row = tk.Frame(self.list_inner, bg=COLORS["bg"],
                           highlightbackground=COLORS["border"],
                           highlightthickness=1)
            row.pack(fill="x", pady=2)
            row.columnconfigure(1, weight=1)

            tk.Label(row, bg=c["color"], width=2,
                     highlightbackground=c["color"], highlightthickness=1
                     ).grid(row=0, column=0, sticky="ns", padx=(8, 6), ipady=10)

            tk.Label(row, text=c["name"], font=FONTS["ui"],
                     bg=COLORS["bg"], fg=COLORS["text"], anchor="w"
                     ).grid(row=0, column=1, sticky="w", pady=6)

            tk.Label(row, text=c["type"], font=FONTS["ui_sm"],
                     bg=COLORS["bg"], fg=COLORS["faint"], anchor="e"
                     ).grid(row=0, column=2, sticky="e", padx=8)

            tk.Button(row, text="✕", font=FONTS["ui_sm"],
                      bg=COLORS["bg"], fg=COLORS["faint"], relief="flat",
                      activebackground=COLORS["border"], cursor="hand2",
                      command=lambda cid=c["id"]: self._delete(cid)
                      ).grid(row=0, column=3, padx=6)

    def _pick_color(self):
        from tkinter.colorchooser import askcolor
        result = askcolor(color=self.new_color.get(), title="Pick category color")
        if result[1]:
            self.new_color.set(result[1])
            self.color_preview.config(bg=result[1])

    def _add(self):
        name = self.new_name.get().strip()
        if not name:
            messagebox.showwarning("Missing name", "Enter a category name.")
            return
        add_category(name, self.new_color.get(), self.new_type.get())
        self.new_name.set("")
        self._populate_list()
        if self.on_change:
            self.on_change()

    def _delete(self, cat_id):
        if messagebox.askyesno("Remove category",
                               "Remove this category?\nNotes in it won't be deleted.",
                               icon="warning"):
            delete_category(cat_id)
            self._populate_list()
            if self.on_change:
                self.on_change()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app = FieldNotesApp()
    app.mainloop()
