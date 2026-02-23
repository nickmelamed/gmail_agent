
import os
import json
import base64
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


import cohere

# Load .env if present (local dev / teammate setup)
load_dotenv()

# Gmail scopes
## Read Only: list/get messages
## Compose: Create drafts
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

STATE_PATH = "state.json"
PROFILE_PATH = "profile.txt"

# Configurable credential path (recommended so credentials can live outside repo)
CREDS_PATH = os.getenv("GOOGLE_OAUTH_CREDENTIALS", "credentials.json")

# Tags / runtime controls
def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}

DRY_RUN = _env_flag("DRY_RUN", "0")  # do not create drafts; print what would happen
DEBUG = _env_flag("DEBUG", "0")      # extra logs
FORCE_HEURISTIC_ONLY = _env_flag("FORCE_HEURISTIC_ONLY", "0")  # skip LLM even if key exists

CREATE_DRAFTS = _env_flag("CREATE_DRAFTS", "1")  # allows turning off draft creation without DRY_RUN
MIN_IMPORTANCE_TO_DRAFT = int(os.getenv("MIN_IMPORTANCE_TO_DRAFT", "0"))  # e.g., 40
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "10"))
QUERY = os.getenv("QUERY", "is:unread newer_than:1d")

OUTPUT_JSON_PATH = os.getenv("OUTPUT_JSON_PATH", "").strip()  # if set, writes run summary JSON
CACHE_TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "24"))   # cache LLM results per message-id

# Utilities 

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def _clamp(n: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, n))

def _debug(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")

def load_profile(path: str = PROFILE_PATH) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        # Safe default: empty profile
        return ""


def gmail_service():
    """
    Auth flow:
    - credentials.json: OAuth client secrets downloaded from Google Cloud (NOT committed)
    - token.json: stored user token after first run (NOT committed)
    """
    if not os.path.exists(CREDS_PATH):
        raise FileNotFoundError(
            f"Missing Google OAuth credentials file at: {CREDS_PATH}\n"
            "Download OAuth Desktop credentials from Google Cloud Console and place it there, "
            "or set GOOGLE_OAUTH_CREDENTIALS to the file path."
        )

    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # corrupted state -> reset
            return {"last_run_utc": None, "llm_cache": {}}
    return {"last_run_utc": None, "llm_cache": {}}


def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def list_unread_messages(service, max_results: int = 10):
    """
    Query for unread messages.
    Controlled by env var QUERY. Default: "is:unread newer_than:1d"
    """
    resp = (
        service.users()
        .messages()
        .list(userId="me", q=QUERY, maxResults=max_results)
        .execute()
    )
    return resp.get("messages", [])


def get_message_details(service, msg_id: str) -> dict:
    # Pull many metadata headers to strengthen heuristics (e.g., bulk/spam detection)
    hdrs = [
        "From",
        "To",
        "Cc",
        "Subject",
        "Date",
        "Message-Id",
        "Reply-To",
        "List-Unsubscribe",
        "Precedence",
        "Auto-Submitted",
        "X-Auto-Response-Suppress",
    ]

    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=hdrs,
        )
        .execute()
    )

    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    snippet = msg.get("snippet", "")

    return {
        "id": msg_id,
        "threadId": msg.get("threadId"),
        "labelIds": msg.get("labelIds", []),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "reply_to": headers.get("Reply-To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "message_id": headers.get("Message-Id", ""),
        "list_unsubscribe": headers.get("List-Unsubscribe", ""),
        "precedence": headers.get("Precedence", ""),
        "auto_submitted": headers.get("Auto-Submitted", ""),
        "x_auto_response_suppress": headers.get("X-Auto-Response-Suppress", ""),
        "snippet": snippet,
    }

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
    return service.users().drafts().create(userId="me", body=draft_body).execute()


def parse_from_header(from_header: str) -> str:
    """
    Very rough extraction: if 'Name <email@x.com>' -> 'email@x.com'
    """
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0].strip()
    return from_header.strip()


def _sender_domain(addr: str) -> str:
    addr = parse_from_header(addr)
    if "@" in addr:
        return addr.split("@", 1)[1].lower().strip()
    return ""


def _parsed_date(email_date: str) -> Optional[datetime]:
    if not email_date:
        return None
    try:
        dt = parsedate_to_datetime(email_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None
    
# Heuristic as backup for LLM-generated ranking 

def heuristic_importance(email: dict) -> int:
    """
    Robust baseline heuristic importance score (0-100).
    Designed to be a strong fallback if the LLM fails.

    Signals used (metadata-only):
    - Urgency/action keywords
    - Finance/invoice/payment/legal
    - Scheduling/meeting/interview
    - Security/auth/verification codes
    - Bulk/list email indicators (List-Unsubscribe, Precedence, noreply, etc.)
    - Sender relationship cues (reply-to present, personal email providers, etc.)
    - Recency (hours since received)
    - Thread/reply prefixes
    - Length-ish via snippet length
    """
    subj = (email.get("subject") or "").strip()
    subj_l = subj.lower()
    sender = (email.get("from") or "")
    sender_l = sender.lower()
    snippet = (email.get("snippet") or "")
    snippet_l = snippet.lower()
    domain = _sender_domain(sender)

    # Start relatively low for potential for up/down adjustments
    score = 35

    # Downrank bulk/spam-like patterns 
    bulk_hits = 0
    if "list-unsubscribe" in (email.get("list_unsubscribe") or "").lower():
        bulk_hits += 2
    if any(k in (email.get("precedence") or "").lower() for k in ["bulk", "list", "junk"]):
        bulk_hits += 2
    if any(k in (email.get("auto_submitted") or "").lower() for k in ["auto", "generated"]):
        bulk_hits += 1
    if any(k in sender_l for k in ["no-reply", "noreply", "donotreply"]):
        bulk_hits += 2
    if any(k in domain for k in ["mail.", "mailer.", "news.", "marketing.", "bounce."]):
        bulk_hits += 1

    # Promotional language
    if any(k in subj_l for k in ["sale", "deal", "promo", "promotion", "newsletter", "unsubscribe"]):
        bulk_hits += 1

    if bulk_hits >= 4:
        score -= 35
    elif bulk_hits == 3:
        score -= 25
    elif bulk_hits == 2:
        score -= 15
    elif bulk_hits == 1:
        score -= 8

    # Uprank urgency (e.g., "Action Required")
    if any(w in subj_l for w in ["urgent", "asap", "immediately", "action required", "time sensitive", "final notice"]):
        score += 28
    if any(w in snippet_l for w in ["action required", "please respond", "need your response", "reply by", "deadline"]):
        score += 12

    # Uprank Financial/Legal
    if any(w in subj_l for w in ["invoice", "payment", "overdue", "receipt", "bill", "past due", "wire", "refund"]):
        score += 22
    if any(w in subj_l for w in ["contract", "agreement", "legal", "nda", "tax", "1099", "w-2"]):
        score += 18
    if any(w in snippet_l for w in ["amount due", "past due", "suspension", "collections"]):
        score += 10

    # Uprank Scheduling/Meeting/Interview
    if any(w in subj_l for w in ["meeting", "calendar", "schedule", "reschedule", "interview", "availability", "call"]):
        score += 16
    if any(w in snippet_l for w in ["zoom", "google meet", "teams", "calendar invite"]):
        score += 8

    # Security/Verification 
    # These are often important but not reply-worthy; still high importance so user sees them.
    if any(w in subj_l for w in ["verification code", "security alert", "password reset", "new sign-in", "suspicious"]):
        score += 25
    if re.search(r"\b\d{6}\b", snippet) and any(w in snippet_l for w in ["code", "verification", "otp", "2fa"]):
        score += 20

    # Relationship cues
    # Personal providers can indicate 1:1 email; keep modest (can be noisy).
    if domain in {"gmail.com", "icloud.com", "me.com", "mac.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me", "protonmail.com"}:
        score += 6
    # Reply-To sometimes indicates a real human workflow.
    if (email.get("reply_to") or "").strip():
        score += 5

    # Subject prefix (suggesting existing workflow --> higher importance)
    if subj_l.startswith("re:"):
        score += 6
    if subj_l.startswith("fwd:") or subj_l.startswith("fw:"):
        score += 4

    # Recency heuristic (more recent --> more important)
    dt = _parsed_date(email.get("date", ""))
    if dt:
        age = _utcnow() - dt
        if age <= timedelta(hours=3):
            score += 10
        elif age <= timedelta(hours=12):
            score += 6
        elif age <= timedelta(days=1):
            score += 2
        elif age >= timedelta(days=7):
            score -= 8

    # Snippet Length (Shorter snippets likely not as crucial)
    if len(snippet) > 240:
        score += 4
    elif len(snippet) < 40:
        score -= 3

    return _clamp(score, 0, 100)


# LLM Ranking + Replies 


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
You are an email triage and drafting assistant.
You must be concise, professional, and safe.
Never invent commitments (dates, payments, promises).
If unclear, ask 1-2 short clarifying questions in the reply.

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
        model="command-r-08-2024",
        message=prompt,
        temperature=0.2,
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
    if not cohere_key:
        print("Warning: COHERE_API_KEY is not set. Falling back to heuristic ranking + generic replies.")

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

        if DRY_RUN:
            draft_id = None
            print("\n--- DRY RUN (no draft created) ---")
            print(f"To: {to_email}")
            print(f"Subject: {reply_subject}")
            print(f"Category: {category} | Importance: {importance}")
            print(f"Body:\n{reply_body}\n")
        else:
            draft = create_draft(
                service=service,
                to_email=to_email,
                subject=reply_subject,
                body=reply_body,
            )
            draft_id = draft.get("id")

        results.append(
            {
                "from": e["from"],
                "subject": e["subject"],
                "importance": importance,
                "category": category,
                "draft_id": draft_id,
            }
        )

    # Sort + print summary
    results.sort(key=lambda x: x["importance"], reverse=True)

    if DRY_RUN:
        print("\n=== DRY RUN SUMMARY (highest importance first) ===")
    else:
        print("\n=== Drafts Created (highest importance first) ===")

    for r in results:
        draft_str = f"draft={r['draft_id']}" if r["draft_id"] else "draft=(none)"
        print(
            f'[{r["importance"]:>3}] ({r["category"]}) {r["subject"]}  <-- {r["from"]}   {draft_str}'
        )

    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


if __name__ == "__main__":
    main()