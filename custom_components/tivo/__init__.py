"""
Support for the Tivo receivers.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/tivo/
"""

__version__ = "1.0.2"

# Preload the YAML platform while Home Assistant imports the integration in its
# import executor. Loading it later on the event loop triggers the blocking
# import_module warning on current Home Assistant releases.
from . import media_player  # noqa: F401, E402
