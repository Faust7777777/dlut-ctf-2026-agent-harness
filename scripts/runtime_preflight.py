#!/usr/bin/env python3
"""Probe local solver runtimes and write a capability matrix.

The supervisor stays on the project ``.venv``.  Expensive or
math-heavy CTF solving should use ``tools/bin/sage-python`` when it
needs Sage/fpylll.  This preflight makes that split explicit and
machine-readable so operators do not have to infer capability from
ad-hoc import errors.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT = Path(__file__).resolve().parents[1]

CAPABILITY_IMPORTS: dict[str, list[str]] = {
    "crypto_lattice": [
        "sage.all",
        "fpylll",
        "mpmath",
        "sympy",
        "gmpy2",
    ],
    "crypto_classic": [
        "Crypto.Util.number",
        "z3",
        "sympy",
        "gmpy2",
    ],
    "pwn": [
        "pwn",
        "capstone",
        "unicorn",
    ],
    "web_static": [
        "requests",
        "bs4",
    ],
}

CATEGORY_CAPABILITIES: dict[str, list[str]] = {
    "crypto": ["crypto_classic", "crypto_lattice"],
    "pwn": ["pwn"],
    "web": ["web_static"],
    "reverse": [],
    "misc": [],
    "forensics": [],
}


def _import_probe_source(imports: Iterable[str]) -> str:
    lines = ["import importlib", "missing = []"]
    lines.append(f"imports = {list(imports)!r}")
    lines.extend(
        [
            "for name in imports:",
            "    try:",
            "        importlib.import_module(name)",
            "    except Exception as exc:",
            "        missing.append(f'{name}:{type(exc).__name__}:{exc}')",
            "if missing:",
            "    print('\\n'.join(missing))",
            "    raise SystemExit(1)",
            "print('OK')",
        ]
    )
    return "\n".join(lines)


def _probe_imports(interpreter: Path, imports: list[str], *, root: Path = PROJECT) -> dict:
    if not interpreter.exists():
        return {
            "ok": False,
            "interpreter": str(interpreter),
            "missing": imports,
            "stdout": "",
            "stderr": f"interpreter not found: {interpreter}",
            "returncode": 127,
        }

    proc = subprocess.run(
        [str(interpreter), "-c", _import_probe_source(imports)],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=45,
    )
    missing = []
    if proc.returncode != 0:
        for line in proc.stdout.splitlines():
            if ":" in line:
                missing.append(line.split(":", 1)[0])
        if not missing:
            missing = list(imports)
    return {
        "ok": proc.returncode == 0,
        "interpreter": str(interpreter),
        "missing": missing,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "returncode": proc.returncode,
    }


def _probe_sage_source(interpreter: Path, source: str, *, root: Path = PROJECT) -> dict:
    if not interpreter.exists():
        return {
            "ok": False,
            "interpreter": str(interpreter),
            "stdout": "",
            "stderr": f"interpreter not found: {interpreter}",
            "returncode": 127,
        }

    proc = subprocess.run(
        [str(interpreter), "-c", source],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=45,
    )
    return {
        "ok": proc.returncode == 0,
        "interpreter": str(interpreter),
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "returncode": proc.returncode,
    }


def _coppersmith_probe_source(helper: Path) -> str:
    return "\n".join(
        [
            "from sage.all import *",
            f"load({str(helper)!r})",
            "p = 11412806115674165783",
            "q = 11813486495389702937",
            "N = p * q",
            "a = 0x123456789abcdef",
            "b = 0xfedcba987654321",
            "x_true = 12345",
            "y_true = 67890",
            "c = (a + x_true) * (b + y_true)",
            "R = PolynomialRing(Zmod(N), names=('X', 'Y'))",
            "X, Y = R.gens()",
            "f = (a + X) * (b + Y) - c",
            "roots = small_roots(f, bounds=(2**17, 2**17), m=2, d=2)",
            "recovered = sorted((int(rx), int(ry)) for rx, ry in roots)",
            "print(recovered)",
            "raise SystemExit(0 if (x_true, y_true) in recovered else 1)",
        ]
    )


def _probe_multivariate_coppersmith(
    interpreter: Path,
    *,
    root: Path = PROJECT,
) -> dict:
    helper = root / "tools" / "ctf_helpers" / "crypto" / "multivariate_coppersmith.sage"
    if not helper.exists():
        return {
            "available": False,
            "helper": str(helper),
            "interpreter": str(interpreter),
            "stdout": "",
            "stderr": f"helper not found: {helper}",
            "returncode": 127,
        }

    probe = _probe_sage_source(interpreter, _coppersmith_probe_source(helper), root=root)
    return {
        "available": bool(probe["ok"]),
        "helper": str(helper),
        "interpreter": probe["interpreter"],
        "stdout": probe["stdout"],
        "stderr": probe["stderr"],
        "returncode": probe["returncode"],
    }


def build_report(*, root: Path = PROJECT) -> dict:
    root = root.resolve()
    solver = root / "tools" / "bin" / "sage-python"
    capabilities = {}
    for name, imports in CAPABILITY_IMPORTS.items():
        probe = _probe_imports(solver, imports, root=root)
        capabilities[name] = {
            "available": bool(probe["ok"]),
            "imports": imports,
            "missing": probe["missing"],
            "interpreter": probe["interpreter"],
            "returncode": probe["returncode"],
        }
    capabilities["crypto_multivariate_coppersmith"] = _probe_multivariate_coppersmith(
        solver,
        root=root,
    )

    categories = {}
    for category, required in CATEGORY_CAPABILITIES.items():
        missing_caps = [
            cap for cap in required
            if not capabilities.get(cap, {}).get("available", False)
        ]
        categories[category] = {
            "auto_submit_safe": not missing_caps,
            "required_capabilities": required,
            "missing_capabilities": missing_caps,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(root),
        "solver_interpreter": str(solver),
        "capabilities": capabilities,
        "categories": categories,
    }


def write_report(report: dict, *, root: Path = PROJECT) -> Path:
    out = root / "state" / "runtime_capabilities.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    root = PROJECT
    if argv:
        if len(argv) == 2 and argv[0] == "--root":
            root = Path(argv[1]).resolve()
        else:
            print("usage: runtime_preflight.py [--root PROJECT_ROOT]", file=sys.stderr)
            return 2
    report = build_report(root=root)
    out = write_report(report, root=root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[runtime_capabilities] {out.relative_to(root)}")
    return 0 if all(c["available"] for c in report["capabilities"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
