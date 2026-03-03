"""FastAPI backend to expose the investing agent for a web frontend."""

import asyncio
import csv
import io
import json
import logging
import os
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from logging.handlers import RotatingFileHandler
from pydantic import BaseModel, Field

load_dotenv()
from investing_agent import tools as market_tools


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("investing_agent.api")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_dir = Path(os.getenv("INVESTING_AGENT_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "api.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


LOGGER = _setup_logger()
MAX_TICKER_WORKERS = int(os.getenv("INVESTING_AGENT_MAX_TICKER_WORKERS", "1"))


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


@dataclass
class CitationBook:
    sources: List[Dict[str, str]] = field(default_factory=list)
    _index: Dict[str, int] = field(default_factory=dict)

    def add(self, url: str, label: str = "Source") -> str:
        if not url:
            return ""
        key = url.strip()
        if key not in self._index:
            self.sources.append({"label": label, "url": key})
            self._index[key] = len(self.sources)
        idx = self._index[key]
        return f"[{idx}]({key})"

    def add_many(self, sources: List[Dict[str, str]]) -> str:
        cites = []
        for src in sources or []:
            cites.append(self.add(src.get("url", ""), src.get("label", "Source")))
        return " ".join(c for c in cites if c)

    def bibliography_markdown(self) -> str:
        if not self.sources:
            return ""
        lines = ["## References"]
        for i, source in enumerate(self.sources, start=1):
            lines.append(f"{i}. [{source['label']}]({source['url']})")
        return "\n".join(lines)


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
    LOGGER.info("CSV extraction start bytes=%s", len(content))
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


def _recommendation_from_change(change_pct: Optional[float]) -> str:
    if change_pct is None:
        return "HOLD"
    if change_pct >= 5:
        return "BUY"
    if change_pct <= -5:
        return "SELL"
    return "HOLD"


def _report_header(request: AnalyzeRequest, tickers: List[str]) -> str:
    ticker_text = ", ".join(tickers) if tickers else "N/A"
    return (
        "# Investment Analysis Report\n\n"
        f"**Mode:** {request.mode.title()}  \n"
        f"**Period:** {request.period}  \n"
        f"**Tickers:** {ticker_text}  \n"
        f"**Focus:** {request.focus or 'N/A'}\n"
    )


def _build_market_report(request: AnalyzeRequest, citations: CitationBook, emit: Optional[Callable[[str], None]]) -> str:
    LOGGER.info("Building market report period=%s", request.period)
    if emit:
        emit("Gathering market trend data from web search")

    data = market_tools.fetch_market_trends_data(request.period)
    market_cites = citations.add_many(data.get("sources", []))

    lines = [
        "## Market Overview",
        f"{data.get('overview', 'Market overview unavailable.')} {market_cites}",
        "",
        "| Market | Symbol | Change | Commentary |",
        "|---|---|---:|---|",
    ]

    for item in data.get("indices", []):
        name = item.get("name", "N/A")
        symbol = item.get("symbol", "N/A")
        change = market_tools._fmt_pct(item.get("change_percent"))
        note = item.get("note", "")
        lines.append(f"| {name} | {symbol} | {change} | {note} |")

    lines.append("")
    lines.append("In plain language: this snapshot indicates where risk appetite and volatility are moving right now, which helps frame position sizing and timing.")
    return "\n".join(lines)


def _build_shared_context(
    request: AnalyzeRequest,
    tickers: List[str],
    citations: CitationBook,
    emit: Optional[Callable[[str], None]],
) -> str:
    """Build market + sector context once per report, independent of ticker count."""
    if emit:
        emit("Collecting shared market and sector context")
    LOGGER.info("Shared context start period=%s focus=%s", request.period, request.focus)

    market = market_tools.fetch_market_trends_data(request.period)
    market_cites = citations.add_many(market.get("sources", []))

    sector_name = (request.focus or "technology").strip().lower()
    sector = market_tools.fetch_sector_performance_data(sector_name, tickers=tickers, period=request.period)
    sector_cites = citations.add_many(sector.get("sources", []))

    LOGGER.info(
        "Shared context done period=%s sector=%s market_indices=%s",
        request.period,
        sector.get("sector"),
        len(market.get("indices", [])),
    )

    lines = [
        "## Shared Market Context",
        f"- **Market Overview:** {market.get('overview', 'N/A')} {market_cites}",
        f"- **Sector Focus ({sector.get('sector', sector_name)}):** {sector.get('overview', 'N/A')} {sector_cites}",
        f"- **Sector 1-Month Change:** {market_tools._fmt_pct(sector.get('one_month_change_percent'))}",
    ]
    return "\n".join(lines)


def _build_stock_section(symbol: str, period: str, citations: CitationBook, emit: Optional[Callable[[str], None]]) -> str:
    return _build_stock_section_from_bundle(
        symbol=symbol,
        period=period,
        bundle=_get_stock_bundle(symbol, period, emit),
        citations=citations,
    )


def _get_stock_bundle(symbol: str, period: str, emit: Optional[Callable[[str], None]]) -> Dict[str, Any]:
    LOGGER.info("Collecting stock bundle symbol=%s period=%s", symbol, period)
    if emit:
        emit(f"Collecting price, trend, and fundamentals for {symbol}")
    snapshot = market_tools.fetch_stock_snapshot_data(symbol, period)
    sources = snapshot.get("sources", [])
    return {
        "price": {
            "symbol": symbol,
            "price": snapshot.get("price"),
            "currency": snapshot.get("currency"),
            "open": snapshot.get("open"),
            "high": snapshot.get("day_high"),
            "low": snapshot.get("day_low"),
            "volume": snapshot.get("volume"),
            "change_percent": snapshot.get("change_percent"),
            "latest_trading_day": snapshot.get("latest_trading_day"),
            "summary": "Quote retrieved via OpenAI web search.",
            "sources": sources,
        },
        "history": {
            "symbol": symbol,
            "period": period,
            "start_price": snapshot.get("period_start_price"),
            "end_price": snapshot.get("period_end_price"),
            "high": snapshot.get("period_high"),
            "low": snapshot.get("period_low"),
            "avg_volume": snapshot.get("avg_volume"),
            "change_percent": snapshot.get("period_change_percent"),
            "interpretation": snapshot.get("period_interpretation") or "Trend context via OpenAI web search.",
            "sources": sources,
        },
        "fundamentals": {
            "symbol": symbol,
            "company": snapshot.get("company"),
            "sector": snapshot.get("sector"),
            "industry": snapshot.get("industry"),
            "market_cap": snapshot.get("market_cap"),
            "pe_ratio": snapshot.get("pe_ratio"),
            "dividend_yield": snapshot.get("dividend_yield"),
            "fifty_two_week_high": snapshot.get("fifty_two_week_high"),
            "fifty_two_week_low": snapshot.get("fifty_two_week_low"),
            "recommendation": snapshot.get("recommendation") or "HOLD",
            "rationale": snapshot.get("rationale") or "Fundamental context via OpenAI web search.",
            "sources": sources,
        },
    }


def _build_stock_section_from_bundle(
    symbol: str,
    period: str,
    bundle: Dict[str, Any],
    citations: CitationBook,
) -> str:
    price = bundle.get("price", {})
    history = bundle.get("history", {})
    fundamentals = bundle.get("fundamentals", {})

    price_cites = citations.add_many(price.get("sources", []))
    hist_cites = citations.add_many(history.get("sources", []))
    fund_cites = citations.add_many(fundamentals.get("sources", []))

    change = history.get("change_percent")
    signal = _recommendation_from_change(change)

    if signal == "BUY":
        friendly = "Momentum and context are supportive. Consider accumulating gradually rather than all at once."
    elif signal == "SELL":
        friendly = "Downside pressure looks elevated. Consider reducing exposure or tightening risk controls."
    else:
        friendly = "Signals are mixed. Holding and waiting for confirmation is reasonable."

    lines = [
        f"### {symbol}",
        f"- **Current Price:** {market_tools._fmt_currency(price.get('price'), 4)} {price_cites}",
        f"- **Period Change ({period}):** {market_tools._fmt_pct(change)} {hist_cites}",
        f"- **Range (High/Low):** {market_tools._fmt_currency(history.get('high'))} / {market_tools._fmt_currency(history.get('low'))} {hist_cites}",
        f"- **Average Volume:** {history.get('avg_volume', 'N/A')} {hist_cites}",
        f"- **Valuation Snapshot:** P/E {fundamentals.get('pe_ratio', 'N/A')}, Dividend {fundamentals.get('dividend_yield', 'N/A')} {fund_cites}",
        f"- **Interpretation:** {history.get('interpretation', 'Trend interpretation unavailable.')} {hist_cites}",
        f"- **Recommendation:** **{fundamentals.get('recommendation', signal)}**. {fundamentals.get('rationale', friendly)}",
        f"- **Friendly Guidance:** {friendly}",
    ]
    return "\n".join(lines)


def _build_research_report(request: AnalyzeRequest, tickers: List[str], citations: CitationBook, emit: Optional[Callable[[str], None]]) -> str:
    LOGGER.info("Building research report tickers=%s period=%s", tickers, request.period)
    if not tickers:
        raise HTTPException(status_code=400, detail="At least one ticker is required for research mode.")

    shared_context = _build_shared_context(request, tickers, citations, emit)
    sections = [
        "## Research Findings",
        "This section explains what the data means and how each name fits the current market context.",
        "",
        shared_context,
    ]
    bundles: Dict[str, Dict[str, Any]] = {}

    worker_count = min(MAX_TICKER_WORKERS, max(1, len(tickers)))
    LOGGER.info("Research concurrent fetch start tickers=%s workers=%s", len(tickers), worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_get_stock_bundle, symbol, request.period, emit): symbol for symbol in tickers}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                bundles[symbol] = future.result()
                LOGGER.info("Research bundle ready symbol=%s", symbol)
            except Exception as exc:
                LOGGER.exception("Research bundle failed symbol=%s error=%s", symbol, exc)
                bundles[symbol] = {}

    for symbol in tickers:
        sections.append("")
        sections.append(
            _build_stock_section_from_bundle(
                symbol=symbol,
                period=request.period,
                bundle=bundles.get(symbol, {}),
                citations=citations,
            )
        )
    return "\n".join(sections)


def _build_recommend_report(request: AnalyzeRequest, tickers: List[str], citations: CitationBook, emit: Optional[Callable[[str], None]]) -> str:
    LOGGER.info("Building recommend report tickers=%s period=%s", tickers, request.period)
    symbols = tickers or ["AAPL", "MSFT", "NVDA"]
    if emit:
        emit("Building recommendation report")

    shared_context = _build_shared_context(request, symbols, citations, emit)

    summary_table = [
        "## Recommendation Summary",
        "| Ticker | Action | Trend | Why It Matters |",
        "|---|---|---:|---|",
    ]

    narrative = [
        "",
        "## What This Means",
        "Below is a friendlier interpretation of the numbers and practical next steps:",
        "",
        shared_context,
    ]
    details = ["", "## Detailed Notes"]

    bundles: Dict[str, Dict[str, Any]] = {}
    worker_count = min(MAX_TICKER_WORKERS, max(1, len(symbols)))
    LOGGER.info("Recommend concurrent fetch start tickers=%s workers=%s", len(symbols), worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_get_stock_bundle, symbol, request.period, emit): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                bundles[symbol] = future.result()
                LOGGER.info("Recommend bundle ready symbol=%s", symbol)
            except Exception as exc:
                LOGGER.exception("Recommend bundle failed symbol=%s error=%s", symbol, exc)
                bundles[symbol] = {}

    for symbol in symbols:
        bundle = bundles.get(symbol, {})
        history = bundle.get("history", {})
        fundamentals = bundle.get("fundamentals", {})
        hist_cites = citations.add_many(history.get("sources", []))
        fund_cites = citations.add_many(fundamentals.get("sources", []))

        change = history.get("change_percent")
        action = fundamentals.get("recommendation") or _recommendation_from_change(change)
        why = history.get("interpretation", "Trend context unavailable")

        summary_table.append(
            f"| {symbol} | **{action}** | {market_tools._fmt_pct(change)} | {why} {hist_cites} |"
        )

        narrative.append(
            f"- **{symbol}: {action}** — {fundamentals.get('rationale', 'Action based on current trend and risk balance.')} {fund_cites}"
        )

        details.append(
            _build_stock_section_from_bundle(
                symbol=symbol,
                period=request.period,
                bundle=bundle,
                citations=citations,
            )
        )
        details.append("")

    closing = [
        "## Portfolio Guidance",
        "- Prioritize risk management: scale entries, avoid oversized single-name positions.",
        "- Re-check thesis after major macro events or earnings updates.",
        "- If risk tolerance is low, tilt toward HOLD signals and reduce high-volatility exposure.",
    ]

    return "\n".join(summary_table + narrative + details + closing)


def _build_custom_report(request: AnalyzeRequest, citations: CitationBook, emit: Optional[Callable[[str], None]]) -> str:
    LOGGER.info("Building custom report query=%s", (request.query or "")[:200])
    if not request.query:
        raise HTTPException(status_code=400, detail="A query is required for custom mode.")

    if emit:
        emit("Running custom web research")

    links = market_tools._google_top_links(request.query, limit=3)
    snippets = []
    for link in links:
        cite = citations.add(link, "Custom query source")
        snippets.append(f"- Source {cite}: {link}")

    summary = [
        "## Custom Query Results",
        f"**Question:** {request.query}",
        "",
        "### Source Overview",
        *snippets,
        "",
        "### Analyst Note",
        "Use the cited links to validate current context; web headlines can shift quickly, so prioritize recency and source quality.",
    ]
    return "\n".join(summary)


def _generate_report(
    request: AnalyzeRequest,
    tickers: List[str],
    emit: Optional[Callable[[str], None]] = None,
) -> str:
    LOGGER.info("Generate report start mode=%s period=%s tickers=%s", request.mode, request.period, tickers)
    citations = CitationBook()
    parts = [_report_header(request, tickers)]

    if request.mode == "market":
        parts.append(_build_market_report(request, citations, emit))
    elif request.mode == "research":
        parts.append(_build_research_report(request, tickers, citations, emit))
    elif request.mode == "recommend":
        parts.append(_build_recommend_report(request, tickers, citations, emit))
    elif request.mode == "custom":
        parts.append(_build_custom_report(request, citations, emit))
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported mode: {request.mode}")

    refs = citations.bibliography_markdown()
    if refs:
        parts.append("\n" + refs)

    report = "\n\n".join(parts)
    LOGGER.info("Generate report done mode=%s length=%s refs=%s", request.mode, len(report), len(citations.sources))
    return report


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/extract-holdings", response_model=HoldingsExtractResponse)
async def extract_holdings(file: UploadFile = File(...)) -> HoldingsExtractResponse:
    LOGGER.info("extract_holdings endpoint file=%s", file.filename)
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
    LOGGER.info("analyze endpoint mode=%s period=%s tickers=%s", request.mode, request.period, tickers)
    try:
        output = _generate_report(request, tickers)
    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("analyze endpoint failed mode=%s error=%s", request.mode, exc)
        raise HTTPException(status_code=500, detail=f"Execution failed: {exc}")

    return AnalyzeResponse(
        mode=request.mode,
        period=request.period,
        tickers=tickers,
        output=output,
    )


def _analyze_stream_response(request: AnalyzeRequest):
    tickers = _normalize_tickers(request.tickers)
    LOGGER.info("analyze_stream endpoint mode=%s period=%s tickers=%s", request.mode, request.period, tickers)
    events: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def emit_message(message: str) -> None:
        events.put({"type": "status", "message": message})

    def worker() -> None:
        emit_message("Starting report generation")
        try:
            output = _generate_report(request, tickers, emit=emit_message)
            events.put({"type": "final", "output": output})
        except HTTPException as exc:
            LOGGER.exception("analyze_stream HTTPException mode=%s error=%s", request.mode, exc.detail)
            events.put({"type": "error", "message": exc.detail})
        except Exception as exc:
            LOGGER.exception("analyze_stream failed mode=%s error=%s", request.mode, exc)
            events.put({"type": "error", "message": f"Execution failed: {exc}"})
        finally:
            events.put({"type": "done"})

    threading.Thread(target=worker, daemon=True).start()

    async def event_generator():
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        done = False
        while not done:
            try:
                event = events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue

            if event.get("type") == "done":
                done = True
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    return _analyze_stream_response(request)


@app.get("/api/analyze/stream")
async def analyze_stream_get(
    mode: Mode = Query("market"),
    tickers: str = Query(""),
    period: str = Query("1mo"),
    focus: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    model: str = Query("gpt-5-nano"),
):
    ticker_list = [t.strip() for t in re.split(r"[,\s]+", tickers or "") if t.strip()]
    request = AnalyzeRequest(
        mode=mode,
        tickers=ticker_list,
        period=period,
        focus=focus,
        query=query,
        model=model,
    )
    return _analyze_stream_response(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=True)
