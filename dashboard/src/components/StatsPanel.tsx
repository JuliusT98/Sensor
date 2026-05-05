'use client'

import { FlightData } from '@/lib/types'

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-[#1e2433] bg-[#0d1117] p-4">
      <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest mb-3">
        {title}
      </div>
      {children}
    </div>
  )
}

function Stat({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="flex justify-between items-baseline py-1 border-b border-[#1e2433] last:border-0">
      <span className="text-xs text-slate-400">{label}</span>
      <span className="text-sm font-mono text-white">
        {value}
        {unit && <span className="text-slate-500 text-xs ml-1">{unit}</span>}
      </span>
    </div>
  )
}

export default function StatsPanel({ data }: { data: FlightData }) {
  const { meta, points } = data

  const hotSpots = [...points]
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)

  const coldSpots = [...points]
    .sort((a, b) => a.value - b.value)
    .slice(0, 5)

  const coverage = ((meta.v_max - meta.v_min) / meta.v_mean * 100).toFixed(1)

  return (
    <div className="flex flex-col gap-3 p-4">

      {/* NIR Statistics */}
      <Card title="NIR Statistiken">
        <Stat label="Minimum"   value={meta.v_min.toFixed(1)} />
        <Stat label="Maximum"   value={meta.v_max.toFixed(1)} />
        <Stat label="Mittelwert" value={meta.v_mean.toFixed(1)} />
        <Stat label="Std. Abw." value={meta.v_std.toFixed(1)} />
        <Stat label="Varianz"   value={coverage} unit="%" />
      </Card>

      {/* Color scale */}
      <Card title="Farbskala">
        <div
          className="h-3 w-full rounded"
          style={{ background: 'linear-gradient(to right, #006837, #66bd63, #d9ef8b, #fee08b, #f46d43, #a50026)' }}
        />
        <div className="flex justify-between mt-1.5 text-xs text-slate-500 font-mono">
          <span>{meta.v_min.toFixed(0)}</span>
          <span>{((meta.v_min + meta.v_max) / 2).toFixed(0)}</span>
          <span>{meta.v_max.toFixed(0)}</span>
        </div>
      </Card>

      {/* Flight info */}
      <Card title="Fluginfo">
        <Stat label="Messpunkte" value={meta.point_count.toString()} />
        <Stat label="Zentrum Lat" value={meta.center_lat.toFixed(5)} unit="°" />
        <Stat label="Zentrum Lon" value={meta.center_lon.toFixed(5)} unit="°" />
        <Stat label="Generiert"  value={new Date(meta.generated_at).toLocaleTimeString('de-AT')} />
      </Card>

      {/* Top hotspots */}
      <Card title={`Top ${hotSpots.length} Hotspots`}>
        <div className="flex flex-col gap-1.5">
          {hotSpots.map((p, i) => (
            <div key={i} className="flex items-center gap-2">
              <div
                className="w-2.5 h-2.5 rounded-sm shrink-0"
                style={{ background: `rgb(${p.r},${p.g},${p.b})` }}
              />
              <span className="text-xs font-mono text-white flex-1">{p.value.toFixed(1)}</span>
              <span className="text-[10px] text-slate-600">{p.time}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Cold spots */}
      <Card title={`Top ${coldSpots.length} Coldspots`}>
        <div className="flex flex-col gap-1.5">
          {coldSpots.map((p, i) => (
            <div key={i} className="flex items-center gap-2">
              <div
                className="w-2.5 h-2.5 rounded-sm shrink-0"
                style={{ background: `rgb(${p.r},${p.g},${p.b})` }}
              />
              <span className="text-xs font-mono text-white flex-1">{p.value.toFixed(1)}</span>
              <span className="text-[10px] text-slate-600">{p.time}</span>
            </div>
          ))}
        </div>
      </Card>

    </div>
  )
}
