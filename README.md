# wechatbot

Listens to your WeChat supplier groups and chats, stores them in a local SQLite DB, and lets Claude summarize them with full conversation context. Read-only for now.

> **Unofficial.** Uses a personal-account Wechaty puppet, which violates WeChat's ToS. The account may be restricted or banned. Use a low send volume; don't use your most critical account if you can avoid it.

## Setup
```
npm install
npm run bridge     # scan the QR code with WeChat; leave running 24/7
```
Default puppet is `wechaty-puppet-wechat4u` (free, web-protocol; may not work for newer accounts). Set `WB_PUPPET` (and the matching token env, e.g. `WECHATY_PUPPET_SERVICE_TOKEN`) for another puppet/gateway.

## Using it
Stage 1 is a read-only listener. Open Claude Code in this folder and say "brief me". Claude reads `wb digest` and summarizes; it never sends. Reply to suppliers yourself.

Commands: `digest, convs, thread, search, note, read`.
Only messages received while the bridge is running are stored.
