import test from 'node:test'
import assert from 'node:assert/strict'
import { openDb, upsertConv, addMessage, findConv } from '../src/db.js'

test('findConv resolves by substring and rejects ambiguity', () => {
  const db = openDb(':memory:')
  upsertConv(db, { id: '@@1', name: 'Shenzhen Cables Group', kind: 'room' })
  upsertConv(db, { id: '@2', name: 'Shenzhen Plastics', kind: 'person' })
  assert.equal(findConv(db, 'cables').id, '@@1')
  assert.throws(() => findConv(db, 'shenzhen'), /ambiguous/)
  assert.throws(() => findConv(db, 'nope'), /No conversation/)
})

test('addMessage is idempotent', () => {
  const db = openDb(':memory:')
  upsertConv(db, { id: '@2', name: 'A', kind: 'person' })
  const m = { id: 'm1', convId: '@2', sender: 'A', direction: 'in', type: 'text', text: 'hi', ts: 1 }
  addMessage(db, m); addMessage(db, m)
  assert.equal(db.prepare('SELECT COUNT(*) c FROM messages').get().c, 1)
})
