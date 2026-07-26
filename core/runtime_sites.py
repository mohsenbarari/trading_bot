"""Configuration-free physical-site and logical-authority constants."""

AUTHORITY_FOREIGN = "foreign"
AUTHORITY_WEBAPP = "webapp"

SITE_BOT_FI = "bot_fi"
SITE_WEBAPP_FI = "webapp_fi"
SITE_WEBAPP_IR = "webapp_ir"
SITE_WITNESS = "witness"

WEBAPP_SITES = frozenset({SITE_WEBAPP_FI, SITE_WEBAPP_IR})
# The Witness is an independent physical role.  It has WebApp control-plane
# authority but must never impersonate either WebApp deployment identity.
PHYSICAL_SITES = frozenset({SITE_BOT_FI, SITE_WITNESS, *WEBAPP_SITES})
LOGICAL_AUTHORITIES = frozenset({AUTHORITY_FOREIGN, AUTHORITY_WEBAPP})
