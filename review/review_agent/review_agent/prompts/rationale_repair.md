## SYSTEM

Rewrite a compliance **rationale** so every factual claim is anchored in the provided quotes.

Rules:
1. Preserve the original compliance **status** meaning (do not change COMPLIANT/NON_COMPLIANT judgment).
2. Use only facts present in contract_quote and policy_quote — no new amounts, parties, or obligations.
3. Professional evaluative language is allowed when directly tied to quoted differences.
4. Minimum 5 characters; be concise.

Return JSON: `{"rationale": "..."}`

## USER

status: {status}

dimension: {dimension_label}

contract_quote:
```
{contract_quote}
```

policy_quote:
```
{policy_quote}
```

original_rationale: {rationale}

Rewrite rationale to be fully supported by the quotes above.
