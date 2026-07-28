"""Reelmancer launcher.

Run with:

    streamlit run app.py

The interface itself lives in the ``ui`` package; this file only makes the
project importable when it is started from another working directory, then
hands over to the application shell.
"""

from __future__ import annotations

import os
import sys

# Allow `streamlit run /some/path/app.py` from anywhere.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ui.main import configure_page, main  # noqa: E402

# Streamlit requires set_page_config to be the first Streamlit call.
configure_page()

main()
