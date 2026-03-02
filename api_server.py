"""FastAPI backend to expose the investing agent for a web frontend."""

import csv
import io
import re
from typing import List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from investing_agent import InvestingAgent


load_dotenv()


Mode = Literal["market", "research", "recommend", "custom"]


class AnalyzeRequest(BaseModel):
    mode: Mode = "market"
    tickers: List[str] = Field(default_factory=list)
    period: str = "1mo"
    focus: Optional[str] = None
    query: Optional[str] = None
    model: str = "gpt-5-nano"


class AnalyzeResponse(BaseModel):
    mode: Mode
    period: str
    tickers: List[str]
    output: str


class HoldingsExtractResponse(BaseModel):
    tickers: List[str]
    count: int
    symbol_column: Optional[str] = None


app = FastAPI(title="Investing Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalize_tickers(raw_tickers: List[str]) -> List[str]:
    normalized = []
    seen = set()
    for raw in raw_tickers:
        symbol = raw.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    return normalized


def _extract_tickers_from_csv(content: bytes) -> HoldingsExtractResponse:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        return HoldingsExtractResponse(tickers=[], count=0, symbol_column=None)

    normalized_fields = {field.lower().strip(): field for field in reader.fieldnames if field}
    symbol_candidates = []

    for key in normalized_fields:
        if key in {"symbol", "ticker", "tickers"}:
            symbol_candidates.insert(0, normalized_fields[key])
        elif "symbol" in key or "ticker" in key:
            symbol_candidates.append(normalized_fields[key])

    symbol_column = symbol_candidates[0] if symbol_candidates else None
    if symbol_column is None:
        return HoldingsExtractResponse(tickers=[], count=0, symbol_column=None)

    tickers = []
    seen = set()
    symbol_pattern = re.compile(r"^[A-Z][A-Z0-9.\-^=]{0,11}$")

    for row in reader:
        raw_symbol = (row.get(symbol_column) or "").strip().upper()
        if not raw_symbol:
            continue
        if not symbol_pattern.match(raw_symbol):
            continue
        if raw_symbol in {"LONG", "SHORT", "CAD", "USD", "N/A"}:
            continue
        if raw_symbol not in seen:
            seen.add(raw_symbol)
            tickers.append(raw_symbol)

    return HoldingsExtractResponse(
        tickers=tickers,
        count=len(tickers),
        symbol_column=symbol_column,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/extract-holdings", response_model=HoldingsExtractResponse)
async def extract_holdings(file: UploadFile = File(...)) -> HoldingsExtractResponse:
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    result = _extract_tickers_from_csv(content)
    if result.count == 0:
        raise HTTPException(
            status_code=400,
            detail="No ticker symbols found in CSV. Ensure a Symbol/Ticker column exists.",
        )
    return result


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    tickers = _normalize_tickers(request.tickers)

    try:
        agent = InvestingAgent(model=request.model)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to initialize agent: {exc}")

    try:
        if request.mode == "market":
            output = agent.analyze_market(period=request.period)
        elif request.mode == "research":
            if not tickers:
                raise HTTPException(status_code=400, detail="At least one ticker is required for research mode.")
            chunks = []
            for ticker in tickers:
                chunks.append(f"=== {ticker} ===\n{agent.research_stock(ticker, timeframe=request.period)}")
            output = "\n\n".join(chunks)
        elif request.mode == "recommend":
            output = agent.get_recommendations(
                tickers=tickers or None,
                period=request.period,
                focus=request.focus,
            )
        elif request.mode == "custom":
            if not request.query:
                raise HTTPException(status_code=400, detail="A query is required for custom mode.")
            output = agent.custom_query(request.query)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported mode: {request.mode}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Execution failed: {exc}")

    return AnalyzeResponse(
        mode=request.mode,
        period=request.period,
        tickers=tickers,
        output=output,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=True)
