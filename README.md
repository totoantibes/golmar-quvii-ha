# Golmar / Quvii Local (Home Assistant)

Local control of **Golmar G2Call+** video door stations from Home Assistant.
You sign in **once** with your Golmar account; the integration retrieves each
panel's local access key and then opens doors **100 % locally** over your LAN —
no cloud in the loop after setup.

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

## Requirements

- A **Wi‑Fi‑connected Golmar monitor**, already set up in the **G2Call+** app and
  reachable on your home network. (Bus‑only / non‑Wi‑Fi Golmar monitors won't work.)
- The monitor must be on the **same LAN as Home Assistant**.

**Supported hardware:** any G2Call+‑compatible Golmar **Wi‑Fi** monitor *should*
work — chiefly the **ART 7W** (Art 7 Wi‑Fi) and **SOUL** Wi‑Fi families.
**Tested / confirmed:** `ART7W‑G2+`. Other models are untested candidates —
please report what works (or doesn't) so this list can grow.

## How it works

1. **Cloud, once at setup:** signs in with your account and retrieves each
   panel's local access key and identifier.
2. **LAN discovery:** finds your panels on the local network and matches them to
   your account.
3. **Local control:** each door/lock becomes a Home Assistant **button** that
   opens the door directly on your LAN.

The cloud is contacted only at setup and for a periodic key refresh — opening a
door never touches the internet.

## Install

### 1. Add the integration (HACS — recommended)

- **Once it's in the HACS default store:** HACS → search **“Golmar / Quvii
  Local”** → **Download**.
- **Until then (custom repository):** HACS → ⋮ (top‑right) → **Custom
  repositories** → URL `https://github.com/totoantibes/golmar-quvii-ha`,
  category **Integration** → **Add** → then find it in HACS → **Download**.

*Manual alternative:* copy `custom_components/golmar_quvii/` into your HA
`config/custom_components/` folder.

### 2. Restart Home Assistant

### 3. Configure

**Settings → Devices & Services → Add Integration → “Golmar / Quvii Local”** →
enter your account (email or `+phone`) and password. Leave the advanced **App ID
/ OEM ID** fields as‑is for Golmar. Your panels are then discovered
automatically and appear as devices with open‑door buttons.

## Entities

For each panel you get an open‑door **button** per door/lock — the block door
panels (`Door 1 Lock 1`, …) and the general/street panels (`General Panel 1
Lock 1`, `General Panel 1 Lock 2`, …), grouped under a device. Only the channels
physically wired to your panel actuate anything; the rest accept the command but
do nothing, so rename/disable to match your wiring (e.g. *Street – Car Entry*),
then use them in dashboards, automations and Siri Shortcuts like any button.

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

- **Panel must be reachable on the same LAN as Home Assistant.** If
  auto‑discovery misses it, the button shows unavailable.
- The number of doors/locks isn't reported, so four buttons are created per
  panel; disable the ones you don't use.
- **Security:** see the ⚠️ warning at the top — anyone on your LAN who has the
  key can open the doors. Keep it on a trusted, segmented network.
- **Credentials:** your account password is used only once to fetch the local
  keys and is then stored by HA like any other integration credential; the key
  and door control stay on your LAN. The panel uses a self‑signed certificate
  (LAN only).
- **Unofficial & unsupported:** this is not an official integration; the vendor
  may change their service at any time and break it. Use at your own risk.

## Disclaimer

Community project. **Not affiliated with, authorised by, or endorsed by Golmar or
Quvii.** All product names and trademarks belong to their respective owners.
