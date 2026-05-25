import os
import tempfile
import shutil
import subprocess
import numpy as np

from osgeo import gdal

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFile,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterRasterDestination,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsRasterLayer,
    QgsSettings,
)

gdal.UseExceptions()


class _ClassStylePostProcessor(QgsProcessingLayerPostProcessorInterface):
    _instance = None

    def postProcessLayer(self, layer, context, feedback):
        if isinstance(layer, QgsRasterLayer):
            qml = os.path.join(os.path.dirname(__file__), 'huhola_classification.qml')
            if os.path.exists(qml):
                layer.loadNamedStyle(qml)
                layer.triggerRepaint()
                sidecar = os.path.splitext(layer.source())[0] + '.qml'
                layer.saveNamedStyle(sidecar)

    @staticmethod
    def create():
        _ClassStylePostProcessor._instance = _ClassStylePostProcessor()
        return _ClassStylePostProcessor._instance


class HuHoLaAlgorithm(QgsProcessingAlgorithm):
    """
    Classifies peatland microtopography from a DEM into:
        1 = Hollow  (depression)
        2 = Hummock (elevated)
        3 = Lawn    (flat)

    The hol-hum raster (hollow_layer - hummock_layer, no fix_flats) is always
    computed from plain fill-depressions runs and is used both for classification
    and as an index of hummock/hollow height or depth.

    The WTD proxy is the same hol-hum surface but derived from a fill run with
    fix_flats=True on the original DEM only (Step 1), which produces a
    hydrologically consistent drainage gradient across flat areas. The inverted
    DEM fill (Step 2) never uses fix_flats (paper section 2.2.3).
    """

    DEM = 'DEM'
    FILL_METHOD = 'FILL_METHOD'
    THRESHOLD = 'THRESHOLD'
    WBT_FOLDER = 'WBT_FOLDER'
    FIX_FLATS = 'FIX_FLATS'
    FLAT_INCREMENT = 'FLAT_INCREMENT'
    ENABLE_WTD = 'ENABLE_WTD'
    OUTPUT_CLASSIFICATION = 'OUTPUT_CLASSIFICATION'
    OUTPUT_HOL_HUM = 'OUTPUT_HOL_HUM'
    OUTPUT_WTD = 'OUTPUT_WTD'

    FILL_METHODS = ['Wang & Liu (default)', 'Simple', 'Planchon & Darboux']
    FILL_TOOLS = [
        'FillDepressionsWangAndLiu',
        'FillDepressions',
        'FillDepressionsPlanchonAndDarboux',
    ]
    WTD_FLAT_INCREMENT = 0.001  # default per WTD_proxy.ipynb

    # ------------------------------------------------------------------ #
    # Algorithm metadata                                                   #
    # ------------------------------------------------------------------ #

    def name(self):
        return 'huhola_classify'

    def displayName(self):
        return 'HuHoLa Microtopography Classification'

    def group(self):
        return 'HuHoLa'

    def groupId(self):
        return 'huhola'

    def shortHelpString(self):
        return (
            'Classifies peatland microtopography from a DEM into:\n'
            '  1 = Hollow  (blue)\n'
            '  2 = Hummock (red)\n'
            '  3 = Lawn    (grey)\n\n'
            'Fill algorithm:\n'
            '  Wang & Liu — recommended default\n'
            '  Simple — faster, less accurate on flat terrain\n'
            '  Planchon & Darboux — alternative\n\n'
            'Outputs:\n'
            '  Classification raster  — styled automatically (1/2/3)\n'
            '  Hol-hum raster         — hollow_layer - hummock_layer,\n'
            '                           no fix_flats; proxy for hummock/\n'
            '                           hollow height or depth\n'
            '  WTD proxy raster       — same but Step 1 uses fix_flats=True\n'
            '                           (flat_increment user-settable, default 0.001)\n'
            '                           for a hydrologically consistent surface\n\n'
            'Requires only the WhiteboxTools binary.\n'
            'Download: https://www.whiteboxgeo.com/download-whiteboxtools/'
        )

    def createInstance(self):
        return HuHoLaAlgorithm()

    # ------------------------------------------------------------------ #
    # Parameter definitions                                                #
    # ------------------------------------------------------------------ #

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, 'Input DEM'
        ))

        self.addParameter(QgsProcessingParameterEnum(
            self.FILL_METHOD,
            'Depression fill algorithm',
            options=self.FILL_METHODS,
            defaultValue=0,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD,
            'Fill threshold (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.04,
            minValue=0.0,
            maxValue=1.0,
        ))

        self.addParameter(QgsProcessingParameterFile(
            self.WBT_FOLDER,
            'WhiteboxTools executable folder',
            behavior=QgsProcessingParameterFile.Folder,
            defaultValue=QgsSettings().value('huhola/wbt_path', ''),
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.ENABLE_WTD,
            'Enable WTD proxy output',
            defaultValue=True,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.FIX_FLATS,
            'Apply fix_flats when filling DEM  [WTD proxy only]',
            defaultValue=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.FLAT_INCREMENT,
            'Flat increment (m)  [used only when fix_flats is enabled]',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=self.WTD_FLAT_INCREMENT,
            minValue=1e-6,
            maxValue=1.0,
        ))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_CLASSIFICATION, 'Output classification raster'
        ))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_HOL_HUM,
            'Output hol-hum raster (height/depth index, no fix_flats)',
            optional=True,
            createByDefault=True,
        ))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_WTD,
            'Output WTD proxy raster',
            optional=True,
            createByDefault=True,
        ))

    # ------------------------------------------------------------------ #
    # Main processing                                                      #
    # ------------------------------------------------------------------ #

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        fill_idx = self.parameterAsEnum(parameters, self.FILL_METHOD, context)
        threshold = self.parameterAsDouble(parameters, self.THRESHOLD, context)
        wbt_folder = self.parameterAsString(parameters, self.WBT_FOLDER, context)
        enable_wtd = self.parameterAsBoolean(parameters, self.ENABLE_WTD, context)
        fix_flats = self.parameterAsBoolean(parameters, self.FIX_FLATS, context)
        flat_increment = self.parameterAsDouble(parameters, self.FLAT_INCREMENT, context)
        out_class = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_CLASSIFICATION, context
        )
        out_hol_hum = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_HOL_HUM, context
        )
        out_wtd = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_WTD, context
        )

        wbt_exe = self._find_wbt_exe(wbt_folder)
        if wbt_exe is None:
            raise QgsProcessingException(
                f'WhiteboxTools executable not found in: {wbt_folder}\n'
                'Expected WhiteboxTools.exe (Windows) or whitebox_tools (Linux/Mac).\n'
                'Download from https://www.whiteboxgeo.com/download-whiteboxtools/'
            )

        fill_tool = self.FILL_TOOLS[fill_idx]

        tmpdir = tempfile.mkdtemp()
        try:
            results = self._classify(
                wbt_exe, fill_tool, fix_flats, flat_increment,
                dem_layer.source(), threshold,
                enable_wtd, out_class, out_hol_hum, out_wtd,
                tmpdir, feedback,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        QgsSettings().setValue('huhola/wbt_path', wbt_folder)

        if context.willLoadLayerOnCompletion(out_class):
            context.layerToLoadOnCompletionDetails(out_class).setPostProcessor(
                _ClassStylePostProcessor.create()
            )

        return results

    # ------------------------------------------------------------------ #
    # GDAL helpers                                                         #
    # ------------------------------------------------------------------ #

    def _read_raster(self, path):
        ds = gdal.Open(path, gdal.GA_ReadOnly)
        if ds is None:
            raise QgsProcessingException(f'Cannot open raster: {path}')
        band = ds.GetRasterBand(1)
        arr = band.ReadAsArray().astype(np.float64)
        nodata = band.GetNoDataValue()
        gt = ds.GetGeoTransform()
        proj = ds.GetProjection()
        ds = None
        return arr, gt, proj, nodata

    def _write_raster(self, path, arr, gt, proj, gdal_dtype, nodata):
        driver = gdal.GetDriverByName('GTiff')
        height, width = arr.shape
        ds = driver.Create(path, width, height, 1, gdal_dtype)
        if ds is None:
            raise QgsProcessingException(f'Cannot create raster: {path}')
        ds.SetGeoTransform(gt)
        ds.SetProjection(proj)
        band = ds.GetRasterBand(1)
        band.WriteArray(arr)
        if nodata is not None:
            band.SetNoDataValue(nodata)
        ds.FlushCache()
        ds = None

    # ------------------------------------------------------------------ #
    # WhiteboxTools helper                                                 #
    # ------------------------------------------------------------------ #

    def _find_wbt_exe(self, folder):
        for name in ('WhiteboxTools.exe', 'whitebox_tools.exe', 'whitebox_tools'):
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                return p
        return None

    def _fill_depressions(self, wbt_exe, fill_tool, dem_path, output_path,
                          fix_flats, feedback, flat_increment=None):
        cmd = [
            wbt_exe,
            f'--run={fill_tool}',
            f'--dem={dem_path}',
            f'--output={output_path}',
            '--verbose=false',
        ]
        if fix_flats:
            inc = flat_increment if flat_increment is not None else self.WTD_FLAT_INCREMENT
            cmd += ['--fix_flats', f'--flat_increment={inc}']
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout.strip():
            feedback.pushInfo(result.stdout.strip())
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise QgsProcessingException(
                f'WhiteboxTools {fill_tool} failed (exit {result.returncode}).\n{detail}'
            )

    # ------------------------------------------------------------------ #
    # Core HuHoLa logic                                                   #
    # ------------------------------------------------------------------ #

    def _classify(self, wbt_exe, fill_tool, fix_flats, flat_increment,
                  dem_path, threshold, enable_wtd,
                  out_class, out_hol_hum, out_wtd, tmpdir, feedback):

        # --- 1. Read DEM -----------------------------------------------
        feedback.pushInfo('Reading DEM...')
        feedback.setProgress(5)

        dem, gt, proj, orig_nodata = self._read_raster(dem_path)
        if orig_nodata is not None:
            dem = np.where(dem == orig_nodata, np.nan, dem)

        nodata_mask = np.isnan(dem)
        wbt_meta_nodata = 0.0

        dem_prep = np.where(nodata_mask, 0.0, dem)
        p_dem_prep = os.path.join(tmpdir, 'dem_prep.tif')
        self._write_raster(p_dem_prep, dem_prep, gt, proj,
                           gdal.GDT_Float64, wbt_meta_nodata)

        if feedback.isCanceled():
            return {}

        # --- 2. Fill original DEM without fix_flats --------------------
        # Used for classification and hol-hum height/depth index.
        feedback.pushInfo(f'Step 1: Filling original DEM (fix_flats=False, {fill_tool})...')
        feedback.setProgress(10)

        p_filled = os.path.join(tmpdir, 'dem_filled.tif')
        self._fill_depressions(wbt_exe, fill_tool,
                               p_dem_prep, p_filled, False, feedback)

        filled_dem, _, _, _ = self._read_raster(p_filled)
        filled_dem[filled_dem < -500] = np.nan
        filled_dem[nodata_mask] = np.nan
        hollows_lyr = filled_dem - dem
        hollows_lyr[nodata_mask] = np.nan

        if feedback.isCanceled():
            return {}

        # --- 3. Fill inverted DEM (fix_flats never applied, per paper) -
        feedback.pushInfo('Step 2: Filling inverted DEM (fix_flats=False, always)...')
        feedback.setProgress(30)

        dem_inv = np.nanmax(dem) - dem
        dem_inv_prep = np.where(nodata_mask, 0.0, dem_inv)
        p_inv = os.path.join(tmpdir, 'dem_inv.tif')
        self._write_raster(p_inv, dem_inv_prep, gt, proj,
                           gdal.GDT_Float64, wbt_meta_nodata)

        p_filled_inv = os.path.join(tmpdir, 'dem_inv_filled.tif')
        self._fill_depressions(wbt_exe, fill_tool,
                               p_inv, p_filled_inv, False, feedback)

        filled_inv, _, _, _ = self._read_raster(p_filled_inv)
        filled_inv[filled_inv < -500] = np.nan
        filled_inv[nodata_mask] = np.nan
        hummock_lyr = filled_inv - dem_inv
        hummock_lyr[nodata_mask] = np.nan

        if feedback.isCanceled():
            return {}

        # --- 4. hol-hum (no fix_flats) ---------------------------------
        # Positive = hollow tendency, negative = hummock tendency.
        # Used for classification and as a height/depth index of hummocks/hollows.
        hol_hum_class = hollows_lyr - hummock_lyr

        # --- 5. Classify -----------------------------------------------
        feedback.pushInfo('Classifying microtopography...')
        feedback.setProgress(50)

        valid = ~nodata_mask
        classification = np.where(nodata_mask, -9999, 3).astype(np.int16)
        classification[valid & (hol_hum_class > threshold)] = 1    # hollow
        classification[valid & (hol_hum_class < -threshold)] = 2   # hummock

        # Secondary override: where both individual layers are positive,
        # resolve by magnitude (matches original HuHoLa code)
        both_pos = valid & (hollows_lyr > 0) & (hummock_lyr > 0)
        classification[both_pos & (hollows_lyr > hummock_lyr)] = 1
        classification[both_pos & (hummock_lyr >= hollows_lyr)] = 2

        # --- 6. Write classification ------------------------------------
        feedback.pushInfo('Writing classification raster...')
        feedback.setProgress(65)

        self._write_raster(out_class, classification.astype(np.float64),
                           gt, proj, gdal.GDT_Int16, -9999)
        results = {self.OUTPUT_CLASSIFICATION: out_class}

        # --- 7. Write hol-hum (no fix_flats) ---------------------------
        if out_hol_hum:
            feedback.pushInfo('Writing hol-hum raster...')
            hh_out = hol_hum_class.copy()
            hh_out[nodata_mask] = -9999.0
            self._write_raster(out_hol_hum, hh_out, gt, proj,
                               gdal.GDT_Float64, -9999.0)
            results[self.OUTPUT_HOL_HUM] = out_hol_hum

        if feedback.isCanceled():
            return results

        # --- 8. WTD proxy (fix_flats on Step 1 only, if requested) -----
        if enable_wtd and out_wtd:
            if fix_flats:
                # Re-run Step 1 with fix_flats=True to get a hydrologically
                # consistent hollow layer for the WTD proxy surface.
                feedback.pushInfo(
                    f'WTD proxy: re-filling original DEM '
                    f'(fix_flats=True, flat_increment={flat_increment})...'
                )
                feedback.setProgress(75)
                p_filled_ff = os.path.join(tmpdir, 'dem_filled_ff.tif')
                self._fill_depressions(wbt_exe, fill_tool,
                                       p_dem_prep, p_filled_ff, True, feedback,
                                       flat_increment=flat_increment)
                filled_dem_ff, _, _, _ = self._read_raster(p_filled_ff)
                filled_dem_ff[filled_dem_ff < -500] = np.nan
                filled_dem_ff[nodata_mask] = np.nan
                hollows_lyr_ff = filled_dem_ff - dem
                hollows_lyr_ff[nodata_mask] = np.nan
                hol_hum_wtd = hollows_lyr_ff - hummock_lyr
            else:
                # fix_flats disabled — WTD proxy is the same as hol_hum_class
                hol_hum_wtd = hol_hum_class

            feedback.pushInfo('Writing WTD proxy raster...')
            feedback.setProgress(90)
            wtd = hol_hum_wtd.copy()
            valid_wtd = wtd[~nodata_mask]
            wtd_min = float(valid_wtd.min()) if valid_wtd.size > 0 else 0.0
            wtd_max = float(valid_wtd.max()) if valid_wtd.size > 0 else 1.0
            wtd[nodata_mask] = -9999.0
            self._write_raster(out_wtd, wtd, gt, proj, gdal.GDT_Float64, -9999.0)
            feedback.pushInfo(f'WTD proxy range: {wtd_min:.4f} to {wtd_max:.4f} m')
            results[self.OUTPUT_WTD] = out_wtd

        feedback.setProgress(100)
        feedback.pushInfo('HuHoLa classification complete.')
        return results
