/* global React */
const { useEffect, useRef, useState } = React;

// ============================================================
// Primitives — reusable HUD elements
// ============================================================

function Panel({ title, num, meta, children, className = '', flush = false }) {
  return (
    <div className={`panel ${flush ? 'flush' : ''} ${className}`}>
      <span className="corner-tl"></span>
      <span className="corner-tr"></span>
      {(title || meta) && (
        <div className="panel-head">
          <div className="panel-title">
            {num && <span className="panel-title-num">{num}</span>}
            <span>{title}</span>
            <span className="panel-title-line"></span>
          </div>
          {meta && <div className="panel-meta">{meta}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

// Animated SVG ring with arc fill + tick marks + rotating sweep
function HudRing({ value, max, label, unit, accent, motion, scenario }) {
  const cx = 200, cy = 200;
  const ringR = 168;
  const ticks = 60;
  const pct = Math.max(0, Math.min(1, value / max));
  const arc = pct * 270; // 270deg arc
  const startA = -225; // starts at 225° from top going clockwise
  const sweepRef = useRef(0);
  const [sweep, setSweep] = useState(0);

  useEffect(() => {
    if (motion === 'off') return;
    let raf;
    const step = () => {
      sweepRef.current = (sweepRef.current + 0.4) % 360;
      setSweep(sweepRef.current);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [motion]);

  // arc path helper
  const polarToXY = (r, angleDeg) => {
    const a = (angleDeg - 90) * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const arcPath = (r, startDeg, endDeg) => {
    const [x1, y1] = polarToXY(r, startDeg);
    const [x2, y2] = polarToXY(r, endDeg);
    const large = (endDeg - startDeg) > 180 ? 1 : 0;
    return `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`;
  };

  // ticks
  const tickEls = [];
  for (let i = 0; i <= ticks; i++) {
    const a = -135 + (i / ticks) * 270;
    const inner = ringR - 8;
    const outer = i % 5 === 0 ? ringR + 4 : ringR - 2;
    const [x1, y1] = polarToXY(inner, a);
    const [x2, y2] = polarToXY(outer, a);
    const lit = (i / ticks) <= pct;
    tickEls.push(
      <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
        stroke={lit ? 'var(--acc)' : 'var(--line-3)'}
        strokeWidth={i % 5 === 0 ? 1.5 : 0.8}
        opacity={lit ? 0.95 : 0.5} />
    );
  }

  // value labels at ticks
  const valLabels = [];
  for (let i = 0; i <= 6; i++) {
    const a = -135 + (i / 6) * 270;
    const [x, y] = polarToXY(ringR + 18, a);
    const v = Math.round((i / 6) * max);
    valLabels.push(
      <text key={i} x={x} y={y + 3} textAnchor="middle"
        fontSize="9" fill="var(--t3)" fontFamily="var(--mono)" letterSpacing="1">
        {v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v}
      </text>
    );
  }

  return (
    <svg viewBox="0 0 400 400" width="100%" height="100%" style={{ maxHeight: 380 }}>
      {/* outer faint ring */}
      <circle cx={cx} cy={cy} r={ringR + 22} fill="none" stroke="var(--line)" strokeWidth="0.5" />
      {/* dotted reference */}
      <circle cx={cx} cy={cy} r={ringR + 8} fill="none" stroke="var(--line-2)" strokeWidth="0.5" strokeDasharray="2 4" opacity="0.5" />
      {/* base track */}
      <path d={arcPath(ringR, -135, 135)} stroke="var(--line-2)" strokeWidth="3" fill="none" opacity="0.4" />
      {/* progress arc */}
      <path d={arcPath(ringR, -135, -135 + arc)}
        stroke="var(--acc)" strokeWidth="3" fill="none"
        strokeLinecap="round"
        style={{ filter: 'drop-shadow(0 0 6px var(--acc-glow))' }} />
      {/* ticks */}
      {tickEls}
      {/* tick labels */}
      {valLabels}
      {/* sweep line */}
      {motion !== 'off' && (
        <line
          x1={cx} y1={cy}
          x2={cx + (ringR - 18) * Math.cos((sweep - 90) * Math.PI / 180)}
          y2={cy + (ringR - 18) * Math.sin((sweep - 90) * Math.PI / 180)}
          stroke="var(--acc)" strokeWidth="1" opacity="0.18"
        />
      )}
      {/* inner decorative ring */}
      <circle cx={cx} cy={cy} r={ringR - 30} fill="none" stroke="var(--line-2)" strokeWidth="0.5" opacity="0.6" />
      <circle cx={cx} cy={cy} r={ringR - 60} fill="none" stroke="var(--line)" strokeWidth="0.5" />
      {/* center crosshair */}
      <line x1={cx - 4} y1={cy} x2={cx + 4} y2={cy} stroke="var(--t4)" strokeWidth="0.5" />
      <line x1={cx} y1={cy - 4} x2={cx} y2={cy + 4} stroke="var(--t4)" strokeWidth="0.5" />
      {/* anchor markers at extents */}
      {[-135, -90, 0, 90, 135].map((a, i) => {
        const [x, y] = polarToXY(ringR + 32, a);
        return (
          <g key={i} opacity="0.5">
            <circle cx={x} cy={y} r="1.5" fill="var(--t3)" />
          </g>
        );
      })}
    </svg>
  );
}

// Background SVG grid for charts
function ChartGrid({ width, height, xSteps = 6, ySteps = 4 }) {
  const lines = [];
  for (let i = 0; i <= xSteps; i++) {
    const x = (i / xSteps) * width;
    lines.push(<line key={'x'+i} x1={x} y1={0} x2={x} y2={height} stroke="var(--line)" strokeWidth="0.5" opacity="0.5" />);
  }
  for (let i = 0; i <= ySteps; i++) {
    const y = (i / ySteps) * height;
    lines.push(<line key={'y'+i} x1={0} y1={y} x2={width} y2={y} stroke="var(--line)" strokeWidth="0.5" opacity="0.5" />);
  }
  return <g>{lines}</g>;
}

// Animated counter (eases from prev value to current)
function AnimatedNumber({ value, decimals = 0, motion }) {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);
  const startRef = useRef(performance.now());

  useEffect(() => {
    if (motion === 'off') { setDisplay(value); return; }
    fromRef.current = display;
    startRef.current = performance.now();
    const dur = 600;
    let raf;
    const step = (t) => {
      const k = Math.min(1, (t - startRef.current) / dur);
      const eased = 1 - Math.pow(1 - k, 3);
      const v = fromRef.current + (value - fromRef.current) * eased;
      setDisplay(v);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, motion]);

  return <>{Number(display).toFixed(decimals)}</>;
}

Object.assign(window, { Panel, HudRing, ChartGrid, AnimatedNumber });
