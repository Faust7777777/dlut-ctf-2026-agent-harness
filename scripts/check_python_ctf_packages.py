from __future__ import annotations

import importlib
import importlib.metadata
import os
import subprocess
from pathlib import Path


MODULES = {
    "Core HTTP / parsing": [
        ("requests", "requests"),
        ("bs4", "beautifulsoup4"),
        ("lxml", "lxml"),
        ("yaml", "PyYAML"),
        ("jsonschema", "jsonschema"),
        ("rapidfuzz", "rapidfuzz"),
    ],
    "Crypto / solving": [
        ("pwn", "pwntools"),
        ("Crypto", "pycryptodome"),
        ("z3", "z3-solver"),
        ("gmpy2", "gmpy2"),
        ("sympy", "sympy"),
        ("sage.all", "sagemath"),
    ],
    "Binary / emulation": [
        ("capstone", "capstone"),
        ("keystone", "keystone-engine"),
        ("unicorn", "unicorn"),
        ("angr", "angr"),
        ("claripy", "claripy"),
        ("r2pipe", "r2pipe"),
        ("lief", "lief"),
    ],
    "Forensics / packets": [
        ("scapy", "scapy"),
        ("pyshark", "pyshark"),
        ("volatility3", "volatility3"),
        ("PIL", "Pillow"),
        ("cv2", "opencv-python"),
        ("numpy", "numpy"),
        ("pandas", "pandas"),
    ],
}


def version_of(module: object, package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return str(getattr(module, "__version__", ""))


def sage_version() -> str:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    sage_bin = root / "tools" / "sage-env" / "bin"
    env["PATH"] = f"{sage_bin}:{env.get('PATH', '')}"
    proc = subprocess.run(
        [
            str(root / "tools" / "bin" / "sage-python"),
            "-c",
            "import sage.all; import sage.version; print(sage.version.version)",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def main() -> None:
    out = Path("reports/python_packages_report.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Python CTF Packages Report", "", "| Group | Module | Package | Status | Version |", "|---|---|---|---|---|"]
    for group, modules in MODULES.items():
        for module_name, package_name in modules:
            try:
                if module_name == "sage.all":
                    version = sage_version()
                else:
                    mod = importlib.import_module(module_name)
                    version = version_of(mod, package_name)
                lines.append(f"| {group} | `{module_name}` | `{package_name}` | OK | `{version}` |")
            except Exception as exc:
                lines.append(f"| {group} | `{module_name}` | `{package_name}` | MISSING | `{type(exc).__name__}: {exc}` |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
