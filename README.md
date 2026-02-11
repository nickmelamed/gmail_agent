# Daily Gmail Inbox Checking Agent (Proof of Concept)

This repository contains a local, human-in-the-loop AI agent that checks a Gmail inbox, prioritizes unread emails, and generates draft replies using an LLM (Cohere).

The goal is to reduce inbox time by automating your email-checking experience into a simple review-and-send workflow. 

This is a proof of concept, but it is fully functional and intentionally designed to be safe (no auto-sending).

---

## What it does 

When run, the agent:

1. Authenticates to Gmail using OAuth
2. Pulls unread emails (recent by default)
3. Ranks each email by importance
4. Categorizes the email (work / school / admin / etc.)
5. Generates a draft reply using a user fact profile
6. Saves the reply as a Gmail Draft for human review

A DRY_RUN mode is available to test without creating drafts.

---

## High-level system design

### Why Gmail Drafts (not auto-send)?
- Prevents accidental or incorrect replies
- Keeps a human in the loop
- Makes the system safe for real inboxes

### Why a user fact profile?
- Allows personalization without hard-coding logic
- Easy to edit and reason about
- Mimics how a real assistant would use user context

### Why heuristics + LLM?
- Heuristics provide a safe fallback
- LLM improves prioritization and reply quality
- The system still works if the LLM is unavailable

---

## Repository structure

```text
daily_inbox_agent/
├── agent.py            # Main agent logic
├── setup.sh            # One-command local setup
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── profile.txt         # User fact profile (safe to commit)
├── .env.example        # Environment variable template (safe to commit)

# Local-only (NOT committed):
├── credentials.json    # Google OAuth client secrets
├── token.json          # Generated after first OAuth run
├── .env                # Local secrets (Cohere key, flags)
├── state.json          # Local run state
'```