import { useEffect, useState } from 'react'
import styles from './Settings.module.css'

type JlcStatus = 'not_configured' | 'missing' | 'downloading' | 'ready' | 'error'

interface JlcpartsStatus {
  status: JlcStatus
  path?: string
  size_mb?: number
}

export function Settings({ active }: { active: boolean }) {
  const [jlc, setJlc] = useState<JlcpartsStatus | null>(null)
  const [triggering, setTriggering] = useState(false)

  async function fetchStatus() {
    try {
      const resp = await fetch('/jlcparts/status')
      if (resp.ok) setJlc(await resp.json())
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    if (!active) return
    fetchStatus()
  }, [active])

  // Poll while downloading
  useEffect(() => {
    if (jlc?.status !== 'downloading') return
    const id = setInterval(fetchStatus, 2000)
    return () => clearInterval(id)
  }, [jlc?.status])

  async function startDownload() {
    setTriggering(true)
    try {
      const resp = await fetch('/jlcparts/download', { method: 'POST' })
      if (resp.ok) {
        setJlc(prev => prev ? { ...prev, status: 'downloading' } : { status: 'downloading' })
      }
    } finally {
      setTriggering(false)
    }
  }

  function statusLabel(s: JlcStatus): string {
    switch (s) {
      case 'not_configured': return 'Not configured'
      case 'missing': return 'Not downloaded'
      case 'downloading': return 'Downloading…'
      case 'ready': return 'Ready'
      case 'error': return 'Download error'
    }
  }

  return (
    <div className={styles.container}>
      <h2 className={styles.heading}>Settings</h2>

      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>JLC Parts Database</h3>
        <p className={styles.description}>
          Local JLCPCB/LCSC parts catalog for offline spec lookup.
          The database is several hundred MB and is fetched from the jlcparts project.
        </p>
        {jlc && (
          <div className={styles.row}>
            <span className={`${styles.statusDot} ${styles[jlc.status]}`} />
            <span className={styles.statusText}>
              {statusLabel(jlc.status)}
              {jlc.status === 'ready' && jlc.size_mb != null && ` (${jlc.size_mb} MB)`}
            </span>
            {(jlc.status === 'missing' || jlc.status === 'error') && (
              <button
                className={styles.downloadBtn}
                onClick={startDownload}
                disabled={triggering}
              >
                Download
              </button>
            )}
            {jlc.status === 'ready' && (
              <button
                className={styles.downloadBtn}
                onClick={startDownload}
                disabled={triggering}
              >
                Re-download
              </button>
            )}
          </div>
        )}
        {jlc?.path && <div className={styles.path}>{jlc.path}</div>}
      </section>
    </div>
  )
}
