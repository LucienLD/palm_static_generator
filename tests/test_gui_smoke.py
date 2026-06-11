"""Headless smoke test for the GUI: instantiate, paint zones, generate.

Drives the application without a human by setting state directly and
monkey-patching the file dialogs.  Intended to run under a virtual framebuffer
(Xvfb).  Run with:  python -m palm_static_generator.tests.test_gui_smoke
"""

import os
import tempfile

from tkinter import filedialog, messagebox

from palm_static_generator.core import palm_types, writer
from palm_static_generator.gui.app import PalmStaticGUI
from palm_static_generator.tests.test_writer import make_reference


def main():
    tmp = tempfile.mkdtemp(prefix="palm_smoke_")
    ref = os.path.join(tmp, "INIT_3d.001.nc")
    out = os.path.join(tmp, "smoke_static")
    make_reference(ref)

    app = PalmStaticGUI()
    app.update()

    # simulate "Load reference NetCDF" without a real dialog
    grid = writer.read_reference_grid(ref)
    app.reference = grid
    app.config_data["reference_file"] = ref
    app.zlad_heights = [float(v) for v in grid["zu"][0:palm_types.N_ZLAD]]
    app.var["domain_nx"].set(str(grid["nx"]))
    app.var["domain_ny"].set(str(grid["ny"]))
    app.var["domain_dx"].set(str(grid["dx"]))
    app.var["domain_dy"].set(str(grid["dy"]))
    app._set_gated_state(True)
    app.grid_canvas.set_domain(grid["nx"], grid["ny"])
    app.update()

    # paint a few zones programmatically through the canvas callback
    app.active_tool.set("building")
    app.on_canvas_draw(6, 20, 20, 34)
    app.active_tool.set("vegetation")
    app.on_canvas_draw(6, 26, 6, 12)
    app.active_tool.set("pavement")
    app.on_canvas_draw(28, 34, 6, 18)
    app.active_tool.set("soil")
    app.on_canvas_draw(6, 12, 6, 9)
    app.update()

    assert len(app.zones) == 4, "expected 4 zones, got {}".format(len(app.zones))

    # --- domain recap reflects the loaded reference + painted cells ----
    app._update_domain_recap()
    recap = app.domain_recap.cget("text")
    assert "40 × 40 cells" in recap and "80.0 × 80.0 m" in recap, \
        "domain recap wrong: {!r}".format(recap)
    # building 14×14=196, vegetation 20×6=120, pavement 6×12=72 → 388 painted;
    # soil 6×3=18 counted as an overlay.
    assert "Painted: 388 / 1600" in recap, "painted count wrong: {!r}".format(recap)
    assert "soil overlay: 18" in recap, "soil overlay count wrong: {!r}".format(recap)

    # --- UTM auto-fill of origin_x / origin_y --------------------------
    app.var["ga_origin_lat"].set("48.8534")
    app.var["ga_origin_lon"].set("2.3488")
    app.compute_utm()
    ex = float(app.var["ga_origin_x"].get())
    nyv = float(app.var["ga_origin_y"].get())
    assert abs(ex - 452230.0) < 5.0 and abs(nyv - 5411363.0) < 5.0, \
        "UTM fill wrong: E={} N={}".format(ex, nyv)
    assert "zone 31N" in app.utm_label.cget("text")

    # --- zoom / pan plumbing -------------------------------------------
    app.grid_canvas.zoom_in()
    app.grid_canvas.zoom_in()
    assert app.grid_canvas.zoom > 1.0, "zoom_in did not increase zoom"
    app.grid_canvas.zoom_out()
    app.grid_canvas.zoom_reset()
    assert abs(app.grid_canvas.zoom - 1.0) < 1e-9, "zoom_reset failed"
    app.grid_canvas._pan("x", 3)
    app.grid_canvas._pan("y", -3)
    app.update()

    # exercise the property panel for each selection
    for i in range(len(app.zones)):
        app.selected_index = i
        app._refresh_properties_for_selection()
        app.update()

    # edit a building height through the zone dict + summary refresh
    app.zones[0]["building_height"] = 12.5
    app.refresh_zone_list()

    # list ops
    app.selected_index = 2
    app.move_up()
    app.duplicate_zone()
    app.delete_zone()
    app.update()

    # generate via monkey-patched save dialog
    filedialog.asksaveasfilename = lambda *a, **k: out
    messagebox.showinfo = lambda *a, **k: None
    messagebox.showerror = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("GUI reported an error: {} {}".format(a, k)))
    app.generate_netcdf()

    assert os.path.exists(out), "generate did not write output"
    app.destroy()
    print("GUI SMOKE TEST PASSED — output:", out)


if __name__ == "__main__":
    main()
