"""Platform tool discovery entry point."""

from support_agent.tools_impl import RetailTools


class Tools(RetailTools):
    """Expose the stable customer API tools."""
