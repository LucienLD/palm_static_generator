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
    "soil": "#A0522D",
}
#: Background (unassigned) colour — a light tint standing in for "pavement at
#: 50% opacity" (tkinter has no real alpha channel).
BACKGROUND_COLOR = "#d7d7d7"
GRID_COLOR = "#bdbdbd"
SELECT_COLOR = "#ff1744"

MIN_CELL_PX = 4         # smallest fit cell size
MAX_CELL_PX = 20        # largest fit cell size (before zoom)
MIN_ZOOMED_PX = 1       # absolute lower bound on a zoomed cell
MAX_ZOOMED_PX = 80      # absolute upper bound on a zoomed cell
ZOOM_MIN = 0.25
ZOOM_MAX = 8.0
ZOOM_STEP = 1.25
PAN_UNITS = 3           # arrow-key / wheel pan amount (scroll "units")


class GridCanvas(tk.Frame):
    """A scalable, zoomable, pannable 2-D grid the user paints zones onto."""

    def __init__(self, parent, get_active_tool, get_zones, on_draw, on_pick,
                 **kwargs):
        super().__init__(parent, **kwargs)
        self._get_active_tool = get_active_tool
        self._get_zones = get_zones
        self._on_draw = on_draw
        self._on_pick = on_pick

        self.nx = 0
        self.ny = 0
        self.cs = MIN_CELL_PX           # current (zoomed) cell size in pixels
        self.zoom = 1.0                 # 1.0 == fit-to-window
        self.selected_index = None
        self._enabled = False

        self._drag_start = None         # (i, j) cell at button press
        self._drag_now = None           # (i, j) cell under cursor while dragging

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
    def set_domain(self, nx, ny):
        """Set the domain size (cells = ``nx+1`` by ``ny+1``) and redraw."""
        self.nx = int(nx)
        self.ny = int(ny)
        self.selected_index = None
        self.zoom = 1.0
        self.redraw()

    def set_selected(self, index):
        """Highlight zone ``index`` (or ``None`` to clear) and redraw."""
        self.selected_index = index
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

        # selection outline
        if (self.selected_index is not None and self._enabled
                and 0 <= self.selected_index < len(zones)):
            z = zones[self.selected_index]
            x0, y0, x1, y1 = self._cell_rect(z["i0"], z["i1"], z["j0"], z["j1"])
            c.create_rectangle(x0, y0, x1, y1, outline=SELECT_COLOR, width=3)

        # live drag preview
        if self._drag_start is not None and self._drag_now is not None:
            i0, i1, j0, j1 = self._drag_bounds()
            x0, y0, x1, y1 = self._cell_rect(i0, i1, j0, j1)
            color = ZONE_COLORS.get(self._get_active_tool(), "#000000")
            c.create_rectangle(x0, y0, x1, y1, outline=color, width=2, dash=(4, 3))

    def _draw_zone(self, zone):
        x0, y0, x1, y1 = self._cell_rect(zone["i0"], zone["i1"],
                                         zone["j0"], zone["j1"])
        color = ZONE_COLORS.get(zone["type"], "#000000")
        if zone["type"] == "soil":
            # soil is an overlay on the surface initial conditions: draw it
            # stippled so the underlying surface colour remains visible.
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=color,
                                         outline=color, stipple="gray25")
        else:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

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
        self._drag_start = self._event_cell(event)
        self._drag_now = self._drag_start

    def _on_motion_drag(self, event):
        if not self._enabled or self._drag_start is None:
            return
        self._drag_now = self._event_cell(event)
        i, j = self._drag_now
        self._set_status("dragging  (i, j) = ({}, {})".format(i, j))
        self.redraw()

    def _on_release(self, event):
        if not self._enabled or self._drag_start is None:
            return
        start = self._drag_start
        end = self._event_cell(event)
        self._drag_now = end
        i0, i1, j0, j1 = self._drag_bounds()
        self._drag_start = None
        self._drag_now = None

        if start == end:
            # no drag → treat as a pick of the topmost zone at this cell
            self._on_pick(self._zone_at(start[0], start[1]))
            self.redraw()
        else:
            self._on_draw(i0, i1, j0, j1)

    def _on_hover(self, event):
        if not self._enabled:
            return
        i, j = self._event_cell(event)
        self._set_status("(i, j) = ({}, {})".format(i, j))

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
