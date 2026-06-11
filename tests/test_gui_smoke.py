"""Pytest GUI smoke test: instantiate, paint every tool, exercise new features.

Skipped automatically when no X display is available.  Under a headless node
run it with a virtual framebuffer::

    Xvfb :99 -screen 0 1280x800x24 &
    DISPLAY=:99 pytest palm_static_generator/tests/test_gui_smoke.py
"""

import os
import sys

import pytest

from palm_static_generator.core import palm_types, writer
from palm_static_generator.tests.test_writer import make_reference

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"), reason="no X display available")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    try:
        from palm_static_generator.gui import app as appmod
    except Exception as exc:  # pragma: no cover - tk not built
        pytest.skip("tkinter unavailable: {}".format(exc))

    # silence dialogs
    for name in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(appmod.messagebox, name, lambda *a, **k: None)

    ref = str(tmp_path / "INIT_3d.001.nc")
    make_reference(ref)
    monkeypatch.setattr(appmod.filedialog, "askopenfilename", lambda *a, **k: ref)

    application = appmod.PalmStaticGUI()
    application.update()
    application.load_reference()      # uses the patched askopenfilename
    application.update()
    application._ref_path = ref
    application._appmod = appmod
    yield application
    application.destroy()


def _paint(app, tool, i0, i1, j0, j1):
    app.active_tool.set(tool)
    app.on_canvas_draw(i0, i1, j0, j1)


def test_paint_all_tools_and_generate(app, tmp_path, monkeypatch):
    assert app.reference is not None and app.reference["nx"] == 39

    _paint(app, "building", 6, 20, 20, 34)
    _paint(app, "vegetation", 6, 26, 6, 12)
    _paint(app, "pavement", 28, 34, 6, 18)
    _paint(app, "water", 28, 34, 26, 34)
    _paint(app, "terrain", 0, 40, 0, 40)
    _paint(app, "soil", 6, 12, 6, 9)
    _paint(app, "background", 7, 9, 22, 24)   # eraser punches a hole
    app.update()
    assert len(app.zones) == 7

    # recap shows painted/erased/overlay counts
    recap = app.domain_recap.cget("text")
    assert "40 × 40 cells" in recap and "soil overlay:" in recap and "terrain:" in recap

    # generate via patched save dialog
    out = str(tmp_path / "smoke_static")
    monkeypatch.setattr(app._appmod.filedialog, "asksaveasfilename",
                        lambda *a, **k: out)
    app.generate_netcdf()
    assert os.path.exists(out)


def test_undo_redo(app):
    n0 = len(app.zones)
    _paint(app, "building", 1, 5, 1, 5)
    assert len(app.zones) == n0 + 1
    app.undo()
    assert len(app.zones) == n0
    app.redo()
    assert len(app.zones) == n0 + 1


def test_numeric_bounds_edit(app):
    _paint(app, "pavement", 2, 6, 2, 6)
    zone = app.zones[-1]
    app.selected_index = len(app.zones) - 1
    app._refresh_properties_for_selection()
    # simulate editing the bounds spinboxes
    import tkinter as tk
    vars_ = {k: tk.StringVar(value=str(v)) for k, v in
             (("i0", 3), ("i1", 9), ("j0", 1), ("j1", 7))}
    app._commit_bounds(zone, vars_)
    assert (zone["i0"], zone["i1"], zone["j0"], zone["j1"]) == (3, 9, 1, 7)


def test_scene_round_trip(app, tmp_path, monkeypatch):
    _paint(app, "building", 6, 12, 6, 12)
    _paint(app, "water", 20, 28, 20, 28)
    n = len(app.zones)
    scene = str(tmp_path / "case.scene.yaml")
    monkeypatch.setattr(app._appmod.filedialog, "asksaveasfilename",
                        lambda *a, **k: scene)
    app.save_scene()
    assert os.path.exists(scene)

    app.zones = []
    app.refresh_zone_list()
    monkeypatch.setattr(app._appmod.filedialog, "askopenfilename",
                        lambda *a, **k: scene)
    app.load_scene()
    assert len(app.zones) == n


def test_move_zone(app):
    _paint(app, "pavement", 4, 8, 4, 8)
    idx = len(app.zones) - 1
    app.selected_index = idx
    app.active_tool.set("move")
    gc = app.grid_canvas
    gc._begin_edit(5, 5)          # grab inside the zone
    gc._apply_edit(7, 7)          # drag +2, +2 cells
    gc._editing = None
    app.on_zone_edit_commit()     # mimic mouse release
    z = app.zones[idx]
    assert (z["i0"], z["i1"], z["j0"], z["j1"]) == (6, 10, 6, 10)
    app.undo()
    z = app.zones[idx]
    assert (z["i0"], z["i1"], z["j0"], z["j1"]) == (4, 8, 4, 8)


def test_reshape_zone(app):
    _paint(app, "pavement", 4, 8, 4, 8)
    idx = len(app.zones) - 1
    app.selected_index = idx
    app.active_tool.set("reshape")
    gc = app.grid_canvas
    gc._begin_edit(7, 5)          # grab the right edge (cell i1-1 = 7)
    gc._apply_edit(9, 5)          # drag it to include cell 9 -> i1 = 10
    gc._editing = None
    app.on_zone_edit_commit()
    z = app.zones[idx]
    assert z["i0"] == 4 and z["i1"] == 10 and z["j0"] == 4 and z["j1"] == 8


def test_clipboard_copy_cut_paste_duplicate(app):
    _paint(app, "pavement", 4, 8, 4, 8)
    app.selected_index = len(app.zones) - 1
    n = len(app.zones)

    app.copy_zone()
    app.paste_zone()
    assert len(app.zones) == n + 1
    pasted = app.zones[app.selected_index]
    assert (pasted["i0"], pasted["i1"], pasted["j0"], pasted["j1"]) == (5, 9, 5, 9)
    assert "copy" in pasted["label"]

    app.cut_zone()                       # removes the pasted zone, keeps a copy
    assert len(app.zones) == n
    app.paste_zone()                     # pastes the cut zone back
    assert len(app.zones) == n + 1
    app.undo()                           # undo the paste
    assert len(app.zones) == n

    app.selected_index = n - 1
    app.duplicate_zone()                 # Ctrl+D path
    assert len(app.zones) == n + 1
    assert app._clip(lambda: None) == "break"


def test_select_tool_and_group_move(app):
    _paint(app, "pavement", 2, 6, 2, 6)       # idx 0
    _paint(app, "building", 10, 14, 10, 14)   # idx 1
    _paint(app, "water", 20, 24, 20, 24)      # idx 2
    gc = app.grid_canvas

    # marquee select via the canvas Select path (covers z0 and z1)
    app.active_tool.set("select")
    gc._select_additive = False
    gc._finish_select((0, 0), (15, 15), 0, 16, 0, 16)
    assert app.selected_indices == {0, 1}

    # select z0 + z2, then group-move them together with the Move tool
    app.on_canvas_select({0, 2}, 0)
    assert app.selected_indices == {0, 2}
    app.active_tool.set("move")
    gc._begin_edit(3, 3)                        # grab inside z0 (it is selected)
    gc._apply_edit(5, 5)                        # drag +2, +2
    gc._editing = None
    app.on_zone_edit_commit()
    assert (app.zones[0]["i0"], app.zones[0]["j0"]) == (4, 4)
    assert (app.zones[2]["i0"], app.zones[2]["j0"]) == (22, 22)
    assert app.zones[1]["i0"] == 10            # unselected zone unchanged
    assert app.selected_indices == {0, 2}      # selection preserved

    app.undo()                                 # one step restores both
    assert (app.zones[0]["i0"], app.zones[0]["j0"]) == (2, 2)
    assert (app.zones[2]["i0"], app.zones[2]["j0"]) == (20, 20)


def test_delete_removes_all_selected(app):
    _paint(app, "pavement", 2, 6, 2, 6)
    _paint(app, "pavement", 10, 14, 10, 14)
    _paint(app, "pavement", 20, 24, 20, 24)
    app.on_canvas_select({0, 2}, 2)
    app.delete_zone()
    assert len(app.zones) == 1                 # only the middle zone remains


def test_utm_fill(app):
    app.var["ga_origin_lat"].set("48.8534")
    app.var["ga_origin_lon"].set("2.3488")
    app.compute_utm()
    assert abs(float(app.var["ga_origin_x"].get()) - 452230.0) < 5.0
    assert "zone 31N" in app.utm_label.cget("text")


if __name__ == "__main__":
    sys.exit(pytest.main([os.path.abspath(__file__), "-q"]))
