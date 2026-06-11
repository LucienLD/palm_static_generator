"""PALM static-driver GUI generator.

Layout
------
* :mod:`palm_static_generator.core`  — GUI-independent logic:
  :mod:`~palm_static_generator.core.writer` (NetCDF writer),
  :mod:`~palm_static_generator.core.palm_types` (type tables) and
  :mod:`~palm_static_generator.core.geo` (lat/lon → UTM).
* :mod:`palm_static_generator.gui`   — the tkinter application
  (:mod:`~palm_static_generator.gui.app`) and the painting canvas widget.
* ``config/``  — the ``config.yaml`` template.
* ``tests/``   — headless smoke tests.

Launch the GUI with ``python -m palm_static_generator``.
"""

__version__ = "1.0.0"
