# Gmail Inbox Rank-and-Reply Agent

This repository contains a local, human-in-the-loop AI agent that
prioritizes unread emails and generates draft replies using Claude
(Anthropic).

The goal is to reduce inbox time by automating your email-checking
experience into a simple review-and-send workflow.

------------------------------------------------------------------------

## What it does

When run, the agent:

1.  Authenticates to Gmail using OAuth
2.  Pulls unread emails (configurable query, recent by default)
3.  Scores and ranks emails by importance using heuristics + Claude
4.  Categorizes each email (work / personal / finance / etc.)
5.  Generates a draft reply guided by your user profile
6.  Saves the reply as a Gmail Draft for human review

Claude uses tool-calling to triage each email — choosing between
drafting a reply, requesting clarification, or escalating for human
review.

------------------------------------------------------------------------

## Runtime Modes & Flags

Agent behavior is controlled by environment variables.

### DRY_RUN

    DRY_RUN=1

-   No Gmail drafts are created.
-   Prioritization and generated replies are printed to the console.
-   Recommended for first-time testing.

When unset or `DRY_RUN=0`, drafts are created in Gmail.

------------------------------------------------------------------------

### CREATE_DRAFTS

    CREATE_DRAFTS=0

-   Disables draft creation independently of `DRY_RUN`.
-   Useful when you want the run summary printed without touching Gmail.

Default: `1` (drafts are created).

------------------------------------------------------------------------

### FORCE_HEURISTIC_ONLY

    FORCE_HEURISTIC_ONLY=1

-   Skips all Claude API calls even if `ANTHROPIC_API_KEY` is set.
-   Falls back to heuristic scoring and a generic reply body.
-   Useful for testing without API usage or running without a key.

Default: `0` (Claude is used when `ANTHROPIC_API_KEY` is present).

------------------------------------------------------------------------

### MIN_IMPORTANCE_TO_DRAFT

    MIN_IMPORTANCE_TO_DRAFT=40

-   Only emails scored at or above this threshold get a draft.
-   Spam-like emails are always skipped regardless of score.

Default: `0` (all non-spam emails get a draft).

------------------------------------------------------------------------

### MAX_RESULTS

    MAX_RESULTS=10

-   Controls how many unread emails are fetched per run.

Default: `10`.

------------------------------------------------------------------------

### QUERY

    QUERY=is:unread newer_than:2d

-   Overrides the Gmail search query used to fetch emails.

Default: `is:unread newer_than:1d`.

------------------------------------------------------------------------

### CACHE_TTL_HOURS

    CACHE_TTL_HOURS=12

-   How long (in hours) Claude's triage result for a message is cached
    in `state.json` before being re-requested.

Default: `24`.

------------------------------------------------------------------------

### OUTPUT_JSON_PATH

    OUTPUT_JSON_PATH=run_output.json

-   If set, writes a JSON summary of the run (emails processed,
    importance scores, draft IDs) to the specified path.

Default: unset (no file written).

------------------------------------------------------------------------

### DEBUG

    DEBUG=1

-   Prints extra diagnostic logs during the run.

Default: `0`.

------------------------------------------------------------------------

### GOOGLE_OAUTH_CREDENTIALS

    GOOGLE_OAUTH_CREDENTIALS=/path/to/credentials.json

-   Path to the OAuth Desktop client secrets file from Google Cloud.

Default: `credentials.json` (in the working directory).

------------------------------------------------------------------------

### Heuristic Fallback

The system uses a **hybrid architecture**:

-   If Claude is available → AI-driven triage via tool-calling
-   If Claude fails or is disabled → heuristic fallback

Heuristics detect:
- Urgency keywords
- Bulk / promotional signals
- Financial and legal keywords
- Meeting and calendar signals
- Security alerts and OTP codes
- Email recency and sender type

This ensures reliability and graceful degradation if the API is
unavailable.

------------------------------------------------------------------------

## High-level system design

### Claude tool-calling triage

Instead of prompting for free-form JSON, Claude calls one of three
defined tools per email:

| Tool | When used |
|---|---|
| `triage_email` | Normal emails — scores, categorizes, drafts a reply |
| `request_clarification` | Unclear ask or deadline — drafts a clarifying question |
| `escalate` | Contracts, financial commitments, sensitive items — flags for human review |

### Why Gmail Drafts (not auto-send)?

-   Prevents accidental or incorrect replies
-   Keeps a human in the loop
-   Aligns with "assistant, not autopilot" philosophy

### Why a user profile?

-   Allows personalization without hard-coding logic
-   Tone, style rules, availability, and decision rules are all
    configurable in `profile.txt`
-   The profile is parsed by `profile_schema.py` and injected into the
    Claude system prompt as structured XML

### Why heuristics + LLM?

-   Heuristics provide deterministic fallback
-   Claude improves prioritization nuance and reply quality
-   System remains robust even without API access

------------------------------------------------------------------------

## Repository structure

    gmail_agent/
    ├── agent.py           # CLI entry point — auth, fetch, cache, run loop
    ├── core.py            # Shared processing logic (used by agent.py + web/app.py)
    ├── rank_reply.py      # Heuristic scoring + Claude llm_rank_and_reply()
    ├── tools.py           # Claude tool definitions (triage_email, request_clarification, escalate)
    ├── profile_schema.py  # UserProfile dataclass + profile.txt parser
    ├── profile.txt        # User fact profile (name, tone, style rules, decision rules)
    ├── requirements.txt
    ├── setup.sh
    ├── README.md
    ├── .env.example

    web/
    ├── app.py             # Flask web app (Google OAuth, Firestore token storage, /run endpoint)
    ├── requirements.txt
    ├── Dockerfile
    ├── Procfile

    # Local-only (NOT committed):
    ├── credentials.json
    ├── token.json
    ├── .env
    ├── state.json         # Persists last_run_utc + LLM cache

------------------------------------------------------------------------

# Web Setup

There is a web version where users authenticate via OAuth and drafts are
generated server-side. Users can also edit their triage profile in the
browser at `/profile`.

------------------------------------------------------------------------

## Architecture Overview

-   Flask web app (`web/app.py`)
-   Google OAuth (Web Application client)
-   Firestore (token + user profile storage)
-   Claude API / Anthropic SDK (server-side inference)
-   Cloud Run (container hosting)

------------------------------------------------------------------------

## User Cloud Run

Visit:

https://gmail-agent-1029211064550.us-central1.run.app

Authenticate with Gmail and approve access.

------------------------------------------------------------------------

## Web Setup for Developers

### Environment Variables (Cloud Run)

Set:

-   `FLASK_SECRET_KEY`
-   `ANTHROPIC_API_KEY`
-   `GOOGLE_OAUTH_CLIENT_JSON`

Optional:

-   `MIN_IMPORTANCE_TO_DRAFT`

------------------------------------------------------------------------

# Local Setup

## Requirements

-   Python 3.10+
-   Gmail account with Gmail API enabled in Google Cloud
-   Anthropic API key (unless running in `FORCE_HEURISTIC_ONLY` mode)

------------------------------------------------------------------------

## Clone

    git clone https://github.com/nickmelamed/gmail_agent.git
    cd gmail_agent

------------------------------------------------------------------------

## Run setup

    ./setup.sh

------------------------------------------------------------------------

## Configure `.env`

Minimum:

    ANTHROPIC_API_KEY=YOUR_ANTHROPIC_API_KEY

Optional flags:

    DRY_RUN=1
    FORCE_HEURISTIC_ONLY=0
    CREATE_DRAFTS=1
    MIN_IMPORTANCE_TO_DRAFT=0
    MAX_RESULTS=10
    QUERY=is:unread newer_than:1d
    CACHE_TTL_HOURS=24
    OUTPUT_JSON_PATH=
    GOOGLE_OAUTH_CREDENTIALS=credentials.json
    DEBUG=0

------------------------------------------------------------------------

## Run the agent

    source .venv/bin/activate
    python agent.py

First run:
- Browser opens for Gmail authentication
- `token.json` is created

Subsequent runs:
- Unread emails are fetched and triaged
- Drafts appear in Gmail → Drafts (unless `DRY_RUN=1` or
  `CREATE_DRAFTS=0`)
- Run summary printed to console; optionally written to
  `OUTPUT_JSON_PATH`

------------------------------------------------------------------------

# Scheduling (Optional)

Use cron to schedule automated runs.

------------------------------------------------------------------------

# Security Notes

Do NOT commit:

-   credentials.json
-   web_credentials.json
-   token.json
-   .env
-   state.json

If secrets were committed: rotate immediately and remove from git
history.

------------------------------------------------------------------------

# Common Issues

### No unread emails found

Default query: `is:unread newer_than:1d`

Override with the `QUERY` env var or modify `list_unread_messages()` in
`agent.py`.

------------------------------------------------------------------------

### Claude not running

Check:
- `ANTHROPIC_API_KEY` is set
- `FORCE_HEURISTIC_ONLY` is not `1`

If disabled, system falls back to heuristic scoring and generic replies.

------------------------------------------------------------------------

### Permission errors

Delete `token.json` and re-authenticate.

------------------------------------------------------------------------

# Design Philosophy

This agent is intentionally:

-   Human-in-the-loop
-   Draft-based (not auto-send)
-   Hybrid (Claude tool-calling + heuristics)
-   Safe by default (`DRY_RUN` supported)
-   Modular: `core.py` is shared between the CLI and web app
