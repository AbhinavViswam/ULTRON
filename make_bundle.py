"""Builds a shareable Ultron zip.

    python make_bundle.py            writes dist/Ultron-share.zip
    python make_bundle.py --out X    writes to a chosen path

The bundle carries source only. It deliberately excludes every file that
holds personal data or credentials, and refuses to finish if any of them
somehow ends up inside — shipping this is meant to be safe by construction,
not by remembering to check.

The recipient runs setup.bat, which builds their own virtual environment,
then supplies their own API key on Ultron's first-run settings screen.
"""

import fnmatch
import os
import re
import sys
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "dist", "Ultron-share.zip")

# Everything the recipient needs, named explicitly. An allowlist rather than
# a filter, so a new private file appearing in the project cannot silently
# join the next bundle.
INCLUDE_FILES = [
    "gui.py",
    "main.py",
    "install_shortcuts.py",
    "setup.bat",
    "requirements.txt",
    "settings.default.json",
    "README.md",
    "SETUP.md",
]

INCLUDE_TREES = [
    ("ultron", ("*.py",)),
    ("resources", ("*.ico",)),
]

# Names that must never appear in a bundle, checked again after writing.
FORBIDDEN_NAMES = {
    "keys.json", "credentials.json", "token.json", "usage.json",
    "settings.json", "ultron.db", ".env",
}
FORBIDDEN_DIRS = {"data", "venv", "screenshots", "__pycache__", ".git"}

# Rough shapes of real credentials, as a second line of defence.
SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30}"),
    re.compile(r"ya29\.[0-9A-Za-z_\-]{20}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r'"client_secret"\s*:\s*"[^"]{8,}"'),
]


def collect():
    """Returns [(absolute path, name inside the zip)] for everything to ship."""
    entries = []

    for name in INCLUDE_FILES:
        path = os.path.join(PROJECT_ROOT, name)
        if os.path.exists(path):
            entries.append((path, name))
        else:
            print(f"  [skip] {name} (not found)")

    for tree, patterns in INCLUDE_TREES:
        root_dir = os.path.join(PROJECT_ROOT, tree)
        for current, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in FORBIDDEN_DIRS]
            for filename in files:
                if not any(fnmatch.fnmatch(filename, p) for p in patterns):
                    continue
                if filename in FORBIDDEN_NAMES:
                    continue
                path = os.path.join(current, filename)
                entries.append((path, os.path.relpath(path, PROJECT_ROOT)))

    return sorted(entries, key=lambda e: e[1])


def audit(entries):
    """Fails loudly rather than shipping anything private."""
    problems = []
    for path, arcname in entries:
        parts = arcname.replace("\\", "/").split("/")
        if os.path.basename(arcname) in FORBIDDEN_NAMES:
            problems.append(f"forbidden file: {arcname}")
        if any(part in FORBIDDEN_DIRS for part in parts[:-1]):
            problems.append(f"forbidden directory: {arcname}")

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                problems.append(f"possible secret in {arcname} ({pattern.pattern})")
    return problems


def main():
    out = DEFAULT_OUT
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    print("Collecting files...")
    entries = collect()

    print(f"Auditing {len(entries)} files for anything private...")
    problems = audit(entries)
    if problems:
        print("\n[ABORTED] The bundle would have contained private data:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in entries:
            archive.write(path, os.path.join("Ultron", arcname))

    # Re-open and check what actually landed inside, not what we intended.
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    leaked = [
        n for n in names
        if os.path.basename(n) in FORBIDDEN_NAMES
        or any(part in FORBIDDEN_DIRS for part in n.split("/")[:-1])
    ]
    if leaked:
        os.remove(out)
        print("\n[ABORTED] Private files reached the archive; it has been deleted:")
        for name in leaked:
            print(f"  - {name}")
        return 1

    size = os.path.getsize(out)
    print(f"\nWrote {out}")
    print(f"  {len(names)} files, {size / 1024:.0f} KB")
    print("\nExcluded by design: keys.json, credentials.json, token.json,")
    print("usage.json, settings.json, data/ (database, browser profile), venv/.")
    print("\nSend the zip. The recipient unzips it and runs setup.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
