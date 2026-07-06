from __future__ import annotations

import re

from .errors import JavLibraryValidationError

FC2_RE = re.compile(r"^FC2(?:[-_\s]*PPV)?[-_\s]*(\d{3,10})$", re.I)
STANDARD_RE = re.compile(r"^([A-Z]{2,12})[-_\s]*(\d{2,8})([A-Z]?)$", re.I)


def normalize_code(raw_code: str) -> str:
    code = raw_code.strip().upper()
    code = (
        code.replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("_", "-")
    )
    code = re.sub(r"\s+", "", code)
    code = re.sub(r"-+", "-", code).strip("-")

    fc2_match = FC2_RE.fullmatch(code)
    if fc2_match:
        return f"FC2-PPV-{fc2_match.group(1)}"

    standard_match = STANDARD_RE.fullmatch(code)
    if standard_match:
        prefix, number, suffix = standard_match.groups()
        return f"{prefix}-{number}{suffix}"

    raise JavLibraryValidationError("番号格式不正确，请使用类似 SSIS-123 或 FC2-PPV-1234567 的格式")
