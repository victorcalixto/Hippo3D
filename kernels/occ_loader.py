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


def _find_module(native_dir: Path, base_name: str, ext: str):
    """Locate the module file, preferring ABI-tagged builds."""
    # 1. Try ABI-tagged build first (e.g. hippo_occ_core.cpython-311-x86_64-linux-gnu.so)
    # On Windows, ABI-tagged names look like hippo_occ_core.cp311-win_amd64.pyd
    candidates = sorted(native_dir.glob(f"{base_name}.*{ext}"))
    if candidates:
        return candidates[-1]  # newest / longest name (most specific)
    # 2. Fallback to plain name
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

    sys.path.insert(0, str(native_dir))

    import hippo_occ_core

    return hippo_occ_core
