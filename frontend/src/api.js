const BASE = import.meta.env.VITE_API_URL || ''

export class ApiError extends Error {
  constructor(message, status, retryAfter) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

async function get(path, params, signal) {
  const qs = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v)
  })
  const res = await fetch(`${BASE}${path}${qs.size ? `?${qs}` : ''}`, { signal })
  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try { detail = (await res.json()).detail || detail } catch { /* non-JSON error body */ }
    throw new ApiError(detail, res.status, res.headers.get('Retry-After'))
  }
  return res
}

export const getConfig = () => get('/api/config').then(r => r.json())
export const geocode = (q, signal) => get('/api/geocode', { q }, signal).then(r => r.json())
export const analysisParams = (region, opts) => ({
  ...(region.box
    ? { south: region.box.south, west: region.box.west, north: region.box.north, east: region.box.east }
    : { lat: region.lat, lon: region.lon }),
  name: region.name,
  start: opts.start, end: opts.end,
  baseline_start: opts.baselineStart, baseline_end: opts.baselineEnd,
})
export const getAnalysis = (region, opts, signal) =>
  get('/api/analysis', analysisParams(region, opts), signal).then(r => r.json())
export const csvUrl = (region, opts) => {
  const qs = new URLSearchParams()
  Object.entries(analysisParams(region, opts)).forEach(([k, v]) => v != null && v !== '' && qs.set(k, v))
  return `${BASE}/api/export.csv?${qs}`
}
