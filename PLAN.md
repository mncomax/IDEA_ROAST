# Idea Roast — Globaler Fortschrittsplan

Letzte Aktualisierung: 2026-03-19 (Meilensteine 1-6 abgeschlossen)

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
| ✅ | Multi-Provider LLM | Schnell | Claude + GPT Routing, Fallback |

---

## Meilenstein 2: Research Engine + Trend-Radar (Woche 2-3)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ✅ | SearXNG Client | Schnell | Async Client, JSON API, News + Academic |
| ✅ | Reddit API Client | Schnell | OAuth2, Subreddit-Search, Source-Tracking |
| ✅ | HN Algolia Client | Schnell | Stories + Comments, Date-Range, Points-Sortierung |
| ✅ | GitHub Search Client | Schnell | Repos + Topics, Rate-Limit Handling |
| ✅ | ProductHunt Client | Schnell | GraphQL + SearXNG Fallback |
| ✅ | Trend-Radar Tool | Stark | pytrends + Reddit/HN/News/GitHub Signale + Chart |
| ✅ | Research Modul | Stark | Parallele Ausfuehrung, 3-Tier Fallback, LLM Queries |
| ✅ | Quellen-System | Schnell | Citations in DB via Repository, Source-Tracking |
| ✅ | Fortschritts-Updates | Schnell | Echtzeit-Feedback via Telegram ProgressCallback |

---

## Meilenstein 3: Analyse + Report (Woche 3-4)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ✅ | Analyse Modul | Stark | 7-Kategorien Scoring via LLM, Parsing, Fallbacks |
| ✅ | Out-of-the-Box Prompts | Stark | Kreative Pivots, parallel mit Devils Advocate |
| ✅ | Report Modul | Stark/Schnell | Template-Telegram + .md Export mit Quellenanhang |
| ✅ | Devils Advocate | Stark | Kill-Argument, riskanteste Annahme, billigster Test |
| ✅ | Deep Dive Handler | Schnell | Inline-Buttons, Quellen, Trend-Details, Folgefragen |

---

## Meilenstein 4: Memory & Profil (Woche 4-5)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ✅ | User Profile | Stark | /profile, interaktive Bearbeitung, auto-Lernen aus Ideen |
| ✅ | Ideen-History | Schnell | /history mit Details, /learn Outcome-Tracking, Snapshots |
| ✅ | Research Cache | Schnell | CacheManager, TTL-basiert, Cache-Stats, in Research integriert |
| ✅ | Pattern Recognition | Stark | Muster ueber Ideen, Vergleich, Themen/Staerken/Blind-Spots |

---

## Meilenstein 5: MiroFish (Woche 5-7)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ✅ | MiroFish Simulation Module | Stark | Persona-Generierung + Reaktionen, asyncio.gather |
| ✅ | /simulate Kommando | Stark | Handler, Retry, Summary, Inline-Buttons |
| ✅ | Disclaimer-System | Schnell | Vorab-Hinweis + Block am Ende jeder Simulation |

---

## Meilenstein 6: Deployment & Polish (laufend)

| Status | Task | Cursor-Modell | Details |
|--------|------|---------------|---------|
| ⬜ | Hetzner CX22 -> CX32 | Schnell | Server-Upgrade (manuell) |
| ✅ | Production Deploy | Schnell | docker-compose.prod.yml, Ressource-Limits, deploy.sh |
| ✅ | Backup-Automation | Schnell | backup.sh + cron-setup, Rotation daily/weekly |
| ✅ | Monitoring | Schnell | RotatingFileHandler, BotMetrics, /stats Command |

---

## Legende

- ✅ Erledigt
- 🔄 In Arbeit
- ⬜ Offen
