# Golmar / Quvii Local (Home Assistant)

Local control of **Golmar G2Call+** video door stations from Home Assistant.
You sign in with your Golmar account; the integration retrieves each panel's
local access key and then opens doors **locally** over your LAN — no cloud in
the loop when a door is opened.

Some newer firmware only opens the panel's local interface while the phone app
is streaming video, which leaves nothing on the LAN to talk to. Those panels can
opt into a **cloud unlock** path instead — see
[How doors are opened](#how-doors-are-opened). Local remains the default.

> **Tested with:** Golmar G2Call+ (`ART7W‑G2+`).
> Golmar is one of several brands built on the **Quvii** platform, so other
> Quvii‑based intercom apps *may* work by setting the App ID / OEM ID in the
> config flow — see [Other Quvii brands](#other-quvii-brands). Only Golmar is
> verified.

> [!CAUTION]
> **Anyone on your LAN can open your doors.** The panel accepts the open command
> from *any* device on your local network that presents the access key — there is
> **no per‑user check at the door** — and Home Assistant stores that key, so
> **your LAN is the only thing protecting your doors**. Before installing:
> - Only run this on a network you fully trust.
> - Keep the panel and other IoT devices on a **segmented / guest‑isolated VLAN** where possible.
> - **Secure and keep updated** your Home Assistant instance.
> - **Never expose** the panel or Home Assistant to the internet.
>
> Anyone who joins your Wi‑Fi and holds the key can open the door. If that isn't
> acceptable for your situation, don't install this.
>
> **Cloud unlock changes this trade‑off.** In `cloud` or `auto` mode the open
> command is sent through the vendor's servers using your account credentials,
> so your LAN is no longer the boundary: anyone who can reach your Home
> Assistant, or who holds your Golmar account password, can open the door from
> anywhere. It also stops working when your internet or the vendor does. Only
> enable it if your panel genuinely cannot be reached locally.

## Requirements

- A **Wi‑Fi‑connected Golmar monitor**, already set up in the **G2Call+** app and
  reachable on your home network. (Bus‑only / non‑Wi‑Fi Golmar monitors won't work.)
- The monitor should be on the **same LAN as Home Assistant**. A panel on another
  subnet or VLAN can still be used by listing its address under **Extra panel
  addresses**; a panel that never exposes its local interface needs cloud mode.

**Supported hardware:** any G2Call+‑compatible Golmar **Wi‑Fi** monitor *should*
work — chiefly the **ART 7W** (Art 7 Wi‑Fi) and **SOUL** Wi‑Fi families.
**Tested / confirmed:** `ART7W‑G2+`. Other models are untested candidates —
please report what works (or doesn't) so this list can grow.

## How it works

1. **Cloud, at setup:** signs in with your account and retrieves each panel's
   local access key and identifier.
2. **Discovery:** finds your panels on the network and matches them to your
   account. Home Assistant's own subnet is swept on port 443 first and port 80
   only if a panel is still missing — the CGI answers identically on both, and
   far fewer hosts listen on 443, which keeps the scan small. Each candidate is
   first probed with a **junk key**: a panel answers `error 401`, anything else
   does not, so your real access key is only ever sent to confirmed panels.
3. **Control:** each door/lock becomes a Home Assistant **button**.

In the default `local` mode the cloud is contacted only at setup and for a
periodic key refresh — opening a door never touches the internet.

## How doors are opened

Set **How to open doors** in the config flow, or later under **Configure**:

| Mode | Behaviour | Use when |
|------|-----------|----------|
| `local` *(default)* | LAN only | Your panel answers on the LAN. Fastest, works with no internet, key never leaves the network. |
| `auto` | LAN first, cloud if that fails | The local interface is usually there but not always. |
| `cloud` | Vendor servers only | The panel exposes no local interface. Discovery is skipped entirely. |

**If your panel only opens its ports while the app streams video, choose
`cloud`, not `auto`.** On the panels reported so far the local interface is shut
within seconds of closing the app, so `auto` spends time on a local attempt that
is almost always going to fail.

Cloud mode needs two extra secrets that the account already hands out: a
short‑lived **dynamic password** per panel (it expires in days, so the device
list is renewed before each expiry rather than monthly) and an **OAuth token**
valid for one hour, minted on demand and cached.

> **Cloud unlock is confirmed working on affected hardware by a contributor**
> (both doors, reliably — thanks @victor-marino), but **not by the author**: this
> project's own panel is not registered on the vendor's control plane at all, so
> every cloud open on it returns *device not registered*. Treat it as tested by
> one person on one panel rather than broadly proven, and please report how it
> behaves on yours.
>
> Two limits worth knowing. Only `door 1` locks 1 and 2 have been exercised over
> the cloud; higher channels (the `General Panel N` street entrances) are
> untested on this path. And the OAuth and control hosts are region‑scoped, but
> only region 1 has ever been contacted.

## Install

### 1. Add the integration (HACS — recommended)

In HACS, search for **“Golmar / Quvii Local”** and click **Download**.

*Manual alternative:* copy `custom_components/golmar_quvii/` into your HA
`config/custom_components/` folder.

### 2. Restart Home Assistant

### 3. Configure

**Settings → Devices & Services → Add Integration → “Golmar / Quvii Local”** →
enter your account (email or `+phone`) and password. Leave the advanced **App ID
/ OEM ID** fields as‑is for Golmar, and leave **How to open doors** on `local`
unless your panel can't be reached on the LAN. **Extra panel addresses** is only
needed for a panel outside Home Assistant's own subnet — a VLAN, say — and takes
a comma‑separated list of addresses.

The integration then finds your panels on the LAN and shows a **“Choose which
doors to control”** step listing every panel/lock it detected — block entrances
as `Door N`, street entrances as `General Panel N`. **Tick the same ones you
open from the official Golmar app** and finish. Only channels physically wired
to your panel open anything; the others are accepted by the panel but do
nothing, so leaving them unticked keeps your buttons clean.

Changed your mind or added a panel later? **Settings → Devices & Services →
Golmar / Quvii Local → Configure** re-opens the same selection any time.

## Entities

Each lock you ticked becomes an open‑door **button** (`Door 1 Lock 1`,
`General Panel 1 Lock 2`, …), grouped under its panel device. Rename them to
match your wiring (e.g. *Street – Car Entry*), then use them in dashboards,
automations and Siri Shortcuts like any button.

## Sign‑in fails although the app works

**This should now sort itself out** — but the history is worth knowing, because
it was the integration's fault.

Your account lives on exactly one of the vendor's regional servers, and asking
any other one about it fails in a way that looks nothing like a routing problem.
The integration used to assume a single server address, which did not exist for
half the regions — so accounts outside Europe and the Americas could not sign in
at all, and the error blamed the password.

Sign‑in now finds the right server by itself and remembers it, so **leave
`Region id` alone**. The first sign‑in may take a few seconds longer while it
looks.

If it still fails, the log names the server's own refusal code. Please
[open an issue](https://github.com/totoantibes/golmar-quvii-ha/issues) with that
code — it means your account is served somewhere this integration doesn't know
about yet, which is worth fixing for everyone.

If the account is a phone number, enter it with the country code exactly as the
app shows it (e.g. `+34…`).

## Other Quvii brands

Golmar is one brand built on the Quvii platform. Other Quvii‑based intercoms use
the same integration but have their own **App ID** / **OEM ID**, and accounts are
scoped to that brand's cloud — so set those two advanced fields in the config
flow.

### Known brands

| Brand  | App     | App ID | OEM ID        | Region | Verified |
|--------|---------|--------|---------------|--------|----------|
| Golmar | G2Call+ | `4053` | `G0053,A0053` | `1`    | ✅       |

If you get another brand working, please open a PR to add a row so others can
just pick it. (App ID / OEM ID are app‑specific values; region `1` = Europe.)

## Notes & limitations

- **In `local` mode the panel must be reachable from Home Assistant.** If
  discovery misses it, the buttons show unavailable; add its address under
  **Extra panel addresses**, or switch to `auto`/`cloud`.
- **Some panels keep every local port shut** unless the phone app is actively
  streaming video from that door — not just 443, and not just the CGI. They
  close again within seconds of leaving the stream. Reported on two units so
  far. **What decides this is not established** — not model, not screen size,
  and not firmware age as far as anyone can tell — so the integration does not
  try to detect it. If your panel behaves this way, select `cloud`.
- **`cloud_available` on a button means a dynamic password is cached** for that
  panel, i.e. the cloud path is configured. It is not a statement that the
  token, the credential and the vendor's service have been checked and work.
- The number of doors/locks isn't reported, so four buttons are created per
  panel; disable the ones you don't use.
- **Security:** see the ⚠️ warning at the top — anyone on your LAN who has the
  key can open the doors, and cloud mode widens that further. Keep it on a
  trusted, segmented network.
- **Credentials:** your account password is stored by HA like any other
  integration credential. In `local` mode it is used only to fetch the local
  keys, and door control stays on your LAN; in `cloud`/`auto` mode it is also
  used to mint the hourly token that carries the open command. The panel uses a
  self‑signed certificate (LAN only).
- **Unofficial & unsupported:** this is not an official integration; the vendor
  may change their service at any time and break it. Use at your own risk.

## Disclaimer

Community project. **Not affiliated with, authorised by, or endorsed by Golmar or
Quvii.** All product names and trademarks belong to their respective owners.
