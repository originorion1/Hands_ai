"""Copy the reviewed Command Center assets into Vercel's static output."""

from pathlib import Path
from shutil import copyfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/orion/presentation/static"
OUTPUT = ROOT / "public"
ASSETS = (
    "index.html",
    "2036.css",
    "motion.css",
    "app.js",
    "report-link.js",
)


def main() -> None:
    if OUTPUT.is_symlink():
        raise RuntimeError("static output cannot be a symbolic link")
    if OUTPUT.exists() and {path.name for path in OUTPUT.iterdir()} - set(ASSETS):
        raise RuntimeError("static output contains an unexpected asset")
    OUTPUT.mkdir(exist_ok=True)
    for name in ASSETS:
        source = SOURCE / name
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"Command Center asset unavailable: {name}")
        if (OUTPUT / name).is_symlink():
            raise RuntimeError(f"static output asset cannot be a symbolic link: {name}")
        copyfile(source, OUTPUT / name)


if __name__ == "__main__":
    main()
