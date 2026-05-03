/* global React */
const { useState, useEffect, useRef } = React;

// ============================================================
// Live telemetry — polls /api/energy every 5 s, exposes the same `data`
// shape the original mock generator produced so the visual components
// don't have to know about the swap.
// ============================================================

const POLL_INTERVAL_MS = 5000;

// Empty/loading state — keeps the visual structure intact while the first
// fetch is in flight.
const EMPTY_DATA = {
  currentDemand: 0,
  currentPrice: 0,
  costPerHour: 0,
  costPerHourDollars: 0,
  priceH: Array(24).fill(0),
  demandH: Array(24).fill(0),
  forecast: Array(12).fill(0).map((_, i) => ({ hour: i, price: 0, isNow: i === 0 })),
  devices: [],
  voltL1: 0,
  voltL2: 0,
  pf: 0,
  indoor: 0,
  outdoor: 0,
  hvacMode: '—',
  setpoint: 0,
  todayKwh: 0,
  todayCost: 0,
  weekKwh: 0,
  weekCost: 0,
  monthKwh: 0,
  monthCost: 0,
  monthDay: 1,
  topCircuit: { name: '—', power_w: 0 },
  eagleSummation: 0,
  eagleSummationAgeS: null,
  log: [],
  powerFlow: { grid_w: 0, home_w: 0, clusters: { hvac: 0, fridge: 0, living: 0, bedrooms: 0 } },
  systemHealth: {
    comed:  { state: 'idle', ageSeconds: null },
    eagle:  { state: 'idle', ageSeconds: null },
    refoss: { state: 'idle', ageSeconds: null },
    tcc:    { state: 'idle', ageSeconds: null },
  },
};

function useLiveTelemetry() {
  const [now, setNow] = useState(() => new Date());
  const [data, setData] = useState(EMPTY_DATA);
  const [apiState, setApiState] = useState('loading'); // 'loading' | 'ok' | 'error'

  // Tick the clock every second (independent of API poll cadence).
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Poll /api/energy on its own cadence.
  useEffect(() => {
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const r = await fetch('/api/energy', { cache: 'no-store' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const json = await r.json();
        if (!cancelled) {
          setData(json);
          setApiState('ok');
        }
      } catch (e) {
        if (!cancelled) setApiState('error');
        console.warn('telemetry fetch failed:', e);
      }
    };
    fetchOnce();
    const timer = setInterval(fetchOnce, POLL_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  return { now, data, apiState };
}

function fmtClock(d) {
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return `${h}:${m}:${s}`;
}
function fmtDate(d) {
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase();
}

// Accent palette (still used; live look hardcodes amber).
const ACCENTS = {
  cyan:   { hex: '#4cd6e5', glow: 'rgba(76,214,229,0.55)', soft: 'rgba(76,214,229,0.08)', dim: '#1a4a52' },
  amber:  { hex: '#f6b756', glow: 'rgba(246,183,86,0.55)', soft: 'rgba(246,183,86,0.08)', dim: '#5a3f1c' },
  green:  { hex: '#6bd49a', glow: 'rgba(107,212,154,0.55)', soft: 'rgba(107,212,154,0.08)', dim: '#1f4a36' },
  violet: { hex: '#a48cf0', glow: 'rgba(164,140,240,0.55)', soft: 'rgba(164,140,240,0.08)', dim: '#3a2e5e' },
  red:    { hex: '#ef5d6f', glow: 'rgba(239,93,111,0.55)', soft: 'rgba(239,93,111,0.08)', dim: '#5a2530' },
};

function applyAccent(name) {
  const acc = ACCENTS[name] || ACCENTS.amber;
  document.documentElement.style.setProperty('--acc', acc.hex);
  document.documentElement.style.setProperty('--acc-glow', acc.glow);
  document.documentElement.style.setProperty('--acc-soft', acc.soft);
  document.documentElement.style.setProperty('--acc-dim', acc.dim);
}

Object.assign(window, {
  ACCENTS, useLiveTelemetry, applyAccent,
  fmtClock, fmtDate,
});
