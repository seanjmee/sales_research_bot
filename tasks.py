import os
import json
import re
import ssl
import certifi
from datetime import datetime, timedelta
from celery import Celery
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from slack_bolt import App
from slack_sdk import WebClient
from dotenv import load_dotenv
import anthropic

load_dotenv()

# Celery config
celery = Celery('tasks', broker='redis://localhost:6379/0')
celery.conf.beat_schedule = {
    'scan-calendars-every-6-hours': {
        'task': 'tasks.scan_all_calendars',
        'schedule': 21600.0,  # 6 hours in seconds
    },
}

# Create SSL context with certifi certificates
ssl_context = ssl.create_default_context(cafile=certifi.where())

# Create WebClient with SSL context
slack_client = WebClient(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    ssl=ssl_context
)

# Slack client
slack_app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    client=slack_client
)
claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def load_tokens():
    try:
        with open('user_tokens.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_tokens(tokens):
    with open('user_tokens.json', 'w') as f:
        json.dump(tokens, f, indent=2)

def load_notified_meetings():
    """Track which meetings we've already notified about"""
    try:
        with open('notified_meetings.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_notified_meetings(notified):
    with open('notified_meetings.json', 'w') as f:
        json.dump(notified, f, indent=2)

def get_meetings_for_user(user_creds):
    """Fetch meetings for a single user"""
    credentials = Credentials(
        token=user_creds['token'],
        refresh_token=user_creds.get('refresh_token'),
        token_uri=user_creds['token_uri'],
        client_id=user_creds['client_id'],
        client_secret=user_creds['client_secret'],
        scopes=user_creds['scopes']
    )
    
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

@celery.task
def trigger_research_with_context(company_name, slack_user_id, meeting_summary, channel_id, thread_ts):
    """Background task to generate research with context tracking"""
    try:
        brief = research_company(company_name)
        # Convert markdown and format for Slack
        formatted_brief = convert_markdown_to_slack(brief)
        
        # Open DM conversation with user
        conversation = slack_app.client.conversations_open(users=[slack_user_id])
        dm_channel_id = conversation['channel']['id']
        
        slack_app.client.chat_postMessage(
            channel=dm_channel_id,
            thread_ts=thread_ts,
            text=f"*Research Brief: {company_name}*\n\n{formatted_brief}\n\n_Meeting: {meeting_summary}_\n\n_💬 Ask me follow-up questions in this thread! (Available for 48 hours)_",
            mrkdwn=True
        )
        
        print(f"✅ Sent research for {company_name} to {slack_user_id}")
    except Exception as e:
        print(f"❌ Error generating research: {e}")
        import traceback
        traceback.print_exc()
        try:
            conversation = slack_app.client.conversations_open(users=[slack_user_id])
            dm_channel_id = conversation['channel']['id']
            slack_app.client.chat_postMessage(
                channel=dm_channel_id,
                thread_ts=thread_ts,
                text=f"❌ Sorry, couldn't generate research for {company_name}: {str(e)}"
            )
        except Exception as send_error:
            print(f"❌ Could not send error message to user: {send_error}")

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

@celery.task
def scan_all_calendars():
    """Scan all connected calendars and send proactive notifications"""
    print("🔍 Scanning all user calendars...")
    
    tokens = load_tokens()
    notified = load_notified_meetings()
    
    for state, data in tokens.items():
        if 'credentials' not in data or 'slack_user_id' not in data:
            continue
        
        slack_user_id = data['slack_user_id']
        user_creds = data['credentials']
        
        try:
            meetings = get_meetings_for_user(user_creds)
            
            for event in meetings:
                event_id = event.get('id')
                summary = event.get('summary', 'No title')
                start = event['start'].get('dateTime', event['start'].get('date'))
                attendees = event.get('attendees', [])
                
                # Check if we've already notified about this meeting
                notification_key = f"{slack_user_id}_{event_id}"
                if notification_key in notified:
                    continue
                
                # Extract external domains
                external_domains = set()
                for attendee in attendees:
                    email = attendee.get('email', '')
                    if '@' in email and 'gmail.com' not in email:
                        domain = email.split('@')[1]
                        external_domains.add(domain)
                
                if not external_domains:
                    continue  # Skip meetings without external attendees
                
                # Send proactive notification
                company = list(external_domains)[0].replace('.com', '').replace('.', ' ').title()
                
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"📅 You have an upcoming meeting:\n*{summary}*\n{start}\n\nWant me to research {company} for you?"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "🔍 Yes, research this"
                                },
                                "style": "primary",
                                "value": json.dumps({
                                    "meeting_id": event_id,
                                    "summary": summary,
                                    "company": company
                                }),
                                "action_id": "proactive_research"
                            },
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Not this one"
                                },
                                "action_id": "skip_research"
                            }
                        ]
                    }
                ]
                
                slack_app.client.chat_postMessage(
                    channel=slack_user_id,
                    blocks=blocks,
                    text=f"Upcoming meeting: {summary}"
                )
                
                # Mark as notified
                notified[notification_key] = {
                    "meeting_id": event_id,
                    "notified_at": datetime.utcnow().isoformat()
                }
                save_notified_meetings(notified)
                
                print(f"✅ Notified {slack_user_id} about {summary}")
                
        except Exception as e:
            print(f"❌ Error scanning calendar for {slack_user_id}: {e}")
    
    print("✅ Calendar scan complete")

@celery.task
def trigger_research(company_name, slack_user_id, meeting_summary):
    """Background task to generate research"""
    try:
        brief = research_company(company_name)
        # Convert markdown and format for Slack
        formatted_brief = convert_markdown_to_slack(brief)
        
        # Open DM conversation with user
        conversation = slack_app.client.conversations_open(users=[slack_user_id])
        channel_id = conversation['channel']['id']
        
        slack_app.client.chat_postMessage(
            channel=channel_id,
            text=f"*Research Brief: {company_name}*\n\n{formatted_brief}\n\n_Meeting: {meeting_summary}_\n_Ask me follow-up questions in this thread!_",
            mrkdwn=True
        )
        
        print(f"✅ Sent research for {company_name} to {slack_user_id}")
    except Exception as e:
        print(f"❌ Error generating research: {e}")
        import traceback
        traceback.print_exc()
        try:
            # Try to send error message
            conversation = slack_app.client.conversations_open(users=[slack_user_id])
            channel_id = conversation['channel']['id']
            slack_app.client.chat_postMessage(
                channel=channel_id,
                text=f"❌ Sorry, couldn't generate research for {company_name}: {str(e)}"
            )
        except Exception as send_error:
            print(f"❌ Could not send error message to user: {send_error}")