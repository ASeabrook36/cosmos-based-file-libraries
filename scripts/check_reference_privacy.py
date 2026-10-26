"""Check repository candidates for operator-specific environment references.

Run before publishing live evidence. An optional --deny-file points to a PRIVATE JSON
object outside the repository, whose values are local paths, resource names, endpoints
or identifiers to reject. Neither that file nor matched values are printed or copied.
The default check rejects concrete absolute user-home paths; placeholders remain valid.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

USER_HOME = re.compile(rb"(?:/" + rb"Users/" + rb"|/" + rb"home/" + rb"|[A-Za-z]:\\Users\\)([^/\\\s\"'<>]+)[/\\]")


def findings(data: bytes, private_values: list[str]) -> list[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for match in USER_HOME.finditer(data):
        found.add((data[: match.start()].count(b"\n") + 1, "absolute user-home path"))
    for value in private_values:
        if value and (position := data.find(value.encode())) >= 0:
            found.add((data[:position].count(b"\n") + 1, "private environment reference"))
    return sorted(found)


def check(root: Path, private_values: list[str]) -> list[dict[str, object]]:
    names = (
        subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
        .decode()
        .split("\0")
    )
    problems = []
    for name in names:
        if not name or not (path := root / name).is_file():
            continue
        for line, reason in findings(path.read_bytes(), private_values):
            problems.append({"file": name, "line": line, "reason": reason})
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deny-file", type=Path, help="private JSON object outside the repository; never commit it")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    values: list[str] = []
    if args.deny_file:
        if args.deny_file.resolve().is_relative_to(root):
            parser.error("the private deny file must be outside the repository")
        data = json.loads(args.deny_file.read_text())
        if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
            parser.error("the private deny file must be a JSON object of strings")
        values = list(data.values())
    problems = check(root, values)
    print(json.dumps({"passed": not problems, "findings": problems}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
