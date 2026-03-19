"""
Shared constants used across all zones.
"""

# Telegram message length limit
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Brainstorm questions in strategic order
BRAINSTORM_QUESTIONS: dict[str, str] = {
    "awaiting_idea": (
        "Hey! Was hast du im Kopf? "
        "Beschreib mir die Idee in 2-3 Saetzen — muss nicht perfekt sein."
    ),
    "asking_persona": (
        "Wer genau hat dieses Problem? "
        "Beschreib mir eine konkrete Person — Job, Alltag, Frustration."
    ),
    "asking_current_solution": (
        "Wie loesen diese Leute das Problem HEUTE ohne dein Tool? "
        "Und was nervt sie am meisten daran?"
    ),
    "asking_switch_trigger": (
        "Warum wuerden sie ZU DIR wechseln? "
        "Was ist der Moment wo jemand sagt 'das brauch ich JETZT'?"
    ),
    "asking_monetization": (
        "Wie verdienst du damit Geld? "
        "Einmalig, monatlich, nutzungsbasiert — was schwebt dir vor?"
    ),
    "asking_distribution": (
        "Letzte Frage: Wie finden Kunden dein Produkt? "
        "Wo haengen die rum — online, offline, welche Communities?"
    ),
}

# Scoring categories (evaluation dimensions)
SCORING_CATEGORIES = [
    "market_demand",
    "trend_timing",
    "competition_gap",
    "time_to_revenue",
    "feasibility",
    "distribution",
    "founder_fit",
]

SCORING_CATEGORY_LABELS: dict[str, str] = {
    "market_demand": "Markt-Nachfrage",
    "trend_timing": "Trend & Timing",
    "competition_gap": "Wettbewerbs-Luecke",
    "time_to_revenue": "Time-to-Revenue",
    "feasibility": "Machbarkeit",
    "distribution": "Distribution",
    "founder_fit": "Founder-Fit",
}

# Research tool names
RESEARCH_TOOLS = [
    "searxng",
    "reddit",
    "hackernews",
    "github",
    "producthunt",
]

# Research cache TTL in seconds (7 days)
RESEARCH_CACHE_TTL = 7 * 24 * 60 * 60

# Trend analysis time range
TREND_LOOKBACK_QUARTERS = 8  # 2 years

# LLM task routing is defined in llm/client.py TASK_ROUTING
# Claude: devils_advocate, analysis, out_of_box (quality-critical)
# GPT:    brainstorm, summarize, report_format, source_query, research_extract (cost-efficient)

# Progress update messages
PROGRESS_MESSAGES = {
    "research_start": "Validierung laeuft...",
    "market_search": "Suche nach Marktdaten...",
    "competitor_search": "Analysiere Wettbewerber...",
    "sentiment_search": "Pruefe Reddit & HN...",
    "trend_analysis": "Trend-Radar wird erstellt...",
    "business_model": "Berechne Business Model...",
    "analysis": "Analyse laeuft...",
    "report": "Report wird erstellt...",
    "devils_advocate": "Devils Advocate prueft...",
    "source_failed": "{source} nicht erreichbar, nutze alternative Quellen",
}

# Export filename template
EXPORT_FILENAME_TEMPLATE = "IDEAROAST_{idea_name}_{date}.md"
