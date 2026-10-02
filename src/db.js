import { DatabaseSync } from 'node:sqlite'
import { mkdirSync } from 'node:fs'
import { dirname } from 'node:path'

const DB_PATH = process.env.WB_DB || 'data/wechat.db'

export function openDb(path = DB_PATH) {
  if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true })
  const db = new DatabaseSync(path)
  db.exec(`
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS convs (
      id TEXT PRIMARY KEY,            -- wechaty room/contact id
      name TEXT NOT NULL,
      kind TEXT NOT NULL,             -- 'room' | 'person'
      notes TEXT DEFAULT '',          -- your own context: what they supply, terms, etc.
      last_read_ts INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY,
      conv_id TEXT NOT NULL REFERENCES convs(id),
      sender TEXT NOT NULL,
      direction TEXT NOT NULL,        -- 'in' | 'out'
      type TEXT NOT NULL,             -- text | image | file | voice | other
      text TEXT NOT NULL,
      ts INTEGER NOT NULL             -- unix ms
    );
    CREATE INDEX IF NOT EXISTS idx_msg_conv_ts ON messages(conv_id, ts);
    CREATE TABLE IF NOT EXISTS outbox (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      conv_id TEXT NOT NULL REFERENCES convs(id),
      text TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'draft', -- draft | approved | sent | failed | discarded
      error TEXT,
      created INTEGER NOT NULL,
      sent INTEGER
    );
  `)
  return db
}

export function upsertConv(db, { id, name, kind }) {
  db.prepare(
    `INSERT INTO convs (id, name, kind) VALUES (?, ?, ?)
     ON CONFLICT(id) DO UPDATE SET name = excluded.name`
  ).run(id, name || id, kind)
}

export function addMessage(db, m) {
  db.prepare(
    `INSERT OR IGNORE INTO messages (id, conv_id, sender, direction, type, text, ts)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  ).run(m.id, m.convId, m.sender, m.direction, m.type, m.text, m.ts)
}

/** Resolve a conversation by exact id, exact name, or unique case-insensitive substring. */
export function findConv(db, query) {
  const exact = db.prepare('SELECT * FROM convs WHERE id = ? OR name = ?').all(query, query)
  if (exact.length === 1) return exact[0]
  const like = db
    .prepare('SELECT * FROM convs WHERE name LIKE ? ESCAPE \'\\\'')
    .all(`%${query.replace(/[%_\\]/g, '\\$&')}%`)
  const hits = exact.length ? exact : like
  if (hits.length === 1) return hits[0]
  if (hits.length === 0) throw new Error(`No conversation matches "${query}"`)
  throw new Error(
    `"${query}" is ambiguous: ${hits.map((h) => h.name).join(', ')}`
  )
}
