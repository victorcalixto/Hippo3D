import importlib.util
import os
import platform
import sys
from pathlib import Path


def _platform_folder():
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        return "linux-x64", "hippo_occ_core.so"

    if system == "windows":
        return "windows-x64", "hippo_occ_core.pyd"

    if system == "darwin":
        if "arm" in machine or "aarch64" in machine:
            return "macos-arm64", "hippo_occ_core.so"
        return "macos-x64", "hippo_occ_core.so"

    if system == "freebsd":
        return "freebsd-x64", "hippo_occ_core.so"

    if system == "openbsd":
        return "openbsd-x64", "hippo_occ_core.so"

    raise RuntimeError(f"Unsupported platform: {system} {machine}")


def _expected_abi_tag():
    """Return the ABI tag fragment for the running interpreter.

    Examples:
        cpython-313-x86_64-linux-gnu
        cpython-311-x86_64-linux-gnu
        cp313-win_amd64
        cp311-win_amd64
    """
    cache_tag = getattr(sys.implementation, "cache_tag", f"cpython-{sys.version_info.major}{sys.version_info.minor}")
    # sysconfig.get_config_var('EXT_SUFFIX') gives us the platform triplet part
    # (e.g. ".cpython-313-x86_64-linux-gnu.so" or ".cp311-win_amd64.pyd").
    import sysconfig
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ""
    # Remove the leading dot and trailing extension to get the full ABI tag.
    if ext_suffix.startswith("."):
        ext_suffix = ext_suffix[1:]
    # Split on the last dot (the file extension) and keep the ABI portion.
    abi_tag = ext_suffix.rsplit(".", 1)[0]
    if not abi_tag:
        abi_tag = f"{cache_tag}-{platform.machine().lower()}"
    return abi_tag


def _find_module(native_dir: Path, base_name: str, ext: str):
    """Locate the module file matching the running interpreter's ABI."""
    abi_tag = _expected_abi_tag()
    # ABI-tagged candidates: base_name.<abi>.ext
    candidates = sorted(native_dir.glob(f"{base_name}.*{ext}"))
    # Prefer a candidate whose ABI tag matches the running interpreter.
    for candidate in candidates:
        if abi_tag in candidate.name:
            return candidate
    # Fallback to the most specific ABI-tagged build, then to the plain name.
    if candidates:
        return candidates[-1]
    plain = native_dir / f"{base_name}{ext}"
    if plain.exists():
        return plain
    return None


def load_occ_core():
    system = platform.system().lower()
    if system == "windows":
        folder, base_name, ext = "windows-x64", "hippo_occ_core", ".pyd"
    elif system == "darwin":
        machine = platform.machine().lower()
        folder = "macos-arm64" if ("arm" in machine or "aarch64" in machine) else "macos-x64"
        base_name, ext = "hippo_occ_core", ".so"
    elif system == "freebsd":
        folder, base_name, ext = "freebsd-x64", "hippo_occ_core", ".so"
    elif system == "openbsd":
        folder, base_name, ext = "openbsd-x64", "hippo_occ_core", ".so"
    else:
        folder, base_name, ext = "linux-x64", "hippo_occ_core", ".so"

    native_dir = Path(__file__).resolve().parents[1] / "native" / folder
    module_path = _find_module(native_dir, base_name, ext)

    if not module_path:
        raise ImportError(f"Native OCC module not found in {native_dir}")

    # On Windows, Python 3.8+ ignores PATH for extension-module DLL resolution
    # and instead uses the process DLL search path. We must add the native
    # folder with os.add_dll_directory() before the .pyd is loaded. PATH is also
    # kept for transitive dependencies loaded by those DLLs.
    # On Unix, modifying LD_LIBRARY_PATH inside the running process is too late
    # for the dynamic linker; the real fix is $ORIGIN RPATH/RUNPATH baked into
    # the module and bundled libraries at build/packaging time. We still set
    # it here for child processes.
    if system == "windows":
        native_str = str(native_dir)
        _original_path = os.environ.get("PATH", "")
        if native_str not in _original_path.split(os.pathsep):
            os.environ["PATH"] = native_str + os.pathsep + _original_path
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(native_str)
    else:
        _original = os.environ.get("LD_LIBRARY_PATH", "")
        native_str = str(native_dir)
        if native_str not in _original.split(os.pathsep):
            os.environ["LD_LIBRARY_PATH"] = native_str + os.pathsep + _original

    spec = importlib.util.spec_from_file_location("hippo_occ_core", str(module_path))
    if not spec or not spec.loader:
        raise ImportError(f"Could not create module spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["hippo_occ_core"] = module
    spec.loader.exec_module(module)
    return module
