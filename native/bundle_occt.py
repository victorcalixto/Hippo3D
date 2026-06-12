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


def _windows_libs(module: Path):
    """Return list of absolute OCCT .dll paths using dumpbin or heuristic."""
    # Candidate directories for OCCT DLLs
    script_dir = Path(__file__).resolve().parent
    occt_local = script_dir / "third_party" / "occt-8.0.0" / "bin"
    search_dirs = []
    for env_key in ("PATH", "OCCT_ROOT"):
        val = os.environ.get(env_key, "")
        if val:
            for part in val.split(os.pathsep):
                p = Path(part)
                if p.is_dir():
                    search_dirs.append(p)
    if occt_local.is_dir():
        search_dirs.append(occt_local)

    dumpbin = shutil.which("dumpbin")
    dll_names = []

    if dumpbin:
        try:
            out = subprocess.check_output(
                [dumpbin, "/dependents", str(module)], text=True
            )
        except subprocess.CalledProcessError:
            out = ""

        in_deps = False
        for line in out.splitlines():
            if "Image has the following dependencies:" in line:
                in_deps = True
                continue
            if in_deps:
                dll_name = line.strip()
                if not dll_name or dll_name.lower().startswith("summary"):
                    break
                if dll_name.lower().startswith("tk"):
                    dll_names.append(dll_name)

    # Fallback heuristic: if dumpbin failed or found nothing, grab all TK*.dll from search dirs
    if not dll_names:
        for d in search_dirs:
            for dll in d.glob("TK*.dll"):
                dll_names.append(dll.name)
        dll_names = sorted(set(dll_names))

    # Resolve each DLL name to a full path in search_dirs
    libs = []
    for dll_name in dll_names:
        found = False
        for d in search_dirs:
            candidate = d / dll_name
            if candidate.is_file():
                libs.append(candidate)
                found = True
                break
        if not found:
            # Also try OCCT_ROOT subdirectories
            occt_root = os.environ.get("OCCT_ROOT", "")
            if occt_root:
                for guess in (
                    Path(occt_root) / "bin" / dll_name,
                    Path(occt_root) / "win64" / "vc14" / "bin" / dll_name,
                    Path(occt_root) / "win64" / "gcc" / "bin" / dll_name,
                ):
                    if guess.is_file():
                        libs.append(guess)
                        break
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
