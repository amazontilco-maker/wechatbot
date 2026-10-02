# wechatbot

Reads your WeChat supplier groups and chats into a local SQLite DB; Claude summarizes them, you decide, Claude drafts replies, and after you approve, the bridge posts them.

> **Unofficial.** Uses a personal-account Wechaty puppet, which violates WeChat's ToS. The account may be restricted or banned. Use a low send volume; don't use your most critical account if you can avoid it.

## Setup
```
npm install
npm run bridge     # scan the QR code with WeChat; leave running 24/7
```
Default puppet is `wechaty-puppet-wechat4u` (free, web-protocol; may not work for newer accounts). Set `WB_PUPPET` (and the matching token env, e.g. `WECHATY_PUPPET_SERVICE_TOKEN`) for another puppet/gateway.

## Using it
Open Claude Code in this folder and say "brief me". Claude follows `CLAUDE.md`:
`wb digest` → you decide → `wb draft` → you confirm → `wb approve` → bridge sends.

Commands: `digest, convs, thread, note, read, draft, outbox, approve, discard`.
Only messages received while the bridge is running are stored.
