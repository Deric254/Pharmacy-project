"""
Converts one square PNG logo into a real multi-resolution .ico for the
Electron installer/shortcuts/taskbar -- so branding a build for a
specific business only ever requires one plain image file, not a
separate icon-editing tool.

Usage:
    python -m scripts.make_icon path/to/logo.png

Writes electron/build/icon.ico, which electron-builder picks up
automatically (its own default convention -- no config change needed).
"""

import sys
from pathlib import Path

from PIL import Image

# Small sizes render crisp in the taskbar; the large one renders crisp
# as a Start Menu tile. A single-resolution .ico looks fine in one
# spot and pixelated in the other -- this is what actually avoids that.
ICON_SIZES = [(16, 16), (32, 32), (48, 48), (256, 256)]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_PATH = REPO_ROOT / "electron" / "build" / "icon.ico"


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.make_icon path/to/logo.png")
        raise SystemExit(1)

    source_path = Path(sys.argv[1])
    if not source_path.exists():
        print(f"[ERROR] {source_path} does not exist.")
        raise SystemExit(1)

    image: Image.Image = Image.open(source_path)

    width, height = image.size
    if width != height:
        print(
            f"[WARNING] {source_path.name} is {width}x{height}, not square. "
            "It will be stretched to fit -- a square source image looks better."
        )

    if image.mode != "RGBA":
        image = image.convert("RGBA")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT_PATH, format="ICO", sizes=ICON_SIZES)

    print(f"Done. Wrote {OUTPUT_PATH}")
    print("build-installer.bat will pick this up automatically on the next build.")


if __name__ == "__main__":
    main()
