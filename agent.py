
import os
import json
from typing import Optional
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import anthropic
from rank_reply import load_profile, get_full_body
from core import process_email, create_draft

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

def _debug(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")


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


def _cache_key(email: dict) -> str:
    parts = [
        str(email.get("id", "")),
        str(email.get("threadId", "")),
        str(email.get("message_id", "")),
        str(email.get("from", "")),
        str(email.get("subject", "")),
        str(email.get("date", "")),
        str(email.get("snippet", ""))[:400],
    ]
    return "||".join(parts)


def _cache_get(state: dict, key: str) -> Optional[dict]:
    cache = state.get("llm_cache", {}) or {}
    item = cache.get(key)
    if not item:
        return None

    ts = item.get("ts_utc")
    if not ts:
        return None

    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None

    if _utcnow() - dt > timedelta(hours=CACHE_TTL_HOURS):
        return None

    data = item.get("data")
    # Only accept cached entries in the new process_email result format
    if isinstance(data, dict) and "should_draft" in data:
        return data
    return None


def _cache_put(state: dict, key: str, data: dict):
    state.setdefault("llm_cache", {})
    state["llm_cache"][key] = {"ts_utc": _utcnow().isoformat(), "data": data}


def main():
    fact_profile = load_profile(PROFILE_PATH)
    state = load_state()

    service = gmail_service()

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if (ANTHROPIC_API_KEY and not FORCE_HEURISTIC_ONLY) else None

    if not ANTHROPIC_API_KEY:
        print("Warning: ANTHROPIC_API_KEY is not set. Falling back to heuristic ranking + generic replies.")
    if FORCE_HEURISTIC_ONLY and ANTHROPIC_API_KEY:
        print("FORCE_HEURISTIC_ONLY=1: Skipping LLM calls; using heuristic ranking + generic replies.")

    msgs = list_unread_messages(service, max_results=MAX_RESULTS)
    if not msgs:
        print("No unread messages found (newer_than:1d).")
        state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return

    emails = [get_message_details(service, m["id"]) for m in msgs]

    # Fetch full body for each email
    for e in emails:
        body = get_full_body(service, e["id"])
        e["body"] = body[:3000]

    results = []
    for e in emails:
        cache_key = _cache_key(e)
        cached = _cache_get(state, cache_key) if claude_client else None

        if cached:
            result = cached
        else:
            result = process_email(claude_client, fact_profile, e, min_importance=MIN_IMPORTANCE_TO_DRAFT)
            if claude_client:
                _cache_put(state, cache_key, result)

        to_email = result["to_email"]
        reply_subject = result["reply_subject"]
        reply_body = result["reply_body"]
        importance = result["importance"]
        category = result["category"]
        should_draft = result["should_draft"]

        if DRY_RUN or (not CREATE_DRAFTS) or (not should_draft):
            draft_id = None
            reason = []
            if DRY_RUN:
                reason.append("DRY_RUN")
            if not CREATE_DRAFTS:
                reason.append("CREATE_DRAFTS=0")
            if not should_draft:
                reason.append(f"below_threshold_or_spam(min={MIN_IMPORTANCE_TO_DRAFT})")
            reason_str = ", ".join(reason) if reason else "no_draft"

            print(f"\n--- NO DRAFT CREATED ({reason_str}) ---")
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
                "id": e.get("id"),
                "threadId": e.get("threadId"),
                "from": e.get("from", ""),
                "subject": e.get("subject", ""),
                "importance": importance,
                "category": category,
                "draft_id": draft_id,
            }
        )

    # Sort + print summary
    results.sort(key=lambda x: x["importance"], reverse=True)

    print("\n=== SUMMARY (highest importance first) ===")
    for r in results:
        draft_str = f"draft={r['draft_id']}" if r["draft_id"] else "draft=(none)"
        print(f'[{r["importance"]:>3}] ({r["category"]}) {r["subject"]}  <-- {r["from"]}   {draft_str}')

    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    # Optional run artifact
    if OUTPUT_JSON_PATH:
        try:
            out = {
                "ran_at_utc": state["last_run_utc"],
                "query": QUERY,
                "max_results": MAX_RESULTS,
                "min_importance_to_draft": MIN_IMPORTANCE_TO_DRAFT,
                "create_drafts": CREATE_DRAFTS,
                "dry_run": DRY_RUN,
                "force_heuristic_only": FORCE_HEURISTIC_ONLY,
                "results": results,
            }
            with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            print(f"\nWrote run summary JSON to: {OUTPUT_JSON_PATH}")
        except Exception as ex:
            print(f"Warning: failed to write OUTPUT_JSON_PATH ({OUTPUT_JSON_PATH}): {ex}")

if __name__ == "__main__":
    main()
