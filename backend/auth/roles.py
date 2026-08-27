"""Tenant membership roles.

Two roles, deliberately. ``owner`` runs the workspace — settings, API keys,
privacy config, member management, and publishing knowledge that changes what
the bot answers. ``operator`` works the conversations — inbox, logs, and the
read views of the knowledge base.

``users.role`` is a plain ``String(32)``, not a database enum, so a third
value is additive later with no data migration. These constants are the
single source of truth for the two values in use today; nothing outside this
module should spell them as literals.
"""

from __future__ import annotations

ROLE_OWNER = "owner"
ROLE_OPERATOR = "operator"

#: Every role an invite may assign and a member may hold.
TENANT_ROLES: tuple[str, ...] = (ROLE_OWNER, ROLE_OPERATOR)
