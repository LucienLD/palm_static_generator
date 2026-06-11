"""Main tkinter application for the PALM static-driver generator.

Run from the project root with::

    python -m palm_static_generator          # or: python -m palm_static_generator.gui.app

The window has three panels: a left configuration column, a central painting
:class:`~palm_static_generator.gui.canvas_widget.GridCanvas`, and a right zone
inspector.  No painting or generation is possible until a PALM 3D output
reference file has been loaded (its vertical grid and domain size drive
everything else).
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import yaml

from ..core import geo, palm_types, writer
from .canvas_widget import GridCanvas

# the bundled config.yaml lives at  <package root>/config/config.yaml
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(_PACKAGE_ROOT, "config", "config.yaml")


# --------------------------------------------------------------------------- #
# A minimal vertically-scrollable frame
# --------------------------------------------------------------------------- #
class ScrollableFrame(tk.Frame):
    """A frame whose ``.inner`` child scrolls vertically."""

    def __init__(self, parent, width=300, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, width=width)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._win, width=e.width),
        )
        # mouse-wheel scrolling while the pointer is over the frame
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _e):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


# --------------------------------------------------------------------------- #
# Main application
# --------------------------------------------------------------------------- #
class PalmStaticGUI(tk.Tk):
    """The PALM static-driver generator main window."""

    def __init__(self, config_path=None):
        super().__init__()
        self.title("PALM Static Driver Generator")
        self.geometry("1280x760")
        self.minsize(1000, 600)

        # ---- application state -------------------------------------------
        self.config_data = self._default_config()
        self.zones = []                 # ordered list of zone dicts
        self.selected_index = None      # primary selection (drives properties)
        self.selected_indices = set()   # all selected zone indices
        self._syncing_selection = False  # guards tree<->state feedback loops
        self.reference = None           # dict from read_reference_grid, or None
        self.zlad_heights = [0.0] * palm_types.N_ZLAD
        self.zsoil_depths = self._compute_zsoil_depths()

        self.active_tool = tk.StringVar(value="building")
        self._gated_widgets = []        # disabled until a reference is loaded
        self._prop_widgets = []         # current property-panel widgets
        self._undo_stack = []           # snapshots of self.zones (deep copies)
        self._redo_stack = []
        self._clipboard = None          # a copied/cut zone dict
        self._domain_warned = False     # warn-once flag for domain edits

        # ---- layout ------------------------------------------------------
        self._build_layout()

        # ---- keyboard shortcuts ------------------------------------------
        self.bind_all("<Control-z>", lambda e: self.undo())
        self.bind_all("<Control-y>", lambda e: self.redo())
        self.bind_all("<Control-Z>", lambda e: self.redo())  # Ctrl+Shift+Z

        # load the bundled config.yaml template if available
        if config_path is None and os.path.exists(DEFAULT_CONFIG_PATH):
            config_path = DEFAULT_CONFIG_PATH
        if config_path and os.path.exists(config_path):
            self._load_config_file(config_path)

        self._set_gated_state(False)
        self.refresh_zone_list()

    # ================================================================== setup
    @staticmethod
    def _default_config():
        """Return a minimal config dict used before any YAML is loaded."""
        return {
            "reference_file": "",
            "domain": {"nx": 39, "ny": 39, "dx": 2.0, "dy": 2.0},
            "output": {"filename": "my_case_static"},
            "global_attributes": {
                "title": "My PALM test case",
                "author": "Your Name",
                "institution": "Your institution",
                "origin_lat": 48.8534,
                "origin_lon": 2.3488,
                "origin_x": 452000.0,
                "origin_y": 5411600.0,
                "origin_z": 35.0,
                "origin_time": "2023-07-15 06:00:00 +00",
                "rotation_angle": 0.0,
            },
            "defaults": {
                "background_type": "pavement",
                "pavement_type": 2,
                "soil_type": 2,
                "soil_temperature": [300.0, 295.0, 290.0, 290.0,
                                     290.0, 290.0, 290.0, 290.0],
                "soil_moisture": 0.05,
                "deep_soil_temperature": 290.0,
            },
        }

    @staticmethod
    def _compute_zsoil_depths():
        dz = np.array(palm_types.ZSOIL_DZ, dtype=float)
        return list(np.round(np.cumsum(dz) - dz / 2.0, 4))

    def _build_layout(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0)
        self.rowconfigure(0, weight=1)

        self._build_left_panel()
        self._build_center_panel()
        self._build_right_panel()

    # ============================================================ left panel
    def _build_left_panel(self):
        container = ScrollableFrame(self, width=320)
        container.grid(row=0, column=0, sticky="nsew")
        left = container.inner

        self.var = {}  # named tk variables for config fields

        # --- Section 1: reference file -----------------------------------
        sec1 = ttk.LabelFrame(left, text="1 · Reference file (required)")
        sec1.pack(fill="x", padx=6, pady=(8, 4))
        ttk.Button(sec1, text="Load reference NetCDF…",
                   command=self.load_reference).pack(fill="x", padx=6, pady=6)
        self.ref_label = tk.Label(sec1, text="No file loaded", fg="red",
                                  anchor="w", wraplength=290, justify="left")
        self.ref_label.pack(fill="x", padx=6, pady=(0, 6))

        # --- Section 2: domain -------------------------------------------
        sec2 = ttk.LabelFrame(left, text="2 · Domain")
        sec2.pack(fill="x", padx=6, pady=4)
        for label, key in (("nx", "domain_nx"), ("ny", "domain_ny"),
                           ("dx [m]", "domain_dx"), ("dy [m]", "domain_dy")):
            ent = self._add_labeled_entry(sec2, label, key, gated=True)
            ent.bind("<FocusOut>", self._on_domain_edit)
        tk.Label(sec2, text="(inferred from reference; edit with care)",
                 fg="#666", font=("TkDefaultFont", 8)).pack(anchor="w", padx=6)
        self.domain_recap = tk.Label(
            sec2, text="Domain: —", anchor="w", justify="left",
            fg="#0d47a1", font=("TkDefaultFont", 9, "bold"))
        self.domain_recap.pack(fill="x", padx=6, pady=(2, 4))
        # keep the recap live as the domain fields change
        for key in ("domain_nx", "domain_ny", "domain_dx", "domain_dy"):
            self.var[key].trace_add("write", lambda *_a: self._update_domain_recap())
        self._add_labeled_entry(sec2, "output filename", "output_filename")

        # --- Section 3: global attributes --------------------------------
        sec3 = ttk.LabelFrame(left, text="3 · Global attributes")
        sec3.pack(fill="x", padx=6, pady=4)
        for label, key in [
            ("title", "ga_title"), ("author", "ga_author"),
            ("institution", "ga_institution"),
            ("origin_lat", "ga_origin_lat"), ("origin_lon", "ga_origin_lon"),
            ("origin_x", "ga_origin_x"), ("origin_y", "ga_origin_y"),
            ("origin_z", "ga_origin_z"), ("origin_time", "ga_origin_time"),
            ("rotation_angle", "ga_rotation_angle"),
        ]:
            self._add_labeled_entry(sec3, label, key)
        ttk.Button(sec3, text="↻ Compute origin_x / origin_y from lat/lon (UTM)",
                   command=self.compute_utm).pack(fill="x", padx=6, pady=(4, 1))
        self.utm_label = tk.Label(sec3, text="", anchor="w", fg="#0d47a1",
                                  font=("TkDefaultFont", 8))
        self.utm_label.pack(fill="x", padx=6, pady=(0, 4))

        # --- Section 4: defaults -----------------------------------------
        sec4 = ttk.LabelFrame(left, text="4 · Defaults")
        sec4.pack(fill="x", padx=6, pady=4)
        self._add_combobox(sec4, "background_type", "def_background",
                           ["pavement", "bare soil"])
        self._add_type_combobox(sec4, "pavement_type", "def_pavement_type",
                                palm_types.PAVEMENT_TYPES)
        self._add_type_combobox(sec4, "soil_type", "def_soil_type",
                                palm_types.SOIL_TYPES)
        self._add_labeled_entry(sec4, "soil_moisture [m³/m³]", "def_soil_moisture")
        self._add_labeled_entry(sec4, "deep_soil_temperature [K]",
                                "def_deep_soil_temperature")
        ttk.Button(sec4, text="Edit default soil-temperature profile…",
                   command=self.edit_default_soil_profile).pack(
            fill="x", padx=6, pady=(2, 4))

        # --- bottom buttons ----------------------------------------------
        cfgbtns = tk.Frame(left)
        cfgbtns.pack(fill="x", padx=6, pady=(10, 2))
        ttk.Button(cfgbtns, text="Load YAML", command=self.load_yaml).pack(
            side="left", expand=True, fill="x", padx=2)
        ttk.Button(cfgbtns, text="Save YAML", command=self.save_yaml).pack(
            side="left", expand=True, fill="x", padx=2)
        scnbtns = tk.Frame(left)
        scnbtns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(scnbtns, text="Load scene", command=self.load_scene).pack(
            side="left", expand=True, fill="x", padx=2)
        self.save_scene_btn = ttk.Button(scnbtns, text="Save scene",
                                         command=self.save_scene)
        self.save_scene_btn.pack(side="left", expand=True, fill="x", padx=2)
        self._gated_widgets.append(self.save_scene_btn)
        self.generate_btn = ttk.Button(left, text="Generate NetCDF",
                                       command=self.generate_netcdf)
        self.generate_btn.pack(fill="x", padx=6, pady=(0, 12))
        self._gated_widgets.append(self.generate_btn)

        self._sync_widgets_from_config()

    def _add_labeled_entry(self, parent, label, key, gated=False):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=6, pady=2)
        tk.Label(row, text=label, width=18, anchor="w").pack(side="left")
        var = tk.StringVar()
        ent = ttk.Entry(row, textvariable=var)
        ent.pack(side="left", fill="x", expand=True)
        self.var[key] = var
        if gated:
            self._gated_widgets.append(ent)
        return ent

    def _add_combobox(self, parent, label, key, values):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=6, pady=2)
        tk.Label(row, text=label, width=18, anchor="w").pack(side="left")
        var = tk.StringVar()
        cb = ttk.Combobox(row, textvariable=var, values=values, state="readonly")
        cb.pack(side="left", fill="x", expand=True)
        self.var[key] = var
        return cb

    def _add_type_combobox(self, parent, label, key, table):
        return self._add_combobox(parent, label, key, palm_types.options(table))

    # ---------------------------------------------------- derived fields
    def _update_domain_recap(self):
        """Refresh the domain-size recap from the domain widgets and zones."""
        nx = self._as_int(self.var["domain_nx"].get(), 0)
        ny = self._as_int(self.var["domain_ny"].get(), 0)
        dx = self._as_float(self.var["domain_dx"].get(), 0.0)
        dy = self._as_float(self.var["domain_dy"].get(), 0.0)
        ncx, ncy = nx + 1, ny + 1
        total = ncx * ncy
        lx, ly = ncx * dx, ncy * dy

        # Walk the zones in paint order: surface zones mark cells painted, the
        # eraser (background) un-paints them; soil/terrain are overlays counted
        # by their footprint union.
        painted = np.zeros((ncy, ncx), dtype=bool)
        soil_mask = np.zeros((ncy, ncx), dtype=bool)
        terr_mask = np.zeros((ncy, ncx), dtype=bool)
        for z in self.zones:
            i0 = max(0, z["i0"]); i1 = min(ncx, z["i1"])
            j0 = max(0, z["j0"]); j1 = min(ncy, z["j1"])
            if i1 <= i0 or j1 <= j0:
                continue
            t = z["type"]
            if t in ("building", "vegetation", "pavement", "water"):
                painted[j0:j1, i0:i1] = True
            elif t == "background":
                painted[j0:j1, i0:i1] = False
            elif t == "soil":
                soil_mask[j0:j1, i0:i1] = True
            elif t == "terrain":
                terr_mask[j0:j1, i0:i1] = True
        n_used = int(painted.sum())
        pct = (100.0 * n_used / total) if total else 0.0

        text = ("Domain: {} × {} cells   |   {:.1f} × {:.1f} m\n"
                "Painted: {} / {} cells ({:.0f}%)   ·   background: {}").format(
            ncx, ncy, lx, ly, n_used, total, pct, total - n_used)
        if soil_mask.any():
            text += "   ·   soil overlay: {}".format(int(soil_mask.sum()))
        if terr_mask.any():
            text += "   ·   terrain: {}".format(int(terr_mask.sum()))
        self.domain_recap.config(text=text)

    def compute_utm(self):
        """Fill origin_x / origin_y from origin_lat / origin_lon via UTM."""
        lat = self._as_float(self.var["ga_origin_lat"].get(), None)
        lon = self._as_float(self.var["ga_origin_lon"].get(), None)
        if lat is None or lon is None:
            messagebox.showerror(
                "UTM", "Enter valid origin_lat and origin_lon first.")
            return
        res = geo.latlon_to_utm(lat, lon)
        self.var["ga_origin_x"].set("{:.1f}".format(res["easting"]))
        self.var["ga_origin_y"].set("{:.1f}".format(res["northing"]))
        self.utm_label.config(
            text="UTM zone {}{}  →  E={:.1f} m, N={:.1f} m".format(
                res["zone"], res["hemisphere"], res["easting"], res["northing"]))

    # ========================================================== center panel
    def _build_center_panel(self):
        center = tk.Frame(self)
        center.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        self.grid_canvas = GridCanvas(
            center,
            get_active_tool=self.active_tool.get,
            get_zones=lambda: self.zones,
            on_draw=self.on_canvas_draw,
            on_pick=self.on_canvas_pick,
            on_edit_begin=self.on_zone_edit_begin,
            on_edit_commit=self.on_zone_edit_commit,
            on_select=self.on_canvas_select,
        )
        self.grid_canvas.grid(row=0, column=0, sticky="nsew")
        self.grid_canvas.canvas.bind("<Delete>", lambda e: self.delete_zone())
        self._bind_clipboard(self.grid_canvas.canvas)

    # =========================================================== right panel
    def _build_right_panel(self):
        right = tk.Frame(self, width=360)
        right.grid(row=0, column=2, sticky="nsew")
        right.grid_propagate(False)
        right.rowconfigure(2, weight=1)

        # tool selector
        tools = ttk.LabelFrame(right, text="Drawing tool")
        tools.pack(fill="x", padx=6, pady=6)
        tool_defs = [
            ("building", "Building"), ("vegetation", "Vegetation"),
            ("pavement", "Pavement"), ("water", "Water"),
            ("soil", "Soil"), ("terrain", "Terrain"),
            ("background", "Erase"),
        ]
        for n, (value, label) in enumerate(tool_defs):
            rb = ttk.Radiobutton(tools, text=label, value=value,
                                 variable=self.active_tool)
            rb.grid(row=n // 4, column=n % 4, sticky="w", padx=4, pady=3)
            self._gated_widgets.append(rb)
        # edit tools (act on existing zones via mouse)
        edit = ttk.LabelFrame(right, text="Edit (mouse)")
        edit.pack(fill="x", padx=6, pady=(0, 6))
        for value, label in (("select", "Select"), ("move", "Move"),
                             ("reshape", "Reshape")):
            rb = ttk.Radiobutton(edit, text=label, value=value,
                                 variable=self.active_tool)
            rb.pack(side="left", padx=6, pady=3)
            self._gated_widgets.append(rb)
        tk.Label(edit, text="Select: click / drag a box (Shift|Ctrl to add) · "
                            "Move drags the whole selection",
                 fg="#666", font=("TkDefaultFont", 8), wraplength=330,
                 justify="left").pack(anchor="w", padx=4)

        # zone list
        listfrm = ttk.LabelFrame(right, text="Zones (bottom → top)")
        listfrm.pack(fill="both", expand=False, padx=6, pady=4)
        cols = ("idx", "type", "bounds", "summary")
        self.tree = ttk.Treeview(listfrm, columns=cols, show="headings", height=8)
        for col, text, width in [
            ("idx", "#", 28), ("type", "Type", 80),
            ("bounds", "Bounds", 120), ("summary", "Summary", 120),
        ]:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        vsb = ttk.Scrollbar(listfrm, orient="vertical", command=self.tree.yview)
        vsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Delete>", lambda e: self.delete_zone())
        self._bind_clipboard(self.tree)

        zbtns = tk.Frame(right)
        zbtns.pack(fill="x", padx=6, pady=2)
        for text, cmd in [
            ("Delete", self.delete_zone), ("Move up", self.move_up),
            ("Move down", self.move_down), ("Duplicate", self.duplicate_zone),
        ]:
            b = ttk.Button(zbtns, text=text, command=cmd)
            b.pack(side="left", expand=True, fill="x", padx=1)
            self._gated_widgets.append(b)

        zbtns2 = tk.Frame(right)
        zbtns2.pack(fill="x", padx=6, pady=(0, 2))
        for text, cmd in [("↶ Undo", self.undo), ("↷ Redo", self.redo)]:
            b = ttk.Button(zbtns2, text=text, command=cmd)
            b.pack(side="left", expand=True, fill="x", padx=1)
            self._gated_widgets.append(b)

        # properties
        propframe = ScrollableFrame(right, width=340)
        propframe.pack(fill="both", expand=True, padx=6, pady=6)
        self.prop_outer = ttk.LabelFrame(propframe.inner, text="Zone properties")
        self.prop_outer.pack(fill="both", expand=True)
        self.prop_frame = tk.Frame(self.prop_outer)
        self.prop_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self._clear_properties()

    # ===================================================== gating / enable
    def _set_gated_state(self, enabled):
        state = "normal" if enabled else "disabled"
        for w in self._gated_widgets:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self.grid_canvas.set_enabled(enabled)

    # ================================================ reference file loading
    def load_reference(self):
        """Open a PALM 3D output file and initialise the domain from it."""
        path = filedialog.askopenfilename(
            title="Select PALM 3D output NetCDF",
            filetypes=[("NetCDF files", "*.nc"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            grid = writer.read_reference_grid(path)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            messagebox.showerror("Reference file error", str(exc))
            return
        self.reference = grid
        self.config_data["reference_file"] = path
        self.zlad_heights = [float(v) for v in grid["zu"][0:palm_types.N_ZLAD]]

        # pre-fill domain fields from the reference
        self.var["domain_nx"].set(str(grid["nx"]))
        self.var["domain_ny"].set(str(grid["ny"]))
        self.var["domain_dx"].set(str(grid["dx"]))
        self.var["domain_dy"].set(str(grid["dy"]))

        self.ref_label.config(
            text="{}\n(nx={}, ny={}, nz={}, dx={}, dy={})".format(
                os.path.basename(path), grid["nx"], grid["ny"],
                len(grid["zu"]), grid["dx"], grid["dy"]),
            fg="#1b5e20",
        )
        self._domain_warned = False
        self._set_gated_state(True)
        self.grid_canvas.set_domain(grid["nx"], grid["ny"], grid["dx"], grid["dy"])
        self._refresh_properties_for_selection()

    # ============================================ config <-> widgets sync
    def _sync_widgets_from_config(self):
        c = self.config_data
        d, o, ga, df = c["domain"], c["output"], c["global_attributes"], c["defaults"]
        self.var["domain_nx"].set(str(d["nx"]))
        self.var["domain_ny"].set(str(d["ny"]))
        self.var["domain_dx"].set(str(d["dx"]))
        self.var["domain_dy"].set(str(d["dy"]))
        self.var["output_filename"].set(str(o["filename"]))
        for key, gk in [
            ("ga_title", "title"), ("ga_author", "author"),
            ("ga_institution", "institution"),
            ("ga_origin_lat", "origin_lat"), ("ga_origin_lon", "origin_lon"),
            ("ga_origin_x", "origin_x"), ("ga_origin_y", "origin_y"),
            ("ga_origin_z", "origin_z"), ("ga_origin_time", "origin_time"),
            ("ga_rotation_angle", "rotation_angle"),
        ]:
            self.var[key].set(str(ga.get(gk, "")))
        self.var["def_background"].set(str(df.get("background_type", "pavement")))
        self.var["def_pavement_type"].set(
            palm_types.option_for(palm_types.PAVEMENT_TYPES, df["pavement_type"]))
        self.var["def_soil_type"].set(
            palm_types.option_for(palm_types.SOIL_TYPES, df["soil_type"]))
        self.var["def_soil_moisture"].set(str(df["soil_moisture"]))
        self.var["def_deep_soil_temperature"].set(str(df["deep_soil_temperature"]))

    def _sync_config_from_widgets(self):
        """Read all left-panel widgets back into ``self.config_data``."""
        c = self.config_data
        c["reference_file"] = self.reference["path"] if self.reference else ""
        c["domain"]["nx"] = self._as_int(self.var["domain_nx"].get(), c["domain"]["nx"])
        c["domain"]["ny"] = self._as_int(self.var["domain_ny"].get(), c["domain"]["ny"])
        c["domain"]["dx"] = self._as_float(self.var["domain_dx"].get(), c["domain"]["dx"])
        c["domain"]["dy"] = self._as_float(self.var["domain_dy"].get(), c["domain"]["dy"])
        c["output"]["filename"] = self.var["output_filename"].get().strip() or "my_case_static"
        ga = c["global_attributes"]
        ga["title"] = self.var["ga_title"].get()
        ga["author"] = self.var["ga_author"].get()
        ga["institution"] = self.var["ga_institution"].get()
        ga["origin_lat"] = self._as_float(self.var["ga_origin_lat"].get(), ga["origin_lat"])
        ga["origin_lon"] = self._as_float(self.var["ga_origin_lon"].get(), ga["origin_lon"])
        ga["origin_x"] = self._as_float(self.var["ga_origin_x"].get(), ga["origin_x"])
        ga["origin_y"] = self._as_float(self.var["ga_origin_y"].get(), ga["origin_y"])
        ga["origin_z"] = self._as_float(self.var["ga_origin_z"].get(), ga["origin_z"])
        ga["origin_time"] = self.var["ga_origin_time"].get()
        ga["rotation_angle"] = self._as_float(
            self.var["ga_rotation_angle"].get(), ga["rotation_angle"])
        df = c["defaults"]
        df["background_type"] = self.var["def_background"].get() or "pavement"
        df["pavement_type"] = palm_types.parse_code(self.var["def_pavement_type"].get())
        df["soil_type"] = palm_types.parse_code(self.var["def_soil_type"].get())
        df["soil_moisture"] = self._as_float(
            self.var["def_soil_moisture"].get(), df["soil_moisture"])
        df["deep_soil_temperature"] = self._as_float(
            self.var["def_deep_soil_temperature"].get(), df["deep_soil_temperature"])

    @staticmethod
    def _as_int(text, fallback):
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _as_float(text, fallback):
        try:
            return float(text)
        except (TypeError, ValueError):
            return fallback

    # ==================================================== canvas callbacks
    def on_canvas_draw(self, i0, i1, j0, j1):
        """Create a new zone of the active tool type from a canvas drag."""
        self._sync_config_from_widgets()
        self._snapshot()
        zone = self._new_zone(self.active_tool.get(), i0, i1, j0, j1)
        self.zones.append(zone)
        self.refresh_zone_list()
        self._select({len(self.zones) - 1}, len(self.zones) - 1)

    def on_canvas_pick(self, index):
        """Single-select the zone picked on the canvas (or clear selection)."""
        self._select(set() if index is None else {index}, index)

    def on_canvas_select(self, indices, primary):
        """Apply a multi-selection from the Select tool (click or marquee)."""
        self._select(set(indices), primary)

    # ------------------------------------------------------ selection core
    def _select(self, indices, primary, rebuild_props=True):
        """Set the selection (a set of indices + a primary) and sync widgets."""
        valid = sorted(i for i in indices if 0 <= i < len(self.zones))
        self.selected_indices = set(valid)
        if primary is not None and primary in self.selected_indices:
            self.selected_index = primary
        elif valid:
            self.selected_index = valid[-1]
        else:
            self.selected_index = None
        self._sync_selection_widgets()
        if rebuild_props:
            self._refresh_properties_for_selection()

    def _sync_selection_widgets(self):
        """Reflect ``selected_indices``/``selected_index`` in tree + canvas."""
        self._syncing_selection = True
        try:
            ids = [str(i) for i in sorted(self.selected_indices)
                   if 0 <= i < len(self.zones)]
            self.tree.selection_set(ids)
            if (self.selected_index is not None
                    and 0 <= self.selected_index < len(self.zones)):
                self.tree.focus(str(self.selected_index))
                self.tree.see(str(self.selected_index))
        finally:
            self._syncing_selection = False
        self.grid_canvas.set_selection(self.selected_indices, self.selected_index)

    def on_zone_edit_begin(self, _index):
        """Snapshot before a canvas move/reshape gesture changes zone(s)."""
        self._snapshot()

    def on_zone_edit_commit(self):
        """Refresh after a canvas move/reshape gesture finishes."""
        self.refresh_zone_list()
        self._select(self.selected_indices, self.selected_index)

    def _new_zone(self, ztype, i0, i1, j0, j1):
        """Build a zone dict with type-appropriate defaults."""
        df = self.config_data["defaults"]
        base = {"type": ztype, "label": ztype.capitalize(),
                "i0": i0, "i1": i1, "j0": j0, "j1": j1}
        if ztype == "building":
            base.update(building_height=10.0, building_type=2)
        elif ztype == "vegetation":
            base.update(vegetation_type=4, soil_type=int(df["soil_type"]),
                        lad=list(palm_types.DEFAULT_LAD_PROFILE))
        elif ztype == "pavement":
            base.update(pavement_type=int(df["pavement_type"]),
                        soil_type=int(df["soil_type"]))
        elif ztype == "water":
            base.update(water_type=1)
        elif ztype == "terrain":
            base.update(terrain_height=0.0)
        elif ztype == "soil":
            base.update(soil_type=int(df["soil_type"]),
                        soil_temperature=list(df["soil_temperature"]),
                        soil_moisture=float(df["soil_moisture"]),
                        deep_soil_temperature=float(df["deep_soil_temperature"]))
        elif ztype == "background":
            base["label"] = "Erase"
        return base

    # ======================================================= zone list
    def refresh_zone_list(self):
        """Rebuild the zone Treeview from ``self.zones``."""
        self.tree.delete(*self.tree.get_children())
        for idx, z in enumerate(self.zones):
            self.tree.insert(
                "", "end", iid=str(idx),
                values=(idx + 1, z["type"].capitalize(),
                        self._bounds_text(z), self._summary_text(z)),
            )
        self._update_domain_recap()

    @staticmethod
    def _bounds_text(z):
        return "i={}:{}, j={}:{}".format(z["i0"], z["i1"], z["j0"], z["j1"])

    @staticmethod
    def _summary_text(z):
        t = z["type"]
        if t == "building":
            return "h={:.1f} m (type {})".format(z["building_height"], z["building_type"])
        if t == "vegetation":
            return palm_types.VEGETATION_TYPES.get(z["vegetation_type"], "?")
        if t == "pavement":
            return "{} (type {})".format(
                palm_types.PAVEMENT_TYPES.get(z["pavement_type"], "?"), z["pavement_type"])
        if t == "water":
            return palm_types.WATER_TYPES.get(z["water_type"], "?")
        if t == "terrain":
            return "zt={:.1f} m".format(z.get("terrain_height", 0.0))
        if t == "soil":
            return "{}, θ={:.2f}".format(
                palm_types.SOIL_TYPES.get(z["soil_type"], "?"), z["soil_moisture"])
        if t == "background":
            return "reset to background"
        return ""

    def _on_tree_select(self, _event):
        if self._syncing_selection:
            return
        idxs = set(int(s) for s in self.tree.selection())
        if idxs == self.selected_indices:
            return                       # echo from our own selection_set → ignore
        focus = self.tree.focus()
        primary = int(focus) if focus else (max(idxs) if idxs else None)
        self._select(idxs, primary)

    # --------------------------------------------------- list operations
    def _current_zone(self):
        if self.selected_index is None:
            return None
        if 0 <= self.selected_index < len(self.zones):
            return self.zones[self.selected_index]
        return None

    def delete_zone(self):
        """Delete all currently selected zones."""
        if not self.selected_indices:
            return
        self._snapshot()
        for i in sorted(self.selected_indices, reverse=True):
            if 0 <= i < len(self.zones):
                del self.zones[i]
        self.refresh_zone_list()
        self._select(set(), None)

    def move_up(self):
        i = self.selected_index
        if i is None or i <= 0:
            return
        self._snapshot()
        self.zones[i - 1], self.zones[i] = self.zones[i], self.zones[i - 1]
        self.refresh_zone_list()
        self._select({i - 1}, i - 1)

    def move_down(self):
        i = self.selected_index
        if i is None or i >= len(self.zones) - 1:
            return
        self._snapshot()
        self.zones[i + 1], self.zones[i] = self.zones[i], self.zones[i + 1]
        self.refresh_zone_list()
        self._select({i + 1}, i + 1)

    def duplicate_zone(self):
        z = self._current_zone()
        if z is None:
            return
        import copy
        self._snapshot()
        clone = copy.deepcopy(z)
        clone["label"] = z.get("label", z["type"]) + " copy"
        pos = self.selected_index + 1
        self.zones.insert(pos, clone)
        self.refresh_zone_list()
        self._select({pos}, pos)

    # ------------------------------------------------------- undo / redo
    def _snapshot(self):
        """Push the current zone list onto the undo stack (clears redo)."""
        import copy
        self._undo_stack.append(copy.deepcopy(self.zones))
        if len(self._undo_stack) > 200:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        """Restore the previous zone-list state."""
        import copy
        if not self._undo_stack:
            return
        self._redo_stack.append(copy.deepcopy(self.zones))
        self.zones = self._undo_stack.pop()
        self._after_undo_redo()

    def redo(self):
        """Re-apply the last undone zone-list state."""
        import copy
        if not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self.zones))
        self.zones = self._redo_stack.pop()
        self._after_undo_redo()

    def _after_undo_redo(self):
        keep = {i for i in self.selected_indices if 0 <= i < len(self.zones)}
        primary = (self.selected_index
                   if self.selected_index is not None
                   and self.selected_index < len(self.zones) else None)
        self.refresh_zone_list()
        self._select(keep, primary)

    # ------------------------------------------------- clipboard (zones)
    def _bind_clipboard(self, widget):
        """Bind Ctrl+C/X/V/D zone clipboard shortcuts on a widget."""
        widget.bind("<Control-c>", lambda e: self._clip(self.copy_zone))
        widget.bind("<Control-x>", lambda e: self._clip(self.cut_zone))
        widget.bind("<Control-v>", lambda e: self._clip(self.paste_zone))
        widget.bind("<Control-d>", lambda e: self._clip(self.duplicate_zone))

    @staticmethod
    def _clip(fn):
        fn()
        return "break"          # don't fall through to other bindings

    def copy_zone(self):
        """Copy the selected zone to the clipboard."""
        import copy
        z = self._current_zone()
        if z is not None:
            self._clipboard = copy.deepcopy(z)

    def cut_zone(self):
        """Copy the selected zone to the clipboard, then delete it."""
        import copy
        z = self._current_zone()
        if z is None:
            return
        self._clipboard = copy.deepcopy(z)
        self.delete_zone()      # snapshots + refreshes

    def paste_zone(self):
        """Paste the clipboard zone, offset by one cell and clamped to the grid."""
        if self._clipboard is None:
            return
        import copy
        self._snapshot()
        clone = copy.deepcopy(self._clipboard)
        nx1 = int(self.config_data["domain"]["nx"]) + 1
        ny1 = int(self.config_data["domain"]["ny"]) + 1
        w = clone["i1"] - clone["i0"]
        h = clone["j1"] - clone["j0"]
        i0 = max(0, min(clone["i0"] + 1, nx1 - w))
        j0 = max(0, min(clone["j0"] + 1, ny1 - h))
        clone["i0"], clone["i1"] = i0, i0 + w
        clone["j0"], clone["j1"] = j0, j0 + h
        clone["label"] = clone.get("label", clone["type"]) + " copy"
        pos = (self.selected_index + 1
               if self.selected_index is not None else len(self.zones))
        self.zones.insert(pos, clone)
        self.refresh_zone_list()
        self._select({pos}, pos)

    # ==================================================== properties panel
    def _clear_properties(self):
        for w in self.prop_frame.winfo_children():
            w.destroy()
        self._prop_widgets = []
        tk.Label(self.prop_frame, text="No zone selected.", fg="#666").pack(
            anchor="w", padx=4, pady=4)

    def _refresh_properties_for_selection(self):
        if len(self.selected_indices) > 1:
            self._show_multi_properties()
            return
        z = self._current_zone()
        if z is None:
            self._clear_properties()
            return
        self._build_properties(z)

    def _show_multi_properties(self):
        """Show a summary panel when several zones are selected."""
        for w in self.prop_frame.winfo_children():
            w.destroy()
        self._prop_widgets = []
        tk.Label(self.prop_frame, text="{} zones selected".format(
            len(self.selected_indices)),
            font=("TkDefaultFont", 9, "bold")).pack(anchor="w", padx=4, pady=(4, 2))
        tk.Label(self.prop_frame,
                 text="Use the Move tool to drag them together.\n"
                      "Delete removes all selected zones.",
                 fg="#666", justify="left").pack(anchor="w", padx=4)

    def _build_properties(self, zone):
        for w in self.prop_frame.winfo_children():
            w.destroy()
        f = self.prop_frame

        tk.Label(f, text=zone["type"].capitalize() + " zone",
                 font=("TkDefaultFont", 9, "bold")).pack(anchor="w", padx=4)
        self._prop_label_entry(f, "Label", zone, "label", kind="str")
        self._prop_bounds(f, zone)

        if zone["type"] == "building":
            self._prop_label_entry(f, "building_height [m]", zone,
                                   "building_height", kind="float")
            self._prop_type_combo(f, "building_type", zone, "building_type",
                                  palm_types.BUILDING_TYPES)
        elif zone["type"] == "vegetation":
            self._prop_type_combo(f, "vegetation_type", zone, "vegetation_type",
                                  palm_types.VEGETATION_TYPES)
            self._prop_type_combo(f, "soil_type", zone, "soil_type",
                                  palm_types.SOIL_TYPES)
            self._prop_lad_profile(f, zone)
        elif zone["type"] == "pavement":
            self._prop_type_combo(f, "pavement_type", zone, "pavement_type",
                                  palm_types.PAVEMENT_TYPES)
            self._prop_type_combo(f, "soil_type", zone, "soil_type",
                                  palm_types.SOIL_TYPES)
        elif zone["type"] == "water":
            self._prop_type_combo(f, "water_type", zone, "water_type",
                                  palm_types.WATER_TYPES)
        elif zone["type"] == "terrain":
            self._prop_label_entry(f, "terrain_height [m]", zone,
                                   "terrain_height", kind="float")
        elif zone["type"] == "soil":
            self._prop_type_combo(f, "soil_type", zone, "soil_type",
                                  palm_types.SOIL_TYPES)
            self._prop_soil_temperature(f, zone)
            self._prop_label_entry(f, "soil_moisture [m³/m³]", zone,
                                   "soil_moisture", kind="float")
            self._prop_label_entry(f, "deep_soil_temperature [K]", zone,
                                   "deep_soil_temperature", kind="float")
        elif zone["type"] == "background":
            tk.Label(f, text="Resets these cells to the default background\n"
                             "surface (an eraser).", fg="#666",
                     justify="left").pack(anchor="w", padx=4, pady=2)

    # ---- property widget builders ------------------------------------
    def _prop_bounds(self, parent, zone):
        """Numeric editor for the zone footprint (i0:i1, j0:j1)."""
        box = ttk.LabelFrame(parent, text="Bounds (cells, i1/j1 exclusive)")
        box.pack(fill="x", padx=4, pady=3)
        nx1 = int(self.config_data["domain"]["nx"]) + 1
        ny1 = int(self.config_data["domain"]["ny"]) + 1
        specs = [("i0", 0, nx1 - 1), ("i1", 1, nx1),
                 ("j0", 0, ny1 - 1), ("j1", 1, ny1)]
        vars_ = {}
        for n, (key, lo, hi) in enumerate(specs):
            cell = tk.Frame(box)
            cell.grid(row=n // 2, column=n % 2, sticky="w", padx=3, pady=1)
            tk.Label(cell, text=key, width=3, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(zone[key]))
            sb = tk.Spinbox(cell, from_=lo, to=hi, textvariable=var, width=6)
            sb.pack(side="left")
            vars_[key] = var
            self._prop_widgets.append(sb)

            def commit(*_a, _zone=zone, _vars=vars_):
                self._commit_bounds(_zone, _vars)
            sb.configure(command=commit)
            sb.bind("<FocusOut>", commit)
            sb.bind("<Return>", commit)

    def _commit_bounds(self, zone, vars_):
        """Validate and apply edited numeric bounds; clamp to the domain."""
        nx1 = int(self.config_data["domain"]["nx"]) + 1
        ny1 = int(self.config_data["domain"]["ny"]) + 1
        i0 = self._as_int(vars_["i0"].get(), zone["i0"])
        i1 = self._as_int(vars_["i1"].get(), zone["i1"])
        j0 = self._as_int(vars_["j0"].get(), zone["j0"])
        j1 = self._as_int(vars_["j1"].get(), zone["j1"])
        i0 = max(0, min(nx1 - 1, i0)); i1 = max(i0 + 1, min(nx1, i1))
        j0 = max(0, min(ny1 - 1, j0)); j1 = max(j0 + 1, min(ny1, j1))
        if (i0, i1, j0, j1) == (zone["i0"], zone["i1"], zone["j0"], zone["j1"]):
            return
        self._snapshot()
        zone.update(i0=i0, i1=i1, j0=j0, j1=j1)
        vars_["i0"].set(str(i0)); vars_["i1"].set(str(i1))
        vars_["j0"].set(str(j0)); vars_["j1"].set(str(j1))
        self._after_zone_edit()

    def _prop_label_entry(self, parent, label, zone, key, kind="str"):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=4, pady=2)
        tk.Label(row, text=label, width=20, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(zone[key]))
        ent = ttk.Entry(row, textvariable=var)
        ent.pack(side="left", fill="x", expand=True)

        def commit(*_a):
            if kind == "float":
                new = self._as_float(var.get(), zone[key])
            elif kind == "int":
                new = self._as_int(var.get(), zone[key])
            else:
                new = var.get()
            if new == zone[key]:
                return
            self._snapshot()
            zone[key] = new
            self._after_zone_edit()

        ent.bind("<FocusOut>", commit)
        ent.bind("<Return>", commit)
        self._prop_widgets.append(ent)

    def _prop_type_combo(self, parent, label, zone, key, table):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=4, pady=2)
        tk.Label(row, text=label, width=20, anchor="w").pack(side="left")
        var = tk.StringVar(value=palm_types.option_for(table, zone[key]))
        cb = ttk.Combobox(row, textvariable=var, state="readonly",
                          values=palm_types.options(table))
        cb.pack(side="left", fill="x", expand=True)

        def on_select(*_a):
            new = palm_types.parse_code(var.get())
            if new == zone[key]:
                return
            self._snapshot()
            zone[key] = new
            self._after_zone_edit()

        cb.bind("<<ComboboxSelected>>", on_select)
        self._prop_widgets.append(cb)

    def _prop_lad_profile(self, parent, zone):
        box = ttk.LabelFrame(parent, text="LAD profile (zlad)")
        box.pack(fill="x", padx=4, pady=4)
        for k in range(palm_types.N_ZLAD):
            row = tk.Frame(box)
            row.pack(fill="x", padx=2, pady=1)
            h = self.zlad_heights[k] if k < len(self.zlad_heights) else 0.0
            tk.Label(row, text="zlad[{}]  {:.1f} m".format(k, h),
                     width=16, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(zone["lad"][k]))
            sb = tk.Spinbox(row, from_=0.0, to=20.0, increment=0.05,
                            textvariable=var, width=8)
            sb.pack(side="left")
            self._wire_list_spinbox(sb, var, zone, "lad", k)
        ttk.Button(box, text="Reset to default",
                   command=lambda z=zone: self._reset_lad(z)).pack(
            anchor="e", padx=2, pady=3)

    def _prop_soil_temperature(self, parent, zone):
        box = ttk.LabelFrame(parent, text="soil_temperature [K] (zsoil)")
        box.pack(fill="x", padx=4, pady=4)
        for k in range(palm_types.N_ZSOIL):
            row = tk.Frame(box)
            row.pack(fill="x", padx=2, pady=1)
            d = self.zsoil_depths[k] if k < len(self.zsoil_depths) else 0.0
            tk.Label(row, text="z={:.3f} m".format(d), width=16,
                     anchor="w").pack(side="left")
            var = tk.StringVar(value=str(zone["soil_temperature"][k]))
            sb = tk.Spinbox(row, from_=200.0, to=350.0, increment=0.5,
                            textvariable=var, width=8)
            sb.pack(side="left")
            self._wire_list_spinbox(sb, var, zone, "soil_temperature", k)

    def _wire_list_spinbox(self, spinbox, var, zone, key, index):
        def commit(*_a):
            new = self._as_float(var.get(), zone[key][index])
            if new == zone[key][index]:
                return
            self._snapshot()
            zone[key][index] = new
            self._after_zone_edit(rebuild=False)

        spinbox.configure(command=commit)
        spinbox.bind("<FocusOut>", commit)
        spinbox.bind("<Return>", commit)
        self._prop_widgets.append(spinbox)

    def _reset_lad(self, zone):
        if list(zone["lad"]) == list(palm_types.DEFAULT_LAD_PROFILE):
            return
        self._snapshot()
        zone["lad"] = list(palm_types.DEFAULT_LAD_PROFILE)
        self._build_properties(zone)
        self._after_zone_edit(rebuild=False)

    def _after_zone_edit(self, rebuild=True):
        """Refresh list summary + canvas after a property change.

        Does not rebuild the properties panel, so the widget being edited keeps
        focus; it only re-applies the selection to the rebuilt tree + canvas.
        """
        if rebuild:
            self.refresh_zone_list()
            self._sync_selection_widgets()
        else:
            self.grid_canvas.redraw()

    # ===================================================== YAML load/save
    def load_yaml(self):
        path = filedialog.askopenfilename(
            title="Load configuration YAML",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")])
        if path:
            self._load_config_file(path)

    def _load_config_file(self, path):
        try:
            with open(path, "r") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("YAML error", str(exc))
            return
        # merge over defaults so missing keys keep sane values
        merged = self._default_config()
        for section in ("domain", "output", "global_attributes", "defaults"):
            if isinstance(data.get(section), dict):
                merged[section].update(data[section])
        if "reference_file" in data:
            merged["reference_file"] = data["reference_file"]
        self.config_data = merged
        self._sync_widgets_from_config()
        self._maybe_load_reference(merged.get("reference_file", ""))

    def _maybe_load_reference(self, ref):
        """Load a reference grid if ``ref`` names an existing file (silent on fail)."""
        if not ref or not os.path.exists(ref):
            return
        try:
            grid = writer.read_reference_grid(ref)
        except Exception:  # noqa: BLE001 - reference optional at load time
            return
        self.reference = grid
        self.zlad_heights = [float(v) for v in grid["zu"][0:palm_types.N_ZLAD]]
        self.ref_label.config(
            text="{}\n(nx={}, ny={}, nz={}, dx={}, dy={})".format(
                os.path.basename(ref), grid["nx"], grid["ny"], len(grid["zu"]),
                grid["dx"], grid["dy"]),
            fg="#1b5e20")
        self._domain_warned = False
        self._set_gated_state(True)
        self.grid_canvas.set_domain(grid["nx"], grid["ny"], grid["dx"], grid["dy"])

    def save_yaml(self):
        self._sync_config_from_widgets()
        path = filedialog.asksaveasfilename(
            title="Save configuration YAML", defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w") as fh:
                yaml.safe_dump(self.config_data, fh, sort_keys=False,
                               default_flow_style=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Save error", str(exc))
            return
        messagebox.showinfo("Saved", "Configuration written to:\n{}".format(path))

    # ===================================================== scene load/save
    def save_scene(self):
        """Save config + the painted zones together as a reusable scene file."""
        self._sync_config_from_widgets()
        path = filedialog.asksaveasfilename(
            title="Save scene", defaultextension=".scene.yaml",
            filetypes=[("Scene YAML", "*.yaml *.yml"), ("All files", "*.*")])
        if not path:
            return
        scene = {"config": self.config_data, "zones": self.zones}
        try:
            with open(path, "w") as fh:
                yaml.safe_dump(scene, fh, sort_keys=False, default_flow_style=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Save error", str(exc))
            return
        messagebox.showinfo(
            "Scene saved",
            "Saved {} zone(s) + config to:\n{}".format(len(self.zones), path))

    def load_scene(self):
        """Load a scene file (config + zones) saved by :meth:`save_scene`."""
        path = filedialog.askopenfilename(
            title="Load scene",
            filetypes=[("Scene YAML", "*.yaml *.yml"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r") as fh:
                scene = yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Scene error", str(exc))
            return
        if not isinstance(scene, dict) or "zones" not in scene:
            messagebox.showerror(
                "Scene error", "This file is not a scene (no 'zones' key).")
            return
        merged = self._default_config()
        cfg = scene.get("config", {})
        for section in ("domain", "output", "global_attributes", "defaults"):
            if isinstance(cfg.get(section), dict):
                merged[section].update(cfg[section])
        merged["reference_file"] = cfg.get("reference_file", "")
        self.config_data = merged
        self.zones = list(scene.get("zones", []))
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._sync_widgets_from_config()
        self._maybe_load_reference(merged.get("reference_file", ""))
        self.refresh_zone_list()
        self._select(set(), None)
        messagebox.showinfo(
            "Scene loaded", "Loaded {} zone(s) from:\n{}".format(
                len(self.zones), path))

    # ============================================ default soil-T profile dialog
    def edit_default_soil_profile(self):
        """Open a dialog to edit the 8-layer default soil-temperature profile."""
        df = self.config_data["defaults"]
        profile = list(df.get("soil_temperature", [290.0] * palm_types.N_ZSOIL))
        top = tk.Toplevel(self)
        top.title("Default soil-temperature profile")
        top.transient(self)
        tk.Label(top, text="Default soil_temperature [K] per soil layer",
                 font=("TkDefaultFont", 9, "bold")).pack(padx=8, pady=(8, 4))
        vars_ = []
        for k in range(palm_types.N_ZSOIL):
            row = tk.Frame(top)
            row.pack(fill="x", padx=8, pady=1)
            depth = self.zsoil_depths[k] if k < len(self.zsoil_depths) else 0.0
            tk.Label(row, text="layer {}  (z={:.3f} m)".format(k, depth),
                     width=20, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(profile[k]))
            tk.Spinbox(row, from_=200.0, to=350.0, increment=0.5,
                       textvariable=var, width=8).pack(side="left")
            vars_.append(var)

        def apply_and_close():
            df["soil_temperature"] = [
                self._as_float(v.get(), profile[k]) for k, v in enumerate(vars_)]
            top.destroy()

        btnrow = tk.Frame(top)
        btnrow.pack(fill="x", padx=8, pady=8)
        ttk.Button(btnrow, text="OK", command=apply_and_close).pack(side="right")
        ttk.Button(btnrow, text="Cancel", command=top.destroy).pack(
            side="right", padx=4)

    # ================================================= domain-edit warning
    def _on_domain_edit(self, _event=None):
        """Warn once if the user edits the domain away from the reference grid."""
        if self.reference is None or self._domain_warned:
            return
        ref = self.reference
        changed = (self._as_int(self.var["domain_nx"].get(), ref["nx"]) != ref["nx"]
                   or self._as_int(self.var["domain_ny"].get(), ref["ny"]) != ref["ny"]
                   or abs(self._as_float(self.var["domain_dx"].get(), ref["dx"])
                          - ref["dx"]) > 1e-9
                   or abs(self._as_float(self.var["domain_dy"].get(), ref["dy"])
                          - ref["dy"]) > 1e-9)
        if changed:
            self._domain_warned = True
            messagebox.showwarning(
                "Domain changed",
                "You changed the domain away from the reference file's grid.\n\n"
                "The x/y coordinate arrays and zone footprints assume the "
                "reference dimensions; mismatches can produce an invalid driver.")

    # ======================================================= generation
    def generate_netcdf(self):
        if self.reference is None:
            messagebox.showerror("No reference file",
                                 "Load a PALM 3D output reference file first.")
            return
        self._sync_config_from_widgets()
        default_name = self.config_data["output"]["filename"]
        path = filedialog.asksaveasfilename(
            title="Write PALM static driver", initialfile=default_name,
            filetypes=[("Static driver", "*"), ("All files", "*.*")])
        if not path:
            return
        try:
            report = writer.write_static_driver(
                self.config_data, self.zones,
                self.reference["zu"], self.reference["zw"], path)
            counts = writer.validate_fields(
                writer.build_fields(self.config_data, self.zones,
                                    self.reference["zu"], self.reference["zw"]))
            messagebox.showinfo(
                "Static driver written",
                "Wrote:\n{}\n\n{}".format(report, counts))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            messagebox.showerror("Generation failed", str(exc))


def main():
    """Launch the GUI."""
    app = PalmStaticGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
