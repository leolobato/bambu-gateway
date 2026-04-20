# Configuring APNs push notifications

This guide walks through enabling iOS push notifications and Live Activities
for the companion [BambuGateway iOS](https://github.com/leolobato/bambu-gateway-ios)
app. The feature is entirely optional — the gateway and the app both work
without it.

## What you get

When APNs is configured:

- **Live Activity on the iPhone Lock Screen and Dynamic Island** while a print
  is running, updated with progress, remaining time, and current layer even
  when the app is closed.
- **Push notifications** when a print pauses, fails, completes, is cancelled,
  goes offline mid-print, or the printer raises an HMS warning.
- **Auto-start Live Activities for prints kicked off from OrcaSlicer or the
  web UI**, not just from the iOS app.

Without APNs, the Live Activity still runs while the iOS app is in the
foreground (driven by local polling), but remote updates and notifications
are disabled.

## Requirements

- A **paid Apple Developer account** ($99/year). The free tier can't create
  APNs Auth Keys.
- The iOS app installed on a device, signed with your team's signing identity.
  The app's bundle ID must match `APNS_BUNDLE_ID` on the gateway.
- The gateway needs outbound HTTPS to `api.push.apple.com` (or
  `api.sandbox.push.apple.com` for debug builds). No inbound access required.

## Clarification: key vs. certificate

This gateway uses the modern **APNs Auth Key (`.p8`)** — a single ES256 key
scoped to your team. It is NOT the same as the older certificate-based
(`.p12`) approach, which Apple has deprecated and which this gateway does
not support.

One `.p8` key can authenticate push for every app on your team. You do not
need a separate key per app.

## Step 1 — Create the key

1. Log in at [developer.apple.com/account](https://developer.apple.com/account).
2. Go to **Keys** → **+** (add a new key).
3. Name it — e.g. "BambuGateway Push".
4. Check **Apple Push Notifications service (APNs)**.
5. Click **Continue** → **Register**.
6. **Download** the `.p8` file. **Apple only lets you download it once.**
   Save it somewhere safe (password manager, secure vault). If you lose it
   you have to revoke the key and create a new one.
7. On the key's detail page, note the **Key ID** (a 10-character string).
8. Find your **Team ID** in the top-right of the developer portal, under
   your name (also 10 characters).

Apple caps you at 2 active APNs keys per team. If you already have two and
need a new one, revoke an unused one first.

## Step 2 — Find your app's bundle ID

On the iOS project side, open `Configuration/LocalSigning.xcconfig` (or
`Configuration/Base.xcconfig` if `LocalSigning.xcconfig` is absent). The
value of `APP_BUNDLE_ID` is what you'll pass to the gateway as
`APNS_BUNDLE_ID`.

It must match **exactly** — APNs rejects pushes whose topic doesn't match the
device token's registered bundle.

## Step 3 — Choose an environment

| Build type | `APNS_ENVIRONMENT` | APNs host |
|---|---|---|
| Xcode → device (cable), dev build | `sandbox` | `api.sandbox.push.apple.com` |
| TestFlight (internal or external) | `production` | `api.push.apple.com` |
| App Store release | `production` | `api.push.apple.com` |

The `.p8` key works for both environments — only the endpoint and the
`aps-environment` entitlement flavor differ. Xcode flips the entitlement
automatically based on the provisioning profile.

Mismatch symptom: pushes return `400 BadDeviceToken` from APNs. This almost
always means the environment setting is wrong for the build of the app
that registered the token.

## Step 4 — Install the key on the gateway

### Bare-metal install

Place the `.p8` somewhere outside the git working tree and tighten
permissions:

```bash
sudo mkdir -p /etc/bambu-gateway
sudo mv ~/Downloads/AuthKey_ABCD123456.p8 /etc/bambu-gateway/
sudo chown bambu-gateway:bambu-gateway /etc/bambu-gateway/AuthKey_ABCD123456.p8
sudo chmod 600 /etc/bambu-gateway/AuthKey_ABCD123456.p8
```

Add to your `.env`:

```env
APNS_KEY_PATH=/etc/bambu-gateway/AuthKey_ABCD123456.p8
APNS_KEY_ID=ABCD123456
APNS_TEAM_ID=YOURTEAMID1
APNS_BUNDLE_ID=org.lobato.BambuGateway
APNS_ENVIRONMENT=sandbox
```

Restart the gateway. Look for `APNs push enabled` in the startup log. If
you instead see `APNs push disabled — set APNS_KEY_PATH and related vars
to enable`, one of the four required variables is empty or the key file
can't be read at that path.

### Docker

Mount the key as a read-only volume and pass the env vars via compose:

```yaml
services:
  bambu-gateway:
    image: ghcr.io/leolobato/bambu-gateway:latest
    volumes:
      - ./secrets/AuthKey_ABCD123456.p8:/data/AuthKey.p8:ro
      - ./data:/data
    environment:
      APNS_KEY_PATH: /data/AuthKey.p8
      APNS_KEY_ID: ABCD123456
      APNS_TEAM_ID: YOURTEAMID1
      APNS_BUNDLE_ID: org.lobato.BambuGateway
      APNS_ENVIRONMENT: sandbox
```

Add `secrets/` to your `.gitignore` so the `.p8` doesn't leak into git.

If you use Docker / Swarm secrets, mount the secret at a fixed path (e.g.
`/run/secrets/apns.p8`) and point `APNS_KEY_PATH` at it.

## Step 5 — Verify

```bash
curl http://<gateway>:4844/api/capabilities
```

Should return:

```json
{"push": true, "live_activities": true}
```

If it still returns `false`, the startup log tells you which check failed.
The most common causes are an empty variable, a misspelled path, or file
permissions preventing the gateway from reading the `.p8`.

From the iOS app, re-open Settings after the gateway restarts. The
**Notifications** section should switch from "Unavailable" to "Enabled" the
next time the app launches.

## Rotating the key

If you ever need to replace the `.p8`:

1. Create the new key in the Apple Developer portal.
2. Drop the new file in the same location (or update `APNS_KEY_PATH`).
3. Update `APNS_KEY_ID` to the new key's ID.
4. Restart the gateway. The JWT cache clears on restart, so the next push
   uses the new key immediately.
5. Revoke the old key in the portal once you're sure the new one works.

Active Live Activities on users' phones keep working through the rotation —
the update tokens don't care which JWT-signing key you use, as long as
both belong to the same team and bundle.

## Security notes

- The `.p8` plus Team ID plus bundle ID are enough for anyone to send push
  to your users. Treat the file like a password.
- The gateway stores push tokens in `devices.json` alongside printer config.
  On its own a push token is useless without the `.p8`, but don't publish
  the file.
- All APNs traffic is outbound-only from the gateway over HTTPS (HTTP/2).
  No inbound ports need to be opened, and the iOS app never talks to Apple
  via the gateway — Apple delivers pushes directly to the device.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `/api/capabilities` returns `push: false` | One of the four required env vars is empty, or the `.p8` path doesn't resolve / isn't readable. Check gateway startup log. |
| Startup log: `APNs push disabled` | Same as above. |
| Pushes silently never arrive | Check the gateway log for APNs response codes. `400 BadDeviceToken` means `APNS_ENVIRONMENT` doesn't match the build of the app that registered. `403 InvalidProviderToken` means Key ID / Team ID / `.p8` don't agree. |
| Settings in iOS app shows "Unavailable" | App was launched before the gateway advertised `push: true`. Force-quit and relaunch the app. |
| Live Activity never appears for prints started outside the iOS app | Push-to-start requires at least one successful app launch after APNs was enabled (to register the start token with the gateway). Open the app once and leave it — next print will get a Live Activity. |
