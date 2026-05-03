/* global React, ReactDOM, useLiveTelemetry, applyAccent, fmtClock, fmtDate,
   HeroPanel, LeftRail, RightRail, BottomStrip */
const { useEffect } = React;

// Live look — no more tweaks panel. Locked to the values the design phase
// landed on. To change, edit here and redeploy.
const LOOK = {
  accent: 'amber',
  motion: 'moderate',
  density: 'comfortable',
  scanlines: true,
  ticker: true,
};

function statusPillClass(state) {
  if (state === 'ok') return 'status-pill ok';
  if (state === 'warn') return 'status-pill warn';
  if (state === 'bad') return 'status-pill bad';
  return 'status-pill idle';
}

function ageLabel(ageSeconds, state) {
  // Only surface the age when something's actually wrong. A healthy ComEd
  // reading is naturally 5-15 min old by clock time (publication lag); we
  // don't want "COMED · 7m" cluttering the pill on an otherwise fine system.
  if (ageSeconds == null) return '';
  if (state === 'ok') return '';
  if (ageSeconds < 3600) return ` · ${Math.round(ageSeconds / 60)}m`;
  return ` · ${Math.round(ageSeconds / 3600)}h`;
}

function Topbar({ now, data, motion, ticker }) {
  const sh = data.systemHealth || {};
  return (
    <div className="topbar">
      <div className="brand">
        <svg className="brand-mark" viewBox="0 0 28 28">
          <rect x="2" y="2" width="24" height="24" fill="none" stroke="var(--acc)" strokeWidth="1" />
          <rect x="6" y="6" width="16" height="16" fill="none" stroke="var(--acc)" strokeWidth="0.5" opacity="0.6" />
          <circle cx="14" cy="14" r="3" fill="var(--acc)" style={{ filter: 'drop-shadow(0 0 4px var(--acc-glow))' }} />
          <line x1="14" y1="2" x2="14" y2="6" stroke="var(--acc)" strokeWidth="1" />
          <line x1="14" y1="22" x2="14" y2="26" stroke="var(--acc)" strokeWidth="1" />
          <line x1="2" y1="14" x2="6" y2="14" stroke="var(--acc)" strokeWidth="1" />
          <line x1="22" y1="14" x2="26" y2="14" stroke="var(--acc)" strokeWidth="1" />
        </svg>
        <div className="brand-text">
          <span className="brand-name">HOME · TELEMETRY</span>
          <span className="brand-sub">NODE · 192.168.20.10 · ENERGY-STACK</span>
        </div>
      </div>

      {ticker ? (
        <div className="ticker">
          <div className="ticker-track">
            {[...Array(2)].map((_, k) => (
              <React.Fragment key={k}>
                <span className="ticker-item"><span className="ticker-key">COMED</span><span className="ticker-val">{(data.currentPrice || 0).toFixed(2)}¢</span></span>
                <span className="ticker-dot"></span>
                <span className="ticker-item"><span className="ticker-key">DEMAND</span><span className="ticker-val">{((data.currentDemand || 0)/1000).toFixed(2)} kW</span></span>
                <span className="ticker-dot"></span>
                <span className="ticker-item"><span className="ticker-key">RATE</span><span className="ticker-val">${(data.costPerHourDollars || 0).toFixed(3)}/hr</span></span>
                <span className="ticker-dot"></span>
                <span className="ticker-item"><span className="ticker-key">PF</span><span className="ticker-val">{(data.pf || 0).toFixed(2)}</span></span>
                <span className="ticker-dot"></span>
                <span className="ticker-item"><span className="ticker-key">L1/L2</span><span className="ticker-val">{(data.voltL1 || 0).toFixed(1)} / {(data.voltL2 || 0).toFixed(1)} V</span></span>
                <span className="ticker-dot"></span>
                <span className="ticker-item"><span className="ticker-key">EAGLE</span><span className="ticker-val">{(data.eagleSummation || 0).toFixed(2)} kWh</span></span>
                <span className="ticker-dot"></span>
                <span className="ticker-item"><span className="ticker-key">TODAY</span><span className="ticker-val">{(data.todayKwh || 0).toFixed(1)} kWh · ${(data.todayCost || 0).toFixed(2)}</span></span>
                <span className="ticker-dot"></span>
                <span className="ticker-item"><span className="ticker-key">TOP</span><span className="ticker-val">{data.topCircuit?.name || '—'} · {Math.round(data.topCircuit?.power_w || 0)} W</span></span>
                <span className="ticker-dot"></span>
              </React.Fragment>
            ))}
          </div>
        </div>
      ) : <div></div>}

      <div className="clock">
        <div className="status-row">
          <div className={statusPillClass(sh.comed?.state)}><div className="status-led"></div>COMED{ageLabel(sh.comed?.ageSeconds, sh.comed?.state)}</div>
          <div className={statusPillClass(sh.eagle?.state)}><div className="status-led"></div>EAGLE-3{ageLabel(sh.eagle?.ageSeconds, sh.eagle?.state)}</div>
          <div className={statusPillClass(sh.refoss?.state)}><div className="status-led"></div>REFOSS{ageLabel(sh.refoss?.ageSeconds, sh.refoss?.state)}</div>
          <div className={statusPillClass(sh.tcc?.state)}><div className="status-led"></div>TCC</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
          <span className="clock-time">{fmtClock(now)}</span>
          <span style={{ fontSize: 9, letterSpacing: '0.18em', color: 'var(--t3)' }}>{fmtDate(now)} · CDT</span>
        </div>
      </div>
    </div>
  );
}

function Footer() {
  return (
    <div className="footer">
      <span>INFLUXDB · BUCKET <span className="acc">energy</span> · RETENTION ∞</span>
      <span>GRAFANA 11.4.0 · STACK <span className="acc">v5</span></span>
      <span>POLLERS · COMED 60s · EAGLE 30s · REFOSS 30s · TCC 600s</span>
      <span>BUILD <span className="acc">live</span> · SOPS-AGE</span>
    </div>
  );
}

function App() {
  const { now, data, apiState } = useLiveTelemetry();

  useEffect(() => { applyAccent(LOOK.accent); }, []);
  useEffect(() => { document.body.dataset.motion = LOOK.motion; }, []);

  return (
    <>
      <div className="bg-grid"></div>
      {LOOK.scanlines && <div className="scanlines"></div>}
      <div className="vignette"></div>

      <div className="app" data-density={LOOK.density}>
        <Topbar now={now} data={data} motion={LOOK.motion} ticker={LOOK.ticker} />

        <HeroPanel data={data} motion={LOOK.motion} now={now} />

        <div className="main-grid">
          <div className="col">
            <LeftRail data={data} motion={LOOK.motion} />
          </div>

          <div className="col">
            <BottomStrip data={data} motion={LOOK.motion} />
          </div>

          <div className="col">
            <RightRail data={data} motion={LOOK.motion} now={now} />
          </div>
        </div>

        <Footer />

        {apiState === 'error' && (
          <div style={{
            position: 'fixed', bottom: 12, right: 12, zIndex: 99,
            fontFamily: 'var(--mono)', fontSize: 10, padding: '6px 10px',
            background: 'rgba(239,93,111,0.15)', border: '1px solid rgba(239,93,111,0.4)',
            color: 'var(--bad)', letterSpacing: '0.16em',
          }}>
            API · DEGRADED · serving stale
          </div>
        )}
      </div>
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
