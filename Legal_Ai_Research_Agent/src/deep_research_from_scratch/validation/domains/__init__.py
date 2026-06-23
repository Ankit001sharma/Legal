"""Domain-specific validation adapters."""

from deep_research_from_scratch.validation.domains.generic import GenericDomainAdapter
from deep_research_from_scratch.validation.domains.legal import LegalDomainAdapter

__all__ = ["GenericDomainAdapter", "LegalDomainAdapter"]


def get_domain_adapter(domain: str):
    """Return the validation adapter for the configured domain."""
    if domain == "legal":
        return LegalDomainAdapter()
    return GenericDomainAdapter()
