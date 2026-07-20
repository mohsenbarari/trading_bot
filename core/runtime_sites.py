"""Configuration-free physical-site and logical-authority constants."""

AUTHORITY_FOREIGN = "foreign"
AUTHORITY_WEBAPP = "webapp"

SITE_BOT_FI = "bot_fi"
SITE_WEBAPP_FI = "webapp_fi"
SITE_WEBAPP_IR = "webapp_ir"

WEBAPP_SITES = frozenset({SITE_WEBAPP_FI, SITE_WEBAPP_IR})
PHYSICAL_SITES = frozenset({SITE_BOT_FI, *WEBAPP_SITES})
LOGICAL_AUTHORITIES = frozenset({AUTHORITY_FOREIGN, AUTHORITY_WEBAPP})
