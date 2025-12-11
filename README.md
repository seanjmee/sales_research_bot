# SalesResearcher

AI-powered sales meeting prep assistant that proactively researches your prospects before meetings.

## Overview

SalesResearcher is a Slack bot that integrates with Google Calendar to automatically detect upcoming sales meetings and generate research briefs on the companies you're meeting with. It uses Claude AI to synthesize company information and provides an interactive Q&A experience.

## Features

### Current (MVP)
- 🔗 **Google Calendar Integration** - OAuth flow to connect user calendars
- 📅 **Proactive Meeting Detection** - Scans calendar every 6 hours for meetings 24-48 hours out
- 🤖 **AI-Powered Research** - Claude Sonnet 4 generates contextual company briefs
- 💬 **Slack-Native Experience** - All interactions happen in Slack
- 🎯 **Smart Company Detection** - Extracts company domains from meeting attendees
- 🔄 **Background Processing** - Celery workers handle research generation asynchronously

### Commands
- `/connect-calendar` - Connect your Google Calendar
- `/upcoming-meetings` - View meetings in next 24-48 hours with research buttons
- `/research [Company Name]` - Manually trigger research on any company

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     EXTERNAL SERVICES                        │
├─────────────────────────────────────────────────────────────┤
│  Google Calendar API  │  Slack API  │  Anthropic API        │
└──────────┬────────────┴──────┬──────┴────────┬──────────────┘
           │                   │               │
┌──────────▼───────────────────▼───────────────▼──────────────┐
│                  FASTAPI + SLACK BOLT APP                    │
│  - Slack event handlers                                      │
│  - Google OAuth callbacks                                    │
│  - Command routing                                           │
└────────────────────────┬─────────────────────────────────────┘
                         │
                    ┌────▼────┐
                    │  REDIS  │
                    └────┬────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│                    CELERY WORKERS                            │
│  - Calendar scanner (every 6 hours)                          │
│  - Research generation                                       │
│  - Proactive notifications                                   │
└──────────────────────────────────────────────────────────────┘
```

## Tech Stack

- **Python 3.9+**
- **Slack Bolt SDK** - Slack app framework with Socket Mode
- **Google Calendar API** - Calendar integration
- **Anthropic Claude API** - AI research generation
- **Celery + Redis** - Background job processing
- **Flask** - OAuth callback handling

## Setup

### Prerequisites
- Python 3.9+
- Redis server
- Slack workspace (admin access to install apps)
- Google Cloud project
- Anthropic API key

### 1. Clone and Install Dependencies

```bash
git clone <your-repo>
cd sales-research-bot
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install slack-bolt anthropic google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client celery redis flask python-dotenv
```

### 2. Slack App Setup

1. Go to https://api.slack.com/apps
2. Create new app "From scratch"
3. Enable **Socket Mode**:
   - Socket Mode → Toggle ON
   - Generate App-Level Token with `connections:write` scope
4. Add **Bot Token Scopes**:
   - `chat:write`
   - `im:history`
   - `im:read`
   - `im:write`
   - `app_mentions:read`
5. Enable **Event Subscriptions**:
   - Subscribe to: `message.im`, `app_mention`
6. Enable **Interactivity & Shortcuts**
7. Create **Slash Commands**:
   - `/connect-calendar`
   - `/upcoming-meetings`
   - `/research`
8. Enable **App Home**:
   - Toggle "Messages Tab" ON
9. Install app to workspace

### 3. Google Cloud Setup

1. Go to https://console.cloud.google.com
2. Create new project
3. Enable **Google Calendar API**
4. Create **OAuth 2.0 credentials**:
   - Application type: Web application
   - Authorized redirect URIs: `http://localhost:3000/oauth/callback`
5. Configure **OAuth consent screen**:
   - Add scope: `../auth/calendar.readonly`
   - Add test users (your email)

### 4. Anthropic API

1. Get API key from https://console.anthropic.com
2. Add to `.env` file

### 5. Environment Variables

Create `.env` file:

```bash
# Slack
SLACK_BOT_TOKEN=xoxb-your-token
SLACK_SIGNING_SECRET=your-secret
SLACK_APP_TOKEN=xapp-your-token

# Google
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# Anthropic
ANTHROPIC_API_KEY=sk-ant-your-key
```

### 6. Start Redis

```bash
# Mac
brew services start redis

# Linux
sudo systemctl start redis

# Or use Docker
docker run -d -p 6379:6379 redis
```

### 7. Run the Application

**Terminal 1 - Slack Bot:**
```bash
python app.py
```

**Terminal 2 - Celery Worker:**
```bash
celery -A tasks worker --loglevel=info --beat
```

## Usage

### First Time Setup
1. In Slack, find the SalesResearcher bot in Apps
2. Send `/connect-calendar`
3. Click the OAuth link and authorize Google Calendar access
4. Done! The bot will now scan your calendar every 6 hours

### Daily Workflow
1. Bot automatically detects meetings 24-48 hours out
2. Sends you a Slack message: "Want me to research [Company]?"
3. Click "Yes, research this"
4. Receive AI-generated brief in ~30 seconds
5. Ask follow-up questions in the thread (coming soon)

### Manual Research
- Use `/research Salesforce` to manually research any company
- Use `/upcoming-meetings` to see all upcoming meetings with research buttons

## Data Storage

Currently using JSON files (will migrate to PostgreSQL):
- `user_tokens.json` - OAuth tokens and user credentials
- `notified_meetings.json` - Tracking which meetings have been notified

## Roadmap

### Phase 1 (Current MVP) ✅
- [x] Slack bot with Socket Mode
- [x] Google Calendar OAuth
- [x] Proactive meeting detection
- [x] AI research generation
- [x] Background job processing

### Phase 2 (Next Sprint)
- [ ] Conversational follow-ups (Q&A in threads)
- [ ] Account memory (research history per company)
- [ ] Credit system (free tier: 10 researches/month)
- [ ] Web scraping for richer research
- [ ] PostgreSQL database

### Phase 3 (Production Ready)
- [ ] Deploy to Railway/Render
- [ ] Stripe payment integration
- [ ] Research quality tiers (free vs paid)
- [ ] Better error handling & logging
- [ ] User onboarding flow

### Phase 4 (Scale Features)
- [ ] Team/workspace billing
- [ ] CRM integration (Salesforce, HubSpot)
- [ ] RAG on past meeting notes
- [ ] Custom research templates
- [ ] Slack App Directory listing

## Development

### Project Structure
```
sales-research-bot/
├── app.py              # Main Slack bot application
├── tasks.py            # Celery background tasks
├── .env                # Environment variables (not in git)
├── user_tokens.json    # User OAuth tokens (not in git)
├── notified_meetings.json  # Meeting notification tracking
└── README.md
```

### Testing Manually
```bash
# Trigger calendar scan immediately (don't wait 6 hours)
celery -A tasks call tasks.scan_all_calendars
```

### Common Issues

**"dispatch_failed" error**
- Make sure slash commands are registered in Slack app settings
- Reinstall the app after adding new commands

**"Access blocked" on Google OAuth**
- Add your email as a test user in Google Cloud Console
- OAuth consent screen → Test users

**Redis connection error**
- Make sure Redis is running: `redis-cli ping` should return `PONG`

## License

MIT

## Contributing

This is currently a solo MVP project. Contributions welcome once we hit production!

## Contact

Built by Sean Mee
- GitHub: [your-github]
- Email: [your-email]

---

**Current Status:** MVP in active development 🚀
**Last Updated:** December 2024
