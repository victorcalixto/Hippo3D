#!/usr/bin/env python3
"""
bundle_occt.py  —  Copy OpenCASCADE (OCCT) shared libraries next to the native module.

Usage:
    python bundle_occt.py [--platform PLATFORM] [--build-dir BUILD] [--out-dir OUT]

This makes the Hippo3D add-on self-contained so end-users do not need to
install OCCT system-wide. The script locates the OCCT libraries that the
built module links against and copies them into the platform output folder.

Supported platforms:
    linux-x64, macos-arm64, macos-x64, freebsd-x64, openbsd-x64, windows-x64

Requirements:
    - Linux/FreeBSD/OpenBSD: ldd
    - macOS:           otool -L
    - Windows:         dumpbin /dependents (via VS Developer Prompt)
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _detect_platform():
    """Return the canonical platform folder name for the current machine."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux":
        return "linux-x64"
    if system == "darwin":
        return "macos-arm64" if ("arm" in machine or "aarch64" in machine) else "macos-x64"
    if system == "freebsd":
        return "freebsd-x64"
    if system == "openbsd":
        return "openbsd-x64"
    if system == "windows":
        return "windows-x64"
    raise RuntimeError(f"Unsupported platform: {system} {machine}")


def _find_module(build_dir: Path):
    """Locate the built hippo_occ_core module inside build_dir."""
    candidates = list(build_dir.rglob("hippo_occ_core*.so")) + \
                 list(build_dir.rglob("hippo_occ_core*.pyd")) + \
                 list(build_dir.rglob("hippo_occ_core*.dylib"))
    if not candidates:
        raise FileNotFoundError(
            f"Built module not found under {build_dir}. Please build first."
        )
    return candidates[0]


def _linux_libs(module: Path):
    """Return list of absolute OCCT .so paths using ldd."""
    try:
        out = subprocess.check_output(["ldd", str(module)], text=True)
    except FileNotFoundError:
        print("Error: 'ldd' not found. Cannot discover linked libraries.")
        return []
    libs = []
    # Candidate directories for "not found" OCCT libraries
    script_dir = Path(__file__).resolve().parent
    occt_local = script_dir / "third_party" / "occt-8.0.0" / "lib"
    search_dirs = []
    for env_key in ("LD_LIBRARY_PATH", "OCCT_ROOT"):
        val = os.environ.get(env_key, "")
        if val:
            for part in val.split(os.pathsep):
                p = Path(part)
                if p.is_dir():
                    search_dirs.append(p)
    if occt_local.is_dir():
        search_dirs.append(occt_local)
    for line in out.splitlines():
        # e.g.  libTKernel.so.7 => /usr/lib/x86_64-linux-gnu/libTKernel.so.7 (0x...)
        # e.g.  libTKernel.so.8.0 => not found
        if "=>" not in line:
            continue
        lib_name, rest = line.split("=>", 1)
        lib_name = lib_name.strip()
        if not lib_name.lower().startswith("libtk"):
            continue
        parts = rest.strip().split()
        if parts and parts[0].startswith("/"):
            # Resolved absolute path
            p = Path(parts[0])
            if p.exists():
                libs.append(p)
        else:
            # "not found" — search candidate directories
            for d in search_dirs:
                candidate = d / lib_name
                if candidate.exists():
                    libs.append(candidate)
                    break
    return libs


def _macos_libs(module: Path):
    """Return list of absolute OCCT .dylib paths using otool -L."""
    try:
        out = subprocess.check_output(["otool", "-L", str(module)], text=True)
    except FileNotFoundError:
        print("Error: 'otool' not found. Cannot discover linked libraries.")
        return []
    libs = []
    for line in out.splitlines()[1:]:  # skip first line (self reference)
        parts = line.strip().split()
        if not parts:
            continue
        path = parts[0]
        if path.startswith("@"):
            continue  # skip @rpath, @loader_path, @executable_path
        # Absolute path — check if it smells like OCCT
        p = Path(path)
        if any(k in p.name for k in ("libTK", "libTKernel")):
            libs.append(p)
    return libs


def _discover_occt_root():
    """Discover the OCCT installation root from common Windows locations."""
    script_dir = Path(__file__).resolve().parent
    candidates = []

    # Prefer explicit OCCT_ROOT
    occt_root_env = os.environ.get("OCCT_ROOT", "")
    if occt_root_env:
        candidates.append(Path(occt_root_env))

    # Auto-detect C:\OCCT\opencascade-8.0.0-vc14-64
    candidates.append(Path("C:/OCCT/opencascade-8.0.0-vc14-64"))

    # Local third_party fallback
    candidates.append(script_dir / "third_party" / "occt-8.0.0")

    for root in candidates:
        if root.is_dir() and any(
            (root / sub).is_dir() for sub in ("inc", "include", "win64", "bin")
        ):
            return root.resolve()
    return None


def _windows_search_dirs(occt_root: Path | None):
    """Build a list of directories likely to contain OCCT and 3rdparty DLLs."""
    search_dirs = []

    # OCCT runtime libraries
    if occt_root:
        for sub in (
            occt_root / "win64" / "vc14" / "bin",
            occt_root / "win64" / "vc15" / "bin",
            occt_root / "bin",
            occt_root,
        ):
            if sub.is_dir():
                search_dirs.append(sub)

        # Sibling 3rdparty-vc14-64 directories (OCCT Windows installer layout)
        for parent in (occt_root.parent, occt_root.parent.parent):
            tp = parent / "3rdparty-vc14-64"
            if tp.is_dir():
                # Recursively collect every directory that contains DLLs
                for sub in tp.rglob("*"):
                    if sub.is_dir() and any(sub.glob("*.dll")):
                        search_dirs.append(sub)
                break

    # Allow user to add extra dirs via PATH / OCCT_ROOT
    for env_key in ("OCCT_DLL_PATH", "PATH"):
        val = os.environ.get(env_key, "")
        if val:
            for part in val.split(os.pathsep):
                p = Path(part)
                if p.is_dir():
                    search_dirs.append(p)

    return search_dirs


def _find_dumpbin():
    """Return the path to dumpbin.exe, searching common VS locations."""
    dumpbin = shutil.which("dumpbin")
    if dumpbin:
        return dumpbin

    # Common Visual Studio / BuildTools layout patterns
    program_files = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    ]
    for pf in program_files:
        for edition in ("BuildTools", "Community", "Professional", "Enterprise"):
            base = pf / "Microsoft Visual Studio" / "2022" / edition / "VC" / "Tools" / "MSVC"
            if not base.is_dir():
                continue
            for sub in base.rglob("Hostx64/x64/dumpbin.exe"):
                return str(sub)
            for sub in base.rglob("x64/dumpbin.exe"):
                return str(sub)
    return None


def _windows_dependencies(dll_path: Path):
    """Return the list of direct dependent DLL names for a Windows binary."""
    dumpbin = _find_dumpbin()
    if not dumpbin:
        return []
    try:
        out = subprocess.check_output(
            [dumpbin, "/dependents", str(dll_path)], text=True
        )
    except subprocess.CalledProcessError:
        return []

    names = []
    in_deps = False
    for line in out.splitlines():
        if "Image has the following dependencies:" in line:
            in_deps = True
            continue
        if in_deps:
            dll_name = line.strip()
            if not dll_name or dll_name.lower().startswith("summary"):
                break
            names.append(dll_name)
    return names


# Names that are not system/runtime DLLs and should be bundled when discovered.
_THIRDPARTY_DLL_PATTERNS = {
    "tbb12.dll", "tbb.dll", "jemalloc.dll", "openvr_api.dll",
    "freetype.dll", "freetype6.dll", "freeimage.dll",
    "avcodec-57.dll", "avformat-57.dll", "avutil-55.dll", "swscale-4.dll",
    "zlib.dll", "zlib1.dll", "winmm.dll",
}


def _should_bundle_dll(name: str):
    """Return True for OCCT TK* DLLs and known 3rdparty runtime DLLs."""
    low = name.lower()
    if low.startswith("tk"):
        return True
    if low in _THIRDPARTY_DLL_PATTERNS:
        return True
    return False


def _windows_resolve(name: str, search_dirs):
    """Resolve a DLL name to a full path inside search_dirs."""
    for d in search_dirs:
        candidate = d / name
        if candidate.is_file():
            return candidate
    return None


def _windows_libs(module: Path):
    """Return list of absolute OCCT/3rdparty .dll paths using dumpbin plus recursion."""
    occt_root = _discover_occt_root()
    search_dirs = _windows_search_dirs(occt_root)

    # Seed with direct dependents of the built module
    queue = list(_windows_dependencies(module))
    found_names = set()
    libs = []

    while queue:
        name = queue.pop(0)
        low = name.lower()
        if low in found_names:
            continue
        found_names.add(low)

        if not _should_bundle_dll(name):
            continue

        resolved = _windows_resolve(name, search_dirs)
        if resolved:
            libs.append(resolved)
            # Recurse into this DLL's own dependents
            for dep in _windows_dependencies(resolved):
                if dep.lower() not in found_names:
                    queue.append(dep)
        else:
            print(f"Warning: could not locate {name}")

    # Fallback heuristic: if dumpbin is not available or found nothing useful,
    # collect all candidate DLLs from the discovered OCCT and 3rdparty trees.
    if not libs:
        fallback_names = set()
        for d in search_dirs:
            for dll in d.glob("TK*.dll"):
                if dll.name.lower() not in found_names and dll.name.lower() not in fallback_names:
                    fallback_names.add(dll.name.lower())
                    libs.append(dll)
            for dll in d.glob("*.dll"):
                if _should_bundle_dll(dll.name) and dll.name.lower() not in found_names and dll.name.lower() not in fallback_names:
                    fallback_names.add(dll.name.lower())
                    libs.append(dll)

    return libs


def _copy_libs(libs, dest: Path):
    """Copy libraries into dest, resolving symlinks on Unix."""
    copied = []
    dest.mkdir(parents=True, exist_ok=True)
    for lib in libs:
        if not lib.exists():
            continue
        # Resolve symlink so we bundle the real file
        real = lib.resolve()
        out = dest / real.name
        if out.exists():
            continue
        shutil.copy2(str(real), str(out))
        copied.append(out)
    return copied


def main():
    parser = argparse.ArgumentParser(
        description="Bundle OCCT shared libraries for Hippo3D"
    )
    parser.add_argument(
        "--platform",
        default=_detect_platform(),
        help="Target platform folder (default: auto-detected)",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=Path(__file__).with_name("build"),
        help="CMake build directory (default: native/build)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: native/<platform>)",
    )
    args = parser.parse_args()

    build_dir = args.build_dir.resolve()
    out_dir = (args.out_dir or Path(__file__).with_name(args.platform)).resolve()

    print(f"Platform : {args.platform}")
    print(f"Build dir: {build_dir}")
    print(f"Out dir  : {out_dir}")

    module = _find_module(build_dir)
    print(f"Module   : {module}")

    system = platform.system().lower()
    if system == "linux" or system == "freebsd" or system == "openbsd":
        libs = _linux_libs(module)
    elif system == "darwin":
        libs = _macos_libs(module)
    elif system == "windows":
        libs = _windows_libs(module)
    else:
        raise RuntimeError(f"Unsupported system for bundling: {system}")

    if not libs:
        print("No OCCT libraries detected to bundle.")
        sys.exit(0)

    copied = _copy_libs(libs, out_dir)
    print(f"Copied {len(copied)} libraries to {out_dir}")
    for c in copied:
        print(f"  {c.name}")


if __name__ == "__main__":
    main()
