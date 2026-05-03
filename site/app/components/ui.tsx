export function SectionWrap({ children }: { children: React.ReactNode }) {
  return (
    <div className="sec-wrap">
      {children}
    </div>
  )
}

export function SectionTitle({ title, sub }: { title: string; sub: string }) {
  return (
    <>
      <div style={{ fontSize: '22px', letterSpacing: '4px', marginBottom: '8px' }}>{title}</div>
      <div style={{ color: 'var(--grey)', fontSize: '11px', letterSpacing: '2px', marginBottom: '32px' }}>
        {sub}
      </div>
    </>
  )
}

export function Spinner() {
  return (
    <div style={{ textAlign: 'center', padding: '60px', color: 'var(--grey)', fontSize: '11px', letterSpacing: '3px' }}>
      LOADING...
    </div>
  )
}
