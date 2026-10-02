// Long-running process: logs every WeChat message to SQLite and sends approved outbox items.
// UNOFFICIAL: uses a personal-account puppet. This violates WeChat's ToS; the account can be restricted.
import { WechatyBuilder, types } from 'wechaty'
import qrcode from 'qrcode-terminal'
import { openDb, upsertConv, addMessage } from './db.js'

const db = openDb()
const PUPPET = process.env.WB_PUPPET || 'wechaty-puppet-wechat4u'
const POLL_MS = 3000
const MIN_GAP_MS = 2000 // random 2-6s between sends, to look less robotic
const MAX_GAP_MS = 6000

const TYPE_NAMES = {
  [types.Message.Text]: 'text',
  [types.Message.Image]: 'image',
  [types.Message.Attachment]: 'file',
  [types.Message.Audio]: 'voice',
}

const bot = WechatyBuilder.build({ name: 'wb', puppet: PUPPET })

async function logMessage(msg, direction) {
  const room = msg.room()
  const talker = msg.talker()
  const peer = direction === 'out' ? msg.listener() : talker
  const conv = room
    ? { id: room.id, name: await room.topic(), kind: 'room' }
    : peer
      ? { id: peer.id, name: peer.name(), kind: 'person' }
      : null
  if (!conv) return
  upsertConv(db, conv)
  const type = TYPE_NAMES[msg.type()] || 'other'
  addMessage(db, {
    id: msg.id,
    convId: conv.id,
    sender: direction === 'out' ? 'me' : talker.name(),
    direction,
    type,
    text: type === 'text' ? msg.text() : `[${type}]`,
    ts: msg.date().getTime(),
  })
}

bot
  .on('scan', (qr, status) => {
    if (status === 2) qrcode.generate(qr, { small: true })
    console.log(`Scan QR with WeChat (status ${status})`)
  })
  .on('login', (u) => console.log(`Logged in as ${u.name()}`))
  .on('logout', (u) => console.log(`Logged out ${u.name()}`))
  .on('error', (e) => console.error('bot error', e))
  .on('message', async (msg) => {
    try {
      await logMessage(msg, msg.self() ? 'out' : 'in')
    } catch (e) {
      console.error('log failed', e)
    }
  })

let sending = false
async function flushOutbox() {
  if (sending || !bot.isLoggedIn) return
  sending = true
  try {
    const rows = db
      .prepare("SELECT * FROM outbox WHERE status = 'approved' ORDER BY id")
      .all()
    for (const row of rows) {
      try {
        const target = row.conv_id.startsWith('@@')
          ? await bot.Room.find({ id: row.conv_id })
          : await bot.Contact.find({ id: row.conv_id })
        if (!target) throw new Error('conversation not found on WeChat')
        await target.say(row.text)
        db.prepare("UPDATE outbox SET status='sent', sent=? WHERE id=?").run(Date.now(), row.id)
        console.log(`sent #${row.id}`)
      } catch (e) {
        db.prepare("UPDATE outbox SET status='failed', error=? WHERE id=?").run(String(e.message || e), row.id)
        console.error(`send #${row.id} failed`, e)
      }
      await new Promise((r) => setTimeout(r, MIN_GAP_MS + Math.random() * (MAX_GAP_MS - MIN_GAP_MS)))
    }
  } finally {
    sending = false
  }
}

await bot.start()
setInterval(flushOutbox, POLL_MS)
