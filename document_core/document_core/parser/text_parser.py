"""Heuristic plain-text parser → section tree (PDF/DOCX deferred)."""

from __future__ import annotations

import re
from uuid import UUID

from document_core.schemas.chunk import DocumentTree, SectionNode, StructureConfidence

# Numbered headings: 1., 1.1, 12.2 Indemnification, Section 4, ARTICLE II
_HEADING_RE = re.compile(
    r"^("
    r"(?:section|article|schedule|exhibit|clause)\s+[\divxlc]+[\.\)]?\s*[-–:]?\s*.+|"
    r"\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{2,120}|"
    r"[IVXLC]+\.\s+[A-Z][^\n]{2,120}|"
    r"\([a-z]\)\s+[A-Z]"
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_INLINE_NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+(.+)$")


def parse_text_to_tree(
    *,
    document_id: UUID,
    title: str,
    text: str,
) -> DocumentTree:
    """Parse raw text into a hierarchical section tree."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    sections: list[SectionNode] = []
    stack: list[tuple[int, SectionNode]] = []
    current_lines: list[str] = []
    current_heading: tuple[str, str, int] | None = None  # section_id, title, level

    def flush_section() -> None:
        nonlocal current_lines, current_heading
        if current_heading is None:
            body = "\n".join(current_lines).strip()
            if body:
                sections.append(
                    SectionNode(
                        section_id="preamble",
                        section_path="preamble",
                        title="Preamble",
                        level=0,
                        text=body,
                    )
                )
            current_lines = []
            return

        section_id, heading_title, level = current_heading
        body = "\n".join(current_lines).strip()
        full_text = f"{heading_title}\n{body}".strip() if body else heading_title
        node = SectionNode(
            section_id=section_id,
            section_path=section_id,
            title=heading_title,
            level=level,
            text=full_text,
        )

        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            stack[-1][1].children.append(node)
        else:
            sections.append(node)

        stack.append((level, node))
        current_lines = []
        current_heading = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current_lines or current_heading:
                current_lines.append("")
            continue

        heading = _match_heading(line)
        if heading:
            flush_section()
            section_id, heading_title, level = heading
            current_heading = (section_id, heading_title, level)
            continue

        current_lines.append(line)

    flush_section()

    if not sections:
        sections.append(
            SectionNode(
                section_id="body",
                section_path="body",
                title=title,
                level=0,
                text=text.strip(),
            )
        )
        confidence = StructureConfidence.LOW
    else:
        confidence = _assess_confidence(sections, text)

    canonical = _build_canonical_text(sections)
    return DocumentTree(
        document_id=document_id,
        title=title,
        canonical_text=canonical,
        sections=sections,
        structure_confidence=confidence,
    )


def _match_heading(line: str) -> tuple[str, str, int] | None:
    if _HEADING_RE.match(line):
        section_id = _derive_section_id(line)
        level = _heading_level(section_id)
        return section_id, line, level

    inline = _INLINE_NUMBERED_RE.match(line)
    if inline:
        section_id, rest = inline.group(1), inline.group(2).strip()
        title = f"{section_id} {rest}"
        return section_id, title, _heading_level(section_id)

    return None


def _derive_section_id(line: str) -> str:
    numbered = re.search(r"(\d+(?:\.\d+)*)", line)
    if numbered:
        return numbered.group(1)
    roman = re.match(r"^([IVXLC]+)\.", line, re.IGNORECASE)
    if roman:
        return roman.group(1).upper()
    letter = re.match(r"^\(([a-z])\)", line, re.IGNORECASE)
    if letter:
        return f"({letter.group(1).lower()})"
    slug = re.sub(r"[^a-z0-9]+", "_", line.lower()).strip("_")[:40]
    return slug or "section"


def _heading_level(section_id: str) -> int:
    if section_id.startswith("("):
        return 3
    if "." in section_id:
        return section_id.count(".") + 1
    return 1


def _assess_confidence(sections: list[SectionNode], full_text: str) -> StructureConfidence:
    if len(sections) >= 3:
        return StructureConfidence.HIGH
    if len(sections) == 1 and len(full_text) > 2000:
        return StructureConfidence.LOW
    if len(sections) >= 1:
        return StructureConfidence.MEDIUM
    return StructureConfidence.LOW


def _build_canonical_text(sections: list[SectionNode]) -> str:
    parts: list[str] = []

    def walk(nodes: list[SectionNode]) -> None:
        for node in nodes:
            parts.append(node.text)
            walk(node.children)

    walk(sections)
    return "\n\n".join(parts).strip()
