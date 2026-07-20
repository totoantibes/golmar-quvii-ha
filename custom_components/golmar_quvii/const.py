"""Constants for the Golmar / Quvii Local integration."""

DOMAIN = "golmar_quvii"

# config-entry keys
CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_APP_ID = "app_id"
CONF_OEM_ID = "oem_id"

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

# Default lock buttons created per device: (door/channel, locknumber, label)
DEFAULT_LOCKS = [
    (1, 1, "Door 1 Lock 1"),
    (1, 2, "Door 1 Lock 2"),
    (2, 1, "Door 2 Lock 1"),
    (2, 2, "Door 2 Lock 2"),
]
