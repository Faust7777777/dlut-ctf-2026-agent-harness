# Stable project entry point for defund's multivariate Coppersmith helper.
#
# Keep the vendored upstream code in tools/sage-env/share/coppersmith/.
# Sage challenge scripts should load this file instead of reaching into the
# Sage environment directly:
#
#   load("tools/ctf_helpers/crypto/multivariate_coppersmith.sage")

from pathlib import Path


def _find_project_root():
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        helper = (
            candidate
            / "tools"
            / "sage-env"
            / "share"
            / "coppersmith"
            / "coppersmith_patched.sage"
        )
        if helper.exists():
            return candidate
    raise FileNotFoundError(
        "multivariate Coppersmith helper missing under tools/sage-env/share/coppersmith"
    )


_helper_path = (
    _find_project_root()
    / "tools"
    / "sage-env"
    / "share"
    / "coppersmith"
    / "coppersmith_patched.sage"
)

if not _helper_path.exists():
    raise FileNotFoundError(f"multivariate Coppersmith helper missing: {_helper_path}")

load(str(_helper_path))
