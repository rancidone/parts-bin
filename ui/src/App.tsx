import { useState } from 'react'
import { Chat } from './Chat'
import { Inventory } from './Inventory'
import { Settings } from './Settings'
import styles from './App.module.css'

type Surface = 'chat' | 'inventory' | 'settings'

export default function App() {
  const [surface, setSurface] = useState<Surface>('chat')

  return (
    <div className={styles.app}>
      <nav className={styles.nav}>
        <span className={styles.logo}>Parts Bin</span>
        <div className={styles.tabs}>
          <button
            className={`${styles.tab} ${surface === 'chat' ? styles.active : ''}`}
            onClick={() => setSurface('chat')}
          >
            Chat
          </button>
          <button
            className={`${styles.tab} ${surface === 'inventory' ? styles.active : ''}`}
            onClick={() => setSurface('inventory')}
          >
            Inventory
          </button>
          <button
            className={`${styles.tab} ${surface === 'settings' ? styles.active : ''}`}
            onClick={() => setSurface('settings')}
          >
            Settings
          </button>
        </div>
      </nav>
      <main className={styles.main}>
        <div style={{ display: surface === 'chat' ? 'contents' : 'none' }}><Chat /></div>
        <div style={{ display: surface === 'inventory' ? 'contents' : 'none' }}><Inventory active={surface === 'inventory'} /></div>
        <div style={{ display: surface === 'settings' ? 'contents' : 'none' }}><Settings active={surface === 'settings'} /></div>
      </main>
    </div>
  )
}
