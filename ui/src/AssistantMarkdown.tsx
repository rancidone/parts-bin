import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Part } from './types'
import styles from './Chat.module.css'

type TextNode = { value?: string; children?: TextNode[] }
function nodeText(node: TextNode): string {
  return node.value ?? node.children?.map(nodeText).join('') ?? ''
}

export function AssistantMarkdown({ text, parts, onOpenPart }: {
  text: string; parts: Part[]; onOpenPart: (id: number) => void
}) {
  return <div className={`${styles.assistantBubble} ${styles.markdown}`}><Markdown remarkPlugins={[remarkGfm]} components={{
    table: ({ children }) => <div className={styles.tableScroll} tabIndex={0} role="region" aria-label="Response table"><table>{children}</table></div>,
    td: ({ node, children, ...props }) => {
      const identity = node ? nodeText(node).trim() : ''
      const matches = parts.filter(part => part.part_number === identity)
      return <td {...props}>{matches.length === 1
        ? <button className={styles.partLink} onClick={() => onOpenPart(matches[0].id!)} aria-label={`View ${identity} in inventory`}>{children}</button>
        : children}</td>
    },
  }}>{text}</Markdown></div>
}
