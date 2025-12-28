# Olamda nima gap?

A Telegram bot that curates feel-good human stories from around the world, translates them to Uzbek, and publishes to a Telegram channel with human approval.

## Features

- Fetches stories from RSS feeds and Reddit
- AI-powered classification to filter feel-good content
- Automatic translation/retelling in Uzbek using Gemini
- Admin approval workflow via Telegram
- Scheduled publishing with rate limiting
- Docker deployment ready

## Setup

### Prerequisites

- Python 3.11+
- Docker (for deployment)
- Telegram Bot Token (from @BotFather)
- Google Gemini API key

### Local Development

```bash
# Clone the repository
git clone https://github.com/Bittabola/nima-gap.git
cd nima-gap

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Run
python -m src.main
```

### Docker Deployment

```bash
# Clone and configure
git clone https://github.com/Bittabola/nima-gap.git
cd nima-gap
cp .env.example .env
# Edit .env with your credentials

# Build and run
docker compose -f docker/docker-compose.yml up -d

# View logs
docker compose -f docker/docker-compose.yml logs -f

# Stop
docker compose -f docker/docker-compose.yml down
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_CHANNEL_ID` | Yes | Channel to publish to (@username or ID) |
| `TELEGRAM_ADMIN_ID` | Yes | Your Telegram user ID |
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `GEMINI_MODEL` | No | Gemini model to use (default: gemini-1.5-flash) |
| `DATABASE_PATH` | No | SQLite database path (default: data/olamda.db) |
| `FETCH_INTERVAL_HOURS` | No | Hours between fetches (default: 3) |
| `PUBLISH_GAP_MINUTES` | No | Minutes between publishes (default: 60) |
| `MAX_NEW_ARTICLES_PER_FETCH` | No | Max articles to process per fetch cycle (default: 10) |
| `LOG_LEVEL` | No | Logging level (default: INFO) |

### Adding Sources

Edit `config/sources.yaml`:

```yaml
sources:
  # RSS feed
  - name: "Source Name"
    url: "https://example.com/feed"
    type: rss

  # Reddit subreddit
  - name: "r/subreddit"
    subreddit: "subreddit"
    type: reddit
```

## Bot Commands

- `/start` - Welcome message
- `/status` - Show pending/approved counts
- `/fetch` - Trigger manual fetch

## License

MIT
