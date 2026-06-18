from opendbc.car.structs import CarParams
from opendbc.car.mg.values import CAR

Ecu = CarParams.Ecu

FW_VERSIONS = {
  CAR.MG_ZS: {
    # TODO: populate via tools/car_porting/auto_fingerprint.py once a route
    # with FW query enabled is captured on the 2025 MG ZS
  },
}
