"""Compatibility policy references; business procedures live in packaged skills."""
from support_agent.skills import policy_reference, library


def service_policy(operation):
    reference = policy_reference(operation)
    return {"operation": operation, **reference,
            "version": library()[reference["skill"]]["version"]}
