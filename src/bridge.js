// Long-running, READ-ONLY listener: logs every WeChat message to SQLite. It never sends anything.
// UNOFFICIAL: uses a personal-account puppet. This violates WeChat's ToS; the account can be restricted.
import { WechatyBuilder, types } from 'wechaty'
import qrcode from 'qrcode-terminal'
import { openDb, upsertConv, addMessage } from './db.js'

const db = openDb()
const PUPPET = process.env.WB_PUPPET || 'wechaty-puppet-wechat4u'


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

await bot.start()
