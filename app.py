import os
import ssl
import certifi
import anthropic
from dotenv import load_dotenv
from slack_bolt import App
from slack_sdk import WebClient
from slack_bolt.adapter.socket_mode import SocketModeHandler
import re


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

app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    client=client
)
# Simple research function
def research_company(company_name):
    """Generate a simple research brief using Claude"""
    prompt = f"""You are a sales research assistant supporting OutSystems sales and presales team in north america. We are pitching a low-code app dev platform for agentic AI workflows and app experiences. Create a brief company overview for {company_name} that would help a sales person prepare for a meeting.

Include the following headings:
- 📈What the company does
- 📊Industry and size (estimate if needed)
- 📰Recent news or developments
- 💡Potential pain points a sales person should know

Keep it concise - 3-4 paragraphs max."""

    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text

# Add this helper function after the research_company function
def convert_markdown_to_slack(text):
    """Convert common markdown to Slack's mrkdwn format"""
    # Convert headers to bold
    text = re.sub(r'^### (.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'*\1*', text, flags=re.MULTILINE)
    
    # Convert markdown links [text](url) to Slack format <url|text>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)
    
    # Convert markdown bold **text** to Slack *text*
    text = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', text)
    
    # Convert markdown italic _text_ (but preserve Slack's _italic_ format)
    # Only convert if not already Slack formatted
    
    # Ensure bullet points use Slack format
    text = re.sub(r'^[\-\*] ', '• ', text, flags=re.MULTILINE)
    
    return text

# Slash command with debug logging
@app.command("/research")
def handle_research_command(ack, say, command):
    print("🎯 /research command received!")  # Debug log
    print(f"Command text: {command['text']}")  # Debug log
    
    ack()
    
    company = command['text'].strip()
    
    if not company:
        say("Please provide a company name: `/research Acme Corp`")
        return
    
    say(f"🔍 Researching {company}... this will take ~30 seconds")
    
    try:
        brief = research_company(company)
        # Convert markdown and send with mrkdwn enabled
        formatted_brief = convert_markdown_to_slack(brief)
        say(f"*Research Brief: {company}*\n\n{formatted_brief}", mrkdwn=True)
    except Exception as e:
        print(f"❌ Error: {str(e)}")  # Debug log
        say(f"❌ Sorry, something went wrong: {str(e)}")

# Respond to "hello"
@app.message("hello")
def say_hello(message, say):
    user = message['user']
    say(f"Hey <@{user}>! 👋 Try `/research Company Name` to test me out.")

# Handle bot mentions - research company or show help
@app.event("app_mention")
def handle_mention(event, say):
    """Handle bot mentions - research company or show help"""
    text = event.get('text', '').strip()
    user = event.get('user')
    
    # Remove bot mention
    text = re.sub(r'<@[^>]+>', '', text).strip().lower()
    
    # Check for help keywords
    help_keywords = ['help', 'hi', 'hello', 'hey', 'what can you do']
    if any(keyword in text for keyword in help_keywords) or not text:
        say(f"Hey <@{user}>! 👋 I'm your sales research assistant.\n\n"
            "*What I can do:*\n"
            "• Research companies - mention me with a company name: `@me Acme Corp`\n"
            "• Use slash command: `/research Company Name`\n\n"
            "*Example:* `@me Microsoft` or `/research Microsoft`")
        return
    
    # Otherwise, treat as company name
    company = text.strip()
    say(f"🔍 Researching {company}... this will take ~30 seconds")
    
    try:
        brief = research_company(company)
        formatted_brief = convert_markdown_to_slack(brief)
        say(f"*Research Brief: {company}*\n\n{formatted_brief}", mrkdwn=True)
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        say(f"❌ Sorry, something went wrong: {str(e)}")

if __name__ == "__main__":
    print("⚡️ Bot is running in Socket Mode!")
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()