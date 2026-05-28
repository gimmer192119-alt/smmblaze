"""Runtime text overrides.

The main handlers file now contains the canonical Russian texts.
This module intentionally keeps a no-op `apply()` so `main.py`
can import it safely without overwriting working messages.
"""


def apply(mod):
    return mod
