import { useMemo, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

const modeDescriptions = {
  market: 'Analyze broad market trends.',
  research: 'Deep research on each ticker provided.',
  recommend: 'Generate buy/sell/hold recommendations.',
  custom: 'Ask a custom investing question.',
}

function parseTickers(rawValue) {
  return rawValue
    .split(/[\s,]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean)
}

export default function App() {
  const [mode, setMode] = useState('recommend')
  const [period, setPeriod] = useState('1mo')
  const [model, setModel] = useState('gpt-5-nano')
  const [tickerInput, setTickerInput] = useState('AAPL, MSFT, NVDA')
  const [focus, setFocus] = useState('growth')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [csvFile, setCsvFile] = useState(null)
  const [uploadingCsv, setUploadingCsv] = useState(false)
  const [csvStatus, setCsvStatus] = useState('')

  const parsedTickers = useMemo(() => parseTickers(tickerInput), [tickerInput])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setResult('')

    if (mode === 'research' && parsedTickers.length === 0) {
      setError('Research mode requires at least one ticker.')
      return
    }

    if (mode === 'custom' && !query.trim()) {
      setError('Custom mode requires a query.')
      return
    }

    const payload = {
      mode,
      period,
      model,
      tickers: parsedTickers,
      focus: focus.trim() || null,
      query: query.trim() || null,
    }

    setLoading(true)
    try {
      const response = await fetch(`${API_BASE}/api/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Request failed')
      }

      setResult(data.output)
    } catch (err) {
      setError(err.message || 'Unexpected error')
    } finally {
      setLoading(false)
    }
  }

  const handleCsvUpload = async () => {
    setError('')
    setCsvStatus('')
    if (!csvFile) {
      setError('Please choose a CSV file first.')
      return
    }

    const formData = new FormData()
    formData.append('file', csvFile)

    setUploadingCsv(true)
    try {
      const response = await fetch(`${API_BASE}/api/extract-holdings`, {
        method: 'POST',
        body: formData,
      })

      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to extract holdings')
      }

      const joined = data.tickers.join(', ')
      setTickerInput(joined)
      setCsvStatus(`Extracted ${data.count} ticker(s) from column: ${data.symbol_column || 'unknown'}.`)
    } catch (err) {
      setError(err.message || 'CSV upload failed')
    } finally {
      setUploadingCsv(false)
    }
  }

  return (
    <main className="min-h-screen bg-gradient-to-br from-brand-100 via-slate-50 to-brand-50 p-4 md:p-8">
      <div className="mx-auto max-w-6xl rounded-3xl border border-brand-100 bg-white/90 p-6 shadow-soft md:p-8">
        <header className="mb-6 flex flex-col gap-3">
          <p className="inline-block w-fit rounded-full bg-brand-100 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-brand-700">
            Investing Agent
          </p>
          <h1 className="text-2xl font-bold text-slate-900 md:text-4xl">Live Market Analysis Dashboard</h1>
          <p className="max-w-3xl text-sm text-slate-600 md:text-base">
            Enter one ticker (like <span className="font-semibold">AAPL</span>) or a list (<span className="font-semibold">AAPL, MSFT, NVDA</span>) and run market analysis through your backend agent.
          </p>
        </header>

        <div className="grid gap-6 md:grid-cols-5">
          <form onSubmit={handleSubmit} className="space-y-4 md:col-span-2">
            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700">Mode</span>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value)}
                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
              >
                <option value="market">Market</option>
                <option value="research">Research</option>
                <option value="recommend">Recommend</option>
                <option value="custom">Custom</option>
              </select>
              <p className="text-xs text-slate-500">{modeDescriptions[mode]}</p>
            </label>

            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700">Ticker Textbox (single or list)</span>
              <textarea
                rows={3}
                value={tickerInput}
                onChange={(e) => setTickerInput(e.target.value)}
                placeholder="AAPL or AAPL,MSFT,NVDA"
                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
              />
              <p className="text-xs text-slate-500">Parsed: {parsedTickers.length ? parsedTickers.join(', ') : 'none'}</p>
            </label>

            <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
              <p className="text-sm font-medium text-slate-700">Or import holdings CSV</p>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-100 file:px-3 file:py-2 file:font-medium file:text-brand-700 hover:file:bg-brand-200"
              />
              <button
                type="button"
                onClick={handleCsvUpload}
                disabled={uploadingCsv}
                className="w-full rounded-xl border border-brand-300 bg-white px-4 py-2 text-sm font-semibold text-brand-700 transition hover:bg-brand-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {uploadingCsv ? 'Extracting holdings...' : 'Extract Tickers From CSV'}
              </button>
              {csvStatus ? <p className="text-xs text-emerald-700">{csvStatus}</p> : null}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="block space-y-2">
                <span className="text-sm font-medium text-slate-700">Period</span>
                <select
                  value={period}
                  onChange={(e) => setPeriod(e.target.value)}
                  className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
                >
                  <option value="1d">1d</option>
                  <option value="5d">5d</option>
                  <option value="1mo">1mo</option>
                  <option value="3mo">3mo</option>
                  <option value="6mo">6mo</option>
                  <option value="1y">1y</option>
                </select>
              </label>

              <label className="block space-y-2">
                <span className="text-sm font-medium text-slate-700">Model</span>
                <input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
                />
              </label>
            </div>

            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700">Focus (optional)</span>
              <input
                value={focus}
                onChange={(e) => setFocus(e.target.value)}
                placeholder="growth, value, dividends"
                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
              />
            </label>

            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700">Custom Query (custom mode)</span>
              <textarea
                rows={4}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="What are the top AI stocks right now?"
                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
              />
            </label>

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-xl bg-brand-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? 'Running analysis...' : 'Run Analysis'}
            </button>
          </form>

          <section className="md:col-span-3">
            <div className="h-full rounded-2xl border border-slate-200 bg-slate-950 p-4 md:p-6">
              <h2 className="mb-3 text-lg font-semibold text-slate-100">Output</h2>

              {error ? (
                <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
              ) : null}

              {!error && !result && !loading ? (
                <p className="text-sm text-slate-400">Run a request to view results here.</p>
              ) : null}

              {loading ? <p className="text-sm text-brand-200">Fetching live data and generating analysis...</p> : null}

              {result ? (
                <pre className="mt-2 max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900 p-4 text-xs text-slate-200 md:text-sm">
                  {result}
                </pre>
              ) : null}
            </div>
          </section>
        </div>
      </div>
    </main>
  )
}
