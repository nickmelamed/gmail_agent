# Gmail Inbox Rank-and-Reply Agent

This repository contains a local, human-in-the-loop AI agent that
prioritizes unread emails and generates draft replies using an LLM
(Cohere).

The goal is to reduce inbox time by automating your email-checking
experience into a simple review-and-send workflow.

------------------------------------------------------------------------

## What it does

When run, the agent:

1.  Authenticates to Gmail using OAuth
2.  Pulls unread emails (recent by default)
3.  Scores and ranks emails by importance
4.  Categorizes each email (work / school / admin / etc.)
5.  Generates a draft reply using a user fact profile
6.  Saves the reply as a Gmail Draft for human review

The agent supports multiple runtime flags for safe testing and debugging
(see **Runtime Modes** below).

------------------------------------------------------------------------

## Runtime Modes & Flags

The agent behavior can be modified using environment variables.

### DRY_RUN

    DRY_RUN=1

-   No Gmail drafts are created.
-   Prioritization and generated replies are printed to the console.
-   Recommended for first-time testing.

When unset or `DRY_RUN=0`, drafts are created in Gmail.

------------------------------------------------------------------------

### LLM_ENABLED

    LLM_ENABLED=0

-   Disables Cohere API usage.
-   The system falls back to heuristic scoring and basic reply logic.
-   Useful for:
    -   Testing without API usage
    -   Debugging
    -   Running without a Cohere key

By default, if `COHERE_API_KEY` is present, LLM mode is enabled.

------------------------------------------------------------------------

### MAX_RESULTS

    MAX_RESULTS=5

-   Controls how many unread emails are fetched.
-   Useful for controlled testing.
-   Defaults to internal value if unset.

------------------------------------------------------------------------

### Heuristic Fallback (Important)

The system is intentionally designed with a **hybrid architecture**:

-   If the LLM is available → AI-driven ranking + reply generation
-   If the LLM fails or is disabled → heuristic fallback

Heuristics: - Detect urgency keywords - Detect known senders - Basic
categorization - Simple template-based responses

This ensures: - Reliability - Lower operational risk - Graceful
degradation if API fails

------------------------------------------------------------------------

## High-level system design

### Why Gmail Drafts (not auto-send)?

-   Prevents accidental or incorrect replies
-   Keeps a human in the loop
-   Aligns with "assistant, not autopilot" philosophy

------------------------------------------------------------------------

### Why a user fact profile?

-   Allows personalization without hard-coding logic
-   Mimics how a real assistant uses context
-   Enables clean separation of logic vs. personal data

------------------------------------------------------------------------

### Why heuristics + LLM?

-   Heuristics provide deterministic fallback
-   LLM improves prioritization nuance and reply quality
-   System remains robust even without API access

------------------------------------------------------------------------

## Repository structure

    main_files
    ├── agent.py
    ├── setup.sh
    ├── requirements.txt
    ├── README.md
    ├── profile.txt
    ├── .env.example

    # web-run 
    ├── app.py
    ├── Procfile
    ├── requirements.txt
    ├── Dockerfile  

    # Local-only (NOT committed):
    ├── credentials.json
    ├── token.json
    ├── .env
    ├── state.json

------------------------------------------------------------------------

# Web Setup

There is a web version where users authenticate via OAuth and drafts are
generated server-side.

------------------------------------------------------------------------

## Architecture Overview

-   Flask web app (`web/app.py`)
-   Google OAuth (Web Application client)
-   Firestore (token storage)
-   Cohere API (server-side inference)
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
-   `COHERE_API_KEY`
-   `GOOGLE_OAUTH_CLIENT_JSON`

Optional runtime flags like `DRY_RUN` and `LLM_ENABLED` can also be set
in Cloud Run for controlled behavior during testing.

------------------------------------------------------------------------

# Local Setup

## Requirements

-   Python 3.10+
-   Gmail account
-   Gmail API enabled in Google Cloud
-   Cohere API key (unless running in heuristic-only mode)

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

    COHERE_API_KEY=YOUR_COHERE_API_KEY

Optional flags:

    DRY_RUN=1
    LLM_ENABLED=1
    MAX_RESULTS=5
    GOOGLE_OAUTH_CREDENTIALS=credentials.json

------------------------------------------------------------------------

## Run the agent

    source .venv/bin/activate
    python agent.py

First run: - Browser opens - Authenticate Gmail - `token.json` created

Subsequent runs: - Unread emails processed - Drafts appear in Gmail →
Drafts (unless DRY_RUN=1)

------------------------------------------------------------------------

# Scheduling (Optional)

Use cron to schedule automated runs.

------------------------------------------------------------------------

# Security Notes

Do NOT commit:

-   credentials.json
-   token.json
-   .env
-   state.json

If secrets were committed: - Rotate immediately - Remove from git
history

------------------------------------------------------------------------

# Common Issues

### No unread emails found

Default query:

    is:unread newer_than:1d

Modify in `list_unread_messages()` if needed.

------------------------------------------------------------------------

### LLM not running

Check: - `COHERE_API_KEY` is set - `LLM_ENABLED=1`

If disabled, system falls back to heuristics.

------------------------------------------------------------------------

### Permission errors

Delete `token.json` and re-authenticate.

------------------------------------------------------------------------

# Design Philosophy

This agent is intentionally:

-   Human-in-the-loop
-   Draft-based (not auto-send)
-   Hybrid (LLM + heuristics)
-   Safe by default (DRY_RUN supported)
-   Modular and extensible
