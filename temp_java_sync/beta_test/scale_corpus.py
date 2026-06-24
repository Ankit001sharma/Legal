"""Real-world-scale fixture corpus: 12 large contracts + 42 playbook policies.

Stress corpus — procedural ``expect_gap`` labels and heuristic scoring only.
For curated gold eval use ``fixtures/scale/enterprise_msa_gold.json``.
"""

from __future__ import annotations

from typing import Any

TENANT_PREFIX = "scale-bench"

# Real-world-inspired policy library (42 playbooks — duplicates per category stress discovery grouping)
POLICY_LIBRARY: list[dict[str, Any]] = [
    {
        "policy_ref": "playbook-compliance-rba-v2026",
        "title": "Enterprise Supplier Code of Conduct (RBA Silver)",
        "policy_type": "vendor",
        "categories": ["compliance"],
        "text": "Suppliers must achieve RBA Silver recognition (160/200 points, no priority findings) at all manufacturing sites. Corporate-level SAQs are required annually. Facility audits must be completed within 60 days when selected.",
    },
    {
        "policy_ref": "playbook-compliance-anti-bribery",
        "title": "Anti-Bribery & Export Controls Playbook",
        "policy_type": "vendor",
        "categories": ["compliance"],
        "text": "Suppliers shall maintain an anti-bribery program consistent with FCPA and UK Bribery Act. No facilitation payments. Export-controlled items require end-user certificates before shipment.",
    },
    {
        "policy_ref": "playbook-compliance-sanctions",
        "title": "Sanctions & Denied Party Screening Standard",
        "policy_type": "vendor",
        "categories": ["compliance"],
        "text": "Screen all parties against OFAC, EU, and UN sanctions lists prior to onboarding and quarterly thereafter. Block transactions with embargoed countries without Legal approval.",
    },
    {
        "policy_ref": "playbook-hr-forced-labor-un",
        "title": "Global Human Rights — Forced Labor Prohibition",
        "policy_type": "vendor",
        "categories": ["human_rights", "labor"],
        "text": "Forced labor in any form is prohibited including bonded labor, prison labor, and trafficking. Recruitment fees must not be charged to workers. OECD-aligned due diligence is mandatory for Tier-1 suppliers.",
    },
    {
        "policy_ref": "playbook-hr-freedom-association",
        "title": "Freedom of Association & Collective Bargaining",
        "policy_type": "vendor",
        "categories": ["human_rights", "labor"],
        "text": "Suppliers must respect freedom of association and collective bargaining where lawful. Retaliation against worker representatives is prohibited.",
    },
    {
        "policy_ref": "playbook-minerals-rmap",
        "title": "Responsible Minerals — RMAP Conformant Sourcing",
        "policy_type": "vendor",
        "categories": ["minerals", "compliance"],
        "text": "Submit Minerals Reporting Templates documenting 3TG smelters. Source only from RMAP-conformant or cross-recognized smelters. Remove high-risk smelters within 90 days of notification.",
    },
    {
        "policy_ref": "playbook-minerals-cmrt",
        "title": "Conflict Minerals CMRT Reporting Standard",
        "policy_type": "vendor",
        "categories": ["minerals"],
        "text": "Annual CMRT submission required by March 31. Smelter lists must be validated against RMI database.",
    },
    {
        "policy_ref": "playbook-environment-cdp",
        "title": "GHG & CDP Reporting — Supply Chain",
        "policy_type": "vendor",
        "categories": ["environment", "sustainability"],
        "text": "Report Scope 1, 2, and material Scope 3 GHG to CDP annually. Public absolute reduction target required. Third-party verification of emissions inventory mandatory.",
    },
    {
        "policy_ref": "playbook-environment-waste",
        "title": "Environmental Compliance & Waste Management",
        "policy_type": "vendor",
        "categories": ["environment"],
        "text": "Hazardous waste manifests retained 7 years. RoHS and REACH compliance certificates required for hardware components.",
    },
    {
        "policy_ref": "playbook-security-mss",
        "title": "Master Security Specification (MSS) — Tier 1",
        "policy_type": "vendor",
        "categories": ["security", "vendor_security"],
        "text": "Conform to MSS controls: encryption at rest AES-256, MFA for admin access, annual penetration testing, 72-hour breach notification to Buyer security team.",
    },
    {
        "policy_ref": "playbook-security-scv-bcp",
        "title": "Supply Chain Visibility & Business Continuity",
        "policy_type": "vendor",
        "categories": ["security", "procurement"],
        "text": "Register all manufacturing sites in SCV portal. Quarterly SCV surveys mandatory. BCP self-assessment annually with RTO ≤ 24 hours for critical components.",
    },
    {
        "policy_ref": "playbook-vendor-security-assessment",
        "title": "Vendor Security Assessment (VSA) Standard",
        "policy_type": "vendor",
        "categories": ["vendor_security"],
        "text": "Complete VSA questionnaire before production access. SOC 2 Type II report required for SaaS vendors handling Buyer data.",
    },
    {
        "policy_ref": "playbook-liability-msa-cap",
        "title": "MSA Liability Cap — 12-Month Fees Standard",
        "policy_type": "msa",
        "categories": ["liability"],
        "text": "Total aggregate liability capped at fees paid in the twelve (12) months preceding the claim. Carve-outs only for confidentiality breach, IP infringement, and gross negligence.",
    },
    {
        "policy_ref": "playbook-liability-super-cap",
        "title": "Liability Super-Cap — Confidentiality & Data Breach",
        "policy_type": "msa",
        "categories": ["liability"],
        "text": "Confidentiality and data breach liability may exceed standard cap up to two times (2x) annual fees or five million dollars ($5M), whichever is greater.",
    },
    {
        "policy_ref": "playbook-liability-consequential",
        "title": "Consequential Damages Mutual Waiver",
        "policy_type": "msa",
        "categories": ["liability"],
        "text": "Neither party liable for indirect, incidental, special, or consequential damages including lost profits, except for breaches of confidentiality or IP indemnity obligations.",
    },
    {
        "policy_ref": "playbook-indemnity-ip",
        "title": "IP Infringement Indemnification Standard",
        "policy_type": "msa",
        "categories": ["indemnity", "ip"],
        "text": "Vendor indemnifies Customer against third-party IP infringement claims arising from deliverables. Vendor must defend at its expense and obtain necessary licenses.",
    },
    {
        "policy_ref": "playbook-indemnity-data-breach",
        "title": "Data Breach Indemnification Playbook",
        "policy_type": "msa",
        "categories": ["indemnity", "privacy"],
        "text": "Processor indemnifies Controller for regulatory fines and notification costs arising from Processor's failure to implement required security controls.",
    },
    {
        "policy_ref": "playbook-indemnity-gross-negligence",
        "title": "Gross Negligence & Willful Misconduct Indemnity",
        "policy_type": "vendor",
        "categories": ["indemnity"],
        "text": "Indemnification obligations are uncapped for gross negligence, willful misconduct, and violations of applicable law.",
    },
    {
        "policy_ref": "playbook-confidentiality-mutual",
        "title": "Mutual Confidentiality — Enterprise Standard",
        "policy_type": "nda",
        "categories": ["confidentiality"],
        "text": "Confidential Information protected with at least reasonable care, no less than same degree as own confidential information. Return or destroy upon termination within 30 days.",
    },
    {
        "policy_ref": "playbook-confidentiality-residuals",
        "title": "Confidentiality — Residuals & Clean Room",
        "policy_type": "nda",
        "categories": ["confidentiality"],
        "text": "Residual knowledge exception prohibited for trade secrets and source code. Clean room procedures required for competitive product development.",
    },
    {
        "policy_ref": "playbook-confidentiality-term",
        "title": "Confidentiality Survival — 5 Year Standard",
        "policy_type": "nda",
        "categories": ["confidentiality"],
        "text": "Confidentiality obligations survive termination for five (5) years; trade secrets survive indefinitely.",
    },
    {
        "policy_ref": "playbook-privacy-gdpr-dpa",
        "title": "GDPR Data Processing Addendum Standard",
        "policy_type": "dpa",
        "categories": ["privacy", "data_retention"],
        "text": "Processor acts only on documented instructions. Sub-processors require prior written authorization. DPIA support and 72-hour breach notification to Controller required.",
    },
    {
        "policy_ref": "playbook-privacy-ccpa",
        "title": "CCPA/CPRA Service Provider Terms",
        "policy_type": "dpa",
        "categories": ["privacy"],
        "text": "Service Provider shall not sell or share personal information. Assist with consumer rights requests within 10 business days.",
    },
    {
        "policy_ref": "playbook-privacy-cross-border",
        "title": "Cross-Border Data Transfer — SCCs Module 2",
        "policy_type": "dpa",
        "categories": ["privacy"],
        "text": "EU personal data transfers require Standard Contractual Clauses Module 2. Transfer impact assessment documented annually.",
    },
    {
        "policy_ref": "playbook-data-retention-90day",
        "title": "Data Retention & Deletion — 90 Day Post-Termination",
        "policy_type": "dpa",
        "categories": ["data_retention"],
        "text": "Delete or return all personal data within 90 days of termination. Certification of deletion required.",
    },
    {
        "policy_ref": "playbook-data-retention-backup",
        "title": "Backup Retention & Crypto-Shredding",
        "policy_type": "dpa",
        "categories": ["data_retention", "security"],
        "text": "Backup retention max 35 days. Crypto-shredding keys upon deletion request within 30 days.",
    },
    {
        "policy_ref": "playbook-termination-convenience",
        "title": "Termination for Convenience — 90 Day Notice",
        "policy_type": "msa",
        "categories": ["termination"],
        "text": "Either party may terminate for convenience upon ninety (90) days written notice. Pro-rata refund of prepaid fees required.",
    },
    {
        "policy_ref": "playbook-termination-material-breach",
        "title": "Material Breach Cure Period — 30 Days",
        "policy_type": "msa",
        "categories": ["termination"],
        "text": "Material breach curable within thirty (30) days of notice. Immediate termination for insolvency, sanctions violation, or data breach.",
    },
    {
        "policy_ref": "playbook-payment-net30",
        "title": "Payment Terms — Net 30 with 2/10 Discount",
        "policy_type": "vendor",
        "categories": ["payment"],
        "text": "Invoices paid net thirty (30) days. Two percent (2%) discount if paid within ten (10) days. Late interest at 1.5% per month.",
    },
    {
        "policy_ref": "playbook-payment-audit-rights",
        "title": "Payment Audit & Benchmarking Rights",
        "policy_type": "vendor",
        "categories": ["payment", "procurement"],
        "text": "Buyer may audit pricing against benchmark indices annually. Most-favored-customer pricing for equivalent volumes.",
    },
    {
        "policy_ref": "playbook-sla-availability",
        "title": "SaaS Availability SLA — 99.9% Monthly",
        "policy_type": "saas",
        "categories": ["sla"],
        "text": "Monthly uptime minimum 99.9%. Service credits 10% of monthly fees per 0.1% below threshold. RTO 4 hours for critical incidents.",
    },
    {
        "policy_ref": "playbook-sla-support",
        "title": "Support Response SLA — P1 within 1 Hour",
        "policy_type": "saas",
        "categories": ["sla"],
        "text": "Priority 1 incidents: response within 1 hour, resolution plan within 4 hours. 24x7 support for production outages.",
    },
    {
        "policy_ref": "playbook-ip-assignment",
        "title": "Work Product IP Assignment to Customer",
        "policy_type": "msa",
        "categories": ["ip"],
        "text": "All work product created under SOW assigned to Customer upon payment. Vendor retains pre-existing IP with license to Customer.",
    },
    {
        "policy_ref": "playbook-ip-open-source",
        "title": "Open Source Software Compliance",
        "policy_type": "software",
        "categories": ["ip", "compliance"],
        "text": "SBOM required for all deliverables. GPL copyleft components require Legal approval. No open source in security-critical modules without review.",
    },
    {
        "policy_ref": "playbook-insurance-cyber",
        "title": "Cyber Liability Insurance — $5M Minimum",
        "policy_type": "vendor",
        "categories": ["insurance"],
        "text": "Maintain cyber liability insurance minimum five million dollars ($5M) per occurrence. Certificate of insurance provided annually.",
    },
    {
        "policy_ref": "playbook-insurance-general",
        "title": "Commercial General Liability — $2M",
        "policy_type": "vendor",
        "categories": ["insurance"],
        "text": "CGL minimum two million dollars ($2M) per occurrence. Buyer named as additional insured.",
    },
    {
        "policy_ref": "playbook-governing-law-delaware",
        "title": "Governing Law — Delaware, USA",
        "policy_type": "msa",
        "categories": ["governing_law"],
        "text": "Governed by Delaware law excluding conflict of laws. Exclusive jurisdiction Delaware state and federal courts.",
    },
    {
        "policy_ref": "playbook-governing-law-uk",
        "title": "Governing Law — England & Wales",
        "policy_type": "msa",
        "categories": ["governing_law"],
        "text": "Governed by laws of England and Wales. LCIA arbitration London seat for disputes exceeding $1M.",
    },
    {
        "policy_ref": "playbook-procurement-subcontract",
        "title": "Subcontractor Flow-Down Requirements",
        "policy_type": "vendor",
        "categories": ["procurement"],
        "text": "All subcontractors bound by equivalent security, HR, and compliance terms. Buyer approval required for critical sub-tier suppliers.",
    },
    {
        "policy_ref": "playbook-ai-usage-restriction",
        "title": "AI/ML Usage Restrictions on Customer Data",
        "policy_type": "saas",
        "categories": ["ai_usage"],
        "text": "Customer data shall not be used to train foundation models without explicit opt-in. AI outputs must be labeled when used in Customer-facing features.",
    },
    {
        "policy_ref": "playbook-ai-usage-transparency",
        "title": "Automated Decision-Making Transparency",
        "policy_type": "saas",
        "categories": ["ai_usage", "privacy"],
        "text": "Disclose automated decision-making under GDPR Art. 22. Human review available for adverse automated decisions.",
    },
    {
        "policy_ref": "playbook-hr-background-checks",
        "title": "Personnel Background Screening",
        "policy_type": "vendor",
        "categories": ["hr", "employment"],
        "text": "Background checks for personnel with access to Buyer systems or facilities. Re-screen upon role change to privileged access.",
    },
    {
        "policy_ref": "playbook-employment-eeo",
        "title": "Equal Employment Opportunity Standard",
        "policy_type": "vendor",
        "categories": ["employment", "hr"],
        "text": "Non-discrimination in hiring and employment. Affirmative action plan where required by applicable law.",
    },
]

# Section templates: (title, category, weak_text, strong_text)
SECTION_TEMPLATES: list[tuple[str, str, str, str]] = [
    (
        "Definitions and Interpretation",
        "general",
        "Capitalized terms have the meanings set forth herein. 'Confidential Information' means any information marked confidential or that should reasonably be understood as confidential.",
        "Capitalized terms have the meanings set forth herein. 'Confidential Information' means any non-public information disclosed in any form, including oral and visual disclosures, whether or not marked.",
    ),
    (
        "Supplier Code of Conduct",
        "compliance",
        "Supplier acknowledges Buyer's general expectations but is not required to achieve third-party social compliance certification, complete SAQs, or share audit reports unless Buyer requests in writing.",
        "Supplier shall comply with Buyer's Supplier Code of Conduct including RBA Silver certification at all manufacturing sites and annual corporate SAQ submission.",
    ),
    (
        "Human Rights and Labor Standards",
        "human_rights",
        "Supplier complies with local labor laws only. Supplier is not required to maintain a formal human rights due diligence program or prohibit recruitment fees.",
        "Supplier prohibits forced labor in all forms, maintains OECD-aligned due diligence, and shall not charge recruitment fees to workers.",
    ),
    (
        "Responsible Minerals and Conflict Minerals",
        "minerals",
        "Supplier is not obligated to complete Minerals Reporting Templates, identify smelters, or remove high-risk smelters unless required by law.",
        "Supplier shall submit CMRT/MRT templates annually and source only from RMAP-conformant smelters designated as low-risk.",
    ),
    (
        "Environmental Compliance and GHG Reporting",
        "environment",
        "Supplier complies with applicable environmental laws. Supplier is not required to report GHG emissions to CDP or publish reduction targets.",
        "Supplier shall report Scope 1, 2, and material Scope 3 emissions to CDP annually with third-party verification and a public absolute reduction target.",
    ),
    (
        "Information Security and MSS",
        "security",
        "Supplier maintains reasonable security practices. Supplier is not required to conform to Buyer's Master Security Specification or participate in security audits.",
        "Supplier shall conform to Buyer's Master Security Specification including AES-256 encryption, MFA, annual penetration testing, and 72-hour breach notification.",
    ),
    (
        "Supply Chain Visibility and Business Continuity",
        "vendor_security",
        "Supplier will notify Buyer of material disruptions when practicable. Supplier is not required to participate in SCV surveys or maintain formal BCP testing.",
        "Supplier shall register all sites in SCV portal, complete quarterly SCV surveys, and maintain BCP with RTO not exceeding 24 hours for critical components.",
    ),
    (
        "Limitation of Liability",
        "liability",
        "Except for gross negligence, total liability shall not exceed one hundred thousand dollars ($100,000). Neither party is liable for consequential damages under any circumstance.",
        "Total aggregate liability shall not exceed fees paid in the twelve (12) months preceding the claim, except for confidentiality breach, IP infringement, or gross negligence.",
    ),
    (
        "Indemnification",
        "indemnity",
        "Vendor indemnifies Customer only for third-party claims directly caused by Vendor's willful misconduct, subject to the liability cap in Section 8.",
        "Vendor shall indemnify, defend, and hold harmless Customer from third-party IP infringement claims and data breach claims arising from Vendor's deliverables or processing.",
    ),
    (
        "Confidential Information",
        "confidentiality",
        "Receiving party shall protect confidential information using commercially reasonable efforts. Residual knowledge of general ideas is permitted without restriction.",
        "Receiving party shall protect Confidential Information using at least the same degree of care as its own, but no less than reasonable care. Residuals exception does not apply to trade secrets.",
    ),
    (
        "Data Protection and Privacy",
        "privacy",
        "Each party handles personal data according to its own privacy policies. Processor may use sub-processors without prior notice and may use data to improve services.",
        "Processor processes personal data only on documented instructions, obtains prior authorization for sub-processors, and provides 72-hour breach notification per GDPR.",
    ),
    (
        "Data Retention and Deletion",
        "data_retention",
        "Upon termination, Processor may retain data in backups indefinitely for disaster recovery purposes without deletion certification.",
        "Processor shall delete or return all personal data within 90 days of termination and provide written certification of deletion including backup crypto-shredding.",
    ),
    (
        "Service Levels and Availability",
        "sla",
        "Vendor targets commercially reasonable uptime. No service credits apply for downtime regardless of duration or business impact.",
        "Vendor guarantees 99.9% monthly availability with service credits of 10% of monthly fees for each 0.1% below threshold.",
    ),
    (
        "Payment Terms and Invoicing",
        "payment",
        "Buyer shall pay undisputed invoices within sixty (60) days. Vendor may charge late fees at Vendor's discretion up to maximum permitted by law.",
        "Invoices paid net thirty (30) days with 2% discount if paid within ten (10) days. Late interest at 1.5% per month on undisputed amounts.",
    ),
    (
        "Intellectual Property Rights",
        "ip",
        "Vendor retains all IP in deliverables. Customer receives a non-exclusive license for internal use only during the term.",
        "All work product created under this Agreement is assigned to Customer upon payment. Vendor retains pre-existing IP with perpetual license to Customer.",
    ),
    (
        "Insurance Requirements",
        "insurance",
        "Vendor maintains insurance appropriate for its business. No minimum coverage amounts or additional insured status required.",
        "Vendor maintains CGL of $2M per occurrence and cyber liability of $5M per occurrence with Buyer named as additional insured.",
    ),
    (
        "Termination",
        "termination",
        "Either party may terminate upon ten (10) days notice for any reason. No refund of prepaid fees upon termination for convenience.",
        "Either party may terminate for convenience upon ninety (90) days notice with pro-rata refund of prepaid fees. Material breach curable within thirty (30) days.",
    ),
    (
        "Governing Law and Dispute Resolution",
        "governing_law",
        "This Agreement is governed by the laws of the Supplier's principal place of business. Disputes resolved by binding arbitration in Supplier's home jurisdiction.",
        "Governed by Delaware law. Exclusive jurisdiction in Delaware state and federal courts unless dispute exceeds $1M then LCIA arbitration.",
    ),
    (
        "Subcontracting and Assignment",
        "procurement",
        "Vendor may subcontract any portion of services without Buyer approval. Vendor may assign this Agreement to any affiliate without consent.",
        "Subcontractors must flow down equivalent security and compliance terms. Buyer approval required for critical sub-tier suppliers. No assignment without consent.",
    ),
    (
        "Artificial Intelligence and Automated Processing",
        "ai_usage",
        "Vendor may use Customer data to train and improve machine learning models. Automated decisions need not be explainable to data subjects.",
        "Customer data shall not train foundation models without opt-in. AI outputs labeled in Customer-facing features. Human review for adverse automated decisions.",
    ),
]

CONTRACT_SCENARIOS: list[dict[str, Any]] = [
    {"contract_ref": "scale-enterprise-msa-2026", "title": "Enterprise Master Services Agreement — GlobalTech / Apex Vendor", "contract_type": "msa"},
    {"contract_ref": "scale-saas-subscription-2026", "title": "SaaS Subscription Agreement — CloudServe Platform", "contract_type": "saas"},
    {"contract_ref": "scale-cloud-hosting-2026", "title": "Cloud Hosting & Infrastructure Agreement — DataVault Inc.", "contract_type": "cloud"},
    {"contract_ref": "scale-professional-services-2026", "title": "Professional Services SOW — Consulting Partners LLC", "contract_type": "professional_services"},
    {"contract_ref": "scale-distribution-2026", "title": "Authorized Distribution Agreement — Channel Partners EMEA", "contract_type": "distribution"},
    {"contract_ref": "scale-oem-hardware-2026", "title": "OEM Hardware Supply Agreement — SiliconWorks Ltd.", "contract_type": "oem"},
    {"contract_ref": "scale-government-federal-2026", "title": "Federal Supply Schedule Task Order — GovContractor Inc.", "contract_type": "vendor"},
    {"contract_ref": "scale-dpa-gdpr-2026", "title": "Data Processing Agreement — EU Controller / US Processor", "contract_type": "dpa"},
    {"contract_ref": "scale-master-vendor-nda-2026", "title": "Master Vendor NDA — Strategic Sourcing Program", "contract_type": "nda"},
    {"contract_ref": "scale-global-frame-2026", "title": "Global Framework Agreement — Multi-Region Procurement", "contract_type": "vendor"},
    {"contract_ref": "scale-software-license-2026", "title": "Enterprise Software License Agreement — ERP Suite", "contract_type": "software"},
    {"contract_ref": "scale-logistics-3pl-2026", "title": "Third-Party Logistics Services Agreement — FreightCo", "contract_type": "logistics"},
]


def _policy_fixture(raw: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "policy_ref": raw["policy_ref"],
        "title": raw["title"],
        "policy_type": raw["policy_type"],
        "categories": raw["categories"],
        "metadata": {
            "source": "scale-benchmark",
            "categories": raw["categories"],
            "review_guidance": raw["text"][:500],
            "preferred_position": raw["text"][:500],
        },
        "sections": [
            {
                "section_id": "1",
                "title": raw["title"],
                "text": raw["text"],
            }
        ],
    }


def _contract_fixture(scenario: dict[str, Any], *, tenant_id: str, index: int) -> dict[str, Any]:
    """Build ~5+ page contract (20 sections, ~3500+ words) with alternating weak/strong clauses."""
    sections: list[dict[str, Any]] = []
    eval_labels: dict[str, dict[str, Any]] = {}
    # Use all 20 section templates; pad with extra definitions for length
    for idx, (title, category, weak, strong) in enumerate(SECTION_TEMPLATES, start=1):
        expect_gap = (idx + index) % 3 != 0  # ~2/3 sections intentionally weak vs playbooks
        body = weak if expect_gap else strong
        # Pad section to ~180-220 words (realistic clause length)
        padding = (
            " The parties acknowledge that this section forms an integral part of the Agreement "
            "and supersedes any prior oral or written understandings relating to the subject matter herein. "
            "Any amendment must be in writing signed by authorized representatives of both parties. "
            "If any provision is held invalid, the remainder continues in full force. "
            "Neither party's failure to enforce any provision constitutes a waiver. "
            "Notices under this section must be delivered to the addresses set forth in the Notices article."
        )
        sections.append(
            {
                "section_id": str(idx),
                "title": title,
                "text": body + padding,
            }
        )
        eval_labels[str(idx)] = {
            "category": category,
            "expect_gap": expect_gap,
            "title": title,
        }

    return {
        "tenant_id": tenant_id,
        "contract_ref": scenario["contract_ref"],
        "title": scenario["title"],
        "contract_type": scenario["contract_type"],
        "metadata": {
            "source": "scale-benchmark",
            "benchmark_index": index,
            "eval_labels": eval_labels,
            "page_estimate": "5-7",
            "section_count": len(sections),
        },
        "sections": sections,
    }


def build_corpus() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (contracts, policies) for all scenarios — shared policy library per tenant."""
    policies = [_policy_fixture(item, tenant_id=f"{TENANT_PREFIX}-shared") for item in POLICY_LIBRARY]
    contracts: list[dict[str, Any]] = []
    for index, scenario in enumerate(CONTRACT_SCENARIOS):
        tenant = f"{TENANT_PREFIX}-{index:02d}"
        contract = _contract_fixture(scenario, tenant_id=tenant, index=index)
        contracts.append(contract)
    return contracts, policies


def policy_count() -> int:
    return len(POLICY_LIBRARY)


def contract_count() -> int:
    return len(CONTRACT_SCENARIOS)
