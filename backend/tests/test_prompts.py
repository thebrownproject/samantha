"""Tests for prompt content: delegation routing keywords, memory section, and length."""

from samantha.prompts import DELEGATION_PROMPT, SYSTEM_PROMPT

# -- Memory section tests --


def test_system_prompt_has_memory_section():
    """SYSTEM_PROMPT must contain a dedicated MEMORY section."""
    assert "MEMORY" in SYSTEM_PROMPT


def test_memory_section_requires_search_on_conversation_start():
    """Prompt must instruct agent to search memory at the start of every conversation."""
    prompt = SYSTEM_PROMPT.lower()
    assert "memory_search" in prompt
    assert "start of every conversation" in prompt


def test_memory_section_requires_save_after_exchanges():
    """Prompt must instruct agent to save to memory after meaningful exchanges."""
    prompt = SYSTEM_PROMPT.lower()
    assert "memory_save" in prompt
    assert "do not wait" in prompt


def test_memory_section_lists_save_triggers():
    """Prompt must list specific events that trigger memory_save."""
    prompt = SYSTEM_PROMPT.lower()
    for trigger in ["preference", "personal information", "corrects you", "project"]:
        assert trigger in prompt, f"Missing memory save trigger: {trigger}"


def test_memory_section_requires_proactive_notes():
    """Prompt must instruct proactive note-taking about patterns and habits."""
    prompt = SYSTEM_PROMPT.lower()
    assert "pattern" in prompt
    assert "proactive" in prompt or "note-taker" in prompt


def test_memory_section_forbids_guessing():
    """Prompt must tell agent to search memory before answering user questions."""
    prompt = SYSTEM_PROMPT.lower()
    assert "never guess" in prompt or "never make things up" in prompt


def test_memory_section_emphasizes_importance():
    """Prompt must convey that memory is the agent's most important behavior."""
    prompt = SYSTEM_PROMPT.lower()
    assert "most important" in prompt
    assert "not optional" in prompt


# -- Delegation tests --


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


# -- Delegation prompt tests --


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


# -- Length guards --


def test_prompts_reasonable_length():
    """Prompts should stay under reasonable limits to avoid bloating context."""
    assert len(SYSTEM_PROMPT) < 5000, f"SYSTEM_PROMPT too long: {len(SYSTEM_PROMPT)}"
    assert len(DELEGATION_PROMPT) < 1500, f"DELEGATION_PROMPT too long: {len(DELEGATION_PROMPT)}"
