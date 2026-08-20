#!/usr/bin/env python3
"""
Windows smoke-test helper for Hippo3D native module.
Run from the GitHub Actions workflow with the native module directory as argv[1].
"""
import sys
import os
import ctypes
import glob
import importlib.util

native_dir = os.path.abspath(sys.argv[1])
pyd_candidates = sorted(glob.glob(os.path.join(native_dir, "hippo_occ_core*.pyd")))
if not pyd_candidates:
    raise SystemExit("No hippo_occ_core*.pyd found in " + native_dir)
pyd = pyd_candidates[-1]

print("Native dir:", native_dir)
print("PYD:", pyd)
print("Python:", sys.executable)
print("cwd:", os.getcwd())

if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(native_dir)
    print("Added dll directory")

print("Files in native dir:")
for f in sorted(os.listdir(native_dir)):
    print(" ", f)

# Try loading each DLL individually to find the missing one
for dll in sorted(glob.glob(os.path.join(native_dir, "*.dll"))):
    try:
        ctypes.WinDLL(dll)
    except Exception as e:
        print(f"FAILED to load {os.path.basename(dll)}: {e}")
    else:
        print(f"OK {os.path.basename(dll)}")

# Try loading the pyd with ctypes for a clearer error
print("Trying ctypes.WinDLL on pyd...")
try:
    ctypes.WinDLL(pyd)
    print("ctypes pyd OK")
except Exception as e:
    print(f"ctypes pyd FAILED: {e}")

# Finally try the importlib route
print("Trying importlib import...")
sys.path.insert(0, native_dir)
spec = importlib.util.spec_from_file_location("hippo_occ_core", pyd)
mod = importlib.util.module_from_spec(spec)
sys.modules["hippo_occ_core"] = mod
spec.loader.exec_module(mod)
print("SUCCESS:", mod.make_box_mesh(1, 1, 1).keys())
