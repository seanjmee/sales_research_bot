import os
import ssl
import certifi
import anthropic
from dotenv import load_dotenv
from slack_bolt import App
from slack_sdk import WebClient
from slack_bolt.adapter.socket_mode import SocketModeHandler


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

Include:
- What the company does
- Industry and size (estimate if needed)
- Recent news or developments
- Potential pain points a sales person should know

Keep it concise - 3-4 paragraphs max."""

    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text

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
        say(f"*Research Brief: {company}*\n\n{brief}")
    except Exception as e:
        print(f"❌ Error: {str(e)}")  # Debug log
        say(f"❌ Sorry, something went wrong: {str(e)}")

# Respond to "hello"
@app.message("hello")
def say_hello(message, say):
    user = message['user']
    say(f"Hey <@{user}>! 👋 Try `/research Company Name` to test me out.")

if __name__ == "__main__":
    print("⚡️ Bot is running in Socket Mode!")
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()