import { NavLink, useMatch, useNavigate } from 'react-router'
import { Chat } from './Chat'
import { Inventory } from './Inventory'
import { QuantityProvider } from './QuantityProvider'
import styles from './App.module.css'

export default function App() {
  const navigate = useNavigate()
  const chatActive = useMatch('/') !== null
  const inventoryMatch = useMatch('/inventory')
  const partMatch = useMatch('/inventory/:partId')
  const partId = partMatch?.params.partId
  const selectedPartId = partId && /^[1-9]\d*$/.test(partId) && Number.isSafeInteger(Number(partId))
    ? Number(partId) : null
  const inventoryActive = inventoryMatch !== null || selectedPartId !== null
  const tabClass = ({ isActive }: { isActive: boolean }) => `${styles.tab} ${isActive ? styles.active : ''}`

  return (
    <QuantityProvider><div className={styles.app}>
      <nav className={styles.nav}>
        <span className={styles.logo}>Parts Bin</span>
        <div className={styles.tabs}>
          <NavLink to="/" end className={tabClass}>
            Chat
          </NavLink>
          <NavLink to="/inventory" className={tabClass}>
            Inventory
          </NavLink>
        </div>
      </nav>
      <main className={styles.main}>
        <div style={{ display: chatActive ? 'contents' : 'none' }}><Chat onOpenPart={id => navigate(`/inventory/${id}`)} /></div>
        <div style={{ display: inventoryActive ? 'contents' : 'none' }}><Inventory active={inventoryActive} selectedPartId={selectedPartId} onClearSelection={() => navigate('/inventory')} /></div>
        {!chatActive && !inventoryActive && <p role="alert">Page not found. Choose Chat or Inventory above.</p>}
      </main>
    </div></QuantityProvider>
  )
}
