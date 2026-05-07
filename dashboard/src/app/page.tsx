'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import dynamic from 'next/dynamic';
import { Map, Box, Activity, RefreshCw, Upload, FileJson, X } from 'lucide-react';
import { format } from 'date-fns';
import { de } from 'date-fns/locale';
import type { FlightDataResponse, NIRFeatureCollection } from '@/lib/types';
import { computeStats } from '@/lib/stats';
import StatsPanel from '@/components/StatsPanel';

const MapView = dynamic(() => import('@/components/MapView'), {
  ssr: false,
  loading: () => <LoadingPlaceholder label="Karte wird geladen…" />,
});

const View3D = dynamic(() => import('@/components/View3D'), {
  ssr: false,
  loading: () => <LoadingPlaceholder label="3D-Ansicht wird geladen…" />,
});

function LoadingPlaceholder({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center w-full h-full bg-surface">
      <div className="flex flex-col items-center gap-3 text-slate-400">
        <RefreshCw className="animate-spin" size={28} />
        <span className="text-sm font-mono">{label}</span>
      </div>
    </div>
  );
}

type Tab = 'map' | '3d';

/** Parse a GeoJSON file and compute stats — runs entirely in the browser. */
function parseGeoJSON(text: string): FlightDataResponse {
  const geojson = JSON.parse(text) as NIRFeatureCollection;
  if (geojson.type !== 'FeatureCollection' || !Array.isArray(geojson.features)) {
    throw new Error('Ungültiges GeoJSON-Format (FeatureCollection erwartet).');
  }
  return { geojson, stats: computeStats(geojson) };
}

export default function Home() {
  const [data,        setData]        = useState<FlightDataResponse | null>(null);
  const [activeTab,   setActiveTab]   = useState<Tab>('map');
  const [lastUpdate,  setLastUpdate]  = useState<Date | null>(null);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState<string | null>(null);
  const [uploadedFile, setUploadedFile] = useState<string | null>(null); // filename
  const [dragging,    setDragging]    = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Initial load from API (disk) ────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/flight-data')
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((json: FlightDataResponse) => { setData(json); setLastUpdate(new Date()); })
      .catch(e => setError(e instanceof Error ? e.message : 'Unbekannter Fehler'))
      .finally(() => setLoading(false));
  }, []);

  // ── File handling ────────────────────────────────────────────────────────────
  const handleFile = useCallback((file: File) => {
    if (!file.name.match(/\.(geojson|json)$/i)) {
      setError('Nur .geojson oder .json Dateien werden unterstützt.');
      return;
    }
    setLoading(true);
    setError(null);
    const reader = new FileReader();
    reader.onload = e => {
      try {
        const parsed = parseGeoJSON(e.target!.result as string);
        setData(parsed);
        setUploadedFile(file.name);
        setLastUpdate(new Date());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Fehler beim Parsen.');
      } finally {
        setLoading(false);
      }
    };
    reader.onerror = () => { setError('Datei konnte nicht gelesen werden.'); setLoading(false); };
    reader.readAsText(file, 'utf-8');
  }, []);

  const onInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = ''; // reset so the same file can be re-selected
  }, [handleFile]);

  // ── Drag-and-drop (whole window) ─────────────────────────────────────────────
  useEffect(() => {
    const onDragOver = (e: DragEvent) => { e.preventDefault(); setDragging(true); };
    const onDragLeave = (e: DragEvent) => { if (!e.relatedTarget) setDragging(false); };
    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer?.files?.[0];
      if (file) handleFile(file);
    };
    window.addEventListener('dragover', onDragOver);
    window.addEventListener('dragleave', onDragLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragover', onDragOver);
      window.removeEventListener('dragleave', onDragLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [handleFile]);

  const reloadFromDisk = () => {
    setLoading(true);
    setError(null);
    setUploadedFile(null);
    fetch('/api/flight-data')
      .then(r => r.json())
      .then((json: FlightDataResponse) => { setData(json); setLastUpdate(new Date()); })
      .catch(e => setError(e instanceof Error ? e.message : 'Fehler'))
      .finally(() => setLoading(false));
  };

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'map', label: 'Karte',      icon: <Map  size={14} /> },
    { id: '3d',  label: '3D Ansicht', icon: <Box  size={14} /> },
  ];

  return (
    <div className="flex flex-col h-screen bg-surface overflow-hidden">

      {/* ── Header ── */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-border bg-panel shrink-0">
        <div className="flex items-center gap-3">
          <Activity size={18} className="text-accent" />
          <span className="font-semibold text-base tracking-wide text-slate-100">NIR Dashboard</span>
          <span className="text-xs text-slate-500 font-mono">Spraylogic UAV</span>
        </div>

        <div className="flex items-center gap-3">
          {error && (
            <span className="flex items-center gap-1 text-xs text-red-400 font-mono">
              <X size={12} /> {error}
            </span>
          )}

          {/* Loaded file indicator */}
          {uploadedFile && (
            <span className="flex items-center gap-1.5 text-xs text-accent font-mono bg-accent/10 border border-accent/20 px-2 py-1 rounded">
              <FileJson size={12} />
              {uploadedFile}
            </span>
          )}

          {lastUpdate && (
            <span className="text-xs text-slate-500 font-mono">
              {format(lastUpdate, 'HH:mm:ss', { locale: de })}
            </span>
          )}

          {/* Upload button */}
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium text-slate-200 bg-accent/15 hover:bg-accent/25 border border-accent/30 transition-colors"
            title="GeoJSON-Datei öffnen"
          >
            <Upload size={12} />
            GeoJSON öffnen
          </button>

          {/* Reload from disk */}
          <button
            onClick={reloadFromDisk}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs text-slate-400 hover:text-slate-200 hover:bg-white/5 border border-border transition-colors"
            title="Vom Datenträger neu laden"
          >
            <RefreshCw size={12} />
            Neu laden
          </button>

          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".geojson,.json"
            className="hidden"
            onChange={onInputChange}
          />
        </div>
      </header>

      {/* ── Main ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Map / 3D area */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">

          {/* Tab bar */}
          <div className="flex items-center gap-1 px-4 py-2 border-b border-border bg-panel shrink-0">
            {tabs.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'bg-accent/10 text-accent border border-accent/30'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'
                }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>

          {/* View */}
          <div className="flex-1 relative min-h-0 overflow-hidden">
            {loading ? (
              <div className="flex items-center justify-center w-full h-full">
                <div className="flex flex-col items-center gap-3 text-slate-400">
                  <RefreshCw className="animate-spin" size={24} />
                  <span className="text-sm font-mono">Daten werden geladen…</span>
                </div>
              </div>
            ) : (
              <>
                <div className={activeTab === 'map' ? 'absolute inset-0' : 'hidden'}>
                  <MapView data={data} />
                </div>
                <div className={activeTab === '3d' ? 'absolute inset-0' : 'hidden'}>
                  <View3D data={data} />
                </div>
              </>
            )}
          </div>
        </div>

        {/* Stats panel */}
        <div className="w-80 shrink-0 border-l border-border overflow-y-auto bg-panel">
          <StatsPanel data={data} />
        </div>
      </div>

      {/* ── Drag-and-drop overlay ── */}
      {dragging && (
        <div className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none">
          <div className="absolute inset-0 bg-surface/80 backdrop-blur-sm border-2 border-dashed border-accent rounded-none" />
          <div className="relative flex flex-col items-center gap-4 text-accent">
            <Upload size={48} strokeWidth={1.5} />
            <span className="text-xl font-semibold tracking-wide">GeoJSON hier ablegen</span>
            <span className="text-sm text-slate-400 font-mono">.geojson oder .json</span>
          </div>
        </div>
      )}
    </div>
  );
}
