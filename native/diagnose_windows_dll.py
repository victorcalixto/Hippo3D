"""Deep Windows DLL diagnostic for hippo_occ_core.

Run in Blender's Python console. This checks:
1. Python version / ABI tag match
2. Exported symbols (PyInit_hippo_occ_core must exist)
3. VC++ runtime DLLs present
4. Direct ExtensionFileLoader import with full traceback
5. Dependency Walker-style missing DLL scan
"""

import importlib.machinery
import importlib.util
import os
import sys
import traceback
from pathlib import Path

def deep_diagnose():
    addon_dir = Path(__file__).resolve().parent.parent
    windows_dir = addon_dir / "native" / "windows-x64"
    pyd_candidates = sorted(windows_dir.glob("hippo_occ_core*.pyd"))

    print("=" * 60)
    print("Python:", sys.version)
    print("Executable:", sys.executable)
    print("Tag:", sys.implementation.cache_tag if hasattr(sys.implementation, 'cache_tag') else "N/A")
    print()

    if not pyd_candidates:
        print("ERROR: No .pyd found in", windows_dir)
        return

    pyd_path = pyd_candidates[-1]
    print("Module file:", pyd_path)
    print()

    # 1. Check suffix recognition
    print("=== 1. Suffix Check ===")
    suffixes = importlib.machinery.EXTENSION_SUFFIXES
    matched = [s for s in suffixes if str(pyd_path).endswith(s)]
    print("EXTENSION_SUFFIXES:", suffixes)
    print("Matched suffix:", matched if matched else "NONE — ABI mismatch!")
    print()

    # 2. Check exported symbols using ctypes
    print("=== 2. Exported Symbols ===")
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        hMod = kernel32.LoadLibraryW(str(pyd_path))
        if not hMod:
            print("LoadLibraryW FAILED")
        else:
            # Try to get PyInit_hippo_occ_core address
            init_name = b"PyInit_hippo_occ_core"
            addr = kernel32.GetProcAddress(hMod, init_name)
            print(f"PyInit_hippo_occ_core @ 0x{addr:x}" if addr else "PyInit_hippo_occ_core NOT FOUND")

            # Also check for decorated name (rare, but possible)
            addr2 = kernel32.GetProcAddress(hMod, b"PyInit_hippo_occ_core\x00")
            if addr2 and addr2 != addr:
                print(f"Alternative init @ 0x{addr2:x}")

            kernel32.FreeLibrary(hMod)
    except Exception as e:
        print("Symbol check error:", e)
    print()

    # 3. VC++ Runtime check
    print("=== 3. VC++ Runtime DLLs ===")
    system32 = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32"
    for dll in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll"):
        exists = (system32 / dll).exists()
        print(f"  {dll}: {'OK' if exists else 'MISSING'}")
    print()

    # 4. Direct import attempt with maximum detail
    print("=== 4. Direct Python Import ===")
    os.environ["PATH"] = str(windows_dir) + os.pathsep + os.environ.get("PATH", "")
    try:
        loader = importlib.machinery.ExtensionFileLoader("hippo_occ_core", str(pyd_path))
        print("Loader created:", loader)

        spec = importlib.util.spec_from_file_location("hippo_occ_core", str(pyd_path), loader=loader)
        print("Spec:", spec)

        if spec is None:
            print("FAIL: spec_from_file_location returned None")
            print("This means Python does not recognize this file as a valid extension module.")
            print("Common causes:")
            print("  - Wrong Python version (e.g. cp311 .pyd loaded in Python 3.10)")
            print("  - Wrong architecture (32-bit Python loading 64-bit .pyd)")
            print("  - Missing PyInit_ exported symbol")
            return

        mod = importlib.util.module_from_spec(spec)
        print("Module object:", mod)
        spec.loader.exec_module(mod)
        print("SUCCESS! Module loaded.")
        print("make_box_mesh:", mod.make_box_mesh)
    except Exception:
        traceback.print_exc()
    print()

    # 5. Dependency check via brute-force LoadLibrary of each DLL
    print("=== 5. Per-DLL Load Check ===")
    dlls = sorted(windows_dir.glob("*.dll"))
    failed_dlls = []
    for dll in dlls:
        h = kernel32.LoadLibraryW(str(dll))
        if not h:
            err = kernel32.GetLastError()
            # 126 = not found (missing dependency), 1114 = init failed
            if err not in (126, 127):
                failed_dlls.append((dll.name, err))
        else:
            kernel32.FreeLibrary(h)

    if failed_dlls:
        print(f"FAILED DLLs ({len(failed_dlls)}):")
        for name, err in failed_dlls:
            print(f"  {name}: error {err}")
    else:
        print("All DLLs loaded successfully.")
    print()

if __name__ == "__main__":
    deep_diagnose()
