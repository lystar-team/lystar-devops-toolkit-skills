#!/usr/bin/env python3
"""Compatibility entrypoint with a name that matches patch preparation behavior."""
from __future__ import annotations

from apply_scoped_patch import main


if __name__ == "__main__":
    raise SystemExit(main())
