"""
Shared email processing logic used by both agent.py (CLI) and app.py (web).
"""
import base64
from email.message import EmailMessage

from rank_reply import heuristic_importance, llm_rank_and_reply


def is_spam_like(category: str, email: dict) -> bool:
    cat = (category or "").strip().lower()
    if cat in {"spam", "spam-like", "promotion", "promotional", "newsletter"}:
        return True
    if "list-unsubscribe" in (email.get("list_unsubscribe") or "").lower():
        return True
    if any(k in (email.get("precedence") or "").lower() for k in ["bulk", "list", "junk"]):
        return True
    return False


def parse_from_header(from_header: str) -> str:
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0].strip()
    return from_header.strip()


def process_email(claude_client, fact_profile: str, email: dict, min_importance: int = 0) -> dict:
    """
    Score, categorize, and generate a reply for a single email dict.
    Returns a result dict with importance, category, reply_subject, reply_body, should_draft.
    """
    base_score = heuristic_importance(email)

    if claude_client:
        llm = llm_rank_and_reply(claude_client, fact_profile, email)
        importance = max(0, min(100, int(llm.get("importance", base_score))))
        category = (llm.get("category") or "unknown").strip()
        reply_subject = (llm.get("reply_subject") or f"Re: {email.get('subject', '')}").strip()
        reply_body = (llm.get("reply_body") or "Thanks — received.").strip()
        action = llm.get("action", "draft")
    else:
        importance = base_score
        category = "unknown"
        reply_subject = f"Re: {email.get('subject', '')}".strip()
        reply_body = "Thanks for the email — what would you like me to do next?"
        action = "draft"

    should_draft = (
        importance >= min_importance
        and not is_spam_like(category, email)
        and action != "skip"
    )

    return {
        "id": email.get("id"),
        "threadId": email.get("threadId"),
        "from": email.get("from", ""),
        "subject": email.get("subject", ""),
        "importance": importance,
        "category": category,
        "reply_subject": reply_subject,
        "reply_body": reply_body,
        "should_draft": should_draft,
        "action": action,
        "to_email": parse_from_header(email.get("from", "")),
    }


def create_draft(service, to_email: str, subject: str, body: str) -> dict:
    message = EmailMessage()
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
