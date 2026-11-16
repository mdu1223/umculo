# Security

## Reporting a problem

Please don't open a public issue for a security problem. Use **Security → Report a vulnerability** on this repository's GitHub page instead (a private report only the maintainer sees). Include what you found, how to reproduce it, and which version you're running (shown in Umculo under Status). You'll get a reply within a week.

## Putting Umculo on the internet safely

Umculo is built to run at home. If you want it outside your house, in order of safety:

1. **A VPN like Tailscale or WireGuard.** Nothing is open to the internet; only your own devices can reach it.
2. **A Cloudflare Tunnel (or another reverse proxy) in front of it.** Never forward port 8095 on your router.

If it's on a public address, switch these on in Umculo:

- **2-step sign-in** for the admin account (Your account → 2-step sign-in). The admin can't sign in from the internet without it.
- **App passwords** for music apps (Your account → Devices), so your main password never sits in an app.
- **"Only devices you approve"** for the public address (Users → Public address). New devices wait for your OK, and the main password stops working in apps over the internet.

Behind Cloudflare, also worth having: a rule limiting sign-in attempts on `/api/login` and `/Users/AuthenticateByName`, a country rule if only people in your country use it, minimum TLS 1.2 and Always Use HTTPS. Don't turn on Bot Fight Mode or Cloudflare Access for the music address: music apps can't get past them.

On the server itself: firewall everything except your home network and VPN, and allow SSH passwords only from home (or use keys).

## Stream keys (speakers, TVs, listening together)

Speakers and TVs fetch songs themselves and can't sign in, so the web player adds a stream key to song addresses.
A key belongs to one person, lasts 12 hours, and only opens songs, covers and mix videos (nothing else, and nothing
can be changed with it). A listening room shares the DJ's key with the people in the room while it's open. Keys are
signed with a secret kept in `config/stream.key`; delete that file to cancel every key at once.
The Home Assistant token is kept in `config/homeassistant.json` (readable only by Umculo) and is never sent back to the app.

## Hook addresses (Home Assistant automations)

A hook address (Account → Alarms and automations) lets Home Assistant start or stop music on a speaker as you,
without signing in. It can't do anything else. Only a fingerprint of it is stored; making a new one cancels the old.
The WAHA key is kept in `config/whatsapp.json` and never sent back to the app.
