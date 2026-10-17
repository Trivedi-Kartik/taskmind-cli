"""Keep secrets out of everything TaskMind sends to an AI provider.

Two layers: files that usually hold secrets are never listed, read or searched,
and secret-looking values inside ordinary files are replaced with [REDACTED]
before any text leaves the computer (code reads, searches, diffs).
"""

import fnmatch
import re
from pathlib import PurePosixPath

# Matched against the file name and the full relative path.
SECRET_FILES = [
    ".env", ".env.*", "*.env", ".envrc", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "*.kdbx", "*.asc", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.ppk", ".netrc", ".npmrc",
    ".pypirc", ".dockercfg", ".git-credentials", "credentials", "credentials.*", "*credentials*.json",
    "secrets.*", "*secret*.json", "*secret*.yml", "*secret*.yaml", "*.tfstate", "*.tfstate.*",
    "*.tfvars", "service-account*.json", "*serviceaccount*.json", "*.mobileprovision",
]
SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".docker", ".taskmind"}
ALLOWED = {".env.example", ".env.sample", ".env.template", "example.env", "sample.env"}

REDACTED = "[REDACTED]"
# Standard non-secret values that appear next to secret-sounding names.
COMMON_VALUES = {"bearer", "basic", "digest", "oauth", "jwt", "none", "required", "optional", "header",
                 "headers", "cookie", "session", "token", "password", "apikey", "hidden", "secret",
                 "enabled", "disabled", "public", "private"}
_PATTERNS = [
    # Private key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    # Well-known key formats
    re.compile(r"\b(gsk_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-(proj-)?[A-Za-z0-9_-]{20,}|"
               r"sk_(live|test)_[A-Za-z0-9]{16,}|rk_(live|test)_[A-Za-z0-9]{16,}|xai-[A-Za-z0-9]{20,}|"
               r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|"
               r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|glpat-[A-Za-z0-9_-]{20,}|"
               r"xox[abpors]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"),
    # Passwords inside connection URLs: scheme://user:PASSWORD@host
    re.compile(r"(?<=://)([^:/\s@]+):([^@\s/]{3,})(?=@)"),
]
# name = "value" / name: value, for names that suggest a secret
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|private[_-]?key|access[_-]?key|"
    r"client[_-]?secret|auth)[A-Za-z0-9_.-]*)(['\"]?\s*[:=]\s*)(['\"]?)([^\s'\"#,;]{6,})(\3)")
_PLACEHOLDER = re.compile(r"(?i)^(\$\{?[A-Z_]+\}?|<[^>]+>|x{4,}|\*{4,}|changeme|your[_-].*|example.*|"
                          r"os\.environ.*|process\.env.*|getenv.*|env\(.*|none|null|true|false|\[redacted\])$")


def is_secret_file(path) -> bool:
    p = PurePosixPath(str(path).replace("\\", "/"))
    if p.name.lower() in ALLOWED:
        return False
    if any(part in SECRET_DIRS for part in p.parts[:-1]):
        return True
    name = p.name.lower()
    return any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(str(p).lower(), pat) for pat in SECRET_FILES)


def redact(text) -> str:
    """Replace secret-looking values; keeps the surrounding code readable for the AI."""
    if not text:
        return text
    text = _PATTERNS[0].sub(REDACTED, text)
    text = _PATTERNS[1].sub(REDACTED, text)
    text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}:{REDACTED}", text)

    def assignment(m):
        name, quote, value = m.group(1).lower(), m.group(3), m.group(4)
        if _PLACEHOLDER.match(value) or value.startswith(("self.", "config.", "settings.")) or "(" in value:
            return m.group(0)
        if not quote and re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+|[a-z_]+_[a-z_]+", value):
            return m.group(0)  # code, not a secret: form.password.data, api_key = user_api_key
        if value.isalpha() and (value.lower() in name or value.lower() in COMMON_VALUES):
            return m.group(0)  # password_field = "password", "token_type": "bearer"
        if not quote and value.lower() == name.split(".")[-1]:
            return m.group(0)  # self.api_key = api_key
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(5)}"
    return _ASSIGNMENT.sub(assignment, text)
