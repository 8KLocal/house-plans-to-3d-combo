"""House plans -> 3D: simple desktop app with an in-app model editor (tkinter).

    ./open-app.sh            (or: .venv/bin/python -m hp3d.app)

Main screen = three plain steps (pick a file, build, change it), plus a short
one-question-at-a-time check after each build. The file can be a picture, a PDF
blueprint or a 3D model (STL/OBJ/PLY/3MF/GLB); unreadable types are refused with
a plain message. Everything
technical (exact scale, PDF quality, crop numbers, window heights, part list)
lives under "More options", which starts closed. Technical details of every run
go to out/<project>/app_log.txt and report.json, not the main screen.

Private testing tool: bundled parts have non-commercial / unlicensed / GPL terms,
so it is not sellable as-is. See README.md and INTEGRATION.md.
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk

from . import ROOT
from . import editor as E
from . import furniture as furn_mod
from . import quiz as Q
from .colors import PLAN_HEX
from .raster import OPEN_PATTERNS, UnsupportedFile, file_kind

OUT = ROOT / "out"
COL = dict(PLAN_HEX)   # plan legend; the 3D preview uses the same hues (hp3d.colors)
PINK = "#ff00c8"
NAMES = {"wall_h": "Wall height", "floor_t": "Floor thickness", "sill": "Window bottom",
         "lintel": "Window top", "px_per_inch": "Size (scale)"}


def feet_text(inches: float) -> str:
    ft, inch = divmod(round(float(inches), 1), 12)
    if inch < 0.05:
        return f"{int(ft)} ft"
    return f"{int(ft)} ft {inch:g} in" if ft else f"{inch:g} in"


def parse_feet(s: str) -> float:
    """'12' -> 144 in (the field is labelled feet); '12 ft 6 in', "12'6\"", '150 cm' also work."""
    s = s.strip()
    if not s:
        raise ValueError("type a number of feet first")
    try:
        v = float(s) * 12
    except ValueError:
        v, _ = E.parse_length(s)
    if v <= 0:
        raise ValueError("the measurement must be more than 0")
    return v


class Scrollable(ttk.Frame):
    """Vertical scroll container so 'More options' never pushes buttons off-screen."""

    def __init__(self, parent, width):
        super().__init__(parent)
        self.c = tk.Canvas(self, width=width, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.c.yview)
        self.inner = ttk.Frame(self.c)
        self.inner.bind("<Configure>", lambda _: self.c.configure(scrollregion=self.c.bbox("all")))
        self._win = self.c.create_window(0, 0, anchor="nw", window=self.inner)
        self.c.bind("<Configure>", lambda e: self.c.itemconfigure(self._win, width=e.width))
        self.c.configure(yscrollcommand=sb.set)
        self.c.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for ev in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.c.bind_all(ev, self._wheel, add="+")

    def _wheel(self, ev):
        if not str(ev.widget).startswith(str(self)):
            return
        d = -1 if (getattr(ev, "num", 0) == 4 or getattr(ev, "delta", 0) > 0) else 1
        self.c.yview_scroll(d, "units")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("House plans to 3D")
        self.geometry("1400x900")
        for f in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            tkfont.nametofont(f).configure(size=11)
        base = tkfont.nametofont("TkDefaultFont")
        self.f_step = base.copy(); self.f_step.configure(size=13, weight="bold")
        self.f_big = base.copy(); self.f_big.configure(size=12, weight="bold")
        self.f_sub = base.copy(); self.f_sub.configure(size=11, weight="bold")
        self.f_log = base.copy(); self.f_log.configure(size=10)
        st = ttk.Style(self)
        st.configure("Step.TLabel", font=self.f_step)
        st.configure("Big.TButton", font=self.f_big, padding=8)
        st.configure("Hint.TLabel", foreground="#555")
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.params = E.JobParams()
        self.polys: dict = {k: [] for k in E.KINDS}
        self.parts: list[dict] = []
        self.selected: tuple | None = None
        self.undo_stack: list = []
        self.mode = "select"
        self.crop_source = "none"
        self._measure_pts: list = []
        self._drag = None
        self._imgs: dict = {}
        self._scale = 1.0
        self.kind = "plan"
        self._build_ui()
        self.after(100, self._pump)

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True)
        sc = Scrollable(pw, width=430)
        right = ttk.Frame(pw)
        pw.add(sc, weight=0)
        pw.add(right, weight=1)
        L = sc.inner
        pad = dict(padx=10, pady=3, sticky="we")

        # vars (some only shown under More options)
        self.v_plan, self.v_job = tk.StringVar(), tk.StringVar(value="job1")
        self.v_ppi, self.v_dpi = tk.StringVar(), tk.StringVar(value="100")
        self.v_wall_ft = tk.StringVar(value="8")
        self.v_measure = tk.StringVar()
        self.v_skipcc = tk.BooleanVar(value=False)
        self.v_fixtures = tk.BooleanVar(value=False)
        self.v_answer = tk.StringVar()
        self.v_crop = [tk.StringVar() for _ in range(4)]
        self.v_say = tk.StringVar()
        self.v_step, self.v_unit = tk.StringVar(value="6"), tk.StringVar(value="in")
        self.v_pct = tk.StringVar(value="10")
        self.v_kind = tk.StringVar(value="wall")
        self.v_add = [tk.StringVar() for _ in range(4)]
        self.v_adj = {k: tk.StringVar() for k in ("floor_t", "sill", "lintel")}

        r = 0
        # ---- Step 1
        ttk.Label(L, text="1.  Pick a file", style="Step.TLabel").grid(row=r, column=0, columnspan=2, padx=10, pady=(10, 2), sticky="w"); r += 1
        ttk.Button(L, text="Choose a file (picture, PDF or 3D model)...", command=self.browse).grid(row=r, column=0, columnspan=2, **pad); r += 1
        self.l_plan = ttk.Label(L, text="No file chosen yet. A plan picture, a PDF blueprint, or an STL / 3MF / OBJ / GLB model.",
                                style="Hint.TLabel", wraplength=390)
        self.l_plan.grid(row=r, column=0, columnspan=2, padx=10, sticky="w"); r += 1
        self.b_unit = ttk.Button(L, text="Use just this unit (green outline)", command=self.auto_crop)
        self.b_unit.grid(row=r, column=0, columnspan=2, **pad); r += 1
        self.l_crop = ttk.Label(L, text="", style="Hint.TLabel", wraplength=390)
        self.l_crop.grid(row=r, column=0, columnspan=2, padx=10, sticky="w"); r += 1

        ttk.Label(L, text="Tell me one measurement from the plan so the model is the right size:",
                  wraplength=390).grid(row=r, column=0, columnspan=2, padx=10, pady=(8, 0), sticky="w"); r += 1
        mf = ttk.Frame(L); mf.grid(row=r, column=0, columnspan=2, **pad); r += 1
        ttk.Entry(mf, textvariable=self.v_measure, width=7).pack(side="left")
        ttk.Label(mf, text="feet").pack(side="left", padx=(4, 10))
        ttk.Button(mf, text="Show me where on the plan", command=self.start_measure).pack(side="left", fill="x", expand=True)
        self.l_size = ttk.Label(L, text="Size: not set yet", style="Hint.TLabel", wraplength=390)
        self.l_size.grid(row=r, column=0, columnspan=2, padx=10, sticky="w"); r += 1

        # ---- Step 2
        ttk.Label(L, text="2.  Build the 3D model", style="Step.TLabel").grid(row=r, column=0, columnspan=2, padx=10, pady=(14, 2), sticky="w"); r += 1
        self.b_run = ttk.Button(L, text="Build my 3D model", style="Big.TButton", command=self.run_detect)
        self.b_run.grid(row=r, column=0, columnspan=2, **pad); r += 1

        # ---- Quick questions (only shown while some are open)
        self.qf = ttk.Frame(L)
        self._qf_row = r; r += 1
        ttk.Label(self.qf, text="Quick questions", style="Step.TLabel").pack(anchor="w", pady=(8, 0))
        self.l_qnum = ttk.Label(self.qf, text="", style="Hint.TLabel")
        self.l_qnum.pack(anchor="w")
        self.l_qtext = ttk.Label(self.qf, text="", wraplength=390, justify="left")
        self.l_qtext.pack(anchor="w", pady=(2, 4), fill="x")
        self.qrow_len = ttk.Frame(self.qf)
        qe = ttk.Entry(self.qrow_len, textvariable=self.v_answer, width=14)
        qe.pack(side="left")
        qe.bind("<Return>", lambda _: self.quiz_answer())
        ttk.Button(self.qrow_len, text="OK", command=self.quiz_answer).pack(side="left", padx=4)
        self.qrow_yn = ttk.Frame(self.qf)
        ttk.Button(self.qrow_yn, text="Yes", width=8, command=lambda: self.quiz_answer("yes")).pack(side="left")
        ttk.Button(self.qrow_yn, text="No", width=8, command=lambda: self.quiz_answer("no")).pack(side="left", padx=4)
        self.b_qskip = ttk.Button(self.qf, text="Skip (keep my guess)", command=self.quiz_skip)
        self._q = None

        # ---- Step 3
        ttk.Label(L, text="3.  Change the model", style="Step.TLabel").grid(row=r, column=0, columnspan=2, padx=10, pady=(14, 2), sticky="w"); r += 1
        ttk.Label(L, text="Type what you want changed:").grid(row=r, column=0, columnspan=2, padx=10, sticky="w"); r += 1
        sf = ttk.Frame(L); sf.grid(row=r, column=0, columnspan=2, **pad); r += 1
        e = ttk.Entry(sf, textvariable=self.v_say)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda _: self.say())
        ttk.Button(sf, text="Change it", command=self.say).pack(side="left", padx=(6, 0))
        ttk.Label(L, text='e.g. "make the walls 9 feet", "add a single bed at 120, 80",\n"recolor selected red", "turn selected left"',
                  style="Hint.TLabel").grid(row=r, column=0, columnspan=2, padx=10, sticky="w"); r += 1

        wf = ttk.Frame(L); wf.grid(row=r, column=0, columnspan=2, padx=10, pady=(8, 3), sticky="we"); r += 1
        ttk.Label(wf, text="Wall height").pack(side="left")
        ttk.Entry(wf, textvariable=self.v_wall_ft, width=5).pack(side="left", padx=4)
        ttk.Label(wf, text="feet").pack(side="left")
        ttk.Button(wf, text="Set", command=self.set_wall_height).pack(side="left", padx=6)

        ttk.Button(L, text="Add a wall  (then drag on the plan)", command=self.start_add).grid(row=r, column=0, columnspan=2, **pad); r += 1
        self.l_sel = ttk.Label(L, text="Click a part on the plan to select it.", style="Hint.TLabel", wraplength=390)
        self.l_sel.grid(row=r, column=0, columnspan=2, padx=10, pady=(6, 0), sticky="w"); r += 1
        af = ttk.Frame(L); af.grid(row=r, column=0, columnspan=2, **pad); r += 1
        ttk.Label(af, text="Move selected:").pack(side="left")
        for t, d in [("Left", "left"), ("Right", "right"), ("Up", "up"), ("Down", "down")]:
            ttk.Button(af, text=t, width=6, command=lambda d=d: self.move(d)).pack(side="left", padx=1)
        bf = ttk.Frame(L); bf.grid(row=r, column=0, columnspan=2, **pad); r += 1
        ttk.Button(bf, text="Delete selected", command=self.delete).pack(side="left", fill="x", expand=True)
        ttk.Button(bf, text="Undo", command=self.undo).pack(side="left", fill="x", expand=True, padx=(6, 0))

        # ---- What happened
        ttk.Label(L, text="What happened", style="Step.TLabel").grid(row=r, column=0, columnspan=2, padx=10, pady=(14, 2), sticky="w"); r += 1
        self.log_w = ScrolledText(L, height=6, wrap="word", font=self.f_log, background="#f6f6f2", relief="flat")
        self.log_w.grid(row=r, column=0, columnspan=2, padx=10, pady=3, sticky="we"); r += 1

        # ---- More options (collapsed)
        self.b_more = ttk.Button(L, text="+ More options", command=self.toggle_more)
        self.b_more.grid(row=r, column=0, columnspan=2, padx=10, pady=(10, 3), sticky="w"); r += 1
        self.more = ttk.Frame(L)
        self._more_row = r; r += 1
        self._build_more(self.more)
        L.columnconfigure(0, weight=1)

        # ---- right: plan + 3D side by side
        right.columnconfigure(0, weight=1); right.columnconfigure(1, weight=1); right.rowconfigure(1, weight=1)
        ttk.Label(right, text="Your plan", style="Step.TLabel").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        ttk.Label(right, text="3D model", style="Step.TLabel").grid(row=0, column=1, sticky="w", padx=8, pady=(8, 2))
        self.cv = tk.Canvas(right, background="#ffffff", highlightthickness=1, highlightbackground="#ccc")
        self.cv.grid(row=1, column=0, sticky="nsew", padx=(8, 4))
        self.cv3 = tk.Canvas(right, background="#ffffff", highlightthickness=1, highlightbackground="#ccc")
        self.cv3.grid(row=1, column=1, sticky="nsew", padx=(4, 8))
        self.l_plan_hint = ttk.Label(right, text="", style="Hint.TLabel", wraplength=480)
        self.l_plan_hint.grid(row=2, column=0, sticky="w", padx=8, pady=(2, 8))
        self.l_files = ttk.Label(right, text="", style="Hint.TLabel", wraplength=480)
        self.l_files.grid(row=2, column=1, sticky="w", padx=8, pady=(2, 8))
        for c in (self.cv, self.cv3):
            c.bind("<Configure>", lambda _: self.redraw())
        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.tell("Hi! Start with step 1: choose a plan file.")

    def _build_more(self, f):
        pad = dict(padx=4, pady=2, sticky="w")
        r = 0
        ttk.Label(f, text="Size and plan reading", font=self.f_sub).grid(row=r, column=0, columnspan=4, **pad); r += 1
        for lab, var, note in [("Exact scale (pixels per inch)", self.v_ppi, ""),
                               ("PDF quality (DPI)", self.v_dpi, "")]:
            ttk.Label(f, text=lab).grid(row=r, column=0, columnspan=2, **pad)
            ttk.Entry(f, textvariable=var, width=8).grid(row=r, column=2, **pad); r += 1
        ttk.Checkbutton(f, text="Faster: skip finding rooms", variable=self.v_skipcc).grid(row=r, column=0, columnspan=4, **pad); r += 1
        ttk.Checkbutton(f, text="Try to find fixtures drawn on the plan (often wrong)",
                        variable=self.v_fixtures).grid(row=r, column=0, columnspan=4, **pad); r += 1

        ttk.Label(f, text="Area of the plan to use (pixels)", font=self.f_sub).grid(row=r, column=0, columnspan=4, padx=4, pady=(8, 2), sticky="w"); r += 1
        cf = ttk.Frame(f); cf.grid(row=r, column=0, columnspan=4, **pad); r += 1
        for i, n in enumerate(["left", "top", "right", "bottom"]):
            ttk.Label(cf, text=n).grid(row=0, column=2 * i, padx=2)
            ttk.Entry(cf, textvariable=self.v_crop[i], width=5).grid(row=0, column=2 * i + 1, padx=2)
        cb = ttk.Frame(f); cb.grid(row=r, column=0, columnspan=4, **pad); r += 1
        ttk.Button(cb, text="Draw the area by hand", command=lambda: self.set_mode("crop")).pack(side="left")
        ttk.Button(cb, text="Use the whole plan", command=self.clear_crop).pack(side="left", padx=4)

        ttk.Label(f, text="Heights (inches)", font=self.f_sub).grid(row=r, column=0, columnspan=4, padx=4, pady=(8, 2), sticky="w"); r += 1
        for k, lab in [("floor_t", "Floor thickness"), ("sill", "Window bottom (sill)"), ("lintel", "Window top")]:
            ttk.Label(f, text=lab).grid(row=r, column=0, columnspan=2, **pad)
            ttk.Entry(f, textvariable=self.v_adj[k], width=8).grid(row=r, column=2, **pad); r += 1
        ttk.Button(f, text="Apply heights", command=self.apply_adjust).grid(row=r, column=0, columnspan=2, **pad); r += 1

        ttk.Label(f, text="Parts", font=self.f_sub).grid(row=r, column=0, columnspan=4, padx=4, pady=(8, 2), sticky="w"); r += 1
        self.lb = tk.Listbox(f, height=7, exportselection=False, width=44)
        self.lb.grid(row=r, column=0, columnspan=4, padx=4, sticky="we"); r += 1
        self.lb.bind("<<ListboxSelect>>", self.on_list)
        pf = ttk.Frame(f); pf.grid(row=r, column=0, columnspan=4, **pad); r += 1
        ttk.Label(pf, text="Move step").pack(side="left")
        ttk.Entry(pf, textvariable=self.v_step, width=4).pack(side="left", padx=2)
        ttk.Combobox(pf, textvariable=self.v_unit, values=["in", "px"], width=3, state="readonly").pack(side="left")
        ttk.Entry(pf, textvariable=self.v_pct, width=4).pack(side="left", padx=(10, 2))
        ttk.Label(pf, text="%").pack(side="left")
        ttk.Button(pf, text="Longer", width=7, command=lambda: self.resize(+1)).pack(side="left", padx=2)
        ttk.Button(pf, text="Shorter", width=7, command=lambda: self.resize(-1)).pack(side="left")
        kf = ttk.Frame(f); kf.grid(row=r, column=0, columnspan=4, **pad); r += 1
        ttk.Label(kf, text="Add").pack(side="left")
        ttk.Combobox(kf, textvariable=self.v_kind, values=list(E.KINDS), width=7, state="readonly").pack(side="left", padx=2)
        for v in self.v_add:
            ttk.Entry(kf, textvariable=v, width=4).pack(side="left", padx=1)
        ttk.Button(kf, text="Add", width=5, command=self.add).pack(side="left", padx=2)
        ttk.Button(kf, text="Drag it", width=7, command=lambda: self.set_mode("add")).pack(side="left")

        ttk.Label(f, text="Project", font=self.f_sub).grid(row=r, column=0, columnspan=4, padx=4, pady=(8, 2), sticky="w"); r += 1
        jf = ttk.Frame(f); jf.grid(row=r, column=0, columnspan=4, **pad); r += 1
        ttk.Label(jf, text="Name").pack(side="left")
        ttk.Entry(jf, textvariable=self.v_job, width=16).pack(side="left", padx=4)
        ttk.Button(jf, text="Open earlier project", command=self.open_job).pack(side="left")
        of = ttk.Frame(f); of.grid(row=r, column=0, columnspan=4, **pad); r += 1
        ttk.Button(of, text="Rebuild 3D", command=self.rebuild).pack(side="left")
        ttk.Button(of, text="Go back to what was found", command=self.reset_polys).pack(side="left", padx=4)
        ttk.Button(of, text="All phrases", command=lambda: messagebox.showinfo("Phrases", E.HELP)).pack(side="left")
        self.l_assume = ttk.Label(f, text="", style="Hint.TLabel", wraplength=400, justify="left")
        self.l_assume.grid(row=r, column=0, columnspan=4, padx=4, pady=6, sticky="w"); r += 1

    def toggle_more(self):
        if self.more.winfo_ismapped():
            self.more.grid_remove(); self.b_more.configure(text="+ More options")
        else:
            self.more.grid(row=self._more_row, column=0, columnspan=2, padx=10, sticky="we")
            self.b_more.configure(text="- Fewer options")

    # ------------------------------------------------------------ messages
    def tell(self, s: str):
        """Plain-language line for the 'What happened' box."""
        self.q.put(("tell", s))

    def log(self, s: str):
        """Technical detail: goes to out/<project>/app_log.txt, not the main screen."""
        self.q.put(("log", s))

    def _pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "tell":
                    self.log_w.insert("end", val + "\n"); self.log_w.see("end")
                elif kind == "log":
                    try:
                        job = self.job(); job.mkdir(parents=True, exist_ok=True)
                        with open(job / "app_log.txt", "a") as fh:
                            fh.write(time.strftime("%H:%M:%S ") + val + "\n")
                    except OSError:
                        pass
                elif kind == "done":
                    self.busy = False
                    self.b_run.state(["!disabled"])
                    val()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def job(self) -> Path:
        name = self.v_job.get().strip() or "job1"
        return OUT / "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)

    def _bg(self, fn, then=None):
        if self.busy:
            messagebox.showinfo("One moment", "I'm still working on the last step."); return
        self.busy = True
        self.b_run.state(["disabled"])

        def work():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.log(f"ERROR: {e}\n{traceback.format_exc()}")
                self.tell(f"Something went wrong: {e}  (details in {self.job().name}/app_log.txt)")
            self.q.put(("done", then or (lambda: None)))
        threading.Thread(target=work, daemon=True).start()

    def _num(self, var, name, required=False):
        s = var.get().strip()
        if not s:
            if required:
                raise ValueError(f"{name} is required")
            return None
        try:
            v = float(s)
        except ValueError:
            raise ValueError(f"{name}: '{s}' is not a number") from None
        if v <= 0:
            raise ValueError(f"{name} must be more than 0")
        return v

    def _err(self, e):
        self.tell(f"Not changed: {e}")
        messagebox.showwarning("Not changed", str(e))

    def built(self) -> bool:
        job = self.job()
        return (job / "polygons.json").exists() and (job / "source_crop.png").exists() and \
            self.params.plan == self.v_plan.get().strip()

    def set_mode(self, m):
        self.mode = m
        self._measure_pts = []
        hint = {"crop": "Drag a box around the part of the plan you want.",
                "add": f"Drag a box on the plan where the new {self.v_kind.get()} goes.",
                "measure": "Click one end of that measurement on the plan, then the other end."}.get(m)
        if hint:
            self.tell(hint)
        if m == "crop":
            self._force_original = True
        self.redraw()

    def snapshot(self):
        self.undo_stack.append((json.dumps(self.polys), json.dumps(E.asdict(self.params))))
        self.undo_stack = self.undo_stack[-50:]

    def _update_size_label(self):
        ppi = self.params.px_per_inch if self.built() else None
        ppi = ppi or (float(self.v_ppi.get()) if self.v_ppi.get().strip() else None)
        if ppi:
            src = "from your measurement" if ("px_per_inch" in self.params.user_set or self.v_ppi.get().strip()) else "estimated - please check"
            self.l_size.configure(text=f"Size: set ({src}).", foreground="#070")
        elif self.v_plan.get().strip() and getattr(self, "kind", "") == "mesh":
            self.l_size.configure(text="Size: 3D files have no units. I'll guess from the wall height, then ask you.",
                                  foreground="#a60")
        elif self.v_plan.get().lower().endswith(".pdf"):
            self.l_size.configure(text="Size: I'll try to read it from the PDF. A measurement is safer.", foreground="#555")
        else:
            self.l_size.configure(text="Size: not set yet - the model size will be wrong until you do.", foreground="#a60")

    # ------------------------------------------------------------ plan / job
    def browse(self):
        p = filedialog.askopenfilename(initialdir=str(ROOT / "plans"),
                                       filetypes=[("Plans and 3D models", OPEN_PATTERNS), ("All files", "*")])
        if p:
            self.load_plan_file(p)

    def load_plan_file(self, p: str):
        try:
            kind = file_kind(p)
            if kind == "mesh":
                from .raster import load_plan
                self.tell(f"Reading the 3D model {Path(p).name}...")
                self.update_idletasks()
                load_plan(p)          # slices the model once (cached) and fails here, not later, if unreadable
        except UnsupportedFile as e:
            self.tell(f"Can't use that file: {e}")
            messagebox.showwarning("Can't use that file", str(e))
            return
        self.kind = kind
        self.v_plan.set(p)
        self.v_wall_ft.set(f"{E.DEFAULTS['wall_h'] / 12:g}")   # a new file starts from defaults, not the last job
        self.v_job.set(Path(p).stem)
        self.v_ppi.set("")
        self.params = E.JobParams()
        self.polys = {k: [] for k in E.KINDS}
        self.selected = None
        self.clear_crop(quiet=True)
        self.l_plan.configure(text=f"{'3D model' if kind == 'mesh' else 'Plan'}: {Path(p).name}")
        self._green = None
        self.hide_quiz()
        try:
            from .raster import load_plan
            self._green = E.find_green_outline(load_plan(p, dpi=float(self.v_dpi.get() or 100)).rgb)
        except Exception as e:  # noqa: BLE001
            self.log(f"green outline check failed: {e}")
        if kind == "mesh":
            self.b_unit.state(["disabled"])
            self.tell(f"Loaded the 3D model {Path(p).name}. The left picture is a top view of it. "
                      "Press \"Build my 3D model\" and I'll work out the floor, walls, door frames and windows.")
        elif self._green:
            self.b_unit.state(["!disabled"])
            self.tell(f"Loaded {Path(p).name}. It has a green outline around one unit - "
                      "click \"Use just this unit\" so the neighbours aren't built too.")
        else:
            self.b_unit.state(["disabled"])
            self.tell(f"Loaded {Path(p).name}.")
        self._update_size_label()
        self.refresh_parts()
        self.redraw()

    def auto_crop(self):
        if not self.v_plan.get().strip():
            return self._err("choose a plan first")
        box = getattr(self, "_green", None)
        if not box:
            try:
                from .raster import load_plan
                box = E.find_green_outline(load_plan(self.v_plan.get(), dpi=float(self.v_dpi.get() or 100)).rgb)
            except Exception as e:  # noqa: BLE001
                return self._err(f"couldn't open the plan ({e})")
        if not box:
            return self._err("I couldn't find a green outline. Use More options > Draw the area by hand.")
        for v, x in zip(self.v_crop, box):
            v.set(str(x))
        self.crop_source = "auto-green (adjustable)"
        self.l_crop.configure(text="Using just the unit inside the green outline (pink box). Check it covers the outside walls.")
        self.log(f"crop prefilled from green outline: {box}")
        self.tell("OK - I'll only build the unit inside the green outline.")
        self._force_original = True
        self.redraw()

    def clear_crop(self, quiet=False):
        for v in self.v_crop:
            v.set("")
        self.crop_source = "none"
        self.l_crop.configure(text="")
        if not quiet:
            self.tell("Using the whole plan.")
            self._force_original = True
            self.redraw()

    def _crop(self):
        vals = [v.get().strip() for v in self.v_crop]
        if not any(vals):
            return None
        if not all(vals):
            raise ValueError("the area needs all four numbers (or none)")
        return [int(float(v)) for v in vals]

    def open_job(self):
        job = self.job()
        if not (job / "job.json").exists():
            d = filedialog.askdirectory(initialdir=str(OUT))
            if not d:
                return
            self.v_job.set(Path(d).name); job = self.job()
            if not (job / "job.json").exists():
                return self._err(f"{job.name} isn't a project made by this app")
        self.params = E.JobParams.load(job)
        self.kind = "mesh" if self.params.source_kind == "mesh" else "plan"
        self.v_plan.set(self.params.plan)
        self.l_plan.configure(text=f"{'3D model' if self.kind == 'mesh' else 'Plan'}: {Path(self.params.plan).name}")
        self.v_ppi.set(f"{self.params.px_per_inch:g}" if self.params.px_per_inch and "px_per_inch" in self.params.user_set else "")
        self.v_wall_ft.set(f"{self.params.wall_h / 12:g}")
        for v, x in zip(self.v_crop, self.params.crop or ["", "", "", ""]):
            v.set(str(x))
        self.crop_source = self.params.crop_source
        self.polys = E.load_polys(job)
        self.after_build(announce=False)
        self.tell(f"Opened the earlier project \"{job.name}\".")

    def run_detect(self):
        try:
            plan = self.v_plan.get().strip()
            if not plan or not Path(plan).exists():
                raise ValueError("choose a plan file first (step 1)")
            ppi = self._num(self.v_ppi, "exact scale")
            is_pdf = plan.lower().endswith(".pdf")
            is_mesh = file_kind(plan) == "mesh"
            crop = self._crop()
            if not crop and getattr(self, "_green", None):
                if messagebox.askyesno("Just this unit?", "This plan has a green outline around one unit.\n"
                                       "Build only that unit? (No = build the whole page)"):
                    self.auto_crop(); crop = self._crop()
            if not is_pdf and not is_mesh and not ppi:
                if not messagebox.askyesno("Size not set", "You haven't given me a measurement yet, so the model's size "
                                           "will be wrong.\nBuild a rough preview anyway?"):
                    return
            wall_h = parse_feet(self.v_wall_ft.get())
            dpi = self._num(self.v_dpi, "PDF quality", True)
        except ValueError as e:     # UnsupportedFile is a ValueError too
            return self._err(e)
        job = self.job()
        old = E.JobParams.load(job)
        if old.plan != plan:
            old = E.JobParams()
        p = E.JobParams(plan=plan, crop=crop, crop_source=self.crop_source if crop else "none",
                        px_per_inch=ppi, dpi=dpi, wall_h=wall_h, floor_t=old.floor_t, sill=old.sill, lintel=old.lintel,
                        furniture_h=old.furniture_h,
                        skip=["segment_rooms[CubiCasa5k]"] if self.v_skipcc.get() and not self.v_fixtures.get() else [],
                        find_fixtures=bool(self.v_fixtures.get()),
                        user_set=[k for k in old.user_set if k in ("floor_t", "sill", "lintel", "furniture_h")], edits=old.edits)
        if ppi:
            p.user_set.append("px_per_inch")
        was_measured = old.source_kind == "mesh" and "wall_h" in old.measured and abs(wall_h - old.wall_h) < 0.01
        if wall_h != E.DEFAULTS["wall_h"] and not was_measured:
            p.user_set.append("wall_h")
        p.log(f"run requested: plan={Path(plan).name}, ppi={ppi}, wall_h={wall_h}, crop={crop}")
        self.params = p
        self.selected = None
        self._force_original = False
        self.hide_quiz()
        self.tell("Building your 3D model... this can take up to a minute.")
        self.log(f"=== detect {plan} -> {job} ===")

        def work():
            if E.needs_detection(job, p):
                E.detect(job, p, log=self.log)
            else:
                self.log("plan and crop unchanged - reusing saved polygons")
            E.rebuild(job, p, log=self.log)
            self.polys = E.load_polys(job)
        self._bg(work, then=self.after_build)

    # ------------------------------------------------------------ edits
    def after_build(self, announce=True):
        p = self.params
        for k, v in self.v_adj.items():
            v.set(f"{getattr(p, k):g}")
        self.v_wall_ft.set(f"{p.wall_h / 12:g}")
        if p.px_per_inch and "px_per_inch" in p.user_set:
            self.v_ppi.set(f"{p.px_per_inch:g}")
        self.l_assume.configure(text="Things to check:\n* " + "\n* ".join(E.assumptions(p)))
        self.refresh_parts()
        self._update_size_label()
        job = self.job()
        if (job / "report.json").exists():
            rep = json.loads((job / "report.json").read_text())
            st = rep.get("stages", {}).get("editor_rebuild", {})
            ext = st.get("extent_in")
            size = f"about {feet_text(ext[0])} wide x {feet_text(ext[1])} deep x {feet_text(ext[2])} tall" if ext else ""
            rough = "" if "px_per_inch" in p.user_set else " (size is a guess until you answer)"
            left = rep.get("review", {}).get("pending", [])
            status = "Checked: every question answered or skipped." if rep.get("review", {}).get("status") == "done" \
                else f"Not done yet: {len(left)} quick question(s) left."
            self.l_files.configure(text=f"Model {size}{rough}.\n{status}\nSaved in out/{job.name}/  (model.3mf / model.stl for printing)")
            if announce:
                self.tell(f"Built! Model is {size}.")
                if p.source_kind == "mesh":
                    c = rep.get("polygon_counts", {})
                    mi = rep.get("stages", {}).get("mesh_import", {})
                    self.tell(f"From the 3D file: {c.get('wall', 0)} wall piece(s), "
                              f"{'a floor slab' if 'floor_t' in p.measured else 'no floor (I added a base)'}, "
                              f"{c.get('door', 0)} door frame(s), {c.get('window', 0)} window(s), "
                              f"{c.get('furniture', 0)} furniture/low block(s).")
                    for line in mi.get("not_inferred", [])[:2]:
                        self.tell("Note: " + line)
                else:
                    n = len(self.polys.get("wall", []))
                    self.tell(f"I found {n} wall pieces. If walls are missing, use \"Add a wall\" and drag where it goes.")
            self.show_quiz(announce)
        self.redraw()

    # ------------------------------------------------------------ quick questions
    def hide_quiz(self):
        self._q = None
        self.qf.grid_remove()

    def show_quiz(self, announce=False):
        job = self.job()
        if not self.built():
            return self.hide_quiz()
        left = Q.pending(job, self.params, self.polys)
        if not left:
            was_open = self._q is not None
            self.hide_quiz()
            if was_open:
                self.tell("All questions done. The model is marked as checked "
                          f"(out/{job.name}/report.json). Anything you skipped is still listed as a guess.")
            return
        q = left[0]
        new = self._q is None or self._q["id"] != q["id"]
        self._q = q
        self.qf.grid(row=self._qf_row, column=0, columnspan=2, padx=10, sticky="we")
        n_done = len(self.params.quiz or {})
        self.l_qnum.configure(text=f"Question {n_done + 1} of {n_done + len(left)}  -  needed before the model counts as done")
        self.l_qtext.configure(text=q["text"])
        self.qrow_len.pack_forget(); self.qrow_yn.pack_forget(); self.b_qskip.pack_forget()
        (self.qrow_yn if q["kind"] == "yesno" else self.qrow_len).pack(anchor="w", pady=2)
        self.b_qskip.pack(anchor="w", pady=(2, 4))
        if new:
            self.v_answer.set("")
            self.tell("Question: " + q["text"].split("\n")[0])

    def quiz_answer(self, text=None):
        q = self._q
        if q is None:
            return
        if self.busy:
            return messagebox.showinfo("One moment", "I'm still updating the model.")
        text = self.v_answer.get() if text is None else text
        self.snapshot()
        try:
            msg, need = Q.answer(self.job(), self.params, q["id"], text, self.selected)
        except ValueError as e:
            self.undo_stack.pop()
            self.tell(f"Sorry - {e}")
            return
        self.log(f"quiz {q['id']}: '{text}' -> {msg}")
        self.tell(f"You said: {text}. {msg}")
        self.polys = E.load_polys(self.job())
        if need:
            self.rebuild()
        else:
            self.rebuild(quiet=True)

    def quiz_skip(self):
        q = self._q
        if q is None or self.busy:
            return
        self.tell(Q.skip(self.job(), self.params, q["id"]))
        self.rebuild(quiet=True)

    def refresh_parts(self):
        self.parts = E.list_parts(self.polys)
        self.lb.delete(0, "end")
        for i, p in enumerate(self.parts):
            self.lb.insert("end", p["label"] + ("  [added]" if self.polys[p["kind"]][p["index"]].get("added_in_app") else ""))
            self.lb.itemconfig(i, foreground=COL[p["kind"]])
            if self.selected == (p["kind"], p["index"]):
                self.lb.selection_set(i); self.lb.see(i)
        if self.selected:
            self.l_sel.configure(text=f"Selected: a {self.selected[0]} (pink on the plan).", foreground="#c0a")
        else:
            self.l_sel.configure(text="Click a part on the plan to select it.", foreground="#555")

    def on_list(self, _=None):
        s = self.lb.curselection()
        if s:
            p = self.parts[s[0]]
            self.selected = (p["kind"], p["index"])
            self.refresh_parts(); self.redraw()

    @staticmethod
    def _plain(m: str, a: dict | None) -> str:
        if a and a.get("op") == "set":
            if a["param"] == "px_per_inch":
                return "Changed the size (scale)."
            return f"{NAMES.get(a['param'], a['param'])} is now {feet_text(a['value'])}."
        for w, t in (("moved", "Moved the selected part."), ("deleted", "Deleted the selected part."),
                     ("added", "Added it."), ("resized", "Resized the selected part."),
                     ("rotated", "Rotated the selected part."), ("recolored", "Recolored the selected part.")):
            if m.startswith(w):
                return t
        return m

    def _edit(self, actions):
        """Apply editor actions, then rebuild in the background."""
        job = self.job()
        if not self.built():
            return self._err("build the model first (step 2)")
        self.snapshot()
        try:
            msgs, need, self.selected = E.apply_actions(job, self.params, actions, self.selected)
        except ValueError as e:
            self.undo_stack.pop()
            return self._err(e)
        for m, a in zip(msgs, actions):
            self.log("edit: " + m)
            if "\n" not in m:
                self.tell(self._plain(m, a))
        self.polys = E.load_polys(job)
        if need:
            self.rebuild()
        else:
            self.after_build(announce=False)

    def rebuild(self, quiet=False):
        job = self.job()
        if not quiet:
            self.tell("Updating the 3D model...")
        done = (lambda: self.after_build(announce=False)) if quiet else \
            (lambda: self.after_build(announce=False) or self.tell("3D model updated."))
        self._bg(lambda: E.rebuild(job, self.params, log=self.log), then=done)

    def set_wall_height(self):
        try:
            v = parse_feet(self.v_wall_ft.get())
        except ValueError as e:
            return self._err(e)
        if not self.built():
            self.tell(f"Walls will be {feet_text(v)} tall when you build.")
            return
        self._edit([{"op": "set", "param": "wall_h", "value": round(v, 3)}])

    def apply_adjust(self):
        acts = []
        try:
            for k in ("floor_t", "sill", "lintel"):
                s = self.v_adj[k].get().strip()
                if not s:
                    raise ValueError(f"{NAMES[k]} is empty")
                v = float(s)
                if v != getattr(self.params, k):
                    acts.append({"op": "set", "param": k, "value": v})
        except ValueError as e:
            return self._err(e)
        if not self.built():
            return self._err("build the model first (step 2)")
        if not acts:
            return self.rebuild()
        trial = {k: getattr(self.params, k) for k in ("wall_h", "floor_t", "sill", "lintel")}
        trial.update({a["param"]: a["value"] for a in acts})
        if not (trial["wall_h"] > 0 and trial["floor_t"] > 0 and 0 <= trial["sill"] < trial["lintel"] <= trial["wall_h"]):
            return self._err("window bottom must be below window top, and window top no higher than the walls")
        self.snapshot()
        for a in acts:
            self.params.set(a["param"], a["value"])
            self.tell(self._plain("", a))
        self.params.save(self.job())
        self.rebuild()

    def move(self, d):
        try:
            amt = float(self.v_step.get())
        except ValueError:
            return self._err("move step must be a number")
        unit = self.v_unit.get()
        if unit == "in" and not self.params.px_per_inch:
            unit = "px"  # no size known yet: nudge by pixels rather than guess inches
        dx, dy = E._DIRS[d]
        self._edit([{"op": "move", "dx": dx * amt, "dy": dy * amt, "unit": unit}])

    def resize(self, sign):
        try:
            pct = float(self.v_pct.get())
        except ValueError:
            return self._err("% must be a number")
        self._edit([{"op": "resize", "fx": 1 + sign * pct / 100, "fy": 1.0, "axis": "long"}])

    def delete(self):
        self._edit([{"op": "delete"}])

    def start_add(self):
        if not self.built():
            return self._err("build the model first (step 2)")
        self.v_kind.set("wall")
        self.set_mode("add")

    def add(self):
        try:
            r = [float(v.get()) for v in self.v_add]
        except ValueError:
            return self._err("drag the new part on the plan")
        self._edit([{"op": "add", "kind": self.v_kind.get(), "rect": r}])

    def say(self):
        text = self.v_say.get()
        try:
            acts = E.parse_command(text)
        except ValueError as e:
            self.tell(f"Sorry - {e}")
            return
        if any(a["op"] == "undo" for a in acts):
            return self.undo()
        if [a["op"] for a in acts] == ["help"]:
            messagebox.showinfo("Phrases", E.HELP); return
        self.log(f"say: '{text}' -> {acts}")
        self.tell(f"You asked: \"{text}\"")
        self._edit(acts)

    def undo(self):
        if not self.undo_stack:
            return self._err("nothing to undo")
        polys, params = self.undo_stack.pop()
        self.polys = json.loads(polys)
        self.params = E.JobParams(**json.loads(params))
        self.params.log("undo")
        E.save_polys(self.job(), self.polys)
        self.params.save(self.job())
        self.tell("Undid the last change.")
        self.rebuild()

    def reset_polys(self):
        job = self.job()
        if not (job / "polygons_detected.json").exists():
            return self._err("nothing has been built yet")
        if not messagebox.askyesno("Start over?", "Throw away all your part changes and go back to what was found on the plan?"):
            return
        self.snapshot()
        self.polys = json.loads((job / "polygons_detected.json").read_text())
        E.save_polys(job, self.polys)
        self.params.log("reset polygons to detected")
        self.selected = None
        self.rebuild()

    # ------------------------------------------------------------ measurement
    def start_measure(self):
        if not self.v_plan.get().strip():
            return self._err("choose a plan first")
        try:
            self._measure_in = parse_feet(self.v_measure.get())
        except ValueError as e:
            return self._err(f"{e}. Find a length written on the plan (like a room width) and type it in feet.")
        self.set_mode("measure")

    def _finish_measure(self):
        (x0, y0), (x1, y1) = self._measure_pts
        px = math.hypot(x1 - x0, y1 - y0)
        self.set_mode("select")
        if px < 5:
            return self._err("those two clicks were too close - try again")
        ppi = round(px / self._measure_in, 4)
        self.log(f"measurement: {px:.1f} px = {self._measure_in} in -> {ppi} px/in")
        self.v_ppi.set(f"{ppi:g}")
        self.tell(f"Got it: {feet_text(self._measure_in)} on the plan. The model will now be the right size.")
        if self.built():
            self._edit([{"op": "set", "param": "px_per_inch", "value": ppi}])
        self._update_size_label()

    # ------------------------------------------------------------ canvases
    def _fit(self, cv, w, h):
        cw, ch = max(50, cv.winfo_width()), max(50, cv.winfo_height())
        return min(cw / w, ch / h) * 0.98

    def _show_parts(self) -> bool:
        return self.built() and not getattr(self, "_force_original", False) and self.mode != "crop"

    def redraw(self):
        self._draw_plan()
        self._draw_3d()

    def _draw_3d(self):
        cv = self.cv3
        cv.delete("all")
        p = self.job() / "preview.png"
        if not self.built() or not p.exists():
            cv.create_text(20, 20, anchor="nw", fill="#777", text="Your 3D model will show here\nafter step 2.")
            return
        im = Image.open(p)
        s = self._fit(cv, *im.size)
        self._imgs["pv"] = ImageTk.PhotoImage(im.resize((max(1, int(im.width * s)), max(1, int(im.height * s)))))
        cv.create_image(0, 0, anchor="nw", image=self._imgs["pv"])

    def _draw_plan(self):
        cv = self.cv
        cv.delete("all")
        plan = self.v_plan.get().strip()
        if not plan or not Path(plan).exists():
            cv.create_text(20, 20, anchor="nw", fill="#777", text="Choose a plan in step 1.")
            self.l_plan_hint.configure(text="")
            return
        if not self._show_parts():
            try:
                from .raster import load_plan
                key = (plan, self.v_dpi.get())
                if self._imgs.get("orig_key") != key:
                    self._imgs["orig"] = Image.fromarray(load_plan(plan, dpi=float(self.v_dpi.get() or 100)).rgb)
                    self._imgs["orig_key"] = key
            except Exception as e:  # noqa: BLE001
                cv.create_text(20, 20, anchor="nw", text=f"I can't open this plan: {e}"); return
            im = self._imgs["orig"]
            s = self._fit(cv, *im.size); self._scale = s
            self._imgs["o_tk"] = ImageTk.PhotoImage(im.resize((max(1, int(im.width * s)), max(1, int(im.height * s)))))
            cv.create_image(0, 0, anchor="nw", image=self._imgs["o_tk"])
            try:
                c = self._crop()
            except ValueError:
                c = None
            if c:
                cv.create_rectangle(c[0] * s, c[1] * s, c[2] * s, c[3] * s, outline=PINK, width=3, dash=(6, 3))
            self.l_plan_hint.configure(text="Pink box = the part I'll build." if c else "")
        else:
            im = Image.open(self.job() / "source_crop.png").convert("RGB")
            im = Image.blend(im, Image.new("RGB", im.size, "white"), 0.55)
            s = self._fit(cv, *im.size); self._scale = s
            self._imgs["p_tk"] = ImageTk.PhotoImage(im.resize((max(1, int(im.width * s)), max(1, int(im.height * s)))))
            cv.create_image(0, 0, anchor="nw", image=self._imgs["p_tk"])
            for k in E.KINDS:
                for i, p in enumerate(self.polys.get(k, [])):
                    pts = [c * s for xy in p["outer"] for c in xy]
                    if len(pts) < 6:
                        continue
                    sel = self.selected == (k, i)
                    fill = PINK if sel else (furn_mod.plan_hex(p, COL[k]) if k == "furniture" else COL[k])
                    cv.create_polygon(pts, fill=fill, outline=PINK if sel else fill,
                                      width=3 if sel else 1, stipple="" if sel else "gray50", tags=("part", f"{k}:{i}"))
            self.l_plan_hint.configure(text="Dark = walls   Blue = windows   Orange = door frames   Green = furniture   Pink = selected.  Click a part to select it.")
        for (x, y) in self._measure_pts:
            cv.create_oval(x * self._scale - 5, y * self._scale - 5, x * self._scale + 5, y * self._scale + 5, outline=PINK, width=3)

    def on_press(self, ev):
        self._drag = (ev.x, ev.y)
        if self.mode == "measure":
            self._measure_pts.append((ev.x / self._scale, ev.y / self._scale))
            if len(self._measure_pts) == 2:
                self._finish_measure()
            else:
                self.tell("Now click the other end.")
                self.redraw()
            return
        if self.mode == "select" and self._show_parts():
            hit = [t for i in self.cv.find_overlapping(ev.x - 2, ev.y - 2, ev.x + 2, ev.y + 2)
                   for t in self.cv.gettags(i) if ":" in t]
            self.selected = None
            if hit:
                k, i = hit[-1].split(":")
                self.selected = (k, int(i))
            self.refresh_parts(); self.redraw()

    def on_drag(self, ev):
        if self.mode in ("crop", "add") and self._drag:
            self.cv.delete("rubber")
            self.cv.create_rectangle(*self._drag, ev.x, ev.y, outline=PINK, width=2, tags="rubber")

    def on_release(self, ev):
        m, d = self.mode, self._drag
        self._drag = None
        if m not in ("crop", "add") or not d or abs(ev.x - d[0]) < 4 or abs(ev.y - d[1]) < 4:
            return
        s = self._scale
        box = [round(min(d[0], ev.x) / s), round(min(d[1], ev.y) / s), round(max(d[0], ev.x) / s), round(max(d[1], ev.y) / s)]
        self.mode = "select"
        if m == "crop":
            for v, x in zip(self.v_crop, box):
                v.set(str(x))
            self.crop_source = "manual"
            self.l_crop.configure(text="Using the area you drew (pink box).")
            self.log(f"crop set by drag: {box}")
            self.tell("OK - I'll build just the area you drew. Press \"Build my 3D model\".")
            self._force_original = True
            self.redraw()
        else:
            for v, x in zip(self.v_add, box):
                v.set(str(x))
            self.add()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
