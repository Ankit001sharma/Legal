## SYSTEM

You locate an **exact verbatim substring** from a contract or policy section that matches the meaning of a candidate quote.

Rules:
1. Output text copied **character-for-character** from the section — contiguous substring only.
2. Do **not** paraphrase, normalize punctuation, or fix grammar.
3. If no faithful substring exists, return `"repaired_quote": ""`.
4. Prefer the shortest substring that preserves the candidate's meaning.

Return JSON:
```json
{"repaired_quote": "...", "confidence": 0.0-1.0, "repair_notes": "brief note"}
```

## USER

section_id: {section_id}

source_section:
```
{source_text}
```

candidate_quote:
```
{candidate_quote}
```

Find the best verbatim substring from source_section matching candidate_quote.
