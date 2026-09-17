"""Constants for the Golmar / Quvii Local integration."""

DOMAIN = "golmar_quvii"

# config-entry keys
CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_APP_ID = "app_id"
CONF_OEM_ID = "oem_id"
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
DEFAULT_LB = "8"          # cloud load-balancer instance (r<region>-<lb>-sec.qvcloud.net)
CLIENT_TYPE = "3"
# Identifies this integration to the cloud. Deliberately not the phone app's own
# client id: sharing one would put both on the same session.
CLIENT_SUFFIX = "haquviilocal01"

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

# Default lock buttons created per device: (door/channel, locknumber, label).
# "door" is the panel/channel number as reported by get.device.attachInfo:
# channels 1-4 are the block door panels ("Door N"), channels 9-12 are the
# general/street panels ("General Panel N"). Each panel exposes two lock relays.
# A device only actuates the channels physically wired to its bus; the others
# accept the command but do nothing.
DEFAULT_LOCKS = [
    (1, 1, "Door 1 Lock 1"),
    (1, 2, "Door 1 Lock 2"),
    (2, 1, "Door 2 Lock 1"),
    (2, 2, "Door 2 Lock 2"),
    (9, 1, "General Panel 1 Lock 1"),
    (9, 2, "General Panel 1 Lock 2"),
]
