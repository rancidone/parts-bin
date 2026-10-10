import { useEffect, useState, useSyncExternalStore } from 'react'
import { AgentSession } from './agentSession'

export function useAgent() {
  const [session] = useState(() => {
    let storage: Storage | undefined
    try { storage = window.localStorage } catch { /* Browser storage may be disabled. */ }
    return new AgentSession((input, init) => fetch(input, init), storage)
  })
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot)
  useEffect(() => { void session.restore() }, [session])
  return { ...state, submit: session.submit, decide: session.decide, refresh: session.refresh,
    resume: session.resume, newChat: session.newChat, selectConversation: session.selectConversation }
}
