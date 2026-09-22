"""Explicit deciders for in-process calls to the approval service (G-1).

``decided_by`` is derived from an authenticated credential, so a test that
calls ``MissionService`` directly must say who is deciding.
"""

from recipe_engine.founder_runtime.api.identity import OPERATOR_DECIDER, Decider

FOUNDER = Decider(identity="jeramie", founder=True)
OPERATOR = OPERATOR_DECIDER
