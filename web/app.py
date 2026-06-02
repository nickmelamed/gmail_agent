import os
import json
from pathlib import Path
from flask import Flask, redirect, request, session, url_for

import anthropic
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.cloud import firestore
from werkzeug.middleware.proxy_fix import ProxyFix

from rank_reply import extract_body
from core import process_email, create_draft as make_draft

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

GOOGLE_OAUTH_CLIENT_JSON = json.loads(os.environ["GOOGLE_OAUTH_CLIENT_JSON"])
_here = Path(__file__).resolve().parent
DEFAULT_PROFILE_PATH = _here / "profile.txt" if (_here / "profile.txt").exists() else _here.parent / "profile.txt"
MIN_IMPORTANCE_TO_DRAFT = int(os.getenv("MIN_IMPORTANCE_TO_DRAFT", "0"))

db = firestore.Client()
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def save_creds(user_key: str, creds: Credentials):
    db.collection("gmail_tokens").document(user_key).set({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    })

def load_creds(user_key: str) -> Credentials | None:
    doc = db.collection("gmail_tokens").document(user_key).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    return Credentials(**data)

def gmail_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds)

def load_user_profile(user_key: str) -> str:
    doc = db.collection("user_profiles").document(user_key).get()
    if doc.exists:
        return doc.to_dict().get("profile_text", "")
    return DEFAULT_PROFILE_PATH.read_text(encoding="utf-8")

def save_user_profile(user_key: str, profile_text: str):
    db.collection("user_profiles").document(user_key).set({"profile_text": profile_text})


@app.get("/")
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gmail Inbox Rank-And-Reply Agent</title>
        <style>
            body {
                margin: 0;
                height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                background-color: #1e3a8a;
                font-family: Arial, sans-serif;
                color: white;
            }
            .container {
                text-align: center;
                background-color: rgba(255, 255, 255, 0.1);
                padding: 50px 70px;
                border-radius: 20px;
                backdrop-filter: blur(8px);
                box-shadow: 0 8px 25px rgba(0, 0, 0, 0.3);
            }
            h1 { font-size: 2.8rem; margin-bottom: 30px; }
            a {
                display: inline-block;
                margin: 15px 0;
                padding: 15px 30px;
                font-size: 1.2rem;
                text-decoration: none;
                color: #1e3a8a;
                background-color: white;
                border-radius: 30px;
                font-weight: bold;
                transition: 0.3s ease;
            }
            a:hover { background-color: #e5e7eb; transform: scale(1.05); }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Gmail Inbox Rank-and-Reply Agent (POC)</h1>
            <a href="/login">Sign in with Google</a><br/>
            <a href="/run">Run Unread Email Triage</a><br/>
            <a href="/profile">Edit Your Profile</a>
        </div>
    </body>
    </html>
    """

@app.get("/login")
def login():
    flow = Flow.from_client_config(
        GOOGLE_OAUTH_CLIENT_JSON,
        scopes=SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True),
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["state"] = state
    return redirect(auth_url)

@app.get("/oauth2callback")
def oauth2callback():
    flow = Flow.from_client_config(
        GOOGLE_OAUTH_CLIENT_JSON,
        scopes=SCOPES,
        state=session["state"],
        redirect_uri=url_for("oauth2callback", _external=True),
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    user_key = session.get("user_key") or request.remote_addr
    session["user_key"] = user_key
    save_creds(user_key, creds)

    return redirect("/")

@app.get("/run")
def run_agent():
    user_key = session.get("user_key") or request.remote_addr
    creds = load_creds(user_key)
    if not creds:
        return redirect("/login")

    svc = gmail_service(creds)
    fact_profile = load_user_profile(user_key)

    resp = svc.users().messages().list(userId="me", q="is:unread newer_than:1d", maxResults=10).execute()
    messages = resp.get("messages", [])

    drafted = 0
    for m in messages:
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="full",
            metadataHeaders=["From", "To", "Subject", "Date", "List-Unsubscribe", "Precedence"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        email = {
            "id": m["id"],
            "threadId": msg.get("threadId"),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "body": extract_body(msg.get("payload", {}))[:3000],
            "list_unsubscribe": headers.get("List-Unsubscribe", ""),
            "precedence": headers.get("Precedence", ""),
        }
        result = process_email(claude, fact_profile, email, min_importance=MIN_IMPORTANCE_TO_DRAFT)
        if result["should_draft"]:
            make_draft(svc, result["to_email"], result["reply_subject"], result["reply_body"])
            drafted += 1

    return f"Drafted {drafted} replies. Check Gmail -> Drafts."


@app.get("/profile")
def profile_get():
    user_key = session.get("user_key") or request.remote_addr
    profile_text = load_user_profile(user_key)
    saved = request.args.get("saved") == "1"
    escaped = profile_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    saved_banner = '<p style="color:#86efac;">Profile saved.</p>' if saved else ""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Edit Your Profile</title>
      <style>
        body {{ font-family: Arial, sans-serif; background: #1e3a8a; color: white;
                display: flex; justify-content: center; padding: 40px; }}
        .card {{ background: rgba(255,255,255,0.1); border-radius: 16px;
                 padding: 40px; width: 680px; backdrop-filter: blur(8px); }}
        h2 {{ margin-top: 0; }}
        textarea {{ width: 100%; height: 380px; padding: 12px; border-radius: 8px;
                    border: none; font-family: monospace; font-size: 0.9rem; box-sizing: border-box; }}
        button {{ margin-top: 16px; padding: 12px 28px; background: white; color: #1e3a8a;
                  border: none; border-radius: 24px; font-weight: bold;
                  font-size: 1rem; cursor: pointer; }}
        a {{ color: #93c5fd; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h2>Your Email Profile</h2>
        <p>This profile is used to personalize reply tone, style, and decision rules.</p>
        {saved_banner}
        <form method="POST" action="/profile">
          <textarea name="profile_text">{escaped}</textarea><br/>
          <button type="submit">Save Profile</button>
        </form>
        <p><a href="/">&#8592; Back to home</a></p>
      </div>
    </body>
    </html>
    """

@app.post("/profile")
def profile_post():
    user_key = session.get("user_key") or request.remote_addr
    profile_text = request.form.get("profile_text", "")
    save_user_profile(user_key, profile_text)
    return redirect("/profile?saved=1")
