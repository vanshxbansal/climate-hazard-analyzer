import {
  BarElement, CategoryScale, Chart as ChartJS, Filler, Legend, LineElement, LinearScale, PointElement, Tooltip,
} from 'chart.js'
import { Bar, Line } from 'react-chartjs-2'

ChartJS.register(BarElement, CategoryScale, Filler, Legend, LineElement, LinearScale, PointElement, Tooltip)

const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim()

function theme() {
  return { text: css('--muted'), grid: css('--grid'), heat: css('--heat'), cool: css('--cool'), accent: css('--accent') }
}

function base(t, extra = {}) {
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 900, easing: 'easeOutQuart' }, interaction: { mode: 'index', intersect: false },
    plugins: { legend: { labels: { color: t.text, usePointStyle: true, boxWidth: 8 } } },
    ...extra,
    scales: {
      x: { ticks: { color: t.text, maxRotation: 0, autoSkip: true }, grid: { display: false } },
      ...(extra.scales || {}),
    },
  }
}

export function ols(xs, ys) {
  const n = xs.length
  if (n < 3) return null
  const mx = xs.reduce((a, b) => a + b, 0) / n
  const my = ys.reduce((a, b) => a + b, 0) / n
  let sxy = 0, sxx = 0
  xs.forEach((x, i) => { sxy += (x - mx) * (ys[i] - my); sxx += (x - mx) ** 2 })
  if (!sxx) return null
  const slope = sxy / sxx
  return { slope, at: x => my + slope * (x - mx) }
}

export const METRICS = {
  hw_days: { label: 'Heatwave days / year', roll: 'hw_days_rolling5' },
  events: { label: 'Heatwave events / year', roll: 'events_rolling5' },
  max_duration: { label: 'Longest event (days)' },
}

export function FrequencyChart({ yearly, metric }) {
  const t = theme()
  const def = METRICS[metric]
  const years = yearly.map(y => y.year)
  const vals = yearly.map(y => (y.reliable ? y[metric] : null))
  const pts = yearly.filter(y => y.reliable)
  const fit = ols(pts.map(p => p.year), pts.map(p => p[metric]))
  const data = {
    labels: years,
    datasets: [
      {
        type: 'bar', label: def.label, data: vals, backgroundColor: yearly.map(y => (y.reliable ? t.heat : t.grid)),
        borderRadius: 4, borderSkipped: false, order: 3,
      },
      ...(def.roll ? [{
        type: 'line', label: '5-year rolling mean', data: yearly.map(y => y[def.roll]),
        borderColor: t.cool, backgroundColor: t.cool, borderWidth: 2.5, pointRadius: 0, tension: 0.25, spanGaps: false, order: 1,
      }] : []),
      ...(fit ? [{
        type: 'line', label: `Linear trend (${(fit.slope * 10).toFixed(2)} / decade)`,
        data: years.map(y => +fit.at(y).toFixed(3)), borderColor: t.accent, borderDash: [6, 4], borderWidth: 2,
        pointRadius: 0, order: 2,
      }] : []),
    ],
  }
  return (
    <div className="chart" role="img" aria-label={`${def.label} per year with rolling mean and linear trend`}>
      <Bar data={data} options={base(t, { scales: { y: { beginAtZero: true, ticks: { color: t.text, precision: 0 }, grid: { color: t.grid } } } })} />
    </div>
  )
}

export function DurationIntensityChart({ yearly }) {
  const t = theme()
  const data = {
    labels: yearly.map(y => y.year),
    datasets: [
      { type: 'bar', label: 'Mean event duration (days)', data: yearly.map(y => (y.reliable && y.events ? y.mean_duration : null)),
        backgroundColor: t.cool, borderRadius: 2, yAxisID: 'y' },
      { type: 'line', label: 'Mean intensity (°C above threshold)', data: yearly.map(y => (y.reliable ? y.mean_intensity : null)),
        borderColor: t.heat, backgroundColor: t.heat, pointRadius: 3, borderWidth: 2, yAxisID: 'y1', spanGaps: true },
    ],
  }
  return (
    <div className="chart" role="img" aria-label="Mean event duration and intensity per year">
      <Bar data={data} options={base(t, { scales: {
        y: { beginAtZero: true, position: 'left', title: { display: true, text: 'days', color: t.text }, ticks: { color: t.text }, grid: { color: t.grid } },
        y1: { beginAtZero: true, position: 'right', title: { display: true, text: '°C', color: t.text }, ticks: { color: t.text }, grid: { display: false } },
      } })} />
    </div>
  )
}

export function ThresholdChart({ thresholds }) {
  const t = theme()
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const starts = [0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]  // index 59 = 29 Feb
  const data = {
    labels: thresholds.map((_, i) => { const m = starts.findLastIndex(s => i >= s); return i === starts[m] ? months[m] : '' }),
    datasets: [{ label: '95th percentile Tmax threshold (°C)', data: thresholds, borderColor: t.heat, backgroundColor: t.heat + '33',
      fill: true, pointRadius: 0, borderWidth: 2, tension: 0.2 }],
  }
  return (
    <div className="chart short" role="img" aria-label="Heatwave temperature threshold by day of year">
      <Line data={data} options={base(t, {
        plugins: { legend: { labels: { color: t.text, usePointStyle: true, boxWidth: 8 } } },
        scales: { x: { ticks: { color: t.text, autoSkip: false, maxRotation: 0 }, grid: { display: false } },
                  y: { ticks: { color: t.text }, grid: { color: t.grid }, title: { display: true, text: '°C', color: t.text } } },
      })} />
    </div>
  )
}
