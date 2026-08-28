"""Tenant membership roles.

Two roles, and **neither is ever assigned twice**. A workspace has exactly one
``owner`` — the person who created it — who runs it: settings, API keys,
privacy config, member management, and publishing knowledge that changes what
the bot answers. Everybody else is an ``operator``, who works the
conversations: inbox, logs, and the read views of the knowledge base.

The role is written once, when the account is created, and never changes.
There is no promotion, no demotion, and no role on an invitation — an invite
that could name a role would be an invite that could mint a second owner. So
this is not a permission model with transitions to reason about; it is one
fact about how an account came into being, and the auth dependencies read it.

What a seat governs is a different question entirely — whether somebody may
*operate*, where the role says what they may *administer*. See
``backend/seats/``.

``users.role`` is a plain ``String(32)``, not a database enum, so a third
value would be additive with no data migration. These constants are the single
source of truth for the two values in use; nothing outside this module should
spell them as literals. Responses report ``users.role`` as a plain string on
purpose, so a row holding a value an older build has never heard of degrades
instead of failing validation — see ``backend/tenants/schemas.py`` for why.
"""

from __future__ import annotations

ROLE_OWNER = "owner"
ROLE_OPERATOR = "operator"

#: Every role a member may hold. An invite assigns neither: it always
#: creates an operator.
TENANT_ROLES: tuple[str, ...] = (ROLE_OWNER, ROLE_OPERATOR)
