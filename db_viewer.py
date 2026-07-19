"""資料庫記憶可視化編輯器 — 卡片式 UI"""

import json, os, sqlite3, tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "rp_memory.db")

TABLES = {
    "memories": [
        "id",
        "timestamp",
        "user_id",
        "user_name",
        "topic",
        "content",
        "context",
        "mem_type",
    ],
    "world_lore": ["id", "category", "topic", "content"],
    "items": ["id", "timestamp", "name", "description", "quantity", "location"],
    "server_rules": ["id", "server_id", "rule_text", "added_at"],
    "quests": ["id", "title", "description", "status", "created_at", "updated_at"],
    "character_profiles": [
        "char_name",
        "gender_age",
        "intro",
        "appearance",
        "items",
        "experience",
        "updated_at",
    ],
}


def _pk_col(table: str) -> str:
    return "id" if table != "character_profiles" else "char_name"


def get_rows(table: str) -> list[tuple]:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    pk = _pk_col(table)
    cursor.execute(f"SELECT * FROM {table} ORDER BY {pk} DESC LIMIT 500")
    rows = cursor.fetchall()
    conn.close()
    return rows


def delete_row(table: str, row_id):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    pk = _pk_col(table)
    cursor.execute(f"DELETE FROM {table} WHERE {pk} = ?", (row_id,))
    conn.commit()
    conn.close()


def update_row(table: str, row_id, col: str, val: str):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    pk = _pk_col(table)
    cursor.execute(f"UPDATE {table} SET {col} = ? WHERE {pk} = ?", (val, row_id))
    conn.commit()
    conn.close()
    return rows


def delete_row(table: str, row_id: int):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()


def update_row(table: str, row_id: int, col: str, val: str):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute(f"UPDATE {table} SET {col} = ? WHERE id = ?", (val, row_id))
    conn.commit()
    conn.close()


import tkinter.font as tkfont


class Card(tk.Frame):
    def __init__(
        self,
        parent,
        table: str,
        row: tuple,
        cols: list,
        on_edit,
        on_delete,
        font_name="Segoe UI",
        font_size=9,
    ):
        super().__init__(parent, bd=1, relief="solid", padx=8, pady=6, bg="white")
        self.pack(fill="x", padx=4, pady=2)
        self.table = table
        self.row = row
        self.cols = cols
        self.on_edit = on_edit
        self.on_delete = on_delete
        self.font_name = font_name
        self.font_size = font_size
        self._build()

    @property
    def body_font(self):
        return (self.font_name, self.font_size)

    @property
    def small_font(self):
        return (self.font_name, max(7, self.font_size - 4), "bold")

    @property
    def bold_font(self):
        return (self.font_name, self.font_size + 1, "bold")

    def _build(self):
        row = self.row
        cols = self.cols
        top = tk.Frame(self, bg="white")
        top.pack(fill="x")
        tk.Label(
            top, text=f"ID: {row[0]}", font=self.bold_font, fg="#555", bg="white"
        ).pack(side="left")
        theme = "#4a90d9"
        tk.Button(
            top,
            text="✕ 刪除",
            fg="red",
            bd=0,
            cursor="hand2",
            font=self.body_font,
            command=lambda: self.on_delete(self.table, row[0]),
        ).pack(side="right")
        tk.Button(
            top,
            text="✎ 全部編輯",
            fg=theme,
            bd=0,
            cursor="hand2",
            font=self.body_font,
            command=lambda: self._edit_full(),
        ).pack(side="right", padx=5)

        for i, col in enumerate(cols):
            if i == 0:
                continue
            val = str(row[i]) if row[i] else ""
            display = val[:120] + ("..." if len(val) > 120 else "")
            frm = tk.Frame(self, bg="white")
            frm.pack(fill="x", pady=1)
            tk.Label(
                frm,
                text=col,
                font=self.small_font,
                fg="#888",
                width=12,
                anchor="w",
                bg="white",
            ).pack(side="left")
            content_frame = tk.Frame(frm, bg="white")
            content_frame.pack(side="left", fill="x", expand=True)
            if col in ("content", "description", "rule_text"):
                text = tk.Text(
                    content_frame,
                    height=3,
                    wrap="word",
                    font=self.body_font,
                    bd=0,
                    bg="#fafafa",
                    padx=4,
                    pady=2,
                )
                text.insert("1.0", val)
                text.configure(state="disabled")
                text.pack(fill="x")
                text.bind("<Button-1>", lambda e, t=text: t.configure(state="normal"))
                text.bind(
                    "<FocusOut>",
                    lambda e, t=text, c=col, i=row[0]: self._save_text(t, c, i),
                )
            else:
                lbl = tk.Label(
                    content_frame,
                    text=display,
                    anchor="w",
                    justify="left",
                    font=self.body_font,
                    wraplength=700,
                    bg="#fafafa",
                )
                lbl.pack(fill="x")
                lbl.bind(
                    "<Double-1>",
                    lambda e, c=col, v=val, i=row[0]: self._inline_edit(
                        content_frame, lbl, c, v, i
                    ),
                )

    def _save_text(self, text_widget, col, row_id):
        new_val = text_widget.get("1.0", "end-1c").strip()
        text_widget.configure(state="disabled")
        if new_val != self.row[self.cols.index(col)]:
            update_row(self.table, row_id, col, new_val)
            self.on_edit()

    def _inline_edit(self, parent, label, col, old_val, row_id):
        entry = tk.Entry(parent, font=("Segoe UI", 9))
        entry.insert(0, old_val)
        entry.pack(fill="x", before=label)
        label.pack_forget()
        entry.focus()

        def save(e=None):
            new = entry.get()
            entry.destroy()
            if new != old_val:
                update_row(self.table, row_id, col, new)
                self.on_edit()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)

    def _edit_full(self):
        theme = "#4a90d9"
        win = tk.Toplevel(self)
        win.title(f"編輯 {self.table} id={self.row[0]}")
        win.geometry("700x500")
        canvas = tk.Canvas(win)
        scroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas)
        frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        entries = {}
        for i, col in enumerate(self.cols):
            tk.Label(frame, text=col, font=("Segoe UI", 9, "bold")).pack(
                anchor="w", pady=(8, 0)
            )
            val = str(self.row[i]) if self.row[i] else ""
            if col in ("content", "description", "rule_text"):
                e = tk.Text(frame, height=4, font=("Segoe UI", 9))
                e.insert("1.0", val)
            else:
                e = tk.Entry(frame, font=("Segoe UI", 9))
                e.insert(0, val)
            e.pack(fill="x")
            entries[col] = e

        def save_all():
            for col, w in entries.items():
                new = (
                    w.get("1.0", "end-1c").strip()
                    if isinstance(w, tk.Text)
                    else w.get()
                )
                old = (
                    str(self.row[self.cols.index(col)])
                    if self.row[self.cols.index(col)]
                    else ""
                )
                if new != old:
                    update_row(self.table, self.row[0], col, new)
            self.on_edit()
            win.destroy()

        tk.Button(frame, text="儲存全部", bg=theme, fg="white", command=save_all).pack(
            pady=10
        )
        tk.Button(
            frame,
            text="刪除此記錄",
            fg="red",
            command=lambda: (
                delete_row(self.table, self.row[0]),
                self.on_edit(),
                win.destroy(),
            ),
        ).pack()


class DBViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("記憶資料庫瀏覽器")
        self.root.geometry("1000x750")
        self.root.configure(bg="#f0f0f0")

        self.font_name = tk.StringVar(value="微軟正黑體")
        self.font_size = tk.IntVar(value=20)
        self._filter_criteria = {}
        self._filter_entries = {}

        # 設定列
        bar = tk.Frame(root, bg="#e8e8e8", pady=4)
        bar.pack(fill="x")
        tk.Label(bar, text="字體:", bg="#e8e8e8").pack(side="left", padx=(10, 2))
        fonts = sorted(set(tkfont.families()))
        cb = ttk.Combobox(
            bar, textvariable=self.font_name, values=fonts, width=18, state="readonly"
        )
        cb.pack(side="left", padx=2)
        tk.Label(bar, text="大小:", bg="#e8e8e8").pack(side="left", padx=(10, 2))
        ttk.Spinbox(
            bar,
            textvariable=self.font_size,
            from_=10,
            to=36,
            width=4,
            command=self._refresh_all,
        ).pack(side="left", padx=2)
        ttk.Button(bar, text="套用", command=self._refresh_all).pack(
            side="left", padx=8
        )
        ttk.Button(root, text="⟳ 重新整理", command=self._refresh_all).pack(pady=3)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)

        self.canvases = {}
        self.scroll_frames = {}
        for tname in TABLES:
            frm = ttk.Frame(nb)
            nb.add(frm, text=tname)
            self._build_card_tab(frm, tname)

        # 解除全域滑鼠滾輪（切換分頁時重新綁定）
        nb.bind("<<NotebookTabChanged>>", lambda e: self._unbind_scroll())
        self.nb = nb

    def _build_card_tab(self, parent, table_name):
        cols = TABLES[table_name]

        def _get_distinct(col):
            try:
                conn = sqlite3.connect(DB_FILE, timeout=10)
                cursor = conn.cursor()
                cursor.execute(
                    f'SELECT DISTINCT "{col}" FROM {table_name} WHERE "{col}" IS NOT NULL AND "{col}" != \'\' ORDER BY 1 LIMIT 200'
                )
                vals = [r[0] for r in cursor.fetchall()]
                conn.close()
                return [str(v) for v in vals]
            except Exception:
                return []

        # 篩選列
        filt = tk.Frame(parent, bg="#e0e0e0", pady=3)
        filt.pack(fill="x")
        tk.Label(filt, text="篩選:", font=("Segoe UI", 9, "bold"), bg="#e0e0e0").pack(
            side="left", padx=5
        )

        entry_map = {}
        exclude_filter = {"id", "timestamp", "added_at", "created_at", "updated_at"}
        for col in cols:
            if col in exclude_filter:
                continue
            tk.Label(filt, text=col + ":", bg="#e0e0e0", font=("Segoe UI", 8)).pack(
                side="left"
            )
            distinct = _get_distinct(col)
            if distinct:
                cb = ttk.Combobox(
                    filt,
                    values=[""] + distinct,
                    width=10,
                    font=("Segoe UI", 8),
                    state="readonly",
                )
                cb.pack(side="left", padx=1)
                entry_map[col] = cb
            else:
                e = tk.Entry(filt, width=10, font=("Segoe UI", 8))
                e.pack(side="left", padx=1)
                entry_map[col] = e
        self._filter_entries[table_name] = entry_map

        tk.Label(filt, text="全文:", bg="#e0e0e0", font=("Segoe UI", 8)).pack(
            side="left", padx=(8, 2)
        )
        search_e = tk.Entry(filt, width=15, font=("Segoe UI", 8))
        search_e.pack(side="left")
        self._filter_entries[table_name]["__search__"] = search_e

        def do_filter():
            criteria = {}
            for col, e in entry_map.items():
                v = e.get().strip()
                if v:
                    criteria[col] = v.lower()
            txt = search_e.get().strip()
            if txt:
                criteria["__search__"] = txt.lower()
            self._filter_criteria[table_name] = criteria
            self._refresh_cards(table_name)

        def clear_filter():
            for col, e in entry_map.items():
                if isinstance(e, ttk.Combobox):
                    e.set("")
                else:
                    e.delete(0, tk.END)
            search_e.delete(0, tk.END)
            self._filter_criteria.pop(table_name, None)
            self._refresh_cards(table_name)

        tk.Button(filt, text="篩選", font=("Segoe UI", 8), command=do_filter).pack(
            side="left", padx=5
        )
        tk.Button(filt, text="清除", font=("Segoe UI", 8), command=clear_filter).pack(
            side="left"
        )

        # 卡片區
        container = tk.Frame(parent)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg="#f0f0f0", highlightthickness=0)
        scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#f0f0f0")

        scroll_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        win_id = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)

        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def _resize(event):
            canvas.itemconfig(win_id, width=event.width)

        canvas.bind("<Configure>", _resize)

        def _scroll(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _scroll))
        canvas.bind("<Leave>", lambda e: self._unbind_scroll())

        self.canvases[table_name] = canvas
        self.scroll_frames[table_name] = scroll_frame
        self._refresh_cards(table_name)

    def _unbind_scroll(self):
        try:
            self.root.unbind_all("<MouseWheel>")
        except Exception:
            pass

    def _refresh_cards(self, table_name):
        frame = self.scroll_frames.get(table_name)
        if not frame:
            return
        for w in frame.winfo_children():
            w.destroy()
        try:
            rows = get_rows(table_name)
            cols = TABLES[table_name]
            criteria = self._filter_criteria.get(table_name, {})
            for row in rows:
                if criteria:
                    ok = True
                    for col, val in criteria.items():
                        if col == "__search__":
                            content_fields = [
                                str(row[i])
                                for i, c in enumerate(cols)
                                if c
                                in (
                                    "content",
                                    "description",
                                    "rule_text",
                                    "topic",
                                    "name",
                                )
                            ]
                            if not any(
                                val in cf.lower() for cf in content_fields if cf
                            ):
                                ok = False
                                break
                        else:
                            try:
                                idx = cols.index(col)
                            except ValueError:
                                ok = False
                                break
                            cell = str(row[idx]).lower() if row[idx] else ""
                            if val not in cell:
                                ok = False
                                break
                    if not ok:
                        continue
                Card(
                    frame,
                    table_name,
                    row,
                    cols,
                    on_edit=lambda tn=table_name: self._refresh_cards(tn),
                    on_delete=lambda t, rid: (
                        delete_row(t, rid),
                        self._refresh_cards(t),
                    ),
                    font_name=self.font_name.get(),
                    font_size=self.font_size.get(),
                )
        except Exception as e:
            tk.Label(frame, text=f"載入失敗: {e}", bg="#f0f0f0").pack()

    def _refresh_all(self):
        for name in TABLES:
            self._refresh_cards(name)


if __name__ == "__main__":
    root = tk.Tk()
    app = DBViewer(root)
    root.mainloop()
