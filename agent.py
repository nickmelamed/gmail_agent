
import os
import json
import base64
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


import cohere
from rank_reply import heuristic_importance, llm_rank_and_reply, load_profile

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


def _cache_key(email: dict) -> str:
    """
    Cache key should change if the email meaningfully changes.
    Metadata-only fetches can vary; include stable-ish fields.
    """
    parts = [
        str(email.get("id", "")),
        str(email.get("threadId", "")),
        str(email.get("message_id", "")),
        str(email.get("from", "")),
        str(email.get("subject", "")),
        str(email.get("date", "")),
        str(email.get("snippet", ""))[:400],  # cap to keep key small
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
    return data if isinstance(data, dict) else None


def _cache_put(state: dict, key: str, data: dict):
    state.setdefault("llm_cache", {})
    state["llm_cache"][key] = {"ts_utc": _utcnow().isoformat(), "data": data}


def _is_spam_like(category: str, email: dict) -> bool:
    """
    Conservative spam-like check: either LLM says spam-like, or heuristic bulk indicators are strong.
    """
    cat = (category or "").strip().lower()
    if cat in {"spam", "spam-like", "promotion", "promotional", "newsletter"}:
        return True

    # If list-unsubscribe/preference flags are present, treat as spam-like for drafting
    if "list-unsubscribe" in (email.get("list_unsubscribe") or "").lower():
        return True

    if any(k in (email.get("precedence") or "").lower() for k in ["bulk", "list", "junk"]):
        return True

    return False


def main():
    fact_profile = load_profile(PROFILE_PATH)
    state = load_state()

    service = gmail_service()

    cohere_key = os.getenv("COHERE_API_KEY")
    cohere_client = cohere.Client(cohere_key) if (cohere_key and not FORCE_HEURISTIC_ONLY) else None

    if not cohere_key:
        print("Warning: COHERE_API_KEY is not set. Falling back to heuristic ranking + generic replies.")
    if FORCE_HEURISTIC_ONLY and cohere_key:
        print("FORCE_HEURISTIC_ONLY=1: Skipping LLM calls; using heuristic ranking + generic replies.")

    msgs = list_unread_messages(service, max_results=MAX_RESULTS)
    if not msgs:
        print("No unread messages found (newer_than:1d).")
        state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return

    emails = [get_message_details(service, m["id"]) for m in msgs]

    results = []
    for e in emails:
        base_score = heuristic_importance(e)

        # LLM with caching
        cache_key = _cache_key(e)
        cached = _cache_get(state, cache_key) if cohere_client else None

        if cached:
            llm = cached
        elif cohere_client:
            llm = llm_rank_and_reply(cohere_client, fact_profile, e)
            _cache_put(state, cache_key, llm)
        else:
            llm = None

        if llm:
            importance = _clamp(_safe_int(llm.get("importance", base_score)), 0, 100)
            category = (llm.get("category") or "unknown").strip()
            reply_subject = (llm.get("reply_subject") or f"Re: {e.get('subject','')}".strip()).strip()
            reply_body = (llm.get("reply_body") or "Thanks—received.").strip()
        else:
            importance = base_score
            category = "unknown"
            reply_subject = f"Re: {e.get('subject','')}".strip()
            reply_body = "Thanks for the email—received. What would you like me to do next?"

        to_email = parse_from_header(e.get("from", ""))

        should_draft = (importance >= MIN_IMPORTANCE_TO_DRAFT) and (not _is_spam_like(category, e))

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
