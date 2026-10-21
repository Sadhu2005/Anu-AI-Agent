#!/usr/bin/env python3
"""ANU Test Matrix v0.1 (V0.9.13) — detect and run layered tests before push.

Layers (ordered): unit → integration → functional → e2e → regression.
Missing layers are SKIPPED (not a pass). Failures block push and feed diagnose.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

LAYERS = ("unit", "integration", "functional", "e2e", "regression")
DEFAULT_TIMEOUTS = {
    "unit": 120,
    "integration": 180,
    "functional": 240,
    "e2e": 600,
    "regression": 600,
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _redact(text: str) -> str:
    if not text:
        return ""
    out = text
    for pat in (
        r"ghp_[A-Za-z0-9]{20,}",
        r"sk-[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
    ):
        out = re.sub(pat, "[REDACTED]", out)
    return out


def detect_matrix(root, *, config=None) -> dict:
    """Discover which layers exist and how to run them."""
    root = Path(root)
    cfg = config or {}
    required = list(cfg.get("required_layers") or [])
    layers = {}

    # Python layout: tests/<layer>/
    for layer in LAYERS:
        layer_dir = root / "tests" / layer
        if layer_dir.is_dir() and any(layer_dir.rglob("test_*.py")):
            layers[layer] = {
                "available": True,
                "kind": "unittest",
                "argv": [
                    sys.executable, "-m", "unittest", "discover",
                    "-s", str(layer_dir), "-q",
                ],
                "cwd": str(root),
                "env": {"PYTHONPATH": str(root / "src")},
            }
            continue
        layers[layer] = {"available": False, "kind": None, "argv": None}

    # Fallbacks for flat tests/
    if not any(layers[L]["available"] for L in LAYERS):
        flat = root / "tests"
        if flat.is_dir() and any(flat.rglob("test_*.py")):
            layers["unit"] = {
                "available": True,
                "kind": "unittest",
                "argv": [
                    sys.executable, "-m", "unittest", "discover",
                    "-s", "tests", "-q",
                ],
                "cwd": str(root),
                "env": {"PYTHONPATH": str(root / "src")},
            }

    # package.json → npm test as unit if no python layers
    pkg = root / "package.json"
    if pkg.is_file() and not layers["unit"]["available"]:
        layers["unit"] = {
            "available": True,
            "kind": "npm",
            "argv": ["npm", "test", "--silent"],
            "cwd": str(root),
            "env": {},
        }

    # make test / tox / nox
    if (root / "Makefile").is_file() and not layers["unit"]["available"]:
        text = (root / "Makefile").read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?m)^test\s*:", text):
            layers["unit"] = {
                "available": True,
                "kind": "make",
                "argv": ["make", "test"],
                "cwd": str(root),
                "env": {},
            }
    if (root / "tox.ini").is_file() and not layers["regression"]["available"]:
        layers["regression"] = {
            "available": True,
            "kind": "tox",
            "argv": ["tox", "-q"],
            "cwd": str(root),
            "env": {},
        }
    if (root / "noxfile.py").is_file() and not layers["regression"]["available"]:
        layers["regression"] = {
            "available": True,
            "kind": "nox",
            "argv": ["nox", "-q"],
            "cwd": str(root),
            "env": {},
        }

    # CI workflow hints (informational)
    ci_cmds = []
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        for path in wf.glob("*.yml"):
            body = path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"(?m)^\s+run:\s*\|\s*$", body):
                pass
            for line in body.splitlines():
                if "unittest" in line or "pytest" in line or "npm test" in line:
                    ci_cmds.append(line.strip()[:200])

    return {
        "ok": True,
        "root": str(root),
        "layers": layers,
        "required_layers": required,
        "ci_hints": ci_cmds[:20],
        "detected_at": now_iso(),
    }


def run_layer(root, layer, *, spec=None, timeout=None) -> dict:
    """Run one layer; never claims pass without exit code 0."""
    root = Path(root)
    layer = str(layer)
    if spec is None:
        matrix = detect_matrix(root)
        spec = (matrix.get("layers") or {}).get(layer) or {}
    if not spec.get("available") or not spec.get("argv"):
        return {
            "layer": layer,
            "status": "SKIPPED",
            "reason": "layer_unavailable",
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    env = {**os.environ, **(spec.get("env") or {})}
    to = int(timeout if timeout is not None else DEFAULT_TIMEOUTS.get(layer, 300))
    try:
        proc = subprocess.run(
            list(spec["argv"]),
            cwd=spec.get("cwd") or str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=to,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "layer": layer,
            "status": "FAIL",
            "reason": "timeout",
            "exit_code": None,
            "stdout_tail": _redact((exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else ""),
            "stderr_tail": _redact((exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "timeout"),
        }
    except OSError as exc:
        return {
            "layer": layer,
            "status": "FAIL",
            "reason": f"os_error:{exc}",
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": _redact(str(exc)),
        }
    out = _redact(proc.stdout or "")[-4000:]
    err = _redact(proc.stderr or "")[-4000:]
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return {
        "layer": layer,
        "status": status,
        "reason": "exit_0" if status == "PASS" else f"exit_{proc.returncode}",
        "exit_code": proc.returncode,
        "stdout_tail": out,
        "stderr_tail": err,
        "argv": list(spec["argv"]),
    }


def run_matrix(root, *, required_layers=None, timeouts=None, stop_on_fail=True) -> dict:
    """Run ordered gate. FAIL stops push path when stop_on_fail."""
    root = Path(root)
    detected = detect_matrix(root, config={"required_layers": required_layers or []})
    results = []
    blocked = False
    for layer in LAYERS:
        if blocked:
            results.append({
                "layer": layer,
                "status": "SKIPPED",
                "reason": "blocked_by_prior_fail",
                "exit_code": None,
                "stdout_tail": "",
                "stderr_tail": "",
            })
            continue
        to = (timeouts or {}).get(layer)
        res = run_layer(root, layer, spec=detected["layers"].get(layer), timeout=to)
        # Required missing layer → FAIL
        if (
            required_layers
            and layer in required_layers
            and res["status"] == "SKIPPED"
        ):
            res = {
                **res,
                "status": "FAIL",
                "reason": "required_layer_missing",
            }
        results.append(res)
        if res["status"] == "FAIL" and stop_on_fail:
            blocked = True
    passed = [r for r in results if r["status"] == "PASS"]
    failed = [r for r in results if r["status"] == "FAIL"]
    skipped = [r for r in results if r["status"] == "SKIPPED"]
    ok = len(failed) == 0
    return {
        "ok": ok,
        "root": str(root),
        "results": results,
        "passed": [r["layer"] for r in passed],
        "failed": [r["layer"] for r in failed],
        "skipped": [r["layer"] for r in skipped],
        "summary": {
            "pass": len(passed),
            "fail": len(failed),
            "skip": len(skipped),
        },
        "detected_at": detected.get("detected_at"),
        "ci_hints": detected.get("ci_hints") or [],
    }


def write_matrix_artifact(root, report: dict) -> str | None:
    root = Path(root)
    dest_dir = root / ".anu"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "MATRIX_RESULTS.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return str(path)
    except OSError:
        return None


def collect_junit_paths(root) -> list:
    root = Path(root)
    found = []
    for pattern in ("**/junit*.xml", "**/test-results*.xml"):
        for path in root.glob(pattern):
            if path.is_file():
                found.append(str(path))
    return found[:20]


def main(argv=None):
    p = argparse.ArgumentParser(description="ANU Test Matrix")
    p.add_argument("--root", required=True)
    p.add_argument("--required", nargs="*", default=[])
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    report = run_matrix(args.root, required_layers=args.required or None)
    write_matrix_artifact(args.root, report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for r in report["results"]:
            print(f"{r['layer']}: {r['status']} ({r.get('reason')})")
        print("ok" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
