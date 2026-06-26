from dragonpilot.settings import tr

ITEMS = [
  {
    "section": "Device",
    "key": "dp_dev_ignore_sensor_check",
    "type": "toggle_item",
    "title": lambda: tr("Ignore Sensor Check"),
    "description": lambda: tr("Ignore sensor data invalid alert caused by screen I2C hardware issue. Only enable if you see false sensor errors."),
    "flags": "PERSISTENT",
    "param_type": "BOOL",
    "default": "0",
  },
]
