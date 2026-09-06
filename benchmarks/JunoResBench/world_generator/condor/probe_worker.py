#!/usr/bin/env python3
import sys
print("python:", sys.version.split()[0], sys.executable)
for mod in ("numpy", "matplotlib"):
    try:
        m = __import__(mod)
        print(mod, getattr(m, "__version__", "?"), getattr(m, "__file__", "?"), getattr(m, "__path__", ""))
    except Exception as e:
        print(mod, "IMPORT FAIL:", type(e).__name__, e)
import matplotlib
print("has use:", hasattr(matplotlib, "use"))
print("sys.path:", [p for p in sys.path if p])
