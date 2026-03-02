# LLM-Based Investing Agent

An investing agent powered by LangChain and OpenAI, with both CLI and web UI support.

## Features

- Market analysis across major indices
- Stock research and recommendations
- Custom query mode
- Web-scraped market data across tools (price, historical, fundamentals, trends)
- Google top-link scraping for broader context
- React + Tailwind web frontend

## Setup

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Install frontend dependencies:
```bash
cd frontend
npm install
cd ..
```

3. Configure API key in `.env`:
```bash
OPENAI_API_KEY=your_key_here
```

## Run (Web App)

1. Start backend API server:
```bash
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

2. Start frontend:
```bash
cd frontend
npm run dev
```

3. Open:
```text
http://localhost:5173
```

## Web UI Input

- A single textbox accepts one or many tickers.
- You can also upload a holdings CSV from the UI and auto-fill tickers.
- Clicking `Run Analysis` navigates to a dedicated streaming page.
- The streaming page shows live backend/tool updates and final output.
- Examples:
  - `AAPL`
  - `AAPL, MSFT, NVDA`
  - `AAPL MSFT NVDA`

## CLI Usage

### Market Analysis
```bash
python main.py --mode market --period 1mo
```

### Stock Research
```bash
python main.py --mode research --tickers AAPL GOOGL MSFT --period 1mo
```

### Recommendations
```bash
python main.py --mode recommend --tickers AAPL TSLA NVDA --period 1mo
```

### Custom Query
```bash
python main.py --mode custom --query "What are the best dividend stocks in technology?"
```

## API Endpoint

- `POST /api/analyze`
- `POST /api/analyze/stream` (SSE stream of status + tool events + final output)
- `POST /api/extract-holdings` (multipart upload with `file`)

Request body:
```json
{
  "mode": "recommend",
  "tickers": ["AAPL", "MSFT"],
  "period": "1mo",
  "focus": "growth",
  "query": null,
  "model": "gpt-5-nano"
}
```

## Disclaimer

For educational and research use only. This is not financial advice.
