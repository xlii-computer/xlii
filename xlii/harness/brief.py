"""HarnessBrief — shared prompt contract for consult and loop judges."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HarnessBrief:
    kind: str = "consult"
    tier: str = ""
    question: str = ""
    messages: list[dict] | None = None
    context_blocks: list[str] = field(default_factory=list)
    project_root: Path | None = None
    system: str | None = None

    def render_prompt(self) -> str:
        """Flatten brief fields into one harness prompt string."""
        parts: list[str] = []
        if self.system:
            parts.append(self.system.strip())
        parts.extend(b.strip() for b in self.context_blocks if b and b.strip())
        if self.messages:
            for msg in self.messages:
                role = str(msg.get("role", "user"))
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    parts.append(f"[{role}]\n{content.strip()}")
        if self.question.strip():
            parts.append(self.question.strip())
        return "\n\n".join(parts)
