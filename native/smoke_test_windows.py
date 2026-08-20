#!/usr/bin/env python3
"""
Windows smoke-test helper for Hippo3D native module.
Run from the GitHub Actions workflow with the native module directory as argv[1].
"""
import glob
import importlib.util
import os
import shutil
import subprocess
import sys

import ctypes
from ctypes import wintypes


def _find_dumpbin():
    dumpbin = shutil.which("dumpbin")
    if dumpbin:
        return dumpbin
    for pf_env in ("ProgramFiles(x86)", "ProgramFiles"):
        pf = os.environ.get(pf_env)
        if not pf:
            continue
        base = os.path.join(pf, "Microsoft Visual Studio", "2022")
        for edition in ("Enterprise", "Professional", "Community", "BuildTools"):
            candidate = os.path.join(base, edition, "VC", "Tools", "MSVC")
            if not os.path.isdir(candidate):
                continue
            for root, _dirs, files in os.walk(candidate):
                if "dumpbin.exe" in files:
                    return os.path.join(root, "dumpbin.exe")
    return None


def _dependents(path):
    dumpbin = _find_dumpbin()
    if not dumpbin:
        return []
    try:
        out = subprocess.check_output(
            [dumpbin, "/dependents", path], text=True, errors="replace"
        )
    except Exception:
        return []
    names = []
    in_deps = False
    for line in out.splitlines():
        if "Image has the following dependencies:" in line:
            in_deps = True
            continue
        if in_deps:
            name = line.strip()
            if not name or name.lower().startswith("summary"):
                break
            names.append(name)
    return names


# LoadLibraryEx flags: search the DLL's own directory and the directories added
# via os.add_dll_directory(). This matches how Python imports extension modules.
LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
LOAD_LIBRARY_SEARCH_USER_DIRS = 0x00000400
LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000
LOAD_FLAGS = (
    LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
    | LOAD_LIBRARY_SEARCH_USER_DIRS
    | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS
)


def _load_library_ex(path):
    """Use LoadLibraryExW with the same flags Python uses for extension modules."""
    kernel32 = ctypes.windll.kernel32
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    h = kernel32.LoadLibraryExW(path, None, LOAD_FLAGS)
    if not h:
            err = kernel32.GetLastError()
            raise OSError(err, f"LoadLibraryExW failed for {path}")
    return h


def _format_message(code):
    buf = ctypes.create_unicode_buffer(512)
    fmt = ctypes.windll.kernel32.FormatMessageW
    fmt(
        0x00001000,  # FORMAT_MESSAGE_FROM_SYSTEM
        None,
        code,
        0,
        buf,
        len(buf),
        None,
    )
    return buf.value.strip() or f"error {code}"


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

files = sorted(os.listdir(native_dir))
print(f"Files in native dir ({len(files)}):")
for f in files:
    print(" ", f)

# Show direct dependents of the .pyd and flag any that are missing.
deps = _dependents(pyd)
print("Direct dependents of pyd:")
missing = []
for dep in deps:
    present = os.path.exists(os.path.join(native_dir, dep))
    status = "OK" if present else "MISSING"
    print(f"  {dep} ({status})")
    if not present:
        missing.append(dep)

if missing:
    print("WARNING: the following direct dependencies are not in the bundle:")
    for m in missing:
        print("  ", m)

# Try loading each bundled DLL individually to catch broken/missing transitives.
failed_dlls = []
for dll in sorted(glob.glob(os.path.join(native_dir, "*.dll"))):
    try:
        _load_library_ex(dll)
    except Exception as e:
        # Some DLLs are optional (test/draw/visualization) and may depend on
        # system libraries we do not ship; only report them, do not fail here.
        failed_dlls.append((os.path.basename(dll), str(e)))
    else:
        print(f"OK {os.path.basename(dll)}")

if failed_dlls:
    print(f"Note: {len(failed_dlls)} bundled DLLs failed standalone LoadLibraryEx:")
    for name, err in failed_dlls[:20]:
        print(f"  {name}: {err}")
    if len(failed_dlls) > 20:
        print(f"  ... and {len(failed_dlls) - 20} more")

# Finally try importing the .pyd. This is the only test that matters.
print("Trying importlib import...")
sys.path.insert(0, native_dir)
spec = importlib.util.spec_from_file_location("hippo_occ_core", pyd)
mod = importlib.util.module_from_spec(spec)
sys.modules["hippo_occ_core"] = mod
spec.loader.exec_module(mod)
print("SUCCESS:", mod.make_box_mesh(1, 1, 1).keys())
