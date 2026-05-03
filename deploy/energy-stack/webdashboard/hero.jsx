/* global React, Panel, HudRing, ChartGrid, AnimatedNumber */
const { useEffect, useState, useMemo } = React;

// ============================================================
// HERO — central ring + 24h chart + price strip
// ============================================================

function HeroPanel({ data, motion, now }) {
  const { currentDemand, currentPrice, costPerHourDollars,
          priceH, demandH, forecast } = data;

  const nowH = now.getHours();

  const ringMax = Math.max(8000, Math.ceil((currentDemand || 0) / 1000) * 1000);

  const W = 760, H = 220;
  const padL = 40, padR = 16, padT = 14, padB = 26;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const safeDemand = (demandH && demandH.length === 24) ? demandH : Array(24).fill(0);
  const safePrice  = (priceH && priceH.length === 24) ? priceH : Array(24).fill(0);
  const maxDemand = Math.max(...safeDemand, 5000);
  const maxPrice  = Math.max(...safePrice, 6);

  const xAt = i => padL + (i / 23) * innerW;
  const yDemand = v => padT + innerH - (v / maxDemand) * innerH;
  const yPrice  = v => padT + innerH - (v / maxPrice)  * innerH;

  const demandPath = useMemo(() => {
    let p = `M ${xAt(0)} ${padT + innerH}`;
    safeDemand.forEach((v, i) => { p += ` L ${xAt(i)} ${yDemand(v)}`; });
    p += ` L ${xAt(23)} ${padT + innerH} Z`;
    return p;
  }, [safeDemand]);
  const demandLine = useMemo(
    () => safeDemand.map((v, i) => `${i === 0 ? 'M' : 'L'} ${xAt(i)} ${yDemand(v)}`).join(' '),
    [safeDemand]
  );
  const pricePts = safePrice.map((v, i) => ({ x: xAt(i), y: yPrice(v), v }));

  const [sweepX, setSweepX] = useState(0);
  useEffect(() => {
    if (motion === 'off') { setSweepX(xAt(nowH)); return; }
    let raf;
    const step = (t) => {
      setSweepX(xAt(nowH) + Math.sin(t / 800) * 6);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [motion, nowH]);

  return (
    <Panel className="hero-panel" flush title={null} meta={null}>
      <span className="corner-tl"></span>
      <span className="corner-tr"></span>

      {/* LEFT: price + cost */}
      <div className="hero-stat">
        <div className="panel-head" style={{ marginBottom: 0 }}>
          <div className="panel-title">
            <span className="panel-title-num">01</span>
            <span>Grid Rate</span>
          </div>
          <div className="panel-meta"><span className="live">LIVE · COMED</span></div>
        </div>

        <div className="kpi-block" style={{ marginTop: 22 }}>
          <div className="stat-label">Current Price</div>
          <div className="kpi-value" style={{ marginTop: 8 }}>
            <AnimatedNumber value={currentPrice} decimals={2} motion={motion} />
            <span style={{ fontSize: 14, color: 'var(--t3)', marginLeft: 8 }}>¢/kWh</span>
          </div>
          <div className={`kpi-delta ${currentPrice > 7 ? 'up' : currentPrice < 3 ? 'down' : ''}`}>
            {currentPrice > 7 ? '▲' : currentPrice < 3 ? '▼' : '◆'}
            <span>{currentPrice > 7 ? 'ELEVATED' : currentPrice > 4 ? 'NORMAL' : 'LOW'} TIER</span>
          </div>
        </div>

        <div className="kpi-block">
          <div className="stat-label">Hour Avg</div>
          <div className="kpi-value" style={{ fontSize: 22, marginTop: 6 }}>
            <AnimatedNumber value={safePrice[nowH] || 0} decimals={2} motion={motion} />
            <span style={{ fontSize: 11, color: 'var(--t3)', marginLeft: 6 }}>¢</span>
          </div>
        </div>

        <div className="kpi-block">
          <div className="stat-label">Cost · Right Now</div>
          <div className="kpi-value" style={{ fontSize: 22, marginTop: 6, color: 'var(--acc)' }}>
            $<AnimatedNumber value={costPerHourDollars} decimals={3} motion={motion} />
            <span style={{ fontSize: 11, color: 'var(--t3)', marginLeft: 6 }}>/HR</span>
          </div>
        </div>

        <div className="spark-row" style={{ marginTop: 'auto' }}>
          {safePrice.slice(-12).map((v, i) => {
            const isNow = i === safePrice.slice(-12).length - 1;
            return (
              <div key={i} className={`spark-bar ${isNow ? 'now' : ''}`}
                style={{ height: `${Math.min(100, (v / maxPrice) * 100)}%` }} />
            );
          })}
        </div>
        <div className="src" style={{ marginTop: 6 }}>SRC · hourlypricing.comed.com</div>
      </div>

      {/* CENTER: ring */}
      <div className="hero-stat" style={{ borderLeft: '1px solid var(--line)' }}>
        <div className="panel-head" style={{ marginBottom: 0 }}>
          <div className="panel-title">
            <span className="panel-title-num">02</span>
            <span>Whole-Home Demand</span>
          </div>
          <div className="panel-meta"><span className="live">EAGLE-3 · 30s</span></div>
        </div>

        <div className="ring-stage">
          <HudRing value={currentDemand || 0} max={ringMax} accent="cyan" motion={motion} />
          <div className="center-readout">
            <div className="center-eyebrow">INSTANTANEOUS · WATTS</div>
            <div className="center-value">
              <AnimatedNumber value={currentDemand || 0} decimals={0} motion={motion} />
            </div>
            <div className="center-unit">{((currentDemand || 0) / 1000).toFixed(2)} kW</div>
          </div>
        </div>

        <div className="src" style={{ textAlign: 'center', marginTop: 'auto' }}>
          SRC · 192.168.20.192/cgi-bin/post_manager
        </div>
      </div>

      {/* RIGHT: combined chart */}
      <div className="hero-stat" style={{ borderLeft: '1px solid var(--line)' }}>
        <div className="panel-head" style={{ marginBottom: 0 }}>
          <div className="panel-title">
            <span className="panel-title-num">03</span>
            <span>24h Profile</span>
          </div>
          <div className="panel-meta">DEMAND · PRICE</div>
        </div>

        <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ marginTop: 12, maxHeight: 220 }}>
          <g transform={`translate(${padL}, ${padT})`}>
            <ChartGrid width={innerW} height={innerH} xSteps={6} ySteps={4} />
          </g>

          <path d={demandPath} fill="var(--acc-soft)" stroke="none" />
          <path d={demandLine} fill="none" stroke="var(--acc)" strokeWidth="1.4"
            style={{ filter: 'drop-shadow(0 0 4px var(--acc-glow))' }} />

          {pricePts.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r={i === nowH ? 3 : 1.2}
              fill={i === nowH ? 'var(--acc)' : 'var(--t3)'}
              opacity={i === nowH ? 1 : 0.6} />
          ))}

          <line x1={sweepX} y1={padT} x2={sweepX} y2={padT + innerH}
            className="sweep-line" />
          <circle cx={sweepX} cy={yDemand(currentDemand || 0)} r="3.5" fill="var(--acc)"
            style={{ filter: 'drop-shadow(0 0 6px var(--acc-glow))' }} />

          {[0, 6, 12, 18, 23].map(h => (
            <text key={h} x={xAt(h)} y={H - 8} fontSize="9" textAnchor="middle"
              fill="var(--t3)" fontFamily="var(--mono)" letterSpacing="1">
              {String(h).padStart(2, '0')}:00
            </text>
          ))}
          {[0, 0.5, 1].map(p => {
            const v = maxDemand * p;
            const y = padT + innerH - p * innerH;
            return (
              <text key={p} x={padL - 4} y={y + 3} fontSize="9" textAnchor="end"
                fill="var(--t3)" fontFamily="var(--mono)">
                {v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0)}W
              </text>
            );
          })}
        </svg>

        <div style={{ display: 'flex', gap: 18, fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--t3)', marginTop: 8 }}>
          <span><span style={{ color: 'var(--acc)' }}>━</span> DEMAND</span>
          <span><span style={{ color: 'var(--t2)' }}>•</span> PRICE</span>
        </div>

        <div className="subhead" style={{ marginTop: 14 }}>Next-12h Forecast · ¢/kWh · hist avg</div>
        <div className="forecast-strip" style={{ marginTop: 4 }}>
          {(forecast || []).map((f, i) => {
            const cls = f.isNow ? 'now' : f.price > 7 ? 'high' : f.price < 3 ? 'low' : '';
            return (
              <div key={i} className={`forecast-cell ${cls}`}>
                <div className="fc-h">{String(f.hour).padStart(2, '0')}</div>
                <div className="fc-p">{(f.price || 0).toFixed(1)}</div>
              </div>
            );
          })}
        </div>
      </div>
    </Panel>
  );
}

// ============================================================
// Power flow diagram — GRID → HOME → 4 device clusters, animated pulses.
// Cluster wattages come from the API's powerFlow.clusters.
// ============================================================
function PowerFlowDiagram({ data, motion }) {
  const { currentDemand } = data;
  const clusters = (data.powerFlow && data.powerFlow.clusters) || {};
  const W = 320, H = 360;

  const fmt = w => `${((w || 0) / 1000).toFixed(2)} kW`;

  const nodes = {
    grid:     { x: 160, y: 50,  label: 'GRID',     value: fmt(currentDemand), active: (currentDemand || 0) > 0 },
    home:     { x: 160, y: 175, label: 'HOME',     value: fmt(currentDemand), active: true },
    hvac:     { x: 40,  y: 310, label: 'HVAC',     value: fmt(clusters.hvac),     active: (clusters.hvac     || 0) > 50 },
    fridge:   { x: 120, y: 330, label: 'FRIDGES',  value: fmt(clusters.fridge),   active: (clusters.fridge   || 0) > 30 },
    living:   { x: 200, y: 330, label: 'LIVING',   value: fmt(clusters.living),   active: (clusters.living   || 0) > 30 },
    bedrooms: { x: 280, y: 310, label: 'BEDS',     value: fmt(clusters.bedrooms), active: (clusters.bedrooms || 0) > 30 },
  };

  const edges = [
    { from: 'grid', to: 'home', active: (currentDemand || 0) > 0 },
    { from: 'home', to: 'hvac',     active: nodes.hvac.active },
    { from: 'home', to: 'fridge',   active: nodes.fridge.active },
    { from: 'home', to: 'living',   active: nodes.living.active },
    { from: 'home', to: 'bedrooms', active: nodes.bedrooms.active },
  ];

  const [pulse, setPulse] = useState(0);
  useEffect(() => {
    if (motion === 'off') return;
    let raf;
    const step = (t) => { setPulse((t / 1500) % 1); raf = requestAnimationFrame(step); };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [motion]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxHeight: 360 }}>
      <defs>
        <pattern id="dotgrid" width="16" height="16" patternUnits="userSpaceOnUse">
          <circle cx="0" cy="0" r="0.6" fill="var(--line-2)" />
        </pattern>
      </defs>
      <rect width={W} height={H} fill="url(#dotgrid)" opacity="0.5" />

      {edges.map((e, i) => {
        const a = nodes[e.from], b = nodes[e.to];
        return (
          <g key={i}>
            <line x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              className={e.active ? 'flow-path-active' : 'flow-path'} />
            {e.active && motion !== 'off' && [0, 0.33, 0.66].map((off, j) => {
              const k = (pulse + off) % 1;
              const px = a.x + (b.x - a.x) * k;
              const py = a.y + (b.y - a.y) * k;
              return <circle key={j} cx={px} cy={py} r="2.4" className="flow-pulse" opacity={0.9 - k * 0.7} />;
            })}
          </g>
        );
      })}

      {Object.entries(nodes).map(([key, n]) => (
        <g key={key}>
          <rect x={n.x - 32} y={n.y - 14} width="64" height="28" rx="2"
            className={n.active ? 'flow-node-active' : 'flow-node-bg'} />
          <text x={n.x} y={n.y - 2} textAnchor="middle" className="flow-node-label">{n.label}</text>
          <text x={n.x} y={n.y + 9} textAnchor="middle" className="flow-node-value"
            fill={n.active ? 'var(--acc)' : 'var(--t3)'}>{n.value}</text>
        </g>
      ))}
    </svg>
  );
}

Object.assign(window, { HeroPanel, PowerFlowDiagram });
