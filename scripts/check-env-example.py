#!/usr/bin/env python3
"""Check that every env var referenced in code has an entry in `.env.example`.

Detects the failure mode where a community contributor clones the repo, copies
`.env.example` → `.env`, runs the stack, and hits a missing-env-var error
because someone added an `os.environ.get('NEW_VAR')` in code but didn't update
the example file.

Asymmetric semantics:
  - Code references a var NOT in .env.example  → HARD FAIL (drift that bites contributors)
  - .env.example has a var NOT referenced     → SOFT WARN (docs-only entries are legitimate)

Sources scanned for env var references:
  - **/*.py        — os.environ.get('X') / os.environ['X'] / os.getenv('X')
  - docker-compose*.yml + */docker-compose*.yml — ${X} and ${X:-default}

Bash scripts are intentionally NOT scanned — too many false positives from shell
builtins and locals. Add specific bash env-var checks per-file if needed.

Exit codes:
  0 = parity clean (or only soft warnings)
  1 = hard fail (code references vars missing from .env.example)
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Patterns that extract env-var NAMES from various sources
PYTHON_PATTERNS = [
    re.compile(r"""os\.environ\.get\(\s*['"]([A-Z_][A-Z0-9_]*)['"]""", re.MULTILINE),
    re.compile(r"""os\.environ\[\s*['"]([A-Z_][A-Z0-9_]*)['"]""", re.MULTILINE),
    re.compile(r"""os\.getenv\(\s*['"]([A-Z_][A-Z0-9_]*)['"]""", re.MULTILINE),
]
COMPOSE_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-[^}]*)?\}", re.MULTILINE)

# Known env vars from outside the repo that we don't expect in .env.example
# (system env or third-party expectations)
ALLOWLIST = {
    "HOME",
    "PATH",
    "USER",
    "HOSTNAME",
    "PWD",
    "SHELL",
    "TERM",
    "PYTHONPATH",
    "LANG",
    "LC_ALL",
    "TZ",
    # CI-provided variables
    "GITHUB_TOKEN",
    "GITHUB_REF",
    "GITHUB_SHA",
    "GITHUB_ACTOR",
    "CI_VALIDATION_KEY",  # set inline in ci.yml for docker compose config
    # Variables documented at the docker compose syntax level but with defaults
    # baked into the compose file via ${X:-default}
}


def find_python_refs(root: Path) -> dict[str, list[Path]]:
    refs: dict[str, list[Path]] = {}
    for path in root.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__", ".git"} for part in path.parts):
            continue
        # Skip this checker script itself — its docstrings contain example
        # patterns (`os.environ.get('X')`) that match the scanner regex,
        # producing false positives.
        if path.name == "check-env-example.py":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in PYTHON_PATTERNS:
            for var in pattern.findall(text):
                refs.setdefault(var, []).append(path.relative_to(root))
    return refs


def find_compose_refs(root: Path) -> dict[str, list[Path]]:
    refs: dict[str, list[Path]] = {}
    for path in root.rglob("docker-compose*.yml"):
        if any(part in {".git", ".venv"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for var in COMPOSE_PATTERN.findall(text):
            refs.setdefault(var, []).append(path.relative_to(root))
    return refs


# Pattern matching docker-compose `environment:` LHS keys, in any of:
#     environment:
#       - EMBED_MODEL=${RETRIEVER_EMBED_MODEL:-nomic-embed-text}  # translated
#       - DB_PATH=/data/retriever.db                              # hardcoded
#       - OLLA_URL=http://olla:40114                              # hardcoded
# Either way, the container has that KEY set without the user touching
# .env.example. Code referencing KEY inside the container is satisfied.
COMPOSE_TRANSLATED_LHS = re.compile(
    r"-\s*([A-Z_][A-Z0-9_]*)=",
    re.MULTILINE,
)


def find_compose_translated_names(root: Path) -> set[str]:
    """LHS names from docker-compose environment translations (X=${Y}).

    Code references X (inside the container); X is satisfied by the user setting Y
    in .env. The parity check shouldn't fail on X if it's translated this way.
    """
    names: set[str] = set()
    for path in root.rglob("docker-compose*.yml"):
        if any(part in {".git", ".venv"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for var in COMPOSE_TRANSLATED_LHS.findall(text):
            names.add(var)
    return names


def parse_env_example(path: Path) -> set[str]:
    """Extract env var names from .env.example.

    Recognizes both active (`KEY=value`) and commented-documentation
    (`# KEY=value`) lines. Commented-out entries with defaults are
    treated as "documented" — a contributor sees them in the file and
    knows the var exists, even if they don't need to uncomment to use
    the default behavior. The parity check's failure mode is "var is
    referenced in code but undocumented anywhere," and a commented
    `# KEY=default` IS documentation.
    """
    if not path.exists():
        return set()
    keys = set()
    # Recognize `KEY=...` or `# KEY=...` (comment-with-key shape)
    key_pattern = re.compile(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)\s*=")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        # Skip comment-only lines that don't contain a KEY= pattern
        stripped = line.strip()
        if not stripped:
            continue
        m = key_pattern.match(line)
        if m:
            keys.add(m.group(1))
    return keys


def main() -> int:
    if not ENV_EXAMPLE.exists():
        print(f"ERROR: {ENV_EXAMPLE} not found", file=sys.stderr)
        return 1

    example_keys = parse_env_example(ENV_EXAMPLE)
    python_refs = find_python_refs(REPO_ROOT)
    compose_refs = find_compose_refs(REPO_ROOT)
    translated_via_compose = find_compose_translated_names(REPO_ROOT)

    # Combine all code references
    all_refs: dict[str, set[Path]] = {}
    for refs in (python_refs, compose_refs):
        for var, paths in refs.items():
            all_refs.setdefault(var, set()).update(paths)

    # Code references are "satisfied" by either .env.example OR a compose translation
    satisfied = example_keys | translated_via_compose | ALLOWLIST
    code_vars = set(all_refs.keys())
    missing_from_example = code_vars - satisfied
    unused_in_code = example_keys - code_vars - translated_via_compose

    print(f"Scanned {len(python_refs)} Python env refs + {len(compose_refs)} compose refs")
    print(f".env.example declares {len(example_keys)} variables")
    print(f"docker-compose translates {len(translated_via_compose)} env vars from outside→inside names")
    print(f"Allowlist (system/CI variables) suppresses {len(ALLOWLIST)} names")
    print()

    if unused_in_code:
        print(f"⚠ SOFT WARN: {len(unused_in_code)} variable(s) in .env.example not referenced in code:")
        for var in sorted(unused_in_code):
            print(f"    - {var}")
        print("  (Documentation-only entries are legitimate; this is a heads-up.)")
        print()

    if missing_from_example:
        print(
            f"❌ HARD FAIL: {len(missing_from_example)} variable(s) referenced in code but missing from .env.example:"
        )
        for var in sorted(missing_from_example):
            locs = ", ".join(str(p) for p in sorted(all_refs[var]))
            print(f"    - {var}  (referenced in: {locs})")
        print()
        print("  A community contributor copying .env.example → .env will hit a missing-var error")
        print("  when this code runs. Add the variable to .env.example with a placeholder + comment.")
        return 1

    print("✓ .env.example parity clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
