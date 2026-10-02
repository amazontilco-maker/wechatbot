#!/usr/bin/env node
// wb: the interface Claude drives. Read digests, draft replies, approve to send.
import { openDb, findConv } from './db.js'

const [cmd, ...args] = process.argv.slice(2)
const db = openDb()
const fmtTime = (ts) => new Date(ts).toISOString().slice(0, 16).replace('T', ' ')
const out = (s = '') => console.log(s)

function formatMessages(rows) {
  return rows.map((m) => `[${fmtTime(m.ts)}] ${m.direction === 'out' ? 'ME' : m.sender}: ${m.text}`).join('\n')
}

const commands = {
  // wb digest [--all] : unread messages per conversation, with your notes for context
  digest() {
    const all = args.includes('--all')
    const convs = db.prepare('SELECT * FROM convs ORDER BY name').all()
    let shown = 0
    for (const c of convs) {
      const rows = db
        .prepare('SELECT * FROM messages WHERE conv_id = ? AND ts > ? ORDER BY ts')
        .all(c.id, all ? 0 : c.last_read_ts)
      const unread = rows.filter((m) => m.direction === 'in')
      if (!unread.length && !all) continue
      shown++
      out(`=== ${c.name} (${c.kind}) — ${unread.length} new ===`)
      if (c.notes) out(`Notes: ${c.notes}`)
      const ctx = db
        .prepare('SELECT * FROM messages WHERE conv_id = ? AND ts <= ? ORDER BY ts DESC LIMIT 5')
        .all(c.id, all ? 0 : c.last_read_ts)
        .reverse()
      if (ctx.length) out(`-- earlier context --\n${formatMessages(ctx)}\n-- new --`)
      out(formatMessages(rows))
      out()
    }
    if (!shown) out('No new messages.')
  },

  // wb convs
  convs() {
    const rows = db
      .prepare(
        `SELECT c.name, c.kind, c.notes, COUNT(m.id) AS n, MAX(m.ts) AS last
         FROM convs c LEFT JOIN messages m ON m.conv_id = c.id GROUP BY c.id ORDER BY last DESC`
      )
      .all()
    for (const r of rows) out(`${r.name} [${r.kind}] ${r.n} msgs, last ${r.last ? fmtTime(r.last) : '-'}${r.notes ? ` | ${r.notes}` : ''}`)
  },

  // wb thread <conv> [limit]
  thread() {
    const c = findConv(db, args[0])
    const limit = Number(args[1]) || 50
    const rows = db.prepare('SELECT * FROM messages WHERE conv_id = ? ORDER BY ts DESC LIMIT ?').all(c.id, limit).reverse()
    out(`=== ${c.name} ===\n${formatMessages(rows)}`)
  },

  // wb note <conv> <text...> : persistent context about a supplier/group
  note() {
    const c = findConv(db, args[0])
    const text = args.slice(1).join(' ')
    db.prepare('UPDATE convs SET notes = ? WHERE id = ?').run(text, c.id)
    out(`Notes for ${c.name} set.`)
  },

  // wb read [conv...] : mark conversations (default all) as read
  read() {
    const now = Date.now()
    if (!args.length) db.prepare('UPDATE convs SET last_read_ts = ?').run(now)
    else for (const a of args) db.prepare('UPDATE convs SET last_read_ts = ? WHERE id = ?').run(now, findConv(db, a).id)
    out('Marked read.')
  },

  // wb draft <conv> <text...> : queue a draft. NOTHING is sent until `wb approve`.
  draft() {
    const c = findConv(db, args[0])
    const text = args.slice(1).join(' ').trim()
    if (!text) throw new Error('empty message')
    const { lastInsertRowid } = db
      .prepare("INSERT INTO outbox (conv_id, text, status, created) VALUES (?, ?, 'draft', ?)")
      .run(c.id, text, Date.now())
    out(`Draft #${lastInsertRowid} -> ${c.name}: ${text}`)
  },

  // wb outbox : list drafts/pending/recent
  outbox() {
    const rows = db
      .prepare(
        `SELECT o.*, c.name FROM outbox o JOIN convs c ON c.id = o.conv_id
         WHERE o.status IN ('draft','approved','failed') ORDER BY o.id`
      )
      .all()
    if (!rows.length) return out('Outbox empty.')
    for (const r of rows) out(`#${r.id} [${r.status}] -> ${r.name}: ${r.text}${r.error ? `  (error: ${r.error})` : ''}`)
  },

  // wb approve <id...|all> : release drafts to the bridge for sending (only run after the user confirms)
  approve() {
    const ids = args[0] === 'all'
      ? db.prepare("SELECT id FROM outbox WHERE status = 'draft'").all().map((r) => r.id)
      : args.map(Number)
    for (const id of ids) {
      const r = db.prepare("UPDATE outbox SET status='approved' WHERE id = ? AND status IN ('draft','failed')").run(id)
      out(r.changes ? `#${id} approved; bridge will send it.` : `#${id} not found or not a draft.`)
    }
  },

  // wb discard <id...|all>
  discard() {
    const ids = args[0] === 'all'
      ? db.prepare("SELECT id FROM outbox WHERE status = 'draft'").all().map((r) => r.id)
      : args.map(Number)
    for (const id of ids) db.prepare("UPDATE outbox SET status='discarded' WHERE id = ? AND status = 'draft'").run(id)
    out('Discarded.')
  },
}

try {
  if (!commands[cmd]) {
    out(`Usage: wb <${Object.keys(commands).join('|')}> ...`)
    process.exit(cmd ? 1 : 0)
  }
  commands[cmd]()
} catch (e) {
  console.error(`Error: ${e.message}`)
  process.exit(1)
}
