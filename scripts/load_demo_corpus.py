"""Load a demo corpus through the index API.

    python scripts/load_demo_corpus.py <corpus-dir>

``<corpus-dir>/groups/<name>/*`` become one shared library per folder (name from the folder).
Needs DEV_API_KEY, and INDEX_API_URL if the API is not on :8000. Re-running skips libraries that
already exist and files already uploaded to them. Personal notes are uploaded from the demo's
Agents page, to the personal agent's own library.
"""

from __future__ import annotations

import mimetypes
import sys
from pathlib import Path

from fli_demo.client import IndexClient


def main(corpus: Path) -> int:
    client = IndexClient.from_env()
    existing = {lib["name"]: lib["id"] for lib in client.list_libraries("shared")}
    for folder in sorted(p for p in (corpus / "groups").iterdir() if p.is_dir()):
        name = folder.name.replace("-", " ").capitalize()
        library_id = existing.get(name) or client.create_library("shared", name, "")["id"]
        have = {d["fileName"] for d in client.list_documents(library_id)}
        for path in sorted(folder.iterdir()):
            if path.name in have:
                continue
            client.upload(library_id, path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0])
            print(f"{name}: uploaded {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
