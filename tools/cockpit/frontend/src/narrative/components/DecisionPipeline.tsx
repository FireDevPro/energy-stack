export function DecisionPipeline() {
  return (
    <div
      className="narrative-placeholder"
      data-testid="narrative-decision-pipeline"
      style={{
        flex: 'unset',
        padding: '12px 18px',
        background: 'transparent',
        borderBottom: 'none',
        flexDirection: 'row',
        gap: 16,
        justifyContent: 'flex-start',
      }}
    >
      <span className="narrative-placeholder-title" style={{ fontSize: 14 }}>
        Decision pipeline
      </span>
      <span>weather → day type → schedule → price → winner → supervisor → action</span>
      <span className="narrative-placeholder-note" style={{ marginLeft: 'auto' }}>
        wired in PR 4
      </span>
    </div>
  )
}
