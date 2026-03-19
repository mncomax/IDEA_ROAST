# Idea Roast — Globaler Fortschrittsplan

Letzte Aktualisierung: 2026-03-19

---

## Meilenstein 1: Fundament + Brainstorm (Woche 1-2)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ✅ | Projektstruktur | Schnell | Verzeichnisse, __init__.py, .gitignore |
| ✅ | Docker Compose | Schnell | Bot + SearXNG + Redis + faster-whisper |
| ✅ | .env.example + Config | Schnell | Settings-Dataclass, Env-Vars |
| ✅ | Shared Contracts | Stark | types.py, constants.py, exceptions.py |
| ✅ | Telegram Bot Grundgeruest | Schnell | main.py, Handler-Architektur, Access Control |
| ✅ | Voice-to-Text | Schnell | whisper.py Client, voice.py Handler |
| ✅ | SQLite Schema + Repository | Schnell | 6 Tabellen, async Repository |
| ✅ | LLM Client | Schnell | Anthropic Wrapper, Model-Routing |
| ✅ | Brainstorm Modul | Stark | Sokratische Fragen, Zusammenfassung |
| ✅ | Handler-Verdrahtung | Stark | /idea Flow komplett mit DB-Speicherung |
| ✅ | Git Init + Erster Push | Schnell | .gitignore, README, PLAN.md |

---

## Meilenstein 2: Research Engine + Trend-Radar (Woche 2-3)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ⬜ | SearXNG Client | Schnell | Tavily-kompatibler async Client |
| ⬜ | Reddit API Client | Schnell | Async, Source-Tracking, Zeitfenster |
| ⬜ | HN Algolia Client | Schnell | Async, Date-Range Queries |
| ⬜ | GitHub Search Client | Schnell | Async, Star/Repo-Analyse |
| ⬜ | ProductHunt Client | Schnell | GraphQL, Launch-Daten |
| ⬜ | Trend-Radar Tool | Stark | pytrends + Multi-Signal + Chart |
| ⬜ | Research Modul | Stark | Parallele Ausfuehrung, 3-Tier Fallback |
| ⬜ | Quellen-System | Schnell | Citations in DB, Inline-Quellen |
| ⬜ | Fortschritts-Updates | Schnell | Echtzeit-Feedback via Telegram |

---

## Meilenstein 3: Analyse + Report (Woche 3-4)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ⬜ | Analyse Modul | Stark | 7-Kategorien Scoring, Business-Logik |
| ⬜ | Out-of-the-Box Prompts | Stark | Querdenker-Phase |
| ⬜ | Report Modul | Stark/Schnell | Telegram-Format + .md Export |
| ⬜ | Devils Advocate | Stark | Kill-the-idea Phase |
| ⬜ | Deep Dive Handler | Schnell | Inline-Buttons, Quellen-Nachfrage |

---

## Meilenstein 4: Memory & Profil (Woche 4-5)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ⬜ | User Profile | Stark | Lernt ueber Zeit |
| ⬜ | Ideen-History | Schnell | Outcome-Tracking |
| ⬜ | Research Cache | Schnell | TTL-basiert |
| ⬜ | Pattern Recognition | Stark | Muster ueber Ideen erkennen |

---

## Meilenstein 5: MiroFish (Woche 5-7)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ⬜ | MiroFish Docker Deploy | Stark | Self-hosted auf Hetzner |
| ⬜ | /simulate Kommando | Stark | Persona-Konfig aus Validierung |
| ⬜ | Disclaimer-System | Schnell | Klare Kennzeichnung |

---

## Meilenstein 6: Deployment & Polish (laufend)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ⬜ | Hetzner CX22 -> CX32 | Schnell | Server-Upgrade |
| ⬜ | Production Deploy | Schnell | Docker Compose, separates Netzwerk |
| ⬜ | Backup-Automation | Schnell | SQLite + Volumes |
| ⬜ | Monitoring | Schnell | Error Handling, Logging |

---

## Legende

- ✅ Erledigt
- 🔄 In Arbeit
- ⬜ Offen
