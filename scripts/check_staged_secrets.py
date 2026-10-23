"""Reject staged additions containing obvious provider credential prefixes."""

from __future__ import annotations

import re
import subprocess
import sys


KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-(?:or-v1-)?|dahl_|github_pat_|ghp_)"
    r"[A-Za-z0-9_-]{12,}"
)


def main() -> int:
    result = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color"],
        capture_output=True,
        text=True,
        check=True,
    )
    path = "unknown"
    line_number = 0
    findings: list[tuple[str, int]] = []

    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            line_number = int(match.group(1)) if match else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if KEY_PATTERN.search(line[1:]):
                findings.append((path, line_number))
            line_number += 1

    for finding_path, finding_line in findings:
        print(
            f"Potential provider credential in staged content: "
            f"{finding_path}:{finding_line}",
            file=sys.stderr,
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
