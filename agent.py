import os
import json
import base64
from email.message import EmailMessage
from datetime import datetime, timezone

from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import cohere

load_dotenv()

# Gmail scopes
## Read Only: list/get messages 
## Compose: Write an email 
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

STATE_PATH = "state.json"
PROFILE_PATH = "profile.txt"


def load_profile(path: str = PROFILE_PATH) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def gmail_service():
    """
    Auth flow:
    - credentials.json: OAuth client secrets downloaded from Google Cloud
    - token.json: stored user token after first run
    """
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_run_utc": None}


def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def list_unread_messages(service, max_results: int = 10):
    """
    Query for unread messages:
      - is:unread
      - optionally: newer_than:1d
    You can tune this based on your needs.
    """
    query = "is:unread newer_than:1d"
    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    return resp.get("messages", [])


def get_message_details(service, msg_id: str) -> dict:
    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="metadata",
        metadataHeaders=["From", "To", "Subject", "Date", "Message-Id"],
    ).execute()

    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    snippet = msg.get("snippet", "")

    return {
        "id": msg_id,
        "threadId": msg.get("threadId"),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "message_id": headers.get("Message-Id", ""),
        "snippet": snippet,
    }


def heuristic_importance(email: dict) -> int:
    """
    Baseline heuristic ranking of an email.
    Used in event of failure of LLM ranking 
    """
    score = 10
    subj = (email.get("subject") or "").lower()
    sender = (email.get("from") or "").lower()

    if any(w in subj for w in ["urgent", "asap", "immediately", "action required"]):
        score += 40
    if any(w in subj for w in ["invoice", "payment", "overdue", "receipt"]):
        score += 25
    if any(w in sender for w in ["boss@", "advisor@", "prof@", "principal@"]):
        score += 20
    if len(email.get("snippet", "")) > 200:
        score += 5

    return max(0, min(100, score))


def llm_rank_and_reply(cohere_client: cohere.Client, fact_profile: str, email: dict) -> dict:
    """
    Returns:
      {
        "importance": 0-100,
        "category": "...",
        "reply_subject": "...",
        "reply_body": "..."
      }
    """
    prompt = f"""
        "You are an email triage and drafting assistant. "
        "You must be concise, professional, and safe. "
        "Never invent commitments (dates, payments, promises). "
        "If unclear, ask 1-2 short clarifying questions in the reply."
  
USER FACT PROFILE:
{fact_profile}

EMAIL:
From: {email["from"]}
Subject: {email["subject"]}
Date: {email["date"]}
Snippet: {email["snippet"]}

TASK:
1) Rate importance from 0-100.
2) Assign a category (e.g., 'school', 'work', 'robotics', 'admin', 'spam-like').
3) Draft a reply the user can review and send.
Output STRICT JSON with keys:
importance (int), category (str), reply_subject (str), reply_body (str)
"""

    
    response = cohere_client.chat(
        model="command-r-08-2024", # best cohere model for email generation 
        message=prompt,
        temperature=0.2, # want some variability in repsonse, not much
    )

    text = response.text.strip()

    try:
        data = json.loads(text)
    except Exception:
        # Safe fallback
        data = {
            "importance": heuristic_importance(email),
            "category": "unknown",
            "reply_subject": f"Re: {email['subject']}".strip(),
            "reply_body": "Thanks for the message—could you share a bit more detail?",
        }

    # Validation / cleanup
    data["importance"] = int(max(0, min(100, data.get("importance", 0))))
    data["reply_subject"] = data.get("reply_subject") or f"Re: {email['subject']}".strip()
    data["reply_body"] = data.get("reply_body") or "Thanks—received."

    return data


def create_draft(service, to_email: str, subject: str, body: str) -> dict:
    """
    Create a Gmail draft with a simple MIME message.
    """
    message = EmailMessage()
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    draft_body = {"message": {"raw": raw}}
    draft = service.users().drafts().create(userId="me", body=draft_body).execute()
    return draft


def parse_from_header(from_header: str) -> str:
    """
    Very rough extraction: if 'Name <email@x.com>' -> 'email@x.com'
    """
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0].strip()
    return from_header.strip()


def main():
    fact_profile = load_profile()
    state = load_state()

    service = gmail_service()

    cohere_key = os.getenv("COHERE_API_KEY")
    cohere_client = cohere.Client(cohere_key) if cohere_key else None

    msgs = list_unread_messages(service, max_results=10)
    if not msgs:
        print("No unread messages found (newer_than:1d).")
        state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return

    emails = [get_message_details(service, m["id"]) for m in msgs]

    results = []
    for e in emails:
        base_score = heuristic_importance(e)

        if cohere_client:
            llm = llm_rank_and_reply(cohere_client, fact_profile, e)
            importance = llm["importance"]
            category = llm["category"]
            reply_subject = llm["reply_subject"]
            reply_body = llm["reply_body"]
        else:
            importance = base_score
            category = "unknown"
            reply_subject = f"Re: {e['subject']}".strip()
            reply_body = "Thanks for the email—received. What would you like me to do next?"

        to_email = parse_from_header(e["from"])

        draft = create_draft(
            service=service,
            to_email=to_email,
            subject=reply_subject,
            body=reply_body,
        )

        results.append(
            {
                "from": e["from"],
                "subject": e["subject"],
                "importance": importance,
                "category": category,
                "draft_id": draft.get("id"),
            }
        )

    # Sort + print summary
    results.sort(key=lambda x: x["importance"], reverse=True)
    print("\n=== Drafts Created (highest importance first) ===")
    for r in results:
        print(f'[{r["importance"]:>3}] ({r["category"]}) {r["subject"]}  <-- {r["from"]}   draft={r["draft_id"]}')

    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


if __name__ == "__main__":
    main()