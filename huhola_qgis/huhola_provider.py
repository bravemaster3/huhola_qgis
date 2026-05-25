from qgis.core import QgsProcessingProvider
from .huhola_algorithm import HuHoLaAlgorithm
from .huhola_validation import (
    HuHoLaWTDCalibrationAlgorithm,
    HuHoLaClassificationValidationAlgorithm,
)


class HuHoLaProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(HuHoLaAlgorithm())
        self.addAlgorithm(HuHoLaWTDCalibrationAlgorithm())
        self.addAlgorithm(HuHoLaClassificationValidationAlgorithm())

    def id(self):
        return 'huhola'

    def name(self):
        return 'HuHoLa'

    def longName(self):
        return 'HuHoLa Peatland Microtopography'
