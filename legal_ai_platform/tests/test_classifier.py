"""Tests for TaskClassifier."""

from legal_ai_platform.orchestration.classifier import TaskClassifier


def test_default_is_research():
    classifier = TaskClassifier()
    assert classifier.classify("What is Section 420 IPC?") == "research"


def test_explicit_task_type_overrides():
    classifier = TaskClassifier()
    assert classifier.classify("anything", explicit_task_type="contract") == "review"


def test_contract_alias_normalizes_to_review():
    classifier = TaskClassifier()
    assert classifier.normalize_task_type("contract") == "review"
    assert classifier.normalize_task_type("compliance") == "review"


def test_review_keyword():
    classifier = TaskClassifier()
    assert classifier.classify("Review this NDA contract clause") == "review"


def test_review_from_context():
    classifier = TaskClassifier()
    assert (
        classifier.classify(
            "",
            context={
                "contract_document_id": "00000000-0000-4000-8000-000000000001",
                "policy_document_ids": ["00000000-0000-4000-8000-000000000002"],
            },
        )
        == "review"
    )


def test_drafting_keyword():
    classifier = TaskClassifier()
    assert classifier.classify("Draft a legal notice for breach") == "drafting"


def test_summary_keyword():
    classifier = TaskClassifier()
    assert classifier.classify("Summarize this judgment") == "summary"


def test_translation_keyword():
    classifier = TaskClassifier()
    assert classifier.classify("Translate this order to Hindi") == "translation"
