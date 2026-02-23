import os
import json
import base64
from flask import Flask, redirect, request, session, url_for

import cohere
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.cloud import firestore
from email.message import EmailMessage
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

# Store your OAuth web client JSON in env var (or Secret Manager)
GOOGLE_OAUTH_CLIENT_JSON = json.loads(os.environ["GOOGLE_OAUTH_CLIENT_JSON"])

db = firestore.Client()
co = cohere.Client(os.environ["COHERE_API_KEY"])

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
                background-color: #1e3a8a; /* deep blue */
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

            h1 {
                font-size: 2.8rem;
                margin-bottom: 30px;
            }

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

            a:hover {
                background-color: #e5e7eb;
                transform: scale(1.05);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Gmail Inbox Rank-and-Reply Agent (POC)</h1>
            <a href="/login">Sign in with Google</a><br/>
            <a href="/run">Run Unread Email Triage</a>
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

    # For a POC, key by Google account email if you fetch it,
    # or use a stable session key. Simplest: session-based user key.
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

    # 1) list unread
    resp = svc.users().messages().list(userId="me", q="is:unread newer_than:1d", maxResults=10).execute()
    messages = resp.get("messages", [])

    # 2) for each message: fetch metadata + draft response (reuse your logic)
    drafted = 0
    for m in messages:
        msg = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                         metadataHeaders=["From","Subject","Date"]).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        sender = headers.get("From","")
        subject = headers.get("Subject","")
        snippet = msg.get("snippet","")

        # TODO: call Cohere to rank + reply (same as your agent.py)
        reply_subject = f"Re: {subject}"
        reply_body = f"Thanks for the note — quick Q: what timeline are you thinking?"

        # Create Gmail draft
        em = EmailMessage()
        em["To"] = sender
        em["Subject"] = reply_subject
        em.set_content(reply_body)
        raw = base64.urlsafe_b64encode(em.as_bytes()).decode("utf-8")
        svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        drafted += 1

    return f"Drafted {drafted} replies. Check Gmail → Drafts."