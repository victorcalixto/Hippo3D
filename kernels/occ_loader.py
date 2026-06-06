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

    raise RuntimeError(f"Unsupported platform: {system} {machine}")


def load_occ_core():
    folder, filename = _platform_folder()
    native_dir = Path(__file__).resolve().parents[1] / "native" / folder
    module_path = native_dir / filename

    if not module_path.exists():
        raise ImportError(f"Native OCC module not found: {module_path}")

    sys.path.insert(0, str(native_dir))

    import hippo_occ_core

    return hippo_occ_core
