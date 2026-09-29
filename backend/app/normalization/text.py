"""Conservative surface cleanup shared by normalizers and registry lookup."""

import re
import unicodedata


def clean_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Normalization input must be a string")
    value = unicodedata.normalize("NFC", value).lower().strip()
    # Keep meaningful operators, currency symbols, slashes and internal punctuation.
    value = re.sub(r"[_\s]+", " ", value)
    value = re.sub(r"(?<=\w)[‐‑–—-](?=\w)", " ", value)
    return value.strip(" .,:;\t\n\r")
