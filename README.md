# Idea Roast 🔥

Telegram-basierter AI-Sparringspartner zur systematischen Validierung von Geschaeftsideen.

## Features

- **Sokratisches Brainstorming** — Adaptive Fragen schaerfen jede Idee
- **Voice Support** — Sprachnachrichten werden self-hosted transkribiert (faster-whisper)
- **Multi-Source Research** — SearXNG, Reddit, HN, GitHub, ProductHunt parallel
- **Trend-Radar** — Multi-Signal Trend-Analyse mit Chart-Generierung
- **7-Kategorien Scoring** — Qualitative Bewertung mit klaren Empfehlungen
- **Devils Advocate** — Aktiver Versuch die Idee zu killen + billigster Validierungstest
- **Out-of-the-Box Ideen** — Kreative Pivots und unerwartete Perspektiven
- **Persona-Simulation** — KI-generierte Kundenreaktionen als Denkanstoesse
- **Report-Export** — Telegram-Nachricht + Markdown-Datei zum Download
- **Quellenangaben** — Jede Faktenaussage mit Quelle, nie KI-Erfindungen
- **User Profile** — Lernt ueber Zeit Staerken, Branchen, Praeferenzen
- **Ideen-History** — Outcome-Tracking, Muster-Erkennung ueber alle Ideen
- **Research Cache** — TTL-basiertes Caching, spart API-Aufrufe bei Re-Validierung

## Quick Start (Lokal)

```bash
# 1. Repository klonen
git clone <your-repo-url>
cd idea-roast

# 2. .env anlegen
cp .env.example .env
# -> Telegram Bot Token und Anthropic API Key eintragen

# 3. Docker Compose starten (Development)
docker compose up -d

# 3b. Production (mit Ressource-Limits)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 4. Logs pruefen
docker compose logs -f bot
```

## Lokal ohne Docker (Entwicklung)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# .env mit lokalen Werten anlegen (DATABASE_PATH anpassen)
python -m bot.main
```

## Projektstruktur

```
bot/            # Telegram Handler + Commands (Zone A)
modules/        # Business Logic — Brainstorm, Research, Analysis, Report, Simulate (Zone B)
tools/          # Externe API Clients — SearXNG, Reddit, HN, GitHub, ProductHunt (Zone C)
llm/            # LLM Client + Prompts — Claude + GPT Routing (Zone D)
db/             # SQLite Schema + Repository (Zone E)
shared/         # Typen, Konstanten, Exceptions, Logging, Monitoring (Shared Contracts)
scripts/        # Deploy, Backup, Cron-Setup
```

## Fortschritt

Siehe [PLAN.md](PLAN.md) fuer den aktuellen Fortschritt aller Meilensteine.
