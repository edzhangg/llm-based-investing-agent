import { useEffect, useMemo, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

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

function FormPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const prefill = location.state?.prefill

  const [mode, setMode] = useState(prefill?.mode ?? 'recommend')
  const [period, setPeriod] = useState(prefill?.period ?? '1mo')
  const [model, setModel] = useState(prefill?.model ?? 'gpt-5-nano')
  const [tickerInput, setTickerInput] = useState(prefill?.tickerInput ?? 'AAPL, MSFT, NVDA')
  const [focus, setFocus] = useState(prefill?.focus ?? 'growth')
  const [query, setQuery] = useState(prefill?.query ?? '')
  const [error, setError] = useState('')
  const [csvFile, setCsvFile] = useState(null)
  const [uploadingCsv, setUploadingCsv] = useState(false)
  const [csvStatus, setCsvStatus] = useState('')

  const parsedTickers = useMemo(() => parseTickers(tickerInput), [tickerInput])

  const handleSubmit = (e) => {
    e.preventDefault()
    setError('')

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

    navigate('/analysis', {
      state: {
        payload,
        formSnapshot: { mode, period, model, tickerInput, focus, query },
      },
    })
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
            Enter one ticker (like <span className="font-semibold">AAPL</span>) or a list (<span className="font-semibold">AAPL, MSFT, NVDA</span>) and run market analysis.
          </p>
        </header>

        <form onSubmit={handleSubmit} className="grid gap-6 md:grid-cols-5">
          <section className="space-y-4 md:col-span-2">
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

            {error ? <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}

            <button
              type="submit"
              className="w-full rounded-xl bg-brand-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-brand-700"
            >
              Run Analysis
            </button>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-slate-950 p-4 text-slate-200 md:col-span-3 md:p-6">
            <h2 className="mb-3 text-lg font-semibold text-slate-100">How It Works</h2>
            <p className="text-sm text-slate-300">
              Press <span className="font-semibold text-brand-200">Run Analysis</span> to navigate to a dedicated streaming page where you can see tool calls, backend status updates, and final output in real-time.
            </p>
          </section>
        </form>
      </div>
    </main>
  )
}

function AnalysisPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const payload = location.state?.payload
  const formSnapshot = location.state?.formSnapshot

  const [events, setEvents] = useState([])
  const [output, setOutput] = useState('')
  const [status, setStatus] = useState('Connecting...')
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const [showMenu, setShowMenu] = useState(true)

  useEffect(() => {
    if (!payload) return

    let cancelled = false

    const runStream = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/analyze/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })

        if (!response.ok || !response.body) {
          const text = await response.text()
          throw new Error(text || 'Failed to start stream')
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (!cancelled) {
          const { value, done: streamDone } = await reader.read()
          if (streamDone) break

          buffer += decoder.decode(value, { stream: true })
          const chunks = buffer.split('\n\n')
          buffer = chunks.pop() || ''

          for (const chunk of chunks) {
            const dataLine = chunk
              .split('\n')
              .find((line) => line.startsWith('data: '))
            if (!dataLine) continue

            const raw = dataLine.slice(6)
            let event
            try {
              event = JSON.parse(raw)
            } catch {
              continue
            }

            if (event.type === 'connected') {
              setStatus('Connected. Waiting for backend updates...')
              continue
            }

            if (event.type === 'status') {
              setStatus(event.message || 'Working...')
              setEvents((prev) => [...prev, event])
              continue
            }

            if (event.type === 'tool_start' || event.type === 'tool_end') {
              setEvents((prev) => [...prev, event])
              continue
            }

            if (event.type === 'final') {
              setOutput(event.output || '')
              setStatus('Completed')
              continue
            }

            if (event.type === 'error') {
              setError(event.message || 'Unknown error')
              setStatus('Failed')
              continue
            }

            if (event.type === 'done') {
              setDone(true)
              continue
            }
          }
        }
      } catch (err) {
        setError(err.message || 'Stream failed')
        setStatus('Failed')
      }
    }

    runStream()

    return () => {
      cancelled = true
    }
  }, [payload])

  if (!payload) {
    return <Navigate to="/" replace />
  }

  return (
    <main className="min-h-screen bg-gradient-to-br from-brand-100 via-slate-50 to-brand-50 p-4 md:p-8">
      <div className="mx-auto max-w-7xl rounded-3xl border border-brand-100 bg-white/90 p-4 shadow-soft md:p-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900 md:text-2xl">Streaming Analysis</h1>
            <p className="text-sm text-slate-600">Status: {status}{done ? ' • done' : ''}</p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setShowMenu((v) => !v)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              {showMenu ? 'Hide Options' : 'Show Options'}
            </button>
            <button
              type="button"
              onClick={() => navigate('/', { state: { prefill: formSnapshot } })}
              className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-700"
            >
              Back to Form
            </button>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-5">
          {showMenu ? (
            <aside className="rounded-2xl border border-slate-200 bg-slate-50 p-4 md:col-span-2">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-700">Selected Options</h2>
              <pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap rounded-lg bg-white p-3 text-xs text-slate-700">
{JSON.stringify(payload, null, 2)}
              </pre>
            </aside>
          ) : null}

          <section className={`grid gap-4 ${showMenu ? 'md:col-span-3' : 'md:col-span-5'}`}>
            <div className="rounded-2xl border border-slate-200 bg-slate-950 p-4">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-200">Live Backend Updates</h2>
              <div className="max-h-[34vh] overflow-auto space-y-2">
                {events.length === 0 ? (
                  <p className="text-sm text-slate-400">Waiting for tool activity...</p>
                ) : (
                  events.map((event, idx) => (
                    <div key={`${event.type}-${idx}`} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200">
                      <p className="font-semibold text-brand-200">{event.type}</p>
                      {event.message ? <p>{event.message}</p> : null}
                      {event.tool ? <p>tool: {event.tool}</p> : null}
                      {event.input ? <p className="break-all text-slate-300">input: {event.input}</p> : null}
                      {event.output_preview ? <p className="break-all text-slate-300">output: {event.output_preview}</p> : null}
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-slate-950 p-4">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-200">Final Output</h2>
              {error ? <p className="mb-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}
              {!error && !output ? <p className="text-sm text-slate-400">Output will appear when the run completes.</p> : null}
              {output ? (
                <pre className="max-h-[38vh] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900 p-4 text-xs text-slate-200 md:text-sm">
                  {output}
                </pre>
              ) : null}
            </div>
          </section>
        </div>
      </div>
    </main>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<FormPage />} />
        <Route path="/analysis" element={<AnalysisPage />} />
      </Routes>
    </BrowserRouter>
  )
}
