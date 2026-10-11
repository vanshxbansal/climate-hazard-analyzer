import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, csvUrl, geocode, getAnalysis, getConfig } from './api'
import { DurationIntensityChart, FrequencyChart, METRICS, ThresholdChart } from './components/Charts'
import MapPicker from './components/MapPicker'

const clean = s => s.replace(/[^\w\s,.'()-]/gu, '').slice(0, 100)
const r2 = n => Math.round(n * 100) / 100

const EXAMPLES = [
  { name: 'Delhi, India', lat: 28.61, lon: 77.21 },
  { name: 'Phoenix, Arizona', lat: 33.45, lon: -112.07 },
  { name: 'Dubai, UAE', lat: 25.2, lon: 55.27 },
  { name: 'Sydney, Australia', lat: -33.87, lon: 151.21 },
  { name: 'Madrid, Spain', lat: 40.42, lon: -3.7 },
]

function CountUp({ value, decimals = 0, suffix = '' }) {
  const [v, setV] = useState(0)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setV(value); return }
    let raf, t0
    const tick = t => {
      t0 ??= t
      const k = Math.min(1, (t - t0) / 900)
      setV(value * (1 - Math.pow(1 - k, 3)))
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value])
  return <>{v.toFixed(decimals)}{suffix}</>
}

function Skeleton() {
  return (
    <section className="results" aria-busy="true" aria-label="Loading results">
      <div className="panel skel" style={{ height: 150 }} />
      <div className="tiles">{[0, 1, 2, 3].map(i => <div key={i} className="tile skel" style={{ height: 84 }} />)}</div>
      <div className="panel skel" style={{ height: 360 }} />
    </section>
  )
}

const TREND_ROWS = [
  ['hw_days', 'Heatwave days / yr'], ['events', 'Events / yr'], ['mean_duration', 'Mean duration (d)'],
  ['mean_intensity', 'Mean intensity (°C)'], ['max_duration', 'Longest event (d)'],
]

export default function App() {
  const [cfg, setCfg] = useState({ min_year: 1950, max_year: 2025 })
  const [mode, setMode] = useState('city')
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState([])
  const [searching, setSearching] = useState(false)
  const [region, setRegion] = useState(null)           // {name, lat, lon, box?}
  const [pending, setPending] = useState(null)         // first corner of a box
  const [start, setStart] = useState(1990)
  const [end, setEnd] = useState(2020)
  const [bsMode, setBsMode] = useState('wmo')          // 'wmo' = 1991-2020, 'period' = selected range
  const [metric, setMetric] = useState('hw_days')
  const [state, setState] = useState({ status: 'idle' })
  const abort = useRef(null)
  const resultsRef = useRef(null)

  useEffect(() => { getConfig().then(setCfg).catch(() => {}) }, [])

  const baseline = bsMode === 'wmo' ? [1991, 2020] : [start, end]
  const rangeError =
    end - start + 1 < 5 ? 'Choose at least 5 years.' :
    start < cfg.min_year || end > cfg.max_year ? `Years must be between ${cfg.min_year} and ${cfg.max_year}.` :
    baseline[1] - baseline[0] + 1 < 20 ? 'The baseline must span 20+ years: use the 1991-2020 baseline or a longer period.' : null

  const search = async e => {
    e.preventDefault()
    if (query.trim().length < 2) return
    setSearching(true)
    try { setHits((await geocode(query.trim())).results) }
    catch (err) { setState({ status: 'error', message: err.message }) }
    finally { setSearching(false) }
  }

  const pickCity = h => {
    setRegion({ name: clean(h.name), lat: h.lat, lon: h.lon })
    setHits([])
  }

  const onMapPick = (lat, lon) => {
    lat = r2(lat); lon = r2(((lon + 540) % 360) - 180)
    if (mode === 'point') {
      setRegion({ name: `Point ${lat}, ${lon}`, lat, lon })
    } else if (!pending) {
      setPending({ lat, lon }); setRegion(null)
    } else {
      const box = {
        south: Math.min(lat, pending.lat), north: Math.max(lat, pending.lat),
        west: Math.min(lon, pending.lon), east: Math.max(lon, pending.lon),
      }
      setPending(null)
      setRegion(box.north - box.south > 10 || box.east - box.west > 10
        ? null
        : { name: `Box ${box.south}..${box.north}N, ${box.west}..${box.east}E`, lat: (box.south + box.north) / 2, lon: (box.west + box.east) / 2, box })
      if (box.north - box.south > 10 || box.east - box.west > 10)
        setState({ status: 'error', message: 'Box too large: choose an area up to 10° × 10°.' })
    }
  }

  const quick = c => {
    setMode('city'); setPending(null); setHits([])
    const r = { name: c.name, lat: c.lat, lon: c.lon }
    setRegion(r); run(r)
  }

  useEffect(() => {
    if (state.status === 'ok') resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [state.data])  // eslint-disable-line react-hooks/exhaustive-deps

  const switchMode = m => { setMode(m); setPending(null); setRegion(null); setHits([]) }

  const run = useCallback(async (r = region) => {
    if (!r || rangeError) return
    abort.current?.abort()
    abort.current = new AbortController()
    setState({ status: 'loading' })
    try {
      const data = await getAnalysis(r, { start, end, baselineStart: baseline[0], baselineEnd: baseline[1] }, abort.current.signal)
      setState({ status: 'ok', data })
    } catch (err) {
      if (err.name === 'AbortError') return
      const wait = err instanceof ApiError && err.retryAfter ? ` Try again in ~${err.retryAfter}s.` : ''
      setState({ status: 'error', message: err.message + wait })
    }
  }, [region, start, end, baseline[0], baseline[1], rangeError])  // eslint-disable-line react-hooks/exhaustive-deps

  const d = state.data
  const t = d?.trends
  const totalEvents = d?.events.length ?? 0
  const totalDays = d?.yearly.reduce((a, y) => a + y.hw_days, 0) ?? 0
  const longest = d?.events.reduce((a, e) => Math.max(a, e.duration), 0) ?? 0

  return (
    <div className="app">
      <header className="hero">
        <span className="orb o1" /><span className="orb o2" /><span className="orb o3" />
        <div className="hero-inner">
          <span className="eyebrow">ERA5 reanalysis · 30+ years · any place on Earth</span>
          <h1>Is it getting <em>hotter</em> where you live?</h1>
          <p className="sub">Pick a place and see how heatwave frequency, duration and intensity have changed, with honest statistics that tell you when a trend is real and when it is just noise.</p>
        </div>
        <svg className="wave" viewBox="0 0 1200 80" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 50 C 150 10, 300 90, 450 45 S 750 5, 900 50 S 1100 85, 1200 40 L1200 80 L0 80Z" />
        </svg>
      </header>

      <main>
        <section className="panel controls no-print" aria-label="Controls">
          <div className="chips" aria-label="Quick examples">
            <span className="muted">Try:</span>
            {EXAMPLES.map(c => <button key={c.name} className="chip" onClick={() => quick(c)} disabled={state.status === 'loading'}>{c.name.split(',')[0]}</button>)}
          </div>
          <label className="field">Hazard
            <select value="heatwave" onChange={() => {}} aria-label="Hazard type">
              <option value="heatwave">Heatwave</option>
              <option disabled>Drought (planned)</option>
              <option disabled>Heavy rainfall (planned)</option>
            </select>
          </label>

          <div className="seg" role="tablist" aria-label="Region selection method">
            {[['city', 'City / state'], ['point', 'Map point'], ['box', 'Lat-lon box']].map(([m, l]) => (
              <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? 'on' : ''} onClick={() => switchMode(m)}>{l}</button>
            ))}
          </div>

          {mode === 'city' && (
            <form onSubmit={search} className="search">
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. Delhi, Phoenix, Rajasthan" maxLength={80} aria-label="Search place" />
              <button disabled={searching || query.trim().length < 2}>{searching ? '…' : 'Search'}</button>
            </form>
          )}
          {mode === 'city' && hits.length > 0 && (
            <ul className="hits">{hits.map((h, i) => <li key={i}><button type="button" onClick={() => pickCity(h)}>{h.name}</button></li>)}</ul>
          )}
          {mode === 'city' && hits.length === 0 && !searching && query && !region && <p className="hint">Press Search, then pick a match.</p>}
          {mode === 'point' && <p className="hint">Click the map to choose a point.</p>}
          {mode === 'box' && <p className="hint">{pending ? 'Click the opposite corner (max 10° × 10°).' : 'Click two opposite corners on the map. The box is analysed at its centre grid cell.'}</p>}

          <div className="selected" aria-live="polite">
            {region ? <><strong>{region.name}</strong><span>{r2(region.lat)}, {r2(region.lon)}</span></> : <span className="muted">No region selected</span>}
          </div>

          <div className="years">
            <label className="field">From
              <input type="number" value={start} min={cfg.min_year} max={cfg.max_year} onChange={e => setStart(+e.target.value)} />
            </label>
            <label className="field">To
              <input type="number" value={end} min={cfg.min_year} max={cfg.max_year} onChange={e => setEnd(+e.target.value)} />
            </label>
          </div>

          <fieldset className="baseline">
            <legend>Baseline for the 95th percentile</legend>
            <label><input type="radio" checked={bsMode === 'wmo'} onChange={() => setBsMode('wmo')} /> 1991-2020 (WMO normal, recommended)</label>
            <label><input type="radio" checked={bsMode === 'period'} onChange={() => setBsMode('period')} /> Same as selected period</label>
          </fieldset>

          {rangeError && <p className="err">{rangeError}</p>}
          <button className="primary" onClick={() => run()} disabled={!region || !!rangeError || state.status === 'loading'}>
            {state.status === 'loading' ? <><span className="spin" /> Analysing…</> : 'Analyse heatwaves'}
          </button>
          {state.status === 'loading' && <p className="hint">First request for a new location downloads ~30 years of daily data (a few seconds); repeats are served from the database.</p>}
          {state.status === 'error' && <p className="err" role="alert">{state.message}</p>}
        </section>

        <section className="panel mapwrap no-print">
          <MapPicker mode={mode} point={region} box={region?.box} pending={pending} onPick={onMapPick} />
        </section>

        {state.status === 'loading' && !d && <Skeleton />}
        {!d && state.status !== 'loading' && (
          <section className="empty no-print"><div className="panel hint-card"><strong>Start with a place</strong><p>Click an example city, search above, or tap the map. The first analysis of a new location downloads ~30 years of daily data (a few seconds); repeats come straight from the database.</p></div></section>
        )}
        {d && (
          <section className="results" aria-label="Results" ref={resultsRef} key={`${d.region.name}|${d.params.start}|${d.params.end}|${d.params.baseline.start}`}>
            <div className="panel headline">
              <h2>{d.insights.headline}</h2>
              <ul className="notes">{d.insights.bullets.map((b, i) => <li key={i} className={b.level}>{b.text}</li>)}</ul>
              <div className="actions no-print">
                <a className="btn" href={csvUrl(region, { start, end, baselineStart: baseline[0], baselineEnd: baseline[1] })} download>Download CSV</a>
                <button className="btn" onClick={() => window.print()}>Save as PDF</button>
                <span className="muted">{d.meta.cached ? 'Served from cache' : `Computed in ${d.meta.runtime_ms} ms`} · {d.meta.source}</span>
              </div>
            </div>

            <div className="tiles">
              <Tile label="Heatwave events" value={totalEvents} />
              <Tile label="Heatwave days" value={totalDays} />
              <Tile label="Longest event" value={longest} suffix=" d" />
              <Tile label="Valid data" value={d.data_quality.valid_fraction * 100} decimals={1} suffix="%" />
            </div>

            <div className="panel">
              <div className="row">
                <h3>Frequency over time</h3>
                <div className="seg small no-print">
                  {Object.entries(METRICS).map(([k, v]) => (
                    <button key={k} className={metric === k ? 'on' : ''} onClick={() => setMetric(k)}>{v.label.replace(' / year', '')}</button>
                  ))}
                </div>
              </div>
              <FrequencyChart yearly={d.yearly} metric={metric} />
              {d.data_quality.unreliable_years.length > 0 && <p className="hint">Grey bars: years with &lt;90% valid data, excluded from trends.</p>}
            </div>

            <div className="panel">
              <h3>Duration and intensity</h3>
              <DurationIntensityChart yearly={d.yearly} />
            </div>

            <div className="panel">
              <h3>Trend statistics</h3>
              <div className="tablewrap">
                <table>
                  <thead><tr><th>Metric</th><th>First period</th><th>Last period</th><th>Change</th><th>OLS / decade</th><th>Sen / decade</th><th>Mann-Kendall p</th><th>Significant (5%)</th></tr></thead>
                  <tbody>
                    {TREND_ROWS.map(([k, label]) => {
                      const m = t[k]
                      if (!m?.mann_kendall) return <tr key={k}><td>{label}</td><td colSpan="7" className="muted">insufficient data</td></tr>
                      return (
                        <tr key={k}>
                          <td>{label}</td><td>{m.first_period.mean}</td><td>{m.last_period.mean}</td>
                          <td>{m.pct_change == null ? 'n/a' : `${m.pct_change > 0 ? '+' : ''}${m.pct_change}%`}</td>
                          <td>{m.ols_slope_per_decade}</td><td>{m.sen_slope_per_decade}</td><td>{m.mann_kendall.p}</td>
                          <td><span className={`badge ${m.significant ? 'yes' : 'no'}`}>{m.significant ? 'yes' : 'no'}</span></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <p className="hint">Periods are the first/last {t.hw_days.first_period ? t.hw_days.first_period.years[1] - t.hw_days.first_period.years[0] + 1 : '?'} years. Significance requires Mann-Kendall and OLS to agree (p&lt;0.05) over 10+ years.</p>
            </div>

            <div className="panel">
              <h3>What counts as a heatwave here</h3>
              <p className="hint">Tmax above the 95th percentile of the {d.params.baseline.start}-{d.params.baseline.end} baseline for that calendar day (±7 day window), for {d.params.min_days}+ consecutive days.</p>
              <ThresholdChart thresholds={d.threshold_by_day} />
            </div>

            <div className="panel">
              <h3>Most intense events</h3>
              <div className="tablewrap">
                <table>
                  <thead><tr><th>Start</th><th>End</th><th>Days</th><th>Peak Tmax</th><th>Mean exceedance</th></tr></thead>
                  <tbody>
                    {[...d.events].sort((a, b) => b.cumulative_exceedance - a.cumulative_exceedance).slice(0, 10).map(e => (
                      <tr key={e.start}><td>{e.start}</td><td>{e.end}</td><td>{e.duration}</td><td>{e.peak_tmax}°C</td><td>+{e.mean_exceedance}°C</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}
      </main>
      <footer className="no-print">Data: Open-Meteo / ECMWF ERA5 (CC BY 4.0). Map © OpenStreetMap contributors.</footer>
    </div>
  )
}

function Tile({ label, value, decimals = 0, suffix = '' }) {
  return <div className="tile"><div className="v"><CountUp value={value} decimals={decimals} suffix={suffix} /></div><div className="l">{label}</div></div>
}
