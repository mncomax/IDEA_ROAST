# Idea Roast 🔥

Telegram-basierter AI-Sparringspartner zur systematischen Validierung von Geschaeftsideen.

## Features

- **Sokratisches Brainstorming** — 6 gezielte Fragen schaerfen jede Idee
- **Voice Support** — Sprachnachrichten werden self-hosted transkribiert (faster-whisper)
- **Multi-Source Research** — SearXNG, Reddit, HN, GitHub, ProductHunt (kommt in M2)
- **Trend-Radar** — Multi-Signal Trend-Analyse mit Chart (kommt in M2)
- **Qualitatives Scoring** — Keine Scheinpraezision, klare Empfehlungen (kommt in M3)
- **Devils Advocate** — Aktiver Versuch die Idee zu killen (kommt in M3)
- **Quellenangaben** — Jede Faktenaussage mit Quelle, nie KI-Erfindungen

## Quick Start (Lokal)

```bash
# 1. Repository klonen
git clone <your-repo-url>
cd idea-roast

# 2. .env anlegen
cp .env.example .env
# -> Telegram Bot Token und Anthropic API Key eintragen

# 3. Docker Compose starten
docker compose up -d

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
bot/            # Telegram Handler (Zone A)
modules/        # Business Logic (Zone B)
tools/          # Externe API Clients (Zone C)
llm/            # Claude API + Prompts (Zone D)
db/             # SQLite Schema + Repository (Zone E)
shared/         # Typen, Konstanten, Exceptions (Shared Contracts)
```

## Fortschritt

Siehe [PLAN.md](PLAN.md) fuer den aktuellen Fortschritt aller Meilensteine.
