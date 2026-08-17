#!/usr/bin/env python3
"""Dev helper: symlink Hippo3D + its Sverchok plugin into Blender's addons folder.

Usage:
    python scripts/link_to_blender.py [blender_version]

If no version is given, the script tries to detect the latest installed
Blender version under ~/.config/blender/ and uses that.
"""

import os
import sys
from pathlib import Path


def find_latest_blender_version():
    """Return the highest x.y version directory under ~/.config/blender."""
    base = Path.home() / ".config" / "blender"
    if not base.exists():
        return None
    versions = []
    for p in base.iterdir():
        if p.is_dir() and p.name.replace(".", "").isdigit():
            try:
                major, minor = p.name.split(".")
                versions.append(((int(major), int(minor)), p))
            except Exception:
                pass
    if not versions:
        return None
    versions.sort(key=lambda x: x[0])
    return versions[-1][1]


def main():
    if len(sys.argv) >= 2:
        version = sys.argv[1]
        addons_dir = Path.home() / ".config" / "blender" / version / "scripts" / "addons"
    else:
        latest = find_latest_blender_version()
        if latest is None:
            print("Could not find any Blender version under ~/.config/blender/")
            sys.exit(1)
        addons_dir = latest / "scripts" / "addons"
        version = latest.name

    if not addons_dir.exists():
        print(f"Addons directory does not exist: {addons_dir}")
        sys.exit(1)

    repo_root = Path(__file__).resolve().parents[1]
    hippo3d_dir = repo_root
    plugin_dir = repo_root / "h3d_sverchok_plugin"

    # Main Hippo3D add-on
    target_main = addons_dir / "Hippo3D"
    if target_main.exists() or target_main.is_symlink():
        target_main.unlink()
    target_main.symlink_to(hippo3d_dir, target_is_directory=True)
    print(f"  {target_main} -> {hippo3d_dir}")

    # Sverchok plugin
    target_plugin = addons_dir / "h3d_sverchok_plugin"
    if target_plugin.exists() or target_plugin.is_symlink():
        target_plugin.unlink()
    target_plugin.symlink_to(plugin_dir, target_is_directory=True)
    print(f"  {target_plugin} -> {plugin_dir}")

    print(f"\nDone! Restart Blender {version} and enable both add-ons in Preferences.")


if __name__ == "__main__":
    main()
