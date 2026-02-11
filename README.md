# Daily Gmail Inbox Checking Agent (Proof of Concept)

This repository contains a local, human-in-the-loop AI agent that prioritizes unread emails and generates draft replies using an LLM (Cohere).

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

### Why a user fact profile?
- Allows personalization without hard-coding logic
- Mimics how a real assistant would use user context

### Why heuristics + LLM?
- Heuristics provide a safe fallback
- LLM improves prioritization and reply quality

---

## Repository structure

```text
main_files
├── agent.py            # Main agent logic
├── setup.sh            # One-command local setup
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── profile.txt         # User fact profile (safe to commit)
├── .env.example        # Environment variable template (safe to commit)

# web-run 
├── app.py # Flask-based UI 
├── Procfile # for auto-start 
├── requirements.txt # for web 
├── Dockerfile # pinning Python version for Cohere  

# Local-only (NOT committed):
├── credentials.json    # Google OAuth client secrets
├── token.json          # Generated after first OAuth run
├── .env                # Local secrets (Cohere key, flags)
├── state.json          # Local run state
```

## Web setup 

There is a web version of this where you only need to set up the OAuth to utilize this service. 


### Architecture Overview

- Flask web app (`web/app.py`)
- Google OAuth (Web Application client)
- Firestore (token storage)
- Cohere API (server-side inference)
- Cloud Run (container hosting)

---

### User Cloud Run 

Click on the following link: 

https://gmail-agent-1029211064550.us-central1.run.app

And approve the usage on your Gmail account when prompted to log in. 

---

### Web Setup for Devs 

If you are interested in reproducing this work, follow the below steps.

#### Prerequisites

- Flask web app (`web/app.py`)
- Google OAuth (Web Application client)
- Firestore (token storage)
- Cohere API (server-side inference)
- Cloud Run (container hosting)

#### OAuth Configuration 

After deployment, add this to your OAuth Client:

**Authorized JavaScript origins**
https://gmail-agent-1029211064550.us-central1.run.app

**Authorized redirect URIs**
https://gmail-agent-1029211064550.us-central1.run.app

#### Deploy to Cloud Run 

Run this line: 

```bash
gcloud run deploy gmail-agent \
  --source ./web \
  --region us-central1 \
  --allow-unauthenticated
```

After deployment, use the URL that is outputted in this format `https://gmail-agent-xxxxx-uc.a.run.app` in your OAuth settings. 

#### Environment Variables 

Go to Cloud Run --> Edit and Deploy New Revision --> Variables and Secrets and set the following keys: 
- FLASK_SECRET_KEY 
- COHERE_API_KEY
- GOOGLE_OAUTH_CLIENT_JSON 

You should store these in Secret Manager for easy access. 

#### Firestore Setup 

1. Go to Firestore in Google Cloud Console 
2. Create database in Native mode
3. Ensure the Cloud Run service account has permission to read/write Firestone (recommended role: Cloud Datastore User)

#### Test 

Visit `https://YOUR_CLOUD_RUN_URL/` and sign in, then go to `/run` for it to work. You can then go to Gmail to check that the drafts are present in your inbox. 

## Local setup

If you prefer to run this locally, you can do so but will need to have a few extra steps to ensure the agent can run. 

### Requirements

- Python 3.10+
- A Google account with Gmail
- A Google Cloud project with the Gmail API enabled
- A Cohere API key

### Clone the repository

```bash
git clone https://github.com/nickmelamed/gmail_agent.git
cd gmail_agent
```

---

### Run the setup script

```bash
./setup.sh
```

This will:
- Create a virtual environment (.venv)
- Install Python dependencies
- Create .env from .env.example if it doesn’t exist

---

### Configure environment variables

Edit the .env file and set at least:

```env
COHERE_API_KEY=YOUR_COHERE_API_KEY
```

Recommended:

```env
DRY_RUN=0
GOOGLE_OAUTH_CREDENTIALS=credentials.json
```

This will ensure the real run happens, and your Google credentials are set to use this app.

---

### Add Google OAuth credentials (local only)

1. Go to Google Cloud Console
2. Enable the Gmail API
3. Create an OAuth Client ID
   - Application type: Desktop App
4. Download the credentials file
5. Save it as:

```
gmail_agent/credentials.json
```

Do not commit this file.

Alternatively, store it elsewhere and point to it:

```bash
export GOOGLE_OAUTH_CREDENTIALS="$HOME/.secrets/gmail_credentials.json"
```

---

### Edit the user fact profile

Open profile.txt and customize it to your needs. 

---

### Run the agent

```bash
source .venv/bin/activate
python agent.py
```

On first run:
- A browser window will open
- Log into Gmail and approve access
- A token.json file will be created locally

After that:
- Unread emails are processed
- Draft replies appear in Gmail → Drafts

---

## Dry run mode (recommended first)

To test without creating Gmail drafts:

```bash
export DRY_RUN=1
python agent.py
```

This prints prioritization and reply content to the console but creates no drafts.

---

## Scheduling (optional)

Once verified locally, you can schedule it to run daily at 8:00am.

### macOS / Linux (cron)

Edit your crontab:

```bash
crontab -e
```

Add:

```cron
0 8 * * * /ABSOLUTE/PATH/gmail_agent/.venv/bin/python /ABSOLUTE/PATH/gmail_agent/agent.py >> /ABSOLUTE/PATH/gmail_agent/agent.log 2>&1
```

---

## Security notes

Do not commit:
- credentials.json
- token.json
- .env
- state.json

If any secret was accidentally committed:
- Rotate it immediately
- Remove it from git history

---

## Common issues

No unread emails found:
- The default query is is:unread newer_than:1d
- Modify this in list_unread_messages() if needed

Permission or scope errors:
- Delete token.json and rerun to re-authenticate

LLM not running:
- Ensure COHERE_API_KEY is set
- The system falls back to heuristics if not

