"""Tests for prompt content: delegation routing keywords, structure, and length."""

from samantha.prompts import DELEGATION_PROMPT, SYSTEM_PROMPT


def test_system_prompt_contains_delegation_routing():
    """SYSTEM_PROMPT must tell the realtime model when to use reason_deeply."""
    prompt = SYSTEM_PROMPT.lower()
    assert "reason_deeply" in prompt
    assert "delegate" in prompt or "delegation" in prompt


def test_system_prompt_lists_delegate_triggers():
    """SYSTEM_PROMPT should mention categories that warrant delegation."""
    prompt = SYSTEM_PROMPT.lower()
    for trigger in ["math", "analysis", "planning", "code"]:
        assert trigger in prompt, f"Missing delegation trigger: {trigger}"


def test_system_prompt_lists_direct_handling():
    """SYSTEM_PROMPT should mention categories to handle directly."""
    prompt = SYSTEM_PROMPT.lower()
    for direct in ["greeting", "conversation", "calendar"]:
        assert direct in prompt, f"Missing direct-handle category: {direct}"


def test_system_prompt_instructs_natural_narration():
    """Prompt should tell the agent to narrate delegation naturally."""
    prompt = SYSTEM_PROMPT.lower()
    assert "think" in prompt or "moment" in prompt


def test_system_prompt_instructs_summarization():
    """Prompt should tell the agent to summarize delegation results for voice."""
    prompt = SYSTEM_PROMPT.lower()
    assert "summarize" in prompt or "concise" in prompt


def test_delegation_prompt_exists_and_nonempty():
    assert isinstance(DELEGATION_PROMPT, str)
    assert len(DELEGATION_PROMPT.strip()) > 50


def test_delegation_prompt_mentions_voice():
    """Delegation prompt should note results are for spoken delivery."""
    prompt = DELEGATION_PROMPT.lower()
    assert "voice" in prompt or "spoken" in prompt


def test_delegation_prompt_mentions_conciseness():
    prompt = DELEGATION_PROMPT.lower()
    assert "concise" in prompt


def test_prompts_reasonable_length():
    """Prompts should stay under 3000 chars to avoid bloating context."""
    assert len(SYSTEM_PROMPT) < 3000, f"SYSTEM_PROMPT too long: {len(SYSTEM_PROMPT)}"
    assert len(DELEGATION_PROMPT) < 1500, f"DELEGATION_PROMPT too long: {len(DELEGATION_PROMPT)}"
