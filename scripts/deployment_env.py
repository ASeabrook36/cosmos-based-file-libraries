"""Print a deployment's outputs as shell ``export`` lines, named as in spec §10.

    eval "$(python scripts/deployment_env.py --resource-group <rg>)"

Reads ``az deployment group show``; needs ``az login``. Outputs hold endpoints and names,
no keys (local auth is disabled everywhere it can be).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys


def exports(outputs: dict) -> list[str]:
    """Scalar outputs as exports. The CLI mangles output names (``COSMOS_ENDPOINT`` comes back as
    ``cosmoS_ENDPOINT``), so names are upper-cased back to the spec §10 form."""
    lines = []
    for name, entry in sorted(outputs.items(), key=lambda item: item[0].upper()):
        value = entry.get("value")
        if isinstance(value, str | int) and not isinstance(value, bool):
            lines.append(f"export {name.upper()}={shlex.quote(str(value))}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--resource-group", "-g", required=True)
    parser.add_argument("--name", "-n", default="main", help="deployment name (default: main)")
    args = parser.parse_args()
    result = subprocess.run(
        ["az", "deployment", "group", "show", "-g", args.resource_group, "-n", args.name,
         "--query", "properties.outputs", "-o", "json"],
        capture_output=True, text=True,
    )  # fmt: skip
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    lines = exports(json.loads(result.stdout) or {})
    if not any(line.startswith("export COSMOS_ENDPOINT=") for line in lines):
        print(f"deployment {args.name!r} in {args.resource_group} has no COSMOS_ENDPOINT output", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
