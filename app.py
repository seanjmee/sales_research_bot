import os
import ssl
import certifi
import anthropic
from dotenv import load_dotenv
from slack_bolt import App
from slack_sdk import WebClient
from slack_bolt.adapter.socket_mode import SocketModeHandler
import re
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from flask import Flask, request
import threading
import json
from datetime import datetime, timedelta


load_dotenv()

#lets get the anthropic client
claude = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY")
)

# Create SSL context with certifi certificates
ssl_context = ssl.create_default_context(cafile=certifi.where())

# Create WebClient with SSL context
client = WebClient(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    ssl=ssl_context
)

slack_app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    client=client
)

# Flask for OAuth callbacks
flask_app = Flask(__name__)

# Google OAuth config
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
REDIRECT_URI = 'http://localhost:3000/oauth/callback'

# Simple token storage (upgrade to DB later)
def load_tokens():
    try:
        with open('user_tokens.json', 'r') as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_tokens(tokens):
    with open('user_tokens.json', 'w') as f:
        json.dump(tokens, f, indent=2)

# Google Calendar functions
def get_google_auth_url(slack_user_id):
    """Generate Google OAuth URL"""
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    
    if not google_client_id or not google_client_secret:
        raise ValueError("Google OAuth credentials not configured")
    
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": google_client_id,
                "client_secret": google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    # Store state with user_id for callback
    tokens = load_tokens()
    tokens[state] = {'slack_user_id': slack_user_id}
    save_tokens(tokens)
    
    return authorization_url

def get_upcoming_meetings(slack_user_id):
    """Fetch upcoming meetings from Google Calendar"""
    tokens = load_tokens()
    
    # Find user's credentials
    user_creds = None
    for state, data in tokens.items():
        if data.get('slack_user_id') == slack_user_id and 'credentials' in data:
            user_creds = data['credentials']
            break
    
    if not user_creds:
        return None
    
    try:
        credentials = Credentials(
            token=user_creds['token'],
            refresh_token=user_creds.get('refresh_token'),
            token_uri=user_creds['token_uri'],
            client_id=user_creds['client_id'],
            client_secret=user_creds['client_secret'],
            scopes=user_creds['scopes']
        )
        
        # Refresh token if expired
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            # Update stored token
            user_creds['token'] = credentials.token
            tokens = load_tokens()
            for state, data in tokens.items():
                if data.get('slack_user_id') == slack_user_id and 'credentials' in data:
                    data['credentials'] = user_creds
                    break
            save_tokens(tokens)
        
        service = build('calendar', 'v3', credentials=credentials)
        
        # Get events from next 24-48 hours
        now = datetime.utcnow()
        time_min = (now + timedelta(hours=24)).isoformat() + 'Z'
        time_max = (now + timedelta(hours=48)).isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        return events_result.get('items', [])
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return None

# Research function
def research_company(company_name):
    """Generate a simple research brief using Claude"""
    prompt = f"""You are a sales research assistant working for OutSystems, based out of the Boston office. You are an expert Solutions Architect and deep expert on enterprise software development and agentic AI. Create a brief company overview for {company_name} that would help a sales person prepare for a meeting to sell OutSystems' platform.

Include:
- 📈What the company does
- 📊Industry and size (estimate if needed)
- 📰Recent news or developments
- 💡Potential pain points a sales person should know

Keep it concise - 3-4 paragraphs max.

Use markdown formatting for the output."""

    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text

# Store active research contexts (file-based for Celery compatibility)
def load_research_contexts():
    """Load research contexts from file"""
    try:
        with open('research_contexts.json', 'r') as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_research_contexts(contexts):
    """Save research contexts to file"""
    with open('research_contexts.json', 'w') as f:
        json.dump(contexts, f, indent=2)

# In-memory cache (synced with file)
research_contexts = load_research_contexts()

@slack_app.event("message")
def handle_message_events(event, say, client):
    """Handle all messages, including threaded replies"""
    
    # Ignore bot's own messages
    if event.get('bot_id'):
        return
    
    # Only handle threaded messages (replies)
    thread_ts = event.get('thread_ts')
    if not thread_ts:
        return  # Not a thread, ignore
    
    # Check if this thread has an active research context
    context_key = f"{event['channel']}_{thread_ts}"
    
    # Reload contexts from file (in case Celery task updated it)
    research_contexts.update(load_research_contexts())
    
    if context_key not in research_contexts:
        return  # Not a research thread, ignore
    
    context = research_contexts[context_key]
    
    # Check if context has expired (48 hours)
    created_at = datetime.fromisoformat(context['created_at'])
    if datetime.utcnow() - created_at > timedelta(hours=48):
        say(
            "⏰ This research thread has expired (48 hours old). Run a new research to ask more questions!",
            thread_ts=thread_ts
        )
        del research_contexts[context_key]
        save_research_contexts(research_contexts)
        return
    
    # Get user's question
    user_question = event['text']
    
    # Generate response with context
    try:
        conversation_history = context.get('conversation', [])
        
        # Build prompt with original research + conversation history
        messages = [
            {
                "role": "user",
                "content": f"""You are a sales research assistant. Here's the research brief you provided earlier:

{context['research_brief']}

Company: {context['company']}
Meeting: {context.get('meeting_summary', 'N/A')}

Now the user has a follow-up question. Answer it based on the research context and your knowledge."""
            },
            {
                "role": "assistant",
                "content": "I'll answer your follow-up questions based on the research."
            }
        ]
        
        # Add conversation history
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        
        # Add current question
        messages.append({"role": "user", "content": user_question})
        
        # Call Claude
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=messages
        )
        
        answer = response.content[0].text
        
        # Convert markdown and send response in thread
        formatted_answer = convert_markdown_to_slack(answer)
        client.chat_postMessage(
            channel=event['channel'],
            thread_ts=thread_ts,
            text=formatted_answer,
            mrkdwn=True
        )
        
        # Update conversation history
        conversation_history.append({"role": "user", "content": user_question})
        conversation_history.append({"role": "assistant", "content": answer})
        context['conversation'] = conversation_history
        research_contexts[context_key] = context
        save_research_contexts(research_contexts)
        
        print(f"✅ Answered follow-up in thread {thread_ts}")
        
    except Exception as e:
        print(f"❌ Error handling follow-up: {e}")
        client.chat_postMessage(
            channel=event['channel'],
            thread_ts=thread_ts,
            text=f"❌ Sorry, I couldn't answer that: {str(e)}"
        )

# Markdown conversion helper
def convert_markdown_to_slack(text):
    """Convert common markdown to Slack's mrkdwn format"""
    # Split into lines for better processing
    lines = text.split('\n')
    result_lines = []
    
    for line in lines:
        # Convert headers to bold (handle with/without leading spaces and emojis)
        if re.match(r'^\s*###\s+(.+)$', line):
            line = re.sub(r'^\s*###\s+(.+)$', r'*\1*', line)
        elif re.match(r'^\s*##\s+(.+)$', line):
            line = re.sub(r'^\s*##\s+(.+)$', r'*\1*', line)
        elif re.match(r'^\s*#\s+(.+)$', line):
            line = re.sub(r'^\s*#\s+(.+)$', r'*\1*', line)
        else:
            # Convert markdown bold **text** to Slack *text* (but not if already in a header)
            # Handle bold text that might span multiple words
            line = re.sub(r'\*\*([^*\n]+?)\*\*', r'*\1*', line)
            
            # Convert markdown links [text](url) to Slack format <url|text>
            line = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', line)
            
            # Ensure bullet points use Slack format (•)
            line = re.sub(r'^[\-\*]\s+', '• ', line)
            
            # Convert numbered lists to Slack format
            line = re.sub(r'^\d+\.\s+', '• ', line)
        
        result_lines.append(line)
    
    return '\n'.join(result_lines)


@slack_app.action("proactive_research")
def handle_proactive_research(ack, body, client):
    ack()
    
    from tasks import trigger_research_with_context
    
    value = json.loads(body['actions'][0]['value'])
    company = value['company']
    meeting_summary = value['summary']
    slack_user_id = body['user']['id']
    channel_id = body['channel']['id']
    
    # Send initial message
    result = client.chat_postMessage(
        channel=slack_user_id,
        text=f"🔍 Researching {company}... I'll have your brief ready in ~30 seconds"
    )
    
    thread_ts = result['ts']
    
    # Trigger background research with thread context
    trigger_research_with_context.delay(
        company, 
        slack_user_id, 
        meeting_summary,
        channel_id,
        thread_ts
    )

@slack_app.action("skip_research")
def handle_skip_research(ack, say):
    ack()
    say("👍 No problem, skipping this one.")

# Slack commands
@slack_app.command("/research")
def handle_research_command(ack, say, command, client):
    print("🎯 /research command received!")
    ack()
    
    company = command['text'].strip()
    
    if not company:
        say("Please provide a company name: `/research Acme Corp`")
        return
    
    # Send initial message
    say(f"🔍 Researching {company}... this will take ~30 seconds")
    
    try:
        brief = research_company(company)
        
        # Convert markdown and post research in thread
        formatted_brief = convert_markdown_to_slack(brief)
        result = client.chat_postMessage(
            channel=command['channel_id'],
            text=f"*Research Brief: {company}*\n\n{formatted_brief}\n\n_💬 Ask me follow-up questions in this thread! (Available for 48 hours)_",
            mrkdwn=True
        )
        
        # Store context for follow-up questions
        thread_ts = result['ts']
        context_key = f"{command['channel_id']}_{thread_ts}"
        research_contexts[context_key] = {
            'company': company,
            'research_brief': brief,
            'created_at': datetime.utcnow().isoformat(),
            'conversation': []
        }
        save_research_contexts(research_contexts)
        
        print(f"✅ Created research context: {context_key}")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        say(f"❌ Sorry, something went wrong: {str(e)}")

@slack_app.command("/connect-calendar")
def handle_connect_calendar(ack, say, command):
    print("📅 /connect-calendar command received!")
    ack()
    
    try:
        # Check if Google OAuth credentials are configured
        google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
        google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
        
        if not google_client_id or not google_client_secret:
            say("❌ Google Calendar integration is not configured. Please set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` environment variables.")
            return
        
        slack_user_id = command['user_id']
        auth_url = get_google_auth_url(slack_user_id)
        
        say(f"📅 Click here to connect your Google Calendar:\n{auth_url}\n\nI'll be able to see your upcoming meetings and proactively research attendees!")
    except Exception as e:
        print(f"❌ Error in /connect-calendar: {str(e)}")
        import traceback
        traceback.print_exc()
        say(f"❌ Sorry, something went wrong connecting your calendar: {str(e)}")

@slack_app.command("/upcoming-meetings")
def handle_upcoming_meetings(ack, say, command):
    print("📅 /upcoming-meetings command received!")
    ack()
    
    slack_user_id = command['user_id']
    meetings = get_upcoming_meetings(slack_user_id)
    
    if meetings is None:
        say("❌ You haven't connected your calendar yet. Use `/connect-calendar` first!")
        return
    
    if not meetings:
        say("No meetings found in the next 24-48 hours.")
        return
    
    # Build message with interactive buttons
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📅 Your upcoming meetings (next 24-48 hours):"
            }
        }
    ]
    
    for i, event in enumerate(meetings):
        start = event['start'].get('dateTime', event['start'].get('date'))
        summary = event.get('summary', 'No title')
        attendees = event.get('attendees', [])
        
        # Extract external attendee domains (potential companies)
        external_domains = set()
        for attendee in attendees:
            email = attendee.get('email', '')
            if '@' in email and 'gmail.com' not in email:
                domain = email.split('@')[1]
                external_domains.add(domain)
        
        meeting_text = f"*{summary}*\n{start}"
        if attendees:
            meeting_text += f"\n{len(attendees)} attendees"
        if external_domains:
            meeting_text += f"\n Companies: {', '.join(external_domains)}"
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": meeting_text
            },
            "accessory": {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "🔍 Research"
                },
                "value": json.dumps({
                    "meeting_id": event.get('id'),
                    "summary": summary,
                    "domains": list(external_domains)
                }),
                "action_id": f"research_meeting_{i}"
            }
        })
        blocks.append({"type": "divider"})
    
    say(blocks=blocks, text="Your upcoming meetings")

@slack_app.message(re.compile(r"^(hello|hi|hey)$", re.IGNORECASE))
def say_hello(message, say):
    print(f"👋 Hello message received from user {message.get('user')}")
    user = message['user']
    say(f"Hey <@{user}>! 👋\n\nCommands:\n• `/connect-calendar` - Connect Google Calendar\n• `/upcoming-meetings` - See your meetings\n• `/research Company Name` - Research a company")

# Handle bot mentions
@slack_app.event("app_mention")
def handle_mention(event, say):
    """Handle bot mentions - research company or show help"""
    text = event.get('text', '').strip()
    user = event.get('user')
    
    print(f"📢 Bot mentioned by user {user}: {text}")
    
    # Remove bot mention
    text = re.sub(r'<@[^>]+>', '', text).strip().lower()
    
    # Check for help keywords
    help_keywords = ['help', 'hi', 'hello', 'hey', 'what can you do']
    if any(keyword in text for keyword in help_keywords) or not text:
        say(f"Hey <@{user}>! 👋 I'm your sales research assistant.\n\n"
            "*What I can do:*\n"
            "• Research companies - mention me with a company name: `@me Acme Corp`\n"
            "• Use slash command: `/research Company Name`\n"
            "• Connect calendar: `/connect-calendar`\n"
            "• View meetings: `/upcoming-meetings`\n\n"
            "*Example:* `@me Microsoft` or `/research Microsoft`")
        return
    
    # Otherwise, treat as company name
    company = text.strip()
    say(f"🔍 Researching {company}... this will take ~30-60 seconds")
    
    try:
        brief = research_company(company)
        # Convert markdown and send with mrkdwn enabled
        formatted_brief = convert_markdown_to_slack(brief)
        say(f"*Research Brief: {company}*\n\n{formatted_brief}", mrkdwn=True)
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        say(f"❌ Sorry, something went wrong: {str(e)}")

# Flask OAuth callback
@flask_app.route('/oauth/callback')
def oauth_callback():
    state = request.args.get('state')
    code = request.args.get('code')
    
    tokens = load_tokens()
    
    if state not in tokens:
        return "Error: Invalid state", 400
    
    slack_user_id = tokens[state]['slack_user_id']
    
    # Exchange code for tokens
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state
    )
    
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
    # Store credentials
    tokens[state]['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    save_tokens(tokens)
    
    # Notify user in Slack via DM
    try:
        # Open DM conversation with user
        conversation = slack_app.client.conversations_open(users=[slack_user_id])
        channel_id = conversation['channel']['id']
        
        slack_app.client.chat_postMessage(
            channel=channel_id,
            text="✅ Calendar connected! I can now see your upcoming meetings. Use `/upcoming-meetings` to test it out."
        )
    except Exception as e:
        print(f"Error posting to Slack: {e}")
    
    return "✅ Calendar connected! You can close this window and return to Slack."

# Run both Flask and Slack bot
def run_flask():
    flask_app.run(port=3000)

@slack_app.action("research_meeting_0")
@slack_app.action("research_meeting_1")
@slack_app.action("research_meeting_2")
@slack_app.action("research_meeting_3")
@slack_app.action("research_meeting_4")
def handle_research_button(ack, body, say):
    ack()
    
    value = json.loads(body['actions'][0]['value'])
    meeting_summary = value['summary']
    domains = value['domains']
    
    if not domains:
        say(f"❌ Couldn't find a company to research for '{meeting_summary}'. Try `/research Company Name` manually.")
        return
    
    # Research the first company found
    company = domains[0].replace('.com', '').replace('.', ' ').title()
    
    say(f"🔍 Researching {company} for your meeting: *{meeting_summary}*...")
    
    try:
        brief = research_company(company)
        # Convert markdown and send with mrkdwn enabled
        formatted_brief = convert_markdown_to_slack(brief)
        say(f"*Research Brief: {company}*\n\n{formatted_brief}\n\n_Ask me follow-up questions in this thread!_", mrkdwn=True)
    except Exception as e:
        say(f"❌ Sorry, something went wrong: {str(e)}")

if __name__ == "__main__":
    print("⚡️ Bot is running in Socket Mode!")
    print("🌐 Flask OAuth server running on http://localhost:3000")
    
    # Start Flask in separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start Slack bot
    handler = SocketModeHandler(slack_app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()