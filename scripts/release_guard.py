from __future__ import annotations

import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 2_000_000
ALLOWED_FILES = frozenset(
    {
        "README.md",
        "LICENSE",
        "NOTICE.md",
        "SECURITY.md",
        "PUBLIC_RELEASE_MANIFEST.md",
        "pyproject.toml",
        "Dockerfile",
        ".gitignore",
        ".env.example",
        ".github/workflows/ci.yml",
        "frontend/index.html",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/tsconfig.json",
        "frontend/tsconfig.node.json",
        "frontend/vite.config.ts",
        "frontend/public/favicon.svg",
        "frontend/src/main.tsx",
        "frontend/src/vite-env.d.ts",
        "frontend/src/types.ts",
        "frontend/src/api.ts",
        "frontend/src/App.tsx",
        "frontend/src/styles.css",
        "src/gridcast_public/__init__.py",
        "src/gridcast_public/adapters.py",
        "src/gridcast_public/api.py",
        "src/gridcast_public/backtest.py",
        "src/gridcast_public/contracts.py",
        "src/gridcast_public/crypto.py",
        "src/gridcast_public/durable_store.py",
        "src/gridcast_public/executor.py",
        "src/gridcast_public/ports.py",
        "src/gridcast_public/receipts.py",
        "src/gridcast_public/workflow.py",
        "src/gridcast_public/reference_models/__init__.py",
        "src/gridcast_public/reference_models/patchtst.py",
        "src/gridcast_public/reference_models/ftmixer.py",
        "scripts/prune_runtime.py",
        "scripts/release_guard.py",
        "tests/test_api.py",
        "tests/test_backtest.py",
        "tests/conftest.py",
        "tests/test_workflow.py",
        "tests/test_durable_store.py",
        "tests/test_resume.py",
        "tests/test_reference_models.py",
        "docs/architecture.md",
        "docs/backtesting.md",
        "docs/reference_models.md",
    }
)
DANGEROUS_SUFFIXES = {
    ".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".db", ".sqlite", ".sqlite3",
    ".pt", ".pth", ".ckpt", ".onnx", ".safetensors", ".npy", ".npz", ".pkl",
    ".joblib", ".key", ".pem", ".p12", ".pfx", ".secret", ".log", ".wal", ".shm",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential assignment": re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*[=:]\s*['\"]?[^\s'\"]{12,}"
    ),
    "credential URL": re.compile(r"(?i)(?:postgres(?:ql)?|mysql)://[^\s:/]+:[^\s@]+@"),
    "cloud access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}
PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:\\(?:Users|Documents and Settings|A_resume)\\[^\s\"']+"),
    re.compile(r"(?<![\w.])/(?:home|Users|mnt|workspace)/[^\s\"']+"),
    re.compile(r"(?i)\\\\[^\\\s]+\\[^\\\s]+\\(?:Users|A_resume)\\[^\s\"']+"),
)
EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
EMAIL_ALLOWLIST = {"user@example.com", "name@example.com"}


def main() -> int:
    problems: list[str] = []
    found: set[str] = set()
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        try:
            info = path.lstat()
        except OSError as exc:
            problems.append(f"cannot inspect {relative}: {exc}")
            continue
        is_reparse = getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        )
        if path.is_symlink() or is_reparse:
            problems.append(f"link or reparse point forbidden: {relative}")
            continue
        if relative == ".git":
            if not path.is_dir():
                problems.append("root .git metadata must be a real directory")
            continue
        if relative.startswith(".git/"):
            continue
        if path.is_dir():
            continue
        found.add(relative)
        if relative not in ALLOWED_FILES:
            problems.append(f"file outside fixed allowlist: {relative}")
            continue
        if path.suffix.lower() in DANGEROUS_SUFFIXES or path.name.lower() in {".env", "id_rsa"}:
            problems.append(f"dangerous file type: {relative}")
            continue
        if info.st_size > MAX_FILE_BYTES:
            problems.append(f"file exceeds 2 MB: {relative}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            problems.append(f"binary or unreadable file: {relative}")
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                problems.append(f"{label}: {relative}")
        if any(pattern.search(content) for pattern in PRIVATE_PATH_PATTERNS):
            problems.append(f"private absolute path: {relative}")
        for match in EMAIL_PATTERN.findall(content):
            if match.lower() not in EMAIL_ALLOWLIST:
                problems.append(f"possible personal email address: {relative}")
                break

    missing = sorted(ALLOWED_FILES - found)
    problems.extend(f"required allowlisted file missing: {name}" for name in missing)
    if problems:
        print("Public release guard failed:")
        print("\n".join(f"- {item}" for item in sorted(set(problems))))
        return 1
    print(f"Public release guard passed: audited {len(found)} allowlisted files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
