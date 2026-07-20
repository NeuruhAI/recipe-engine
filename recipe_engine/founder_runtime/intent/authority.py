"""Trusted deterministic authority classification.

Model output is deliberately not an input. Unknown or ambiguous requests
escalate; keyword avoidance never produces low authority.
"""

from __future__ import annotations

from recipe_engine.founder_runtime.contracts.authority import (
    AuthorityClass, AuthorityRequirement, FIXTURE_POLICY,
)


POLICY_VERSION = "fr.authority.kernel.v1"


def classify_authority(request_text: str, *, canonical_action: str = "auto") -> list[AuthorityRequirement]:
    text = " ".join((request_text or "").casefold().split())
    read_markers = ("status", "inspect", "show", "list", "report", "read-only", "read only", "where")
    destructive_markers = (
        "delete", "destroy", "erase", "wipe", "drop ", "purge", "remove permanently",
        "unrecoverable", "discard all", "scrub production",
    )
    external_markers = (
        "send", "email", "message", "publish", "push", "deploy", "release", "spend",
        "purchase", "charge", "transfer", "production", "credential", "secret", "rotate key",
        "make it live", "ship to customers", "notify them",
    )
    commit_markers = ("commit", "tag", "merge", "persist", "save permanently")
    execution_markers = ("execute", "run command", "invoke", "start process", "launch")
    mutation_markers = ("edit", "write", "modify", "change", "create file", "patch")

    if any(marker in text for marker in destructive_markers):
        cls, action, code = AuthorityClass.A6, "destructive_action", "DESTRUCTIVE_OR_IRREVERSIBLE"
    elif any(marker in text for marker in external_markers):
        cls, action, code = AuthorityClass.A5, "external_or_production_action", "EXTERNAL_FINANCIAL_CREDENTIAL_OR_PRODUCTION"
    elif any(marker in text for marker in commit_markers):
        cls, action, code = AuthorityClass.A4, "persistent_local_change", "PERSISTENT_CHANGE"
    elif any(marker in text for marker in execution_markers):
        cls, action, code = AuthorityClass.A3, "contained_execution", "PROCESS_OR_TOOL_EXECUTION"
    elif any(marker in text for marker in mutation_markers):
        cls, action, code = AuthorityClass.A2, "local_mutation", "LOCAL_MUTATION"
    elif text and canonical_action == "system_status" and any(marker in text for marker in read_markers):
        cls, action, code = AuthorityClass.A0, "read_only_system_status", "ALLOWLISTED_READ_ONLY_STATUS"
    elif text and any(marker in text for marker in read_markers):
        cls, action, code = AuthorityClass.A1, "local_read_analysis", "READ_ONLY_NOT_ALLOWLISTED_STATUS"
    else:
        cls, action, code = AuthorityClass.A5, "ambiguous_unclassified_action", "AMBIGUOUS_OR_UNKNOWN_ESCALATED"

    # The descriptor may refine the action name but cannot lower the class.
    if canonical_action not in ("", "auto", "system_status") and cls not in (AuthorityClass.A0, AuthorityClass.A1):
        action = canonical_action
    return [AuthorityRequirement(
        schema_version="fr.authority_req.v2",
        authority_class=cls,
        action=action,
        scope="local_fixture" if cls.value < AuthorityClass.A5.value else "protected_or_external",
        reason=code.replace("_", " ").lower(),
        requires_approval=FIXTURE_POLICY[cls] != "allow",
        enhanced=cls == AuthorityClass.A6,
        policy_version=POLICY_VERSION,
        reason_code=code,
    )]
