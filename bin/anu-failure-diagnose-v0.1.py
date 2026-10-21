#!/usr/bin/env python3
"""ANU Failure Diagnose v0.1 (V0.9.13) — classify test failures and write DIAGNOSIS.md."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))

FAILURE_CLASSES = frozenset({
    "assertion",
    "import",
    "timeout",
    "flake",
    "syntax",
    "permission",
    "unknown",
})

DEFAULT_BUDGET = 3


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def classify_failure(stdout: str = "", stderr: str = "", *, reason: str = "") -> dict:
    blob = f"{reason}\n{stdout}\n{stderr}"
    low = blob.lower()
    if "timeout" in low or reason == "timeout":
        return {"class": "timeout", "confidence": 0.9}
    if "permissionerror" in low or "permission denied" in low:
        return {"class": "permission", "confidence": 0.85}
    if "syntaxerror" in low or "indentationerror" in low:
        return {"class": "syntax", "confidence": 0.9}
    if "modulenotfounderror" in low or "importerror" in low or "no module named" in low:
        return {"class": "import", "confidence": 0.9}
    if "assertionerror" in low or "assert " in low or "failed" in low:
        # Flake heuristic: intermittent markers
        if "flaky" in low or "intermittent" in low:
            return {"class": "flake", "confidence": 0.6}
        return {"class": "assertion", "confidence": 0.8}
    return {"class": "unknown", "confidence": 0.3}


def extract_failing_tests(text: str) -> list:
    names = []
    for pat in (
        r"FAIL:\s+(\S+)",
        r"ERROR:\s+(\S+)",
        r"FAILED\s+(\S+)",
        r"(test_\w+)",
    ):
        for m in re.finditer(pat, text or ""):
            names.append(m.group(1))
    # unique preserve order
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:40]


def extract_repair_paths(text: str, *, root: Path | None = None) -> list:
    paths = []
    for m in re.finditer(r'File "([^"]+\.py)", line (\d+)', text or ""):
        paths.append({"file": m.group(1), "line": int(m.group(2))})
    for m in re.finditer(r"([\w./\\-]+\.py):(\d+):", text or ""):
        paths.append({"file": m.group(1), "line": int(m.group(2))})
    # Prefer paths under root when possible
    uniq = []
    seen = set()
    for item in paths:
        key = (item["file"], item["line"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq[:30]


def diagnose_matrix_report(report: dict) -> dict:
    """Build diagnosis from test-matrix run_matrix output."""
    failures = []
    for r in report.get("results") or []:
        if r.get("status") != "FAIL":
            continue
        text = f"{r.get('stdout_tail') or ''}\n{r.get('stderr_tail') or ''}"
        clf = classify_failure(r.get("stdout_tail") or "", r.get("stderr_tail") or "", reason=r.get("reason") or "")
        failures.append({
            "layer": r.get("layer"),
            "class": clf["class"],
            "confidence": clf["confidence"],
            "failing_tests": extract_failing_tests(text),
            "repair_targets": extract_repair_paths(text),
            "reason": r.get("reason"),
            "stdout_tail": (r.get("stdout_tail") or "")[-1500:],
            "stderr_tail": (r.get("stderr_tail") or "")[-1500:],
        })
    primary = failures[0]["class"] if failures else None
    return {
        "ok": True,
        "at": now_iso(),
        "primary_class": primary,
        "failures": failures,
        "summary": f"{len(failures)} failed layer(s); primary={primary}",
    }


def write_diagnosis(root, diagnosis: dict) -> str | None:
    root = Path(root)
    dest = root / ".anu"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        md = dest / "DIAGNOSIS.md"
        lines = [
            "# DIAGNOSIS",
            "",
            f"- at: {diagnosis.get('at')}",
            f"- primary_class: {diagnosis.get('primary_class')}",
            f"- summary: {diagnosis.get('summary')}",
            "",
        ]
        for f in diagnosis.get("failures") or []:
            lines.append(f"## Layer `{f.get('layer')}` — `{f.get('class')}`")
            lines.append("")
            lines.append(f"- reason: {f.get('reason')}")
            tests = f.get("failing_tests") or []
            if tests:
                lines.append("- failing_tests:")
                for t in tests:
                    lines.append(f"  - {t}")
            targets = f.get("repair_targets") or []
            if targets:
                lines.append("- repair_targets:")
                for t in targets:
                    lines.append(f"  - {t.get('file')}:{t.get('line')}")
            lines.append("")
            lines.append("```")
            lines.append((f.get("stderr_tail") or f.get("stdout_tail") or "")[:2000])
            lines.append("```")
            lines.append("")
        md.write_text("\n".join(lines), encoding="utf-8")
        (dest / "DIAGNOSIS.json").write_text(
            json.dumps(diagnosis, indent=2), encoding="utf-8"
        )
        return str(md)
    except OSError:
        return None


def read_diagnosis(root) -> dict | None:
    path = Path(root) / ".anu" / "DIAGNOSIS.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def budget_exhausted(attempts_by_class: dict, failure_class: str, *, budget: int = DEFAULT_BUDGET) -> bool:
    return int(attempts_by_class.get(failure_class) or 0) >= int(budget)


def main(argv=None):
    p = argparse.ArgumentParser(description="ANU Failure Diagnose")
    p.add_argument("--root", required=True)
    p.add_argument("--matrix-json", help="Path to MATRIX_RESULTS.json")
    args = p.parse_args(argv)
    root = Path(args.root)
    report = {}
    src = Path(args.matrix_json) if args.matrix_json else (root / ".anu" / "MATRIX_RESULTS.json")
    if src.is_file():
        report = json.loads(src.read_text(encoding="utf-8"))
    diagnosis = diagnose_matrix_report(report)
    path = write_diagnosis(root, diagnosis)
    print(json.dumps({"diagnosis": diagnosis, "path": path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
