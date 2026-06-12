"""Windows DLL diagnostic script for hippo_occ_core loading failures.

Run this in Blender's Python console (or any Python on the machine)
to identify exactly which DLL is failing to load.
"""

import os
import sys
from pathlib import Path
import ctypes
from ctypes import wintypes

def diagnose_windows_dll_loading():
    addon_dir = Path(__file__).resolve().parent
    windows_dir = addon_dir / "native" / "windows-x64"
    
    if not windows_dir.exists():
        print(f"ERROR: {windows_dir} does not exist")
        return
    
    print(f"Checking: {windows_dir}")
    print(f"Files found: {list(windows_dir.iterdir())}")
    print()
    
    # Extend PATH so Windows can find dependent DLLs
    os.environ["PATH"] = str(windows_dir) + os.pathsep + os.environ.get("PATH", "")
    
    # Find the module
    pyd_files = list(windows_dir.glob("hippo_occ_core*.pyd"))
    if not pyd_files:
        print("ERROR: No .pyd file found")
        return
    
    pyd_path = pyd_files[0]
    print(f"Module: {pyd_path}")
    print()
    
    # Method 1: Try ctypes LoadLibrary for detailed error
    print("=== Method 1: ctypes.LoadLibrary ===")
    kernel32 = ctypes.windll.kernel32
    
    hModule = kernel32.LoadLibraryW(str(pyd_path))
    if not hModule:
        err = kernel32.GetLastError()
        print(f"LoadLibraryW failed with error code: {err}")
        
        # Get detailed error message
        buf = ctypes.create_unicode_buffer(256)
        kernel32.FormatMessageW(
            0x00001000,  # FORMAT_MESSAGE_FROM_SYSTEM
            None, err, 0, buf, 256, None
        )
        print(f"Error message: {buf.value}")
        
        # Try loading each dependency individually
        print("\n=== Checking individual OCCT DLLs ===")
        for dll in sorted(windows_dir.glob("TK*.dll")):
            h = kernel32.LoadLibraryW(str(dll))
            if not h:
                dll_err = kernel32.GetLastError()
                if dll_err != 126:  # 126 = ERROR_MOD_NOT_FOUND (expected for some)
                    print(f"  {dll.name}: FAILED (error {dll_err})")
            else:
                kernel32.FreeLibrary(h)
    else:
        print("LoadLibraryW succeeded!")
        kernel32.FreeLibrary(hModule)
    
    # Method 2: Try Python import with full traceback
    print("\n=== Method 2: Python import ===")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("hippo_occ_core", str(pyd_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        print("Import succeeded!")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    diagnose_windows_dll_loading()
