"""Citation marker display helpers.

Internal answer verification uses ``[#N]`` markers to map sentences to retrieved
chunks. The UI already renders sources separately, so these markers should not be
shown in the user-facing answer text.
"""

from __future__ import annotations

import re

_CITATION_MARKER = re.compile(r"[ \t]*\[#\d+\]")


def strip_citation_markers(text: str) -> str:
    """Remove internal ``[#N]`` markers from a complete display answer."""

    stripped = _CITATION_MARKER.sub("", text)
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    stripped = re.sub(r"[ \t]+(\n)", r"\1", stripped)
    return stripped.strip()


class CitationMarkerStreamFilter:
    """Remove ``[#N]`` markers from streaming token chunks.

    OpenAI streaming can split a marker across chunks, for example ``"["``,
    ``"#1"``, ``"]"``. This small state machine holds only marker-looking
    fragments while passing normal text through immediately.
    """

    def __init__(self) -> None:
        self._state = "text"
        self._digits = ""
        self._pending_space = ""

    def feed(self, text: str) -> str:
        """Consume one raw token chunk and return user-displayable text."""

        output: list[str] = []
        for char in text:
            if self._state == "text":
                if char in " \t":
                    self._pending_space += char
                elif char == "[":
                    self._state = "open"
                else:
                    output.append(self._pending_space)
                    self._pending_space = ""
                    output.append(char)
                continue

            if self._state == "open":
                if char == "#":
                    self._state = "hash"
                else:
                    output.append(self._pending_space)
                    self._pending_space = ""
                    output.append("[")
                    output.append(char)
                    self._state = "text"
                continue

            if self._state == "hash":
                if char.isdigit():
                    self._digits = char
                    self._state = "digits"
                else:
                    output.append(self._pending_space)
                    self._pending_space = ""
                    output.append("[#")
                    output.append(char)
                    self._state = "text"
                continue

            if char.isdigit():
                self._digits += char
                continue
            if char == "]":
                self._digits = ""
                self._pending_space = ""
                self._state = "text"
                continue

            output.append(self._pending_space)
            self._pending_space = ""
            output.append("[#")
            output.append(self._digits)
            output.append(char)
            self._digits = ""
            self._state = "text"

        return "".join(output)

    def flush(self) -> str:
        """Return any incomplete non-marker fragment held at stream end."""

        if self._state == "text":
            pending = self._pending_space
            self._pending_space = ""
            return pending
        if self._state == "open":
            pending = f"{self._pending_space}["
        elif self._state == "hash":
            pending = f"{self._pending_space}[#"
        else:
            pending = f"{self._pending_space}[#{self._digits}"
        self._state = "text"
        self._digits = ""
        self._pending_space = ""
        return pending


__all__ = ["CitationMarkerStreamFilter", "strip_citation_markers"]
