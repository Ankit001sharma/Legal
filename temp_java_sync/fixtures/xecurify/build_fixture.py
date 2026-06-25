#!/usr/bin/env python3
"""Build xecurify_e2e.json from fixtures/xecurify/*.txt policy files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = Path(__file__).resolve().parent
OUT = ROOT / "fixtures" / "xecurify_e2e.json"

POLICIES = [
    ("code-of-conduct", "Xecurify Code of Conduct", "code_of_conduct.txt"),
    ("data-retention", "Data Retention Policy", "data_retention.txt"),
    ("incident-response", "Incident Response Plan", "incident_response.txt"),
    ("security-practices", "Security Practices Policy", "security_practices.txt"),
    ("privacy-policy", "Privacy Policy", "privacy_policy.txt"),
]


def main() -> None:
    policies: list[dict[str, str]] = []
    for ref, title, fname in POLICIES:
        path = DIR / fname
        if not path.is_file():
            raise SystemExit(f"Missing policy file: {path}")
        text = path.read_text(encoding="utf-8").strip()
        policies.append(
            {
                "policy_ref": f"xecurify-{ref}",
                "title": title,
                "text": text,
            }
        )

    nda_path = DIR / "nda_contract.txt"
    if not nda_path.is_file():
        raise SystemExit(f"Missing contract file: {nda_path}")
    contract_text = nda_path.read_text(encoding="utf-8").strip()

    payload = {
        "tenant_id": "e2e-demo",
        "policies": policies,
        "contract_text": contract_text,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(p["text"]) for p in policies) + len(contract_text)
    print(f"Wrote {OUT}")
    print(f"  policies: {len(policies)} | contract chars: {len(contract_text)} | total: {total}")


if __name__ == "__main__":
    main()
