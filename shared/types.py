"""
Shared type definitions used across all zones.

These are the CONTRACTS between modules. Every zone imports from here.
Changes to this file affect the entire system - coordinate carefully.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BrainstormState(str, Enum):
    """Conversation state during brainstorm flow."""
    AWAITING_IDEA = "awaiting_idea"
    ASKING_PERSONA = "asking_persona"
    ASKING_CURRENT_SOLUTION = "asking_current_solution"
    ASKING_SWITCH_TRIGGER = "asking_switch_trigger"
    ASKING_MONETIZATION = "asking_monetization"
    ASKING_DISTRIBUTION = "asking_distribution"
    SUMMARIZING = "summarizing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DONE = "done"


class ValidationState(str, Enum):
    """State of the validation pipeline."""
    IDLE = "idle"
    RESEARCHING = "researching"
    ANALYZING = "analyzing"
    REPORTING = "reporting"
    DEVILS_ADVOCATE = "devils_advocate"
    DEEP_DIVE = "deep_dive"
    COMPLETE = "complete"


class ConfidenceLevel(str, Enum):
    """How reliable is a piece of research data."""
    HIGH = "high"          # Multiple independent sources confirm
    MEDIUM = "medium"      # One solid source
    LOW = "low"            # Indirect hints, LLM reasoning from related data
    NO_DATA = "no_data"    # Honestly communicated: nothing found


class ScoreLevel(str, Enum):
    """Qualitative scoring tier - no fake precision."""
    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    CRITICAL = "critical"
    INSUFFICIENT_DATA = "insufficient_data"


class Recommendation(str, Enum):
    """Final verdict on an idea."""
    GO = "go"
    CONDITIONAL_GO = "conditional_go"
    PIVOT = "pivot"
    NO_GO = "no_go"


class TrendVerdict(str, Enum):
    """Overall trend direction derived from multi-signal analysis."""
    RISING = "rising"
    PLATEAU = "plateau"
    DECLINING = "declining"
    EARLY = "early"
    HYPE_PEAK = "hype_peak"
    INSUFFICIENT_DATA = "insufficient_data"


class StatementType(str, Enum):
    """Whether a report statement is a verified fact or an LLM inference."""
    FACT = "fact"
    ESTIMATE = "estimate"


# ---------------------------------------------------------------------------
# Source / Citation
# ---------------------------------------------------------------------------

@dataclass
class Source:
    """A single research source with full provenance."""
    url: str
    name: str
    snippet: str
    retrieved_at: datetime = field(default_factory=datetime.utcnow)
    source_type: str = ""       # e.g. "reddit", "hackernews", "searxng", "github"
    extra: dict = field(default_factory=dict)  # upvotes, stars, date_published, etc.


@dataclass
class CitedStatement:
    """A statement in the report linked to its evidence."""
    text: str
    statement_type: StatementType
    confidence: ConfidenceLevel
    sources: list[Source] = field(default_factory=list)
    category: str = ""          # e.g. "market", "competition", "sentiment"


# ---------------------------------------------------------------------------
# Brainstorm
# ---------------------------------------------------------------------------

@dataclass
class BrainstormAnswers:
    """Collected answers from the sokratische Fragen."""
    raw_idea: str = ""
    persona: str = ""
    current_solution: str = ""
    switch_trigger: str = ""
    monetization: str = ""
    distribution: str = ""


@dataclass
class IdeaSummary:
    """Structured summary after brainstorm, confirmed by user."""
    problem_statement: str
    target_audience: str
    solution: str
    monetization: str
    distribution_channel: str
    unfair_advantage: str = ""  # auto-filled from user profile
    raw_answers: BrainstormAnswers = field(default_factory=BrainstormAnswers)


# ---------------------------------------------------------------------------
# Research Results
# ---------------------------------------------------------------------------

@dataclass
class ResearchResult:
    """Output from a single research tool."""
    tool_name: str              # "searxng", "reddit", "hackernews", "github", "producthunt"
    statements: list[CitedStatement] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)
    success: bool = True
    error_message: str = ""
    duration_seconds: float = 0.0


@dataclass
class TrendSignal:
    """A single trend data point from one source."""
    source: str                 # "google_trends", "reddit", "hackernews", "news", "github"
    periods: list[str] = field(default_factory=list)   # e.g. ["2024-Q1", "2024-Q2", ...]
    values: list[float] = field(default_factory=list)   # normalized 0-100
    available: bool = True
    error_message: str = ""


@dataclass
class TrendRadarResult:
    """Aggregated trend analysis from multiple signals."""
    signals: list[TrendSignal] = field(default_factory=list)
    verdict: TrendVerdict = TrendVerdict.INSUFFICIENT_DATA
    verdict_reasoning: str = ""
    chart_image_path: str = ""  # path to generated matplotlib PNG
    sources: list[Source] = field(default_factory=list)


@dataclass
class ResearchBundle:
    """All research results for one idea, bundled together."""
    idea_id: int
    results: list[ResearchResult] = field(default_factory=list)
    trend_radar: TrendRadarResult = field(default_factory=TrendRadarResult)
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    total_sources: int = 0


# ---------------------------------------------------------------------------
# Analysis & Scoring
# ---------------------------------------------------------------------------

@dataclass
class CategoryScore:
    """Score for a single evaluation category."""
    category: str               # "market_demand", "trend_timing", "competition_gap", ...
    level: ScoreLevel
    reasoning: str
    key_sources: list[Source] = field(default_factory=list)


@dataclass
class OutOfBoxIdea:
    """A creative alternative perspective."""
    idea: str
    reasoning: str


@dataclass
class AnalysisResult:
    """Complete analysis of an idea."""
    idea_id: int
    scores: list[CategoryScore] = field(default_factory=list)
    recommendation: Recommendation = Recommendation.NO_GO
    recommendation_reasoning: str = ""
    next_step: str = ""
    out_of_box_ideas: list[OutOfBoxIdea] = field(default_factory=list)
    devils_advocate: DevilsAdvocateResult = field(default=None)  # type: ignore[assignment]


@dataclass
class DevilsAdvocateResult:
    """The devil's advocate output."""
    kill_reason: str = ""
    riskiest_assumption: str = ""
    must_be_true: str = ""
    cheapest_test: str = ""


# Fixup: set proper default after DevilsAdvocateResult is defined
AnalysisResult.__dataclass_fields__["devils_advocate"].default_factory = DevilsAdvocateResult  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class ValidationReport:
    """The complete report sent to the user."""
    idea_id: int
    idea_summary: IdeaSummary
    research: ResearchBundle
    analysis: AnalysisResult
    generated_at: datetime = field(default_factory=datetime.utcnow)
    export_file_path: str = ""  # path to .md export


# ---------------------------------------------------------------------------
# User & Session
# ---------------------------------------------------------------------------

@dataclass
class UserProfile:
    """Persistent user profile that evolves over time."""
    telegram_id: int
    name: str = ""
    skills: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    risk_appetite: str = "moderate"     # conservative / moderate / aggressive
    weekly_hours: float = 0.0
    preferred_stack: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ConversationContext:
    """Tracks the state of an active conversation."""
    telegram_chat_id: int
    brainstorm_state: BrainstormState = BrainstormState.AWAITING_IDEA
    validation_state: ValidationState = ValidationState.IDLE
    current_idea_id: Optional[int] = None
    brainstorm_answers: BrainstormAnswers = field(default_factory=BrainstormAnswers)
    idea_summary: Optional[IdeaSummary] = None
    research_bundle: Optional[ResearchBundle] = None
    analysis_result: Optional[AnalysisResult] = None
    message_history: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

from typing import Callable, Awaitable

ProgressCallback = Callable[[str], Awaitable[None]]
"""async def callback(message: str) -> None  — sends progress updates to Telegram."""
