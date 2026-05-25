from qgis.core import QgsApplication
from .huhola_provider import HuHoLaProvider


class HuHoLaPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initGui(self):
        self.provider = HuHoLaProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        QgsApplication.processingRegistry().removeProvider(self.provider)
