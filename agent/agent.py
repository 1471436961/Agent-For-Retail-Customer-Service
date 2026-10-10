"""Platform agent factory; each instance owns its conversation state."""

from support_agent.application import CustomerAgent


def create_agent():
    """Create a fresh agent; never keep conversation state in module globals."""
    return CustomerAgent()
