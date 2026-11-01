"""System prompts: `docs/PROMPTS.md` is the source of truth.

Each prompt is the exact text inside the fenced block directly under its
`## <name>` heading. Modules use that text verbatim — edit the Markdown
file, never a copy in code. (Prompt bodies must not contain fence lines.)
"""

from __future__ import annotations

from pathlib import Path


def _prompts_file() -> Path:
    package_root = Path(__file__).resolve().parents[2] / "docs" / "PROMPTS.md"
    if package_root.exists():
        return package_root
    return Path("docs/PROMPTS.md")  # cwd fallback; a missing file errors loudly


def get_prompt(name: str, path: Path | str | None = None) -> str:
    """Return the exact fenced text under `## <name>` in PROMPTS.md."""
    source = Path(path) if path is not None else _prompts_file()
    lines = source.read_text(encoding="utf-8").splitlines()
    heading = f"## {name}"
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise ValueError(f"Prompt heading {heading!r} not found in {source}.")
    try:
        opener = next(
            i for i in range(start + 1, len(lines))
            if lines[i].strip().startswith("```")
        )
        closer = next(
            i for i in range(opener + 1, len(lines)) if lines[i].strip() == "```"
        )
    except StopIteration:
        raise ValueError(f"Prompt {heading!r} has no complete fenced block.")
    return "\n".join(lines[opener + 1:closer])
