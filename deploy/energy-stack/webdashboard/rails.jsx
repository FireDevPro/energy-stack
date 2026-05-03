/* global React, Panel, AnimatedNumber, PowerFlowDiagram */

// ============================================================
// LEFT RAIL — Active Loads (Refoss per-circuit), Power Quality, Climate
// ============================================================
function LeftRail({ data, motion }) {
  const allDevices = data.devices || [];
  const activeDevices = allDevices.filter(d => d.on);
  const offDevices = allDevices.filter(d => !d.on);
  const totalActive = activeDevices.reduce((s, d) => s + d.power, 0);

  return (
    <>
      <Panel title="Active Loads" num="04" meta={<span className="live">REFOSS · {allDevices.length}CH</span>}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10, fontFamily: 'var(--mono)', fontSize: 11 }}>
          <span style={{ color: 'var(--t3)', letterSpacing: '0.16em' }}>{activeDevices.length} ON · {offDevices.length} STANDBY</span>
          <span style={{ color: 'var(--acc)' }}>{(totalActive / 1000).toFixed(2)} kW</span>
        </div>
        <div className="devices-list">
          {activeDevices.map(d => {
            const pct = Math.min(100, (d.power / 5000) * 100);
            return (
              <div key={d.channel || d.name} className="device-row on">
                <div className="dot-on"></div>
                <div className="device-name">
                  {d.name}
                  <div style={{ height: 2, background: 'var(--bg-3)', marginTop: 4, position: 'relative' }}>
                    <div style={{ position: 'absolute', inset: '0 auto 0 0', width: `${pct}%`, background: 'var(--acc)', boxShadow: '0 0 4px var(--acc-glow)' }}></div>
                  </div>
                </div>
                <div className="device-power">{d.power.toFixed(0)}<span style={{ color: 'var(--t4)', fontSize: 9, marginLeft: 2 }}>W</span></div>
              </div>
            );
          })}
          {offDevices.slice(0, 6).map(d => (
            <div key={d.channel || d.name} className="device-row off">
              <div className="dot-off"></div>
              <div className="device-name">{d.name}</div>
              <div className="device-power">—</div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Power Quality" num="05" meta="L1 · L2 · MAINS PF">
        <div className="gauges-row">
          <div className="mini-gauge">
            <div className="lbl">PF</div>
            <div className="val"><AnimatedNumber value={data.pf || 0} decimals={2} motion={motion} /></div>
            <div className="bar"><span style={{ width: `${Math.min(100, (data.pf || 0) * 100)}%` }}></span></div>
          </div>
          <div className="mini-gauge">
            <div className="lbl">L1</div>
            <div className="val"><AnimatedNumber value={data.voltL1 || 0} decimals={1} motion={motion} /><span className="unit">V</span></div>
            <div className="bar"><span style={{ width: `${Math.min(100, Math.max(0, ((data.voltL1 || 110) - 110) / 20 * 100))}%` }}></span></div>
          </div>
          <div className="mini-gauge">
            <div className="lbl">L2</div>
            <div className="val"><AnimatedNumber value={data.voltL2 || 0} decimals={1} motion={motion} /><span className="unit">V</span></div>
            <div className="bar"><span style={{ width: `${Math.min(100, Math.max(0, ((data.voltL2 || 110) - 110) / 20 * 100))}%` }}></span></div>
          </div>
        </div>
        <div className="src" style={{ marginTop: 12 }}>SRC · refoss em16p · 192.168.20.140</div>
      </Panel>

      <Panel title="Climate" num="06" meta="HONEYWELL TCC · pending">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <div className="mini-gauge">
            <div className="lbl">INDOOR</div>
            <div className="val"><AnimatedNumber value={data.indoor || 0} decimals={1} motion={motion} /><span className="unit">°F</span></div>
          </div>
          <div className="mini-gauge">
            <div className="lbl">OUTDOOR</div>
            <div className="val"><AnimatedNumber value={data.outdoor || 0} decimals={1} motion={motion} /><span className="unit">°F</span></div>
          </div>
        </div>
        <div style={{ marginTop: 10, padding: '10px 12px', background: 'var(--bg-2)', border: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.16em' }}>
          <span style={{ color: 'var(--t3)' }}>HVAC MODE</span>
          <span style={{ color: data.hvacMode === 'COOL' ? 'var(--acc)' : data.hvacMode === 'HEAT' ? 'var(--warn)' : 'var(--t3)' }}>{data.hvacMode || '—'}</span>
        </div>
        <div style={{ marginTop: 6, padding: '10px 12px', background: 'var(--bg-2)', border: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.16em' }}>
          <span style={{ color: 'var(--t3)' }}>SETPOINT</span>
          <span style={{ color: 'var(--t1)' }}>{(data.setpoint || 0).toFixed(0)}°F</span>
        </div>
        <div className="src" style={{ marginTop: 10 }}>PHASE 4 · placeholder values</div>
      </Panel>
    </>
  );
}

// ============================================================
// RIGHT RAIL — Cost Stack, Power Flow, Activity Log
// ============================================================
function RightRail({ data, motion, now }) {
  const hoursRemaining = Math.max(0, 24 - now.getHours());
  const projDay = (data.todayCost || 0) + ((data.costPerHourDollars || 0) * hoursRemaining);
  const projMonth = (data.monthCost || 0)
    + projDay * Math.max(0, 30 - (data.monthDay || 1));

  return (
    <>
      <Panel title="Cost Stack" num="07" meta="$ · CENTS">
        <div className="cost-stack">
          <div className="cost-row">
            <span className="lbl">Right Now</span>
            <span className="val" style={{ color: 'var(--acc)' }}>
              $<AnimatedNumber value={data.costPerHourDollars || 0} decimals={3} motion={motion} /><span className="unit">/HR</span>
            </span>
          </div>
          <div className="cost-row">
            <span className="lbl">Today So Far</span>
            <span className="val">$<AnimatedNumber value={data.todayCost || 0} decimals={2} motion={motion} /></span>
          </div>
          <div className="cost-row proj">
            <span className="lbl">Projected · Today</span>
            <span className="val">$<AnimatedNumber value={projDay} decimals={2} motion={motion} /></span>
          </div>
          <div className="cost-row proj">
            <span className="lbl">Projected · Month</span>
            <span className="val">$<AnimatedNumber value={projMonth} decimals={0} motion={motion} /></span>
          </div>
        </div>
      </Panel>

      <Panel title="Power Flow" num="08" meta="REAL-TIME">
        <PowerFlowDiagram data={data} motion={motion} />
      </Panel>

      <Panel title="Activity Log" num="09" meta="DERIVED">
        <div className="log-list">
          {(data.log || []).map((l, i) => {
            const t = new Date(now.getTime() - (l.dt || 0) * 1000);
            const hh = String(t.getHours()).padStart(2, '0');
            const mm = String(t.getMinutes()).padStart(2, '0');
            const ss = String(t.getSeconds()).padStart(2, '0');
            return (
              <div key={i} className="log-row">
                <span className="log-time">{hh}:{mm}:{ss}</span>
                <span className={`log-tag ${l.tag}`}>{l.tag === 'ok' ? '◆' : l.tag === 'warn' ? '▲' : l.tag === 'bad' ? '✕' : '◇'}</span>
                <span className="log-msg">{l.msg}</span>
              </div>
            );
          })}
        </div>
      </Panel>
    </>
  );
}

// ============================================================
// BOTTOM strip — daily/weekly/monthly + Top Circuit + EAGLE summation
// ============================================================
function BottomStrip({ data, motion }) {
  const summationAge = data.eagleSummationAgeS;
  const ageLabel = summationAge == null
    ? 'unknown'
    : summationAge < 60 ? `synced ${Math.round(summationAge)}s`
    : summationAge < 3600 ? `synced ${Math.round(summationAge / 60)}m`
    : `synced ${Math.round(summationAge / 3600)}h`;
  const ageColor = summationAge != null && summationAge < 120 ? 'var(--ok)' : 'var(--warn)';

  const top = data.topCircuit || { name: '—', power_w: 0 };

  return (
    <div className="bottom-strip">
      <div className="tile">
        <span className="corner-tl"></span>
        <div className="tile-head">
          <span className="tile-label">Today · Energy</span>
          <span className="tile-unit">kWh</span>
        </div>
        <div className="tile-value"><AnimatedNumber value={data.todayKwh || 0} decimals={1} motion={motion} /></div>
        <div className="tile-foot">
          <span>$<AnimatedNumber value={data.todayCost || 0} decimals={2} motion={motion} /></span>
          <span style={{ color: 'var(--t3)' }}>refoss em:1+em:7</span>
        </div>
      </div>

      <div className="tile">
        <span className="corner-tl"></span>
        <div className="tile-head">
          <span className="tile-label">Week · Energy</span>
          <span className="tile-unit">kWh</span>
        </div>
        <div className="tile-value"><AnimatedNumber value={data.weekKwh || 0} decimals={0} motion={motion} /></div>
        <div className="tile-foot">
          <span>${(data.weekCost || 0).toFixed(2)}</span>
          <span style={{ color: 'var(--t3)' }}>since Sun</span>
        </div>
      </div>

      <div className="tile">
        <span className="corner-tl"></span>
        <div className="tile-head">
          <span className="tile-label">Month · Energy</span>
          <span className="tile-unit">kWh</span>
        </div>
        <div className="tile-value"><AnimatedNumber value={data.monthKwh || 0} decimals={0} motion={motion} /></div>
        <div className="tile-foot">
          <span>${(data.monthCost || 0).toFixed(0)}</span>
          <span style={{ color: 'var(--t3)' }}>day {data.monthDay || 1}/30</span>
        </div>
      </div>

      <div className="tile">
        <span className="corner-tl"></span>
        <div className="tile-head">
          <span className="tile-label">Top Circuit</span>
          <span className="tile-unit">W</span>
        </div>
        <div className="tile-value" style={{ fontSize: 22 }}>
          <AnimatedNumber value={top.power_w || 0} decimals={0} motion={motion} />
        </div>
        <div className="tile-foot">
          <span style={{ color: 'var(--acc)', maxWidth: '70%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{top.name}</span>
          <span style={{ color: 'var(--t3)' }}>refoss live</span>
        </div>
      </div>

      <div className="tile">
        <span className="corner-tl"></span>
        <div className="tile-head">
          <span className="tile-label">Meter · Summation</span>
          <span className="tile-unit">EAGLE-3</span>
        </div>
        <div className="tile-value" style={{ fontSize: 22 }}>
          {(data.eagleSummation || 0).toFixed(2)}
        </div>
        <div className="tile-foot">
          <span>kWh delivered · lifetime</span>
          <span style={{ color: ageColor }}>● {ageLabel}</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { LeftRail, RightRail, BottomStrip });
