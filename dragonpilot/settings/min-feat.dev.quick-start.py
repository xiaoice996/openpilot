from dragonpilot.settings import tr

ITEMS = [
  {
    "section": "Device",
    "key": "dp_dev_quick_start",
    "type": "toggle_item",
    "title": lambda: tr("Quick Start"),
    "description": lambda: tr("Skip scons compilation on boot for faster startup. Enable during development to avoid recompiling every reboot. Disable before OTA updates."),
    "flags": "PERSISTENT",
    "param_type": "BOOL",
    "default": "0",
  },
]
