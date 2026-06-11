"""2-D painting canvas widget for the PALM static-driver GUI.

``GridCanvas`` renders the domain as a top-view grid (``nx+1`` x ``ny+1``
cells, j increasing upward / northward) and lets the user paint rectangular
zones by click-dragging.  It supports zooming (toolbar buttons or
Ctrl+mouse-wheel) and panning (scrollbars, mouse-wheel, or the arrow keys when
the canvas has focus).  It is a self-contained tkinter ``Frame`` that talks to
the host application only through the callbacks supplied to ``__init__``.

Callbacks
---------
get_active_tool() -> str
    Current drawing tool (``"building" | "vegetation" | "pavement" | "soil"``).
get_zones() -> list
    The (live) ordered list of zone dictionaries to render.
on_draw(i0, i1, j0, j1)
    Called when the user finishes a drag, with ``numpy``-style slice bounds.
on_pick(index)
    Called when the user clicks (without dragging) a painted cell; ``index`` is
    the topmost zone covering that cell, or ``None`` for background.
"""

import tkinter as tk
from tkinter import ttk

#: Per-type fill colours used by the painter.
ZONE_COLORS = {
    "building": "#8B7355",
    "vegetation": "#4CAF50",
    "pavement": "#9E9E9E",
    "water": "#2196F3",
    "soil": "#A0522D",
    "terrain": "#795548",
    "background": "#d7d7d7",
}
#: Building colour ramp endpoints (short → tall) for height shading.
BUILDING_SHADE_LIGHT = "#c8b89a"
BUILDING_SHADE_DARK = "#4e3f2a"
BUILDING_SHADE_MAX_H = 50.0     # metres mapped to the darkest shade

#: Background (unassigned) colour — a light tint standing in for "pavement at
#: 50% opacity" (tkinter has no real alpha channel).
BACKGROUND_COLOR = "#d7d7d7"
GRID_COLOR = "#bdbdbd"
SELECT_COLOR = "#ff1744"
AXIS_COLOR = "#37474f"
#: Legend entries shown above the canvas (label, colour).
LEGEND_ENTRIES = [
    ("Building", ZONE_COLORS["building"]),
    ("Vegetation", ZONE_COLORS["vegetation"]),
    ("Pavement", ZONE_COLORS["pavement"]),
    ("Water", ZONE_COLORS["water"]),
    ("Soil", ZONE_COLORS["soil"]),
    ("Terrain", ZONE_COLORS["terrain"]),
    ("Erase", ZONE_COLORS["background"]),
]


def _blend(hex1, hex2, t):
    """Linearly blend two ``#rrggbb`` colours; ``t`` in [0, 1]."""
    t = max(0.0, min(1.0, t))
    a = [int(hex1[k:k + 2], 16) for k in (1, 3, 5)]
    b = [int(hex2[k:k + 2], 16) for k in (1, 3, 5)]
    c = [int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3)]
    return "#{:02x}{:02x}{:02x}".format(*c)

MIN_CELL_PX = 4         # smallest fit cell size
MAX_CELL_PX = 20        # largest fit cell size (before zoom)
MIN_ZOOMED_PX = 1       # absolute lower bound on a zoomed cell
MAX_ZOOMED_PX = 80      # absolute upper bound on a zoomed cell
ZOOM_MIN = 0.25
ZOOM_MAX = 8.0
ZOOM_STEP = 1.25
PAN_UNITS = 3           # arrow-key / wheel pan amount (scroll "units")

#: Tools that edit an existing zone (move / reshape) rather than paint a new one.
EDIT_TOOLS = ("move", "reshape")
#: Tool that selects existing zones (click / Shift|Ctrl-click / rubber-band).
SELECT_TOOL = "select"


class GridCanvas(tk.Frame):
    """A scalable, zoomable, pannable 2-D grid the user paints zones onto."""

    def __init__(self, parent, get_active_tool, get_zones, on_draw, on_pick,
                 on_edit_begin=None, on_edit_commit=None, on_select=None,
                 **kwargs):
        super().__init__(parent, **kwargs)
        self._get_active_tool = get_active_tool
        self._get_zones = get_zones
        self._on_draw = on_draw
        self._on_pick = on_pick
        self._on_edit_begin = on_edit_begin or (lambda idx: None)
        self._on_edit_commit = on_edit_commit or (lambda: None)
        self._on_select = on_select or (lambda indices, primary: None)

        self.nx = 0
        self.ny = 0
        self.dx = 1.0
        self.dy = 1.0
        self.cs = MIN_CELL_PX           # current (zoomed) cell size in pixels
        self.zoom = 1.0                 # 1.0 == fit-to-window
        self.selected_index = None      # primary selection (drives properties)
        self.selected_set = set()       # all selected zone indices
        self._enabled = False

        self._drag_start = None         # (i, j) cell at button press
        self._drag_now = None           # (i, j) cell under cursor while dragging
        self._select_additive = False   # Shift/Ctrl held when a marquee started
        self._editing = None            # move/reshape state dict, or None

        # ---- zoom toolbar ------------------------------------------------
        bar = tk.Frame(self)
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="−", width=3, command=self.zoom_out).pack(side="left")
        ttk.Button(bar, text="+", width=3, command=self.zoom_in).pack(side="left")
        ttk.Button(bar, text="Fit", width=4, command=self.zoom_reset).pack(side="left")
        self.zoom_label = tk.Label(bar, text="100%", width=6, anchor="w")
        self.zoom_label.pack(side="left", padx=4)
        tk.Label(bar, text="(Ctrl+wheel zoom · arrow keys pan)",
                 fg="#666", font=("TkDefaultFont", 8)).pack(side="left")

        # ---- legend ------------------------------------------------------
        legend = tk.Frame(self)
        legend.pack(side="top", fill="x")
        tk.Label(legend, text="Legend:", font=("TkDefaultFont", 8),
                 fg="#444").pack(side="left", padx=(2, 4))
        for label, color in LEGEND_ENTRIES:
            tk.Label(legend, text="  ", bg=color, relief="groove", bd=1).pack(
                side="left", padx=(4, 1))
            tk.Label(legend, text=label, font=("TkDefaultFont", 8)).pack(side="left")

        # ---- canvas + scrollbars ----------------------------------------
        body = tk.Frame(self)
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="white", highlightthickness=0,
                                cursor="crosshair", takefocus=True)
        self.hbar = ttk.Scrollbar(body, orient="horizontal",
                                  command=self.canvas.xview)
        self.vbar = ttk.Scrollbar(body, orient="vertical",
                                  command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.hbar.set,
                              yscrollcommand=self.vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.status = tk.Label(self, text="Load a reference file to enable the canvas.",
                               anchor="w", relief="sunken", bd=1)
        self.status.pack(side="bottom", fill="x")

        # ---- bindings ----------------------------------------------------
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", lambda e: self._set_status(""))
        # pan with arrow keys (canvas must have focus)
        self.canvas.bind("<Left>", lambda e: self._pan("x", -PAN_UNITS))
        self.canvas.bind("<Right>", lambda e: self._pan("x", PAN_UNITS))
        self.canvas.bind("<Up>", lambda e: self._pan("y", -PAN_UNITS))
        self.canvas.bind("<Down>", lambda e: self._pan("y", PAN_UNITS))
        # mouse-wheel: plain = vertical pan, Shift = horizontal, Ctrl = zoom
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(seq, self._on_wheel)
            self.canvas.bind("<Shift-" + seq[1:], self._on_wheel_shift)
            self.canvas.bind("<Control-" + seq[1:], self._on_wheel_ctrl)

    # ------------------------------------------------------------------ API
    def set_domain(self, nx, ny, dx=1.0, dy=1.0):
        """Set the domain size (cells = ``nx+1`` by ``ny+1``) and grid spacing."""
        self.nx = int(nx)
        self.ny = int(ny)
        self.dx = float(dx)
        self.dy = float(dy)
        self.selected_index = None
        self.zoom = 1.0
        self.redraw()

    def set_selected(self, index):
        """Highlight a single zone ``index`` (or ``None`` to clear)."""
        self.set_selection(set() if index is None else {index}, index)

    def set_selection(self, indices, primary=None):
        """Highlight a set of zones; ``primary`` is the one driving properties."""
        self.selected_set = set(indices)
        self.selected_index = primary
        self.redraw()

    def set_enabled(self, enabled):
        """Enable or disable painting (canvas is greyed out when disabled)."""
        self._enabled = bool(enabled)
        if not self._enabled:
            self._set_status("Load a reference file to enable the canvas.")
        self.redraw()

    # --------------------------------------------------------- zoom / pan
    def zoom_in(self):
        """Increase the zoom level by one step (centred on the view)."""
        self._set_zoom(self.zoom * ZOOM_STEP)

    def zoom_out(self):
        """Decrease the zoom level by one step (centred on the view)."""
        self._set_zoom(self.zoom / ZOOM_STEP)

    def zoom_reset(self):
        """Reset the zoom to fit-to-window."""
        self._set_zoom(1.0)

    def _set_zoom(self, zoom):
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        # keep the current view centre stable across the zoom change
        cx = sum(self.canvas.xview()) / 2.0
        cy = sum(self.canvas.yview()) / 2.0
        self.zoom = zoom
        self.redraw()
        self._center_view(cx, cy)

    def _center_view(self, cx, cy):
        xv = self.canvas.xview()
        yv = self.canvas.yview()
        span_x = xv[1] - xv[0]
        span_y = yv[1] - yv[0]
        self.canvas.xview_moveto(max(0.0, min(1.0 - span_x, cx - span_x / 2.0)))
        self.canvas.yview_moveto(max(0.0, min(1.0 - span_y, cy - span_y / 2.0)))

    def _pan(self, axis, amount):
        if axis == "x":
            self.canvas.xview_scroll(amount, "units")
        else:
            self.canvas.yview_scroll(amount, "units")
        return "break"

    def _on_wheel(self, event):
        self.canvas.yview_scroll(self._wheel_dir(event) * PAN_UNITS, "units")
        return "break"

    def _on_wheel_shift(self, event):
        self.canvas.xview_scroll(self._wheel_dir(event) * PAN_UNITS, "units")
        return "break"

    def _on_wheel_ctrl(self, event):
        if self._wheel_dir(event) < 0:
            self.zoom_in()
        else:
            self.zoom_out()
        return "break"

    @staticmethod
    def _wheel_dir(event):
        """Return -1 for scroll-up/zoom-in, +1 for scroll-down/zoom-out."""
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        return -1 if getattr(event, "delta", 0) > 0 else 1

    # --------------------------------------------------------- geometry
    @property
    def ncx(self):
        """Number of cells along x (``nx + 1``)."""
        return self.nx + 1

    @property
    def ncy(self):
        """Number of cells along y (``ny + 1``)."""
        return self.ny + 1

    def _fit_cell_size(self):
        w = max(self.canvas.winfo_width(), 1)
        h = max(self.canvas.winfo_height(), 1)
        if self.ncx <= 0 or self.ncy <= 0:
            return MIN_CELL_PX
        cs = min(w // self.ncx, h // self.ncy)
        return max(MIN_CELL_PX, min(MAX_CELL_PX, cs))

    def _cell_rect(self, i0, i1, j0, j1):
        """Pixel rectangle (x0, y0, x1, y1) for slice cells [i0:i1, j0:j1]."""
        cs = self.cs
        px0 = i0 * cs
        px1 = i1 * cs
        # j increases upward: cell j sits at screen row (ncy-1 - j) from top.
        py_top = (self.ncy - j1) * cs
        py_bot = (self.ncy - j0) * cs
        return px0, py_top, px1, py_bot

    def _pixel_to_cell(self, px, py):
        """Map a *canvas* pixel position to a clamped (i, j) cell index."""
        cs = self.cs
        i = int(px // cs)
        row = int(py // cs)
        j = (self.ncy - 1) - row
        i = max(0, min(self.ncx - 1, i))
        j = max(0, min(self.ncy - 1, j))
        return i, j

    def _event_cell(self, event):
        """Convert a mouse event to a cell index, accounting for scrolling."""
        return self._pixel_to_cell(self.canvas.canvasx(event.x),
                                   self.canvas.canvasy(event.y))

    # --------------------------------------------------------- rendering
    def _on_configure(self, _event):
        self.redraw()

    def redraw(self):
        """Repaint the whole canvas: background, zones, grid, selection."""
        c = self.canvas
        c.delete("all")
        if self.ncx <= 0 or self.ncy <= 0:
            return

        self.cs = max(MIN_ZOOMED_PX,
                      min(MAX_ZOOMED_PX, int(round(self._fit_cell_size() * self.zoom))))
        w = self.ncx * self.cs
        h = self.ncy * self.cs
        c.configure(scrollregion=(0, 0, w, h))
        self.zoom_label.config(text="{:.0f}%".format(self.zoom * 100))

        # background
        bg = BACKGROUND_COLOR if self._enabled else "#eeeeee"
        c.create_rectangle(0, 0, w, h, fill=bg, outline="")

        # zones (painter's algorithm: list order, last on top)
        zones = self._get_zones() if self._enabled else []
        for zone in zones:
            self._draw_zone(zone)

        # grid lines (only when cells are large enough to be useful)
        if self.cs >= 6:
            for i in range(self.ncx + 1):
                x = i * self.cs
                c.create_line(x, 0, x, h, fill=GRID_COLOR)
            for jrow in range(self.ncy + 1):
                y = jrow * self.cs
                c.create_line(0, y, w, y, fill=GRID_COLOR)

        self._draw_axes(w, h)

        # selection outlines (all selected zones; the primary is brighter)
        if self._enabled:
            for idx in self.selected_set:
                if not (0 <= idx < len(zones)):
                    continue
                z = zones[idx]
                x0, y0, x1, y1 = self._cell_rect(z["i0"], z["i1"], z["j0"], z["j1"])
                primary = (idx == self.selected_index)
                c.create_rectangle(x0, y0, x1, y1, outline=SELECT_COLOR,
                                   width=3 if primary else 2,
                                   dash=() if primary else (3, 2))

        # live drag preview (paint zone, or rubber-band marquee for Select)
        if self._drag_start is not None and self._drag_now is not None:
            i0, i1, j0, j1 = self._drag_bounds()
            x0, y0, x1, y1 = self._cell_rect(i0, i1, j0, j1)
            if self._get_active_tool() == SELECT_TOOL:
                c.create_rectangle(x0, y0, x1, y1, outline=SELECT_COLOR, width=1,
                                   dash=(2, 2), fill=SELECT_COLOR, stipple="gray12")
            else:
                color = ZONE_COLORS.get(self._get_active_tool(), "#000000")
                c.create_rectangle(x0, y0, x1, y1, outline=color, width=2, dash=(4, 3))

    def _draw_zone(self, zone):
        x0, y0, x1, y1 = self._cell_rect(zone["i0"], zone["i1"],
                                         zone["j0"], zone["j1"])
        ztype = zone["type"]
        color = ZONE_COLORS.get(ztype, "#000000")
        if ztype in ("soil", "terrain"):
            # overlays on the surface initial conditions: draw them stippled so
            # the underlying surface colour stays visible.
            stipple = "gray25" if ztype == "soil" else "gray12"
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=color,
                                         outline=color, stipple=stipple)
        elif ztype == "building":
            # shade by height: taller buildings are drawn darker.
            t = float(zone.get("building_height", 0.0)) / BUILDING_SHADE_MAX_H
            shade = _blend(BUILDING_SHADE_LIGHT, BUILDING_SHADE_DARK, t)
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=shade, outline="")
        else:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

    def _draw_axes(self, w, h):
        """Draw metre-scaled tick labels on the bottom/left edges + a N arrow."""
        c = self.canvas
        # choose a tick spacing (in cells) giving ~60 px between labels
        step = self._nice_step(max(1, int(round(60 / max(self.cs, 1)))))
        for i in range(0, self.ncx + 1, step):
            x = i * self.cs
            c.create_line(x, h, x, h - 5, fill=AXIS_COLOR)
            c.create_text(x + 1, h - 6, text="{:g}".format(i * self.dx),
                          anchor="se", fill=AXIS_COLOR, font=("TkDefaultFont", 7))
        for j in range(0, self.ncy + 1, step):
            y = (self.ncy - j) * self.cs
            c.create_line(0, y, 5, y, fill=AXIS_COLOR)
            c.create_text(6, y + 1, text="{:g}".format(j * self.dy),
                          anchor="nw", fill=AXIS_COLOR, font=("TkDefaultFont", 7))
        c.create_text(4, 4, text="m", anchor="nw", fill=AXIS_COLOR,
                      font=("TkDefaultFont", 7))
        # north arrow (north = +j = up) in the top-right corner of the grid
        ax = w - 14
        c.create_line(ax, 26, ax, 6, fill=AXIS_COLOR, width=2, arrow="last")
        c.create_text(ax, 30, text="N", anchor="n", fill=AXIS_COLOR,
                      font=("TkDefaultFont", 8, "bold"))

    @staticmethod
    def _nice_step(n):
        """Round ``n`` up to the next 1/2/5 × 10^k value."""
        if n <= 1:
            return 1
        for base in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
            if base >= n:
                return base
        return n

    # --------------------------------------------------------- interaction
    def _drag_bounds(self):
        """Inclusive drag cells -> exclusive slice bounds (i0, i1, j0, j1)."""
        i_a, j_a = self._drag_start
        i_b, j_b = self._drag_now
        i0, i1 = min(i_a, i_b), max(i_a, i_b) + 1
        j0, j1 = min(j_a, j_b), max(j_a, j_b) + 1
        return i0, i1, j0, j1

    def _on_press(self, event):
        self.canvas.focus_set()         # so the arrow keys pan this canvas
        if not self._enabled:
            return
        i, j = self._event_cell(event)
        if self._get_active_tool() in EDIT_TOOLS:
            self._begin_edit(i, j)
            return
        if self._get_active_tool() == SELECT_TOOL:
            self._select_additive = bool(event.state & 0x0005)  # Shift | Control
        self._drag_start = (i, j)
        self._drag_now = self._drag_start

    def _on_motion_drag(self, event):
        if not self._enabled:
            return
        i, j = self._event_cell(event)
        if self._editing is not None:
            self._apply_edit(i, j)
            self._set_status("{}  (i, j) = ({}, {})".format(
                self._editing["tool"], i, j))
            return
        if self._drag_start is None:
            return
        self._drag_now = (i, j)
        self._set_status("dragging  (i, j) = ({}, {})".format(i, j))
        self.redraw()

    def _on_release(self, event):
        if not self._enabled:
            return
        if self._editing is not None:
            self._editing = None
            self._on_edit_commit()
            return
        if self._drag_start is None:
            return
        start = self._drag_start
        end = self._event_cell(event)
        self._drag_now = end
        i0, i1, j0, j1 = self._drag_bounds()
        self._drag_start = None
        self._drag_now = None

        tool = self._get_active_tool()
        if tool == SELECT_TOOL:
            self._finish_select(start, end, i0, i1, j0, j1)
        elif start == end:
            # no drag → treat as a pick of the topmost zone at this cell
            self._on_pick(self._zone_at(start[0], start[1]))
            self.redraw()
        else:
            self._on_draw(i0, i1, j0, j1)

    # --------------------------------------------------------- selection
    def _finish_select(self, start, end, i0, i1, j0, j1):
        """Resolve a click or marquee with the Select tool into a selection."""
        zones = self._get_zones()
        if start == end:                       # a plain click
            top = self._zone_at(start[0], start[1])
            if self._select_additive and top is not None:
                final = set(self.selected_set) ^ {top}      # toggle membership
            elif top is None:
                final = set(self.selected_set) if self._select_additive else set()
            else:
                final = {top}
            primary = top if (top is not None and top in final) else (
                max(final) if final else None)
        else:                                  # a rubber-band marquee
            hits = [k for k, z in enumerate(zones)
                    if self._intersects(z, i0, i1, j0, j1)]
            final = (set(self.selected_set) | set(hits)
                     if self._select_additive else set(hits))
            primary = hits[-1] if hits else (max(final) if final else None)
        self._on_select(sorted(final), primary)

    @staticmethod
    def _intersects(z, i0, i1, j0, j1):
        """True if zone ``z`` overlaps the slice rectangle [i0:i1, j0:j1]."""
        return not (z["i1"] <= i0 or z["i0"] >= i1
                    or z["j1"] <= j0 or z["j0"] >= j1)

    # ----------------------------------------------------- move / reshape
    def _zone_for_edit(self, i, j):
        """Zone index to edit: the selected zone if the click is on/near it,
        else the topmost zone under the cursor."""
        zones = self._get_zones()
        if self.selected_index is not None and 0 <= self.selected_index < len(zones):
            z = zones[self.selected_index]
            if (z["i0"] - 1) <= i <= z["i1"] and (z["j0"] - 1) <= j <= z["j1"]:
                return self.selected_index
        return self._zone_at(i, j)

    @staticmethod
    def _orig_of(zones, k):
        z = zones[k]
        return (z["i0"], z["i1"], z["j0"], z["j1"])

    def _begin_edit(self, i, j):
        """Start a move/reshape gesture on the zone(s) under (i, j)."""
        zones = self._get_zones()
        tool = self._get_active_tool()

        if tool == "move":
            idx = self._zone_at(i, j)
            if idx is None:
                self._on_pick(None)
                return
            if idx in self.selected_set and len(self.selected_set) > 1:
                group = sorted(k for k in self.selected_set if 0 <= k < len(zones))
            else:
                if not (self.selected_set == {idx}):
                    self._on_pick(idx)      # select just this zone
                group = [idx]
            self._editing = {"tool": "move", "start": (i, j), "snapshotted": False,
                             "group": [(k, self._orig_of(zones, k)) for k in group]}
            return

        # reshape (single zone)
        idx = self._zone_for_edit(i, j)
        if idx is None:
            self._on_pick(None)
            return
        if idx != self.selected_index or self.selected_set != {idx}:
            self._on_pick(idx)
        orig = self._orig_of(zones, idx)
        xside, yside = self._grab_sides(i, j, orig)
        if xside is None and yside is None:     # middle click → move this zone
            self._editing = {"tool": "move", "start": (i, j), "snapshotted": False,
                             "group": [(idx, orig)]}
            return
        self._editing = {"tool": "reshape", "index": idx, "start": (i, j),
                         "orig": orig, "xside": xside, "yside": yside,
                         "snapshotted": False}

    @staticmethod
    def _grab_sides(i, j, orig):
        """Decide which edges a reshape click grabbed (corner = both axes)."""
        i0, i1, j0, j1 = orig
        tolx = max(1, (i1 - i0) // 4)
        toly = max(1, (j1 - j0) // 4)
        xside = yside = None
        if i <= i0 + tolx - 1:
            xside = "left"
        elif i >= (i1 - 1) - (tolx - 1):
            xside = "right"
        if j <= j0 + toly - 1:
            yside = "bottom"
        elif j >= (j1 - 1) - (toly - 1):
            yside = "top"
        return xside, yside

    def _apply_edit(self, i, j):
        """Apply the live move/reshape to the edited zone(s) and redraw."""
        ed = self._editing
        zones = self._get_zones()
        if ed["tool"] == "move":
            self._apply_move(ed, zones, i, j)
            return
        z = zones[ed["index"]]
        i0, i1, j0, j1 = ed["orig"]
        ni0, ni1, nj0, nj1 = i0, i1, j0, j1
        if ed["xside"] == "left":
            ni0 = max(0, min(i1 - 1, i))
        elif ed["xside"] == "right":
            ni1 = max(i0 + 1, min(self.ncx, i + 1))
        if ed["yside"] == "bottom":
            nj0 = max(0, min(j1 - 1, j))
        elif ed["yside"] == "top":
            nj1 = max(j0 + 1, min(self.ncy, j + 1))
        new = (ni0, ni1, nj0, nj1)
        if (z["i0"], z["i1"], z["j0"], z["j1"]) == new:
            return
        if not ed["snapshotted"]:
            self._on_edit_begin(ed["index"])
            ed["snapshotted"] = True
        z["i0"], z["i1"], z["j0"], z["j1"] = new
        self.redraw()

    def _apply_move(self, ed, zones, i, j):
        """Translate every zone in the move group by a domain-clamped delta."""
        di = i - ed["start"][0]
        dj = j - ed["start"][1]
        group = ed["group"]
        # clamp the shared delta so no zone leaves the domain
        di = max(-min(o[0] for _, o in group),
                 min(self.ncx - max(o[1] for _, o in group), di))
        dj = max(-min(o[2] for _, o in group),
                 min(self.ncy - max(o[3] for _, o in group), dj))
        new = {k: (o[0] + di, o[1] + di, o[2] + dj, o[3] + dj) for k, o in group}
        if all((zones[k]["i0"], zones[k]["i1"], zones[k]["j0"], zones[k]["j1"]) == nb
               for k, nb in new.items()):
            return
        if not ed["snapshotted"]:
            self._on_edit_begin(group[0][0])
            ed["snapshotted"] = True
        for k, (ni0, ni1, nj0, nj1) in new.items():
            z = zones[k]
            z["i0"], z["i1"], z["j0"], z["j1"] = ni0, ni1, nj0, nj1
        self.redraw()

    def _on_hover(self, event):
        if not self._enabled:
            return
        i, j = self._event_cell(event)
        tool = self._get_active_tool()
        cursor = {"move": "fleur", "reshape": "sizing"}.get(tool, "crosshair")
        if self.canvas.cget("cursor") != cursor:
            self.canvas.configure(cursor=cursor)
        xm = (i + 0.5) * self.dx
        ym = (j + 0.5) * self.dy
        self._set_status("(i, j) = ({}, {})    (x, y) = ({:g}, {:g}) m".format(
            i, j, xm, ym))

    def _zone_at(self, i, j):
        """Index of the topmost zone covering cell (i, j), or ``None``."""
        zones = self._get_zones()
        for idx in range(len(zones) - 1, -1, -1):
            z = zones[idx]
            if z["i0"] <= i < z["i1"] and z["j0"] <= j < z["j1"]:
                return idx
        return None

    def _set_status(self, text):
        self.status.config(text=text)
