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


# System libraries that should NOT be bundled — every distro has them.
_SYSTEM_LIBS = {
    "libc.so", "libm.so", "libdl.so", "libpthread.so",
    "librt.so", "libresolv.so", "libnsl.so", "libcrypt.so",
    "libutil.so", "libgcc_s.so", "libstdc++.so", "ld-linux",
    "linux-vdso", "linux-gate",
}


# Known non-OCCT third-party libraries that may be pulled in by OCCT and should
# be bundled so the add-on works on a clean machine.
_THIRDPARTY_SONAME_PREFIXES = {
    "libtbb", "libfreetype", "libfreeimage", "libjemalloc",
    "libavcodec", "libavformat", "libavutil", "libswscale",
    "libpng", "libjpeg", "libtiff", "libz", "libzlib",
}


def _is_system_lib(name: str):
    """Return True if the library is a standard system C/C++ runtime."""
    low = name.lower()
    for prefix in _SYSTEM_LIBS:
        if prefix in low:
            return True
    return False


def _is_occt_or_bundled_thirdparty(name: str):
    """Return True for OCCT TK* libs and known runtime dependencies."""
    low = name.lower()
    if low.startswith("libtk"):
        return True
    for prefix in _THIRDPARTY_SONAME_PREFIXES:
        if low.startswith(prefix):
            return True
    return False


def _linux_libs(module: Path):
    """Return list of absolute OCCT and 3rdparty .so paths using ldd."""
    system = platform.system().lower()
    try:
        out = subprocess.check_output(["ldd", str(module)], text=True)
    except FileNotFoundError:
        print("Error: 'ldd' not found. Cannot discover linked libraries.")
        return []

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
    # On BSDs the system OCCT package lives under /usr/local/lib.
    if system in ("freebsd", "openbsd", "netbsd"):
        for d in ("/usr/local/lib", "/usr/X11R6/lib"):
            p = Path(d)
            if p.is_dir() and p not in search_dirs:
                search_dirs.append(p)

    def _collect_ldd(binary: Path):
        """Return dict {soname: absolute_path_or_None} for a binary."""
        try:
            raw = subprocess.check_output(["ldd", str(binary)], text=True, errors="replace")
        except subprocess.CalledProcessError:
            return {}
        deps = {}
        openbsd_header_seen = False
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Linux format:   libfoo.so => /path/libfoo.so (0x...)
            if "=>" in line:
                lib_name, rest = line.split("=>", 1)
                lib_name = lib_name.strip()
                parts = rest.strip().split()
                if parts and parts[0].startswith("/"):
                    p = Path(parts[0])
                    if p.exists():
                        deps[lib_name] = p
                    else:
                        deps[lib_name] = None
                else:
                    deps[lib_name] = None
                continue
            # OpenBSD format:
            #   Start            End              Type  Open Ref GrpRef Name
            #   00000...         00000...         rlib  0    1    0      /usr/local/lib/libTKernel.so.3.0
            if "Start" in line and "End" in line and "Type" in line and "Name" in line:
                openbsd_header_seen = True
                continue
            if openbsd_header_seen:
                parts = line.split()
                if len(parts) >= 6 and parts[-1].startswith("/"):
                    path = Path(parts[-1])
                    if path.exists():
                        # Soname is the library filename (e.g. libTKernel.so.3.0)
                        deps[path.name] = path
                continue
            # Generic BSD format:     libfoo.so.0 /path/libfoo.so.0
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith("/"):
                lib_name = parts[0]
                p = Path(parts[1])
                if p.exists():
                    deps[lib_name] = p
                else:
                    deps[lib_name] = None
        return deps

    def _resolve_not_found(lib_name):
        for d in search_dirs:
            candidate = d / lib_name
            if candidate.exists():
                return candidate
            # BSD libs are versioned, e.g. libTKernel.so.83.0; try globbing.
            if system in ("freebsd", "openbsd", "netbsd"):
                matches = sorted(d.glob(f"{lib_name}*"))
                if matches:
                    return matches[0]
        return None

    # Breadth-first traversal of dependency tree
    visited = set()
    libs = []
    queue = [module]

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        deps = _collect_ldd(current)
        for lib_name, lib_path in deps.items():
            if _is_system_lib(lib_name):
                continue
            if not _is_occt_or_bundled_thirdparty(lib_name):
                continue
            if lib_path is None:
                lib_path = _resolve_not_found(lib_name)
            if lib_path is None or not lib_path.exists():
                print(f"Warning: could not locate {lib_name}")
                continue
            real = lib_path.resolve()
            if real not in visited:
                libs.append(real)
                queue.append(real)

    # Remove duplicates while preserving order
    seen = set()
    deduped = []
    for p in libs:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def _macos_libs(module: Path):
    """Return list of absolute .dylib paths to bundle on macOS.

    We recursively collect every non-system shared library dependency of the
    native module using otool -L. OCCT libraries are usually referenced as
    @rpath/libTK<...>.8.0.dylib, while 3rdparty deps (tbb, freetype, ...) may
    live under the OCCT install tree, Homebrew prefixes, or system library
    paths. Anything that is not a core Apple framework/System library is copied
    into the add-on so it loads on a clean machine.
    """
    try:
        out = subprocess.check_output(["otool", "-L", str(module)], text=True)
    except FileNotFoundError:
        print("Error: 'otool' not found. Cannot discover linked libraries.")
        return []

    script_dir = Path(__file__).resolve().parent
    search_dirs = []

    occt_root_env = os.environ.get("OCCT_ROOT", "")
    if occt_root_env:
        search_dirs.append(Path(occt_root_env) / "lib")
        search_dirs.append(Path(occt_root_env))

    # Local per-architecture OCCT builds
    machine = platform.machine().lower()
    arch = "arm64" if ("arm" in machine or "aarch64" in machine) else "x86_64"
    search_dirs.append(script_dir / "third_party" / f"occt-8.0.0-{arch}" / "lib")
    search_dirs.append(script_dir / "third_party" / "occt-8.0.0" / "lib")

    # Homebrew / common third-party library locations
    for prefix in (
        "/opt/homebrew/opt/opencascade",
        "/usr/local/opt/opencascade",
        "/opt/homebrew/lib",
        "/usr/local/lib",
        "/usr/lib",
    ):
        search_dirs.append(Path(prefix))

    # System dylibs that are part of macOS and must not be bundled.
    _MACOS_SYSTEM_PREFIXES = (
        "/usr/lib/libSystem",
        "/usr/lib/libc++",
        "/usr/lib/libobjc",
        "/usr/lib/libresolv",
        "/System/Library/Frameworks/",
        "/usr/lib/libpmenergy",
        "/usr/lib/libpthread",
        "/usr/lib/libdl",
        "/usr/lib/libm.dylib",
    )

    def _is_system(path: str) -> bool:
        low = path.lower()
        if "python" in low and low.endswith(".dylib"):
            return True
        return any(path.startswith(p) for p in _MACOS_SYSTEM_PREFIXES) or path.startswith("/usr/lib/libSystem")

    def _resolve(name: str):
        for d in search_dirs:
            candidate = d / name
            if candidate.is_file():
                return candidate.resolve()
        return None

    def _collect(binary: Path):
        try:
            raw = subprocess.check_output(["otool", "-L", str(binary)], text=True)
        except subprocess.CalledProcessError:
            return {}
        deps = {}
        for line in raw.splitlines()[1:]:  # skip self-reference
            parts = line.strip().split()
            if not parts:
                continue
            ref = parts[0]
            deps[ref] = None
        return deps

    libs = []
    seen = set()
    queue = [module]

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)

        for ref in _collect(current):
            if _is_system(ref):
                continue

            if ref.startswith("@rpath/"):
                name = ref[len("@rpath/"):]
                real = _resolve(name)
            elif ref.startswith("@"):
                continue
            else:
                p = Path(ref)
                real = p.resolve() if p.is_file() else _resolve(p.name)

            if real is None:
                print(f"Warning: could not locate macOS dependency {ref}")
                continue
            real = real.resolve()
            if real not in seen:
                libs.append(real)
                queue.append(real)

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


def _windows_vcredist_dir():
    """Return the directory containing the MSVC runtime redistributable DLLs."""
    # GitHub Actions' msvc-dev-cmd sets this directly.
    redist = os.environ.get("VCToolsRedistDir", "")
    if redist:
        p = Path(redist) / "x64" / "Microsoft.VC143.CRT"
        if p.is_dir():
            return p
        # Older env layout
        p = Path(redist)
        if p.is_dir():
            return p

    # Fallback: derive from VS installation directory.
    vs_install = os.environ.get("VSINSTALLDIR", "")
    if vs_install:
        p = Path(vs_install) / "VC" / "Redist" / "MSVC"
        if p.is_dir():
            # Pick the newest versioned subdir
            versions = sorted(
                (d for d in p.iterdir() if d.is_dir()),
                key=lambda d: d.name,
                reverse=True,
            )
            for v in versions:
                crt = v / "x64" / "Microsoft.VC143.CRT"
                if crt.is_dir():
                    return crt

    return None


def _windows_search_dirs(occt_root: Path | None):
    """Build a list of directories from which Windows DLLs should be bundled.

    We bundle every non-system DLL found in these directories. PATH is NOT
    included because it would pull in unrelated system tools and runtimes.
    """
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
                search_dirs.append(tp)
                # Recursively collect every directory that contains DLLs
                for sub in tp.rglob("*"):
                    if sub.is_dir() and any(sub.glob("*.dll")):
                        search_dirs.append(sub)
                break

    # MSVC runtime redistributable DLLs (vcruntime140.dll, msvcp140.dll, etc.)
    vcredist = _windows_vcredist_dir()
    if vcredist:
        search_dirs.append(vcredist)

    # Optional user-supplied extra directory.
    extra = os.environ.get("OCCT_DLL_PATH", "")
    if extra:
        for part in extra.split(os.pathsep):
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


# Microsoft Visual C++ runtime DLLs that must ship with the module so it loads
# on machines that do not have the redistributable installed. The 2015-2022
# runtimes are ABI-compatible and share these names.
_VCRuntime_DLLS = {
    "vcruntime140.dll", "vcruntime140_1.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "vcruntime140_clr0400.dll", "vcruntime140_threads_synch.dll",
    "msvcp140_codecvt_ids.dll",
    "concrt140.dll", "vcamp140.dll", "vccorlib140.dll",
}


# Windows API / system DLLs that are part of the OS and must never be bundled.
# Bundling them causes version conflicts and can break loading on clean machines.
_SYSTEM_DLLS = {
    "advapi32.dll", "authz.dll", "bcrypt.dll", "bcryptprimitives.dll",
    "cabinet.dll", "cfgmgr32.dll", "combase.dll", "comctl32.dll",
    "comdlg32.dll", "credui.dll", "crypt32.dll", "cryptbase.dll",
    "d2d1.dll", "d3d11.dll", "d3d12.dll", "d3d9.dll", "dbghelp.dll",
    "dhcpcsvc.dll", "dnsapi.dll", "dsparse.dll", "dwmapi.dll", "dxgi.dll",
    "esent.dll", "gdi32.dll", "gdiplus.dll", "glu32.dll", "gpapi.dll",
    "imm32.dll", "iphlpapi.dll", "kernel32.dll", "kernelbase.dll",
    "ksuser.dll", "msasn1.dll", "msctf.dll", "mswsock.dll", "ncrypt.dll",
    "netapi32.dll", "normaliz.dll", "nsi.dll", "ntdll.dll", "ntmarta.dll",
    "ole32.dll", "oleacc.dll", "oleaut32.dll", "opengl32.dll", "pdh.dll",
    "powrprof.dll", "profapi.dll", "psapi.dll", "rpcrt4.dll", "rsaenh.dll",
    "sechost.dll", "secur32.dll", "setupapi.dll", "shell32.dll",
    "shlwapi.dll", "srpapi.dll", "sspicli.dll", "user32.dll", "userenv.dll",
    "usp10.dll", "uxtheme.dll", "version.dll", "win32u.dll",
    "windows.storage.dll", "winmm.dll", "wintrust.dll", "ws2_32.dll",
    "wtsapi32.dll", "xinput1_3.dll", "xinput1_4.dll", "xinput9_1_0.dll",
}


def _should_bundle_dll(name: str):
    """Return True for any non-system DLL that might be an OCCT dependency.

    On Windows we cannot rely solely on static dependency analysis (dumpbin may
    miss transitive deps or not be on PATH). We bundle every DLL found in the
    OCCT/3rdparty trees except known Windows system DLLs and the Python runtime
    DLLs, which are copied separately.
    """
    low = name.lower()
    if not low.endswith(".dll"):
        return False
    # Python runtime DLLs are copied explicitly by the build script.
    if low.startswith("python") and low.endswith(".dll"):
        return False
    if low in _SYSTEM_DLLS:
        return False
    return True


def _windows_resolve(name: str, search_dirs):
    """Resolve a DLL name to a full path inside search_dirs."""
    for d in search_dirs:
        candidate = d / name
        if candidate.is_file():
            return candidate
    return None


def _windows_libs(module: Path):
    """Return list of absolute OCCT/3rdparty .dll paths to bundle.

    Strategy: copy every non-system DLL found in the OCCT runtime directory and
    in the 3rdparty-vc14-64 tree. Static dependency analysis via dumpbin is used
    only to warn about unresolved dependencies so we can fix the bundle list if
    a library is genuinely missing from the source trees.
    """
    occt_root = _discover_occt_root()
    search_dirs = _windows_search_dirs(occt_root)

    if not search_dirs:
        print("Warning: no OCCT/3rdparty search directories found.")
        return []

    print("OCCT/3rdparty search directories:")
    for d in search_dirs:
        print(f"  {d}")

    # Collect every non-system DLL in the search trees.
    found_names = set()
    libs = []
    for d in search_dirs:
        for dll in d.rglob("*.dll"):
            if not dll.is_file():
                continue
            low = dll.name.lower()
            if low in found_names:
                continue
            if not _should_bundle_dll(dll.name):
                continue
            found_names.add(low)
            libs.append(dll.resolve())

    # Diagnostic pass: list direct dependents of the module and warn about any
    # that are not present in the collected bundle.
    direct_deps = _windows_dependencies(module)
    if direct_deps:
        print("Direct dependents of the native module:")
        for dep in direct_deps:
            present = "(bundled)" if dep.lower() in found_names else "(MISSING)"
            print(f"  {dep} {present}")

    return libs


def _set_origin_rpath(path: Path):
    """Set RUNPATH/RPATH to $ORIGIN so a shared library finds its neighbors."""
    system = platform.system().lower()
    if system not in ("linux", "freebsd", "openbsd"):
        return
    # Prefer patchelf, fall back to chrpath.
    if shutil.which("patchelf"):
        try:
            subprocess.run(
                ["patchelf", "--set-rpath", "$ORIGIN", str(path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"Warning: patchelf failed for {path}: {e.stderr.decode().strip()}")
    elif shutil.which("chrpath"):
        try:
            subprocess.run(
                ["chrpath", "-r", "$ORIGIN", str(path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"Warning: chrpath failed for {path}: {e.stderr.decode().strip()}")


def _copy_libs(libs, dest: Path):
    """Copy libraries into dest, resolving symlinks on Unix.

    On Linux/BSD the dynamic linker resolves a library by its SONAME, not the
    real filename.  The OCCT packages install e.g. libTKernel.so.8.0.0 with
    SONAME libTKernel.so.8.0, so we also create the SONAME symlink next to the
    real file so the loader can find it at runtime.

    We also set each real library's RPATH/RUNPATH to $ORIGIN so the bundled
    libraries can resolve each other without relying on LD_LIBRARY_PATH.
    """
    copied = []
    dest.mkdir(parents=True, exist_ok=True)
    for lib in libs:
        if not lib.exists():
            continue
        # Resolve symlink so we bundle the real file
        real = lib.resolve()
        out = dest / real.name
        if not out.exists():
            shutil.copy2(str(real), str(out))
            # Ensure the copy is writable so patchelf/chrpath can modify it.
            mode = out.stat().st_mode
            if not (mode & 0o200):
                out.chmod(mode | 0o200)
            _set_origin_rpath(out)
            copied.append(out)

        # Create the SONAME symlink on ELF platforms.
        if platform.system().lower() in ("linux", "freebsd", "openbsd"):
            try:
                soname = subprocess.check_output(
                    ["readelf", "-d", str(real)], text=True
                )
            except (FileNotFoundError, subprocess.CalledProcessError):
                soname = ""
            for line in soname.splitlines():
                if "SONAME" in line and "[" in line and "]" in line:
                    name = line.split("[", 1)[1].split("]", 1)[0]
                    link = dest / name
                    if not link.exists() and name != real.name:
                        link.symlink_to(real.name)
                        copied.append(link)
                    break
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
        # Emit diagnostics so CI logs show why bundling was skipped.
        try:
            raw_ldd = subprocess.check_output(["ldd", str(module)], text=True, errors="replace")
            print("Raw ldd output:")
            for line in raw_ldd.splitlines():
                print("  ", line)
        except Exception as e:
            print(f"Could not run ldd diagnostics: {e}")
        sys.exit(0)

    copied = _copy_libs(libs, out_dir)
    print(f"Copied {len(copied)} libraries to {out_dir}")
    for c in copied:
        print(f"  {c.name}")


if __name__ == "__main__":
    main()
