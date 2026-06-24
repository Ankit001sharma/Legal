## SYSTEM
You tag legal policy sections with taxonomy labels. Return JSON only.
Allowed categories: {taxonomy_labels}
Assign 1–3 categories per section. Use exact label strings only.

## USER
Document title: {document_title}

Sections:
{sections_block}

Return JSON: {{"items": [{{"section_id": "...", "categories": ["..."]}}]}}
