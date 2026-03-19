"""
Custom exceptions used across all zones.
"""


class IdeaRoastError(Exception):
    """Base exception for all Idea Roast errors."""


# ---------------------------------------------------------------------------
# Research errors
# ---------------------------------------------------------------------------

class ResearchError(IdeaRoastError):
    """A research tool failed."""

    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"[{tool_name}] {message}")


class AllSourcesFailedError(IdeaRoastError):
    """Every research source failed for a query."""


class RateLimitError(ResearchError):
    """An external API hit its rate limit."""


# ---------------------------------------------------------------------------
# LLM errors
# ---------------------------------------------------------------------------

class LLMError(IdeaRoastError):
    """Claude API call failed."""


class LLMResponseParsingError(LLMError):
    """Could not parse LLM response into expected structure."""


# ---------------------------------------------------------------------------
# Database errors
# ---------------------------------------------------------------------------

class DatabaseError(IdeaRoastError):
    """Database operation failed."""


class IdeaNotFoundError(DatabaseError):
    """Requested idea ID does not exist."""


# ---------------------------------------------------------------------------
# Voice errors
# ---------------------------------------------------------------------------

class VoiceTranscriptionError(IdeaRoastError):
    """Failed to transcribe voice message."""


# ---------------------------------------------------------------------------
# Validation flow errors
# ---------------------------------------------------------------------------

class InvalidStateError(IdeaRoastError):
    """Operation not allowed in current conversation state."""
