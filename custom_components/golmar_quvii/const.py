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

# Defaults = Golmar G2Call+ (the tested brand). Other Quvii-based brands can
# override app_id / oem_id / region in the config flow (see README).
DEFAULT_APP_ID = "4053"
DEFAULT_OEM_ID = "G0053,A0053"
DEFAULT_REGION = "1"
DEFAULT_LB = "8"          # cloud load-balancer instance (r<region>-<lb>-sec.qvcloud.net)
CLIENT_TYPE = "3"

# Local device CGI auth (generic across Quvii firmware)
CGI_USERNAME = "adminapp2"
CGI_SECURITY = "username"

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
