"""Build DocumentTree from Java-supplied flat sections (no heuristic parser)."""

from __future__ import annotations

from uuid import UUID

from document_core.schemas.chunk import (
    DocumentTree,
    IngestSectionInput,
    SectionNode,
    StructureConfidence,
)


def sections_to_tree(
    *,
    document_id: UUID,
    title: str,
    sections: list[IngestSectionInput],
) -> DocumentTree:
    """Flat sections → DocumentTree with structure_confidence=HIGH."""
    seen: set[str] = set()
    nodes: list[SectionNode] = []
    for section in sections:
        if section.section_id in seen:
            raise ValueError(f"duplicate section_id: {section.section_id}")
        seen.add(section.section_id)
        level = section.level if section.level else 1
        nodes.append(
            SectionNode(
                section_id=section.section_id,
                section_path=section.section_id,
                title=section.title or section.section_id,
                level=level,
                text=section.text.strip(),
                children=[],
            )
        )

    canonical_text = "\n\n".join(
        f"{section.section_id} {section.title}\n{section.text}" for section in sections
    )
    return DocumentTree(
        document_id=document_id,
        title=title,
        canonical_text=canonical_text,
        sections=nodes,
        structure_confidence=StructureConfidence.HIGH,
    )
