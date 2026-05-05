'use client'

import { BarChart2, MapPin, Thermometer, Snowflake, Info } from 'lucide-react'
import { AreaChart, Area, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import { FlightData } from '@/lib/types'

// ── Mini histogram ────────────────────────────────────────────────────────────

function buildHistogram(values: number[], v_min: number, v_max: number, bins = 24) {
  const step   = (v_max - v_min) / bins
  const counts = Array(bins).fill(0)
  values.forEach(v => {
    const i = Math.min(Math.floor((v - v_min) / step), bins - 1)
    counts[i]++
  })
  return counts.map((count, i) => ({ x: +(v_min + i * step).toFixed(2), count }))
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Section({ icon, title, children }: {
  icon: React.ReactNode; title: string; children: React.ReactNode
}) {
  return (
    <div className="border-b border-[var(--border)] last:border-0">
      <div className="flex items-center gap-2 px-4 pt-4 pb-2.5">
        <span className="text-[var(--accent)] opacity-60">{icon}</span>
        <span className="text-[10px] font-semibold text-[var(--muted)] uppercase tracking-widest">{title}</span>
      </div>
      <div className="px-4 pb-4">{children}</div>
    </div>
  )
}

function Row({ label, value, unit, highlight }: {
  label: string; value: string; unit?: string; highlight?: boolean
}) {
  return (
    <div className="flex justify-between items-baseline py-1.5 border-b border-[var(--border)] last:border-0">
      <span className="text-xs text-[var(--muted)]">{label}</span>
      <span className={`text-sm font-mono ${highlight ? 'text-[var(--accent)]' : 'text-slate-100'}`}>
        {value}
        {unit && <span className="text-[var(--muted)] text-xs ml-1">{unit}</span>}
      </span>
    </div>
  )
}

function SpotRow({ rank, value, time, r, g, b }: {
  rank: number; value: number; time: string; r: number; g: number; b: number
}) {
  return (
    <div className="flex items-center gap-2.5 py-1.5 border-b border-[var(--border)] last:border-0">
      <span className="text-[10px] text-[var(--muted)] w-4 text-right shrink-0">{rank}</span>
      <div className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: `rgb(${r},${g},${b})` }} />
      <span className="text-xs font-mono text-slate-100 flex-1">{value.toFixed(2)}</span>
      <span className="text-[10px] text-[var(--muted)] font-mono">{time}</span>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function StatsPanel({ data }: { data: FlightData }) {
  const { meta, points } = data
  const values    = points.map(p => p.value)
  const histogram = buildHistogram(values, meta.v_min, meta.v_max)
  const hotSpots  = [...points].sort((a, b) => b.value - a.value).slice(0, 6)
  const coldSpots = [...points].sort((a, b) => a.value - b.value).slice(0, 5)

  return (
    <div className="flex flex-col">

      {/* Distribution chart */}
      <Section icon={<BarChart2 size={13} />} title="Verteilung">
        <div className="h-16 w-full mb-3">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={histogram} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="distGrad" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%"   stopColor="#006837" />
                  <stop offset="40%"  stopColor="#66bd63" />
                  <stop offset="70%"  stopColor="#f46d43" />
                  <stop offset="100%" stopColor="#a50026" />
                </linearGradient>
              </defs>
              <XAxis dataKey="x" hide />
              <Tooltip
                contentStyle={{
                  background: 'var(--surface-2)',
                  border: '1px solid var(--border-2)',
                  borderRadius: 6,
                  fontSize: 11,
                  color: 'var(--text)',
                  padding: '4px 8px',
                }}
                formatter={(v: number) => [v, 'Punkte']}
                labelFormatter={(l: number) => `Wert: ${l}`}
              />
              <Area
                type="monotone"
                dataKey="count"
                stroke="url(#distGrad)"
                fill="url(#distGrad)"
                strokeWidth={1.5}
                fillOpacity={0.2}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <Row label="Minimum"    value={meta.v_min.toFixed(2)} />
        <Row label="Maximum"    value={meta.v_max.toFixed(2)} />
        <Row label="Mittelwert" value={meta.v_mean.toFixed(2)} highlight />
        <Row label="Std. Abw."  value={meta.v_std.toFixed(2)} />
      </Section>

      {/* Flight info */}
      <Section icon={<MapPin size={13} />} title="Fluginfo">
        <Row label="Messpunkte"  value={meta.point_count.toLocaleString()} />
        <Row label="Zentrum Lat" value={meta.center_lat.toFixed(5)} unit="°" />
        <Row label="Zentrum Lon" value={meta.center_lon.toFixed(5)} unit="°" />
        <Row label="Generiert"   value={new Date(meta.generated_at).toLocaleTimeString('de-AT')} />
      </Section>

      {/* Hotspots */}
      <Section icon={<Thermometer size={13} />} title={`Top ${hotSpots.length} Hotspots`}>
        {hotSpots.map((p, i) => (
          <SpotRow key={i} rank={i + 1} value={p.value} time={p.time} r={p.r} g={p.g} b={p.b} />
        ))}
      </Section>

      {/* Coldspots */}
      <Section icon={<Snowflake size={13} />} title={`Top ${coldSpots.length} Coldspots`}>
        {coldSpots.map((p, i) => (
          <SpotRow key={i} rank={i + 1} value={p.value} time={p.time} r={p.r} g={p.g} b={p.b} />
        ))}
      </Section>

      {/* Color scale */}
      <Section icon={<Info size={13} />} title="Farbskala">
        <p className="text-xs text-[var(--muted)] mb-2">{meta.label}</p>
        <div
          className="h-2.5 w-full rounded-full"
          style={{ background: 'linear-gradient(to right, #006837, #66bd63, #d9ef8b, #fee08b, #f46d43, #a50026)' }}
        />
        <div className="flex justify-between mt-1.5 text-[10px] font-mono text-[var(--muted)]">
          <span>{meta.v_min.toFixed(1)}</span>
          <span>{((meta.v_min + meta.v_max) / 2).toFixed(1)}</span>
          <span>{meta.v_max.toFixed(1)}</span>
        </div>
      </Section>

    </div>
  )
}
