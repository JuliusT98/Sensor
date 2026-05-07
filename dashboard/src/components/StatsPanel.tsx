'use client';

import { useMemo } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ResponsiveContainer,
} from 'recharts';
import { formatDuration, intervalToDuration, parseISO } from 'date-fns';
import { de } from 'date-fns/locale';
import { Satellite, Crosshair, BarChart2, Layers } from 'lucide-react';
import type { FlightDataResponse, NIRFeature } from '@/lib/types';
import { nirToColor, nirToHex } from '@/lib/colormap';

interface StatsPanelProps {
  data: FlightDataResponse | null;
}

interface HistogramBin {
  label: string;
  count: number;
  midpoint: number;
}

function buildHistogram(
  values: number[],
  min: number,
  max: number,
  bins = 20
): HistogramBin[] {
  if (values.length === 0 || max === min) return [];
  const step = (max - min) / bins;
  const counts = new Array<number>(bins).fill(0);
  for (const v of values) {
    const idx = Math.min(Math.floor((v - min) / step), bins - 1);
    counts[idx]++;
  }
  return counts.map((count, i) => ({
    label: (min + i * step).toFixed(0),
    count,
    midpoint: min + (i + 0.5) * step,
  }));
}

function KpiCard({
  label,
  value,
  icon,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  icon: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="bg-surface border border-border rounded-lg p-3 flex items-center gap-3 hover:border-accent/30 transition-colors">
      <div className={`shrink-0 ${accent ? 'text-accent' : 'text-slate-500'}`}>{icon}</div>
      <div className="min-w-0">
        <div className="text-xs text-slate-500 mb-0.5">{label}</div>
        <div className="text-sm font-mono font-medium text-slate-100 truncate">{value}</div>
      </div>
    </div>
  );
}

export default function StatsPanel({ data }: StatsPanelProps) {
  const stats = data?.stats;
  const features = data?.geojson.features ?? [];

  // Last valid feature with nir_values
  const lastValidFeature = useMemo((): NIRFeature | null => {
    for (let i = features.length - 1; i >= 0; i--) {
      const f = features[i];
      if (f.properties.nir_values && f.properties.nir_values.some((v) => v !== null)) {
        return f;
      }
    }
    return null;
  }, [features]);

  const nirMeans = useMemo(
    () =>
      features
        .map((f) => f.properties.nir_mean)
        .filter((v): v is number => v !== null),
    [features]
  );

  const histogram = useMemo(
    () => buildHistogram(nirMeans, stats?.nirMin ?? 0, stats?.nirMax ?? 1),
    [nirMeans, stats]
  );

  const flightDuration = useMemo(() => {
    if (!stats?.firstTs || !stats?.lastTs) return null;
    try {
      const duration = intervalToDuration({
        start: parseISO(stats.firstTs),
        end: parseISO(stats.lastTs),
      });
      return formatDuration(duration, { locale: de, format: ['hours', 'minutes', 'seconds'] }) || '< 1 s';
    } catch {
      return null;
    }
  }, [stats]);

  const nirMin = stats?.nirMin ?? 0;
  const nirMax = stats?.nirMax ?? 1;

  return (
    <div className="flex flex-col h-full p-4 gap-4">
      {/* Title */}
      <div className="shrink-0">
        <h2 className="text-sm font-semibold text-slate-300 tracking-wide">Statistiken</h2>
        <div className="text-xs text-slate-600 font-mono mt-0.5">
          {stats?.count ?? 0} Messpunkte erfasst
        </div>
      </div>

      {/* KPI cards */}
      <div className="flex flex-col gap-2 shrink-0">
        <KpiCard
          label="Messpunkte gesamt"
          value={stats?.count.toLocaleString('de-AT') ?? '—'}
          icon={<BarChart2 size={16} />}
          accent
        />
        <KpiCard
          label="Mit GPS-Fix"
          value={stats?.withGPS.toLocaleString('de-AT') ?? '—'}
          icon={<Satellite size={16} />}
          accent
        />
        <KpiCard
          label="NIR-Mittelwert"
          value={
            stats && stats.nirMean > 0
              ? `${stats.nirMean.toFixed(1)}`
              : '—'
          }
          icon={<Crosshair size={16} />}
        />
        <KpiCard
          label="NIR-Bereich"
          value={
            stats && stats.nirMax > 0
              ? `${stats.nirMin.toFixed(0)} – ${stats.nirMax.toFixed(0)}`
              : '—'
          }
          icon={<Layers size={16} />}
        />
        {flightDuration && (
          <KpiCard
            label="Flugdauer"
            value={flightDuration}
            icon={<BarChart2 size={16} />}
          />
        )}
      </div>

      {/* Divider */}
      <div className="border-t border-border shrink-0" />

      {/* Histogram */}
      <div className="shrink-0">
        <h3 className="text-xs font-medium text-slate-400 mb-2 tracking-wide uppercase">
          NIR-Verteilung
        </h3>
        {histogram.length > 0 ? (
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={histogram} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
              <XAxis
                dataKey="label"
                tick={{ fontSize: 9, fill: '#475569', fontFamily: 'monospace' }}
                tickLine={false}
                axisLine={false}
                interval={4}
              />
              <YAxis
                tick={{ fontSize: 9, fill: '#475569', fontFamily: 'monospace' }}
                tickLine={false}
                axisLine={false}
                width={30}
              />
              <Tooltip
                contentStyle={{
                  background: '#161b27',
                  border: '1px solid #1e2a3a',
                  borderRadius: '6px',
                  fontSize: '11px',
                  fontFamily: 'monospace',
                  color: '#e2e8f0',
                }}
                formatter={(val: number) => [val, 'Anzahl']}
                labelFormatter={(label) => `NIR: ${label}`}
                cursor={{ fill: 'rgba(0,212,255,0.05)' }}
              />
              <Bar dataKey="count" radius={[2, 2, 0, 0]}>
                {histogram.map((bin, i) => {
                  const [r, g, b] = nirToColor(bin.midpoint, nirMin, nirMax);
                  return (
                    <Cell
                      key={i}
                      fill={`rgba(${r},${g},${b},0.85)`}
                    />
                  );
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[160px] flex items-center justify-center text-xs text-slate-600 font-mono">
            Keine Daten
          </div>
        )}
      </div>

      {/* Divider */}
      <div className="border-t border-border shrink-0" />

      {/* Channel breakdown */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <h3 className="text-xs font-medium text-slate-400 mb-2 tracking-wide uppercase">
          Letzter Messpunkt — Kanäle
        </h3>
        {lastValidFeature ? (
          <div className="flex flex-col gap-1.5">
            {lastValidFeature.properties.nir_values.map((val, i) => {
              const chMax = Math.max(
                ...lastValidFeature.properties.nir_values.filter(
                  (v): v is number => v !== null
                )
              );
              const chMin = Math.min(
                ...lastValidFeature.properties.nir_values.filter(
                  (v): v is number => v !== null
                )
              );
              const pct =
                val !== null && chMax > chMin
                  ? ((val - chMin) / (chMax - chMin)) * 100
                  : val !== null
                  ? 100
                  : 0;
              const hexColor =
                val !== null ? nirToHex(val, nirMin, nirMax) : '#1e2a3a';

              return (
                <div key={i} className="flex items-center gap-2">
                  <span className="text-xs font-mono text-slate-500 w-8 shrink-0">
                    CH{i + 1}
                  </span>
                  <div className="flex-1 bg-surface rounded-sm h-4 overflow-hidden">
                    <div
                      className="h-full rounded-sm transition-all duration-300"
                      style={{
                        width: `${pct}%`,
                        backgroundColor: hexColor,
                        opacity: val !== null ? 0.85 : 0.2,
                        minWidth: pct > 0 ? '4px' : '0',
                      }}
                    />
                  </div>
                  <span className="text-xs font-mono text-slate-400 w-16 text-right shrink-0">
                    {val !== null ? val.toFixed(1) : '—'}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-xs text-slate-600 font-mono py-4 text-center">
            Kein gültiger Messpunkt
          </div>
        )}
      </div>

      {/* Bottom info */}
      {stats?.firstTs && (
        <div className="border-t border-border pt-3 shrink-0">
          <div className="text-xs font-mono text-slate-600 space-y-0.5">
            <div>Start: {new Date(stats.firstTs).toLocaleString('de-AT')}</div>
            {stats.lastTs && (
              <div>Ende: {new Date(stats.lastTs).toLocaleString('de-AT')}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
