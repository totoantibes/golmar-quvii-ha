"""Constants for the Golmar / Quvii Local integration."""

DOMAIN = "golmar_quvii"

# config-entry keys
CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_APP_ID = "app_id"
CONF_OEM_ID = "oem_id"
# Fixed account server for brands that do not use the r<region>-<lb>-sec pool.
# Empty -> search the regional pool as usual.
CONF_SERVER_HOST = "server_host"
# Brand preset picked in the config flow (see BRANDS). Only steers the flow: the
# values it implies are stored in their own keys, so entries made before presets
# existed keep working unchanged.
CONF_BRAND = "brand"
# Vimar SSO step: the callback JSON pasted by the user. Never stored; only the
# account and password parsed out of it are.
CONF_SSO_JSON = "sso_json"
# entry OPTIONS: the door/lock buttons the user chose to create, as a list of
# {"umid", "door", "lock", "name"} dicts. Set by the config-flow selection step
# and editable afterwards via the options flow. Empty/absent -> DEFAULT_LOCKS.
CONF_LOCKS = "locks"
# entry OPTIONS: how a button press reaches the panel (see UNLOCK_MODES).
CONF_UNLOCK_MODE = "unlock_mode"
# entry OPTIONS: extra addresses to probe during discovery, comma separated.
# The /24 sweep only covers Home Assistant's own subnet, so a panel on another
# VLAN is invisible to it; listing the address here makes it reachable.
CONF_EXTRA_HOSTS = "extra_hosts"

# Unlock transports.
#   local - LAN only. Fastest, works with no internet, and the panel's key never
#           leaves the network. Requires the panel's CGI to be reachable.
#   cloud - route the open command through the Quvii cloud. Needed by firmware
#           that only opens the local CGI while the app streams video.
#   auto  - try local, fall back to cloud. Right choice when the local CGI comes
#           and goes, which is exactly the firmware that motivates cloud mode.
MODE_LOCAL = "local"
MODE_CLOUD = "cloud"
MODE_AUTO = "auto"
UNLOCK_MODES = [MODE_LOCAL, MODE_AUTO, MODE_CLOUD]
DEFAULT_UNLOCK_MODE = MODE_LOCAL

# Defaults = Golmar G2Call+ (the tested brand). Other Quvii-based brands can
# override app_id / oem_id / region in the config flow (see README).
DEFAULT_APP_ID = "4053"
DEFAULT_OEM_ID = "G0053,A0053"
DEFAULT_REGION = "1"
DEFAULT_SERVER_HOST = ""
DEFAULT_LB = "8"          # cloud load-balancer instance (r<region>-<lb>-sec.qvcloud.net)

# Known Quvii brands. "custom" leaves App ID / OEM ID / server to the user.
#
# Vimar (View Door app, e.g. K42955) has its own account server instead of the
# regional pool: an account made in View Door does not exist on the Golmar
# servers, so signing in there fails with LOGIN_BAD_CREDENTIALS even though the
# password is right. Values extracted from the View Door APK (v2.2.11).
BRAND_GOLMAR = "golmar"
BRAND_VIMAR = "vimar"
BRAND_CUSTOM = "custom"
BRANDS = {
    BRAND_GOLMAR: {
        CONF_APP_ID: DEFAULT_APP_ID,
        CONF_OEM_ID: DEFAULT_OEM_ID,
        CONF_SERVER_HOST: DEFAULT_SERVER_HOST,
    },
    BRAND_VIMAR: {
        CONF_APP_ID: "2078",
        CONF_OEM_ID: "G0078",
        CONF_SERVER_HOST: "vimar.qvcloud.net",
    },
}
BRAND_OPTIONS = [BRAND_GOLMAR, BRAND_VIMAR, BRAND_CUSTOM]
DEFAULT_BRAND = BRAND_GOLMAR

# Brand servers such as vimar.qvcloud.net present Qualvision's own certificate:
# a private CA, no subjectAltName, valid 2017-12-11 to 2037-12-06. Normal
# verification can never pass, so when it fails the connection is retried
# pinned to this exact certificate (SHA-256 of the DER) instead of being
# accepted unverified. The regional r<region>-<lb>-sec hosts have public
# certificates and never reach the pin.
QVCLOUD_CERT_SHA256 = (
    "e7167be07fc554614ce611b1be054c29cfc39926695d6a6717c9e631dc83276a"
)

# Account servers are r<region>-<lb>-sec.qvcloud.net, and **which lb values exist
# depends on the region**: lb 8 serves regions 1, 5, 6 and 7 but does not exist at
# all for 2, 3, 8 or 9. Assuming a single lb therefore does not pick a slower
# route, it makes entire regions unresolvable - which is what kept accounts
# outside Europe and the Americas from signing in. Tried in this order, most
# widely available first, so the common case still hits on the first attempt.
LB_CANDIDATES = ("8", "5", "4", "1", "3", "7", "6", "2", "9")

# Regions observed to answer the login protocol. The field is free text in the
# config flow, so an unlisted region still works if one appears later; this is
# only the search order used when hunting for an account's home server.
REGION_CANDIDATES = ("1", "2", "3", "4", "5", "6", "7", "8", "9")

# The one login result whose meaning is confirmed as bad account/password. Note
# that an account the server has never seen returns this too, so it cannot tell
# "wrong password" from "no such account" - only that neither is the case.
LOGIN_BAD_CREDENTIALS = "100100003"

# Login result meaning "this account is not served by this regional server".
# Confirmed by pointing a known-good account at the wrong region: its home
# region returns 0 and a session, every other live region returns this.
LOGIN_WRONG_REGION = "100101000"
CLIENT_TYPE = "3"
# Identifies this integration to the cloud. Deliberately not the phone app's own
# client id: sharing one would put both on the same session.
CLIENT_SUFFIX = "haquviilocal01"

# Vimar accounts sign in through Vimar's SSO, not with a Quvii password. This
# page bounces through oauth.vimar.com and ends on sso.qvcloud.net/vimar/callback,
# which shows {"result":0,"account":...,"password":...}: the Quvii credentials the
# cloud actually accepts. The password is a ready-made 64-hex value.
VIMAR_SSO_URL = (
    "https://sso.qvcloud.net/vimar/usrpwd/authorize"
    f"?oem=G0078&client_id=003-2078-{CLIENT_SUFFIX}&rgn=1"
)

# Cloud control plane (only used by MODE_CLOUD / MODE_AUTO). Both hosts are
# region scoped in the same "r<region>" style as the account plane. Only region 1
# has been exercised against the live service; other regions follow the pattern
# but are unverified.
OAUTH_HOST_TEMPLATE = "https://oauth2r{region}.qvcloud.net"
OAUTH_PATH = "/qvoauthv2/token"
OPENAPI_HOST_TEMPLATE = "https://tdkopenapir{region}.qvcloud.net"
OPENAPI_CONTROL_PATH = "/openapi-tdk/devctr/synccontrol/singledev"
# Tokens carry an exp claim (one hour, measured) but no expires_in field, so the
# claim is what we cache against. Re-mint a little early to avoid racing it.
TOKEN_EXPIRY_MARGIN = 300
# Fallback when a token has no readable exp claim.
TOKEN_DEFAULT_TTL = 3000

# Local device CGI auth (generic across Quvii firmware)
CGI_USERNAME = "adminapp2"
CGI_SECURITY = "username"
CGI_PATH = "/tdkcgi"
# The CGI answers on both ports with byte-identical responses; 443 is tried first
# because far fewer hosts on a home LAN listen there, which keeps the candidate
# list short. Port 80 is the fallback for panels that do not keep 443 open.
CGI_ENDPOINTS = ((443, "https"), (80, "http"))
# Sent instead of the real key to find out whether a host is a panel at all. A
# panel answers an <error>401</error> envelope; anything else 404s or fails. This
# is what keeps the real key away from unrelated hosts during a sweep.
FINGERPRINT_KEY = "0" * 64

# The channels a panel can address, as get.device.attachInfo reports them on a
# real device: four block door panels ("Door N") and four general/street panels
# ("General Panel N"), each with two lock relays.
#
# Channels 5-8 and 13-16 exist as well but are CCTV inputs - they carry no lock,
# so a button for them can never open anything. Guessing that the door channels
# run 1..8 is the natural mistake and produces buttons that silently do nothing.
DOOR_CHANNELS = (1, 2, 3, 4)
GENERAL_PANEL_CHANNELS = (9, 10, 11, 12)
LOCKS_PER_CHANNEL = (1, 2)

# Offered when the panel cannot be asked what it actually has - which is always
# the case in cloud mode, since enumeration needs the local interface those
# panels do not expose. It covers every channel a panel can address rather than
# a handful, because anything missing here is unreachable from the UI: the only
# workaround is editing this file, which the next update overwrites.
#
# A device only actuates channels physically wired to its bus; the rest accept
# the command and do nothing. Leaving them unticked keeps the buttons clean.
DEFAULT_LOCKS = [
    *(
        (channel, lock, f"Door {position} Lock {lock}")
        for position, channel in enumerate(DOOR_CHANNELS, 1)
        for lock in LOCKS_PER_CHANNEL
    ),
    *(
        (channel, lock, f"General Panel {position} Lock {lock}")
        for position, channel in enumerate(GENERAL_PANEL_CHANNELS, 1)
        for lock in LOCKS_PER_CHANNEL
    ),
]
