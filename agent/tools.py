"""Platform tool discovery entry point."""

from support_agent.adapters.customer_tools import CustomerTools


class Tools(CustomerTools):
    """Expose the application's read-only customer tools to the environment."""
