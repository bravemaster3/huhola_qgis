import os
import csv
import numpy as np

from osgeo import gdal

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingException,
    QgsProcessing,
)

gdal.UseExceptions()

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def _sample_raster_at_points(raster_path, xy_list):
    """Sample a raster at (x, y) coordinate pairs. Returns list of float or nan."""
    ds = gdal.Open(raster_path, gdal.GA_ReadOnly)
    if ds is None:
        raise QgsProcessingException(f'Cannot open raster: {raster_path}')
    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    arr = band.ReadAsArray().astype(np.float64)
    ds = None
    nrows, ncols = arr.shape
    values = []
    for x, y in xy_list:
        col = int((x - gt[0]) / gt[1])
        row = int((y - gt[3]) / gt[5])
        if 0 <= row < nrows and 0 <= col < ncols:
            val = float(arr[row, col])
            if nodata is not None and abs(val - nodata) < 1e-6:
                values.append(np.nan)
            else:
                values.append(val)
        else:
            values.append(np.nan)
    return values


def _read_points(layer, field_name, numeric):
    """Iterate a vector layer, return (xy_list, values) skipping null geometries/attributes."""
    xy_list = []
    values = []
    for feature in layer.getFeatures():
        geom = feature.geometry()
        if geom.isNull():
            continue
        pt = geom.asPoint()
        raw = feature[field_name]
        if raw is None or raw == '' or raw == 'NULL':
            continue
        try:
            val = float(raw) if numeric else int(float(raw))
        except (TypeError, ValueError):
            continue
        xy_list.append((pt.x(), pt.y()))
        values.append(val)
    return xy_list, values


# ======================================================================
# WTD Proxy Calibration
# ======================================================================

class HuHoLaWTDCalibrationAlgorithm(QgsProcessingAlgorithm):

    WTD_PROXY = 'WTD_PROXY'
    FIELD_POINTS = 'FIELD_POINTS'
    FIELD_WTD = 'FIELD_WTD'
    OUTPUT_CSV = 'OUTPUT_CSV'
    OUTPUT_PLOT = 'OUTPUT_PLOT'

    def name(self):
        return 'huhola_wtd_calibration'

    def displayName(self):
        return 'WTD Proxy Calibration'

    def group(self):
        return 'HuHoLa'

    def groupId(self):
        return 'huhola'

    def shortHelpString(self):
        return (
            'Calibrates the WTD proxy raster against field-measured water table\n'
            'depths using simple linear regression.\n\n'
            'Inputs:\n'
            '  WTD proxy raster — from HuHoLa classification with fix_flats=True\n'
            '  Field measurement points — shapefile with dipwell / piezometer readings\n'
            '  Field name — column in the shapefile containing measured WTD values\n\n'
            'Outputs:\n'
            '  CSV with proxy value, measured WTD, and fitted value per point\n'
            '  Scatter plot PNG with regression line, equation, R² and RMSE\n\n'
            'The regression equation (WTD_measured = slope × WTD_proxy + intercept)\n'
            'can then be applied to the whole raster to produce calibrated WTD maps.'
        )

    def createInstance(self):
        return HuHoLaWTDCalibrationAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.WTD_PROXY, 'WTD proxy raster'
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.FIELD_POINTS,
            'Field measurement points',
            types=[QgsProcessing.TypeVectorPoint],
        ))
        self.addParameter(QgsProcessingParameterField(
            self.FIELD_WTD,
            'Field with measured WTD values',
            parentLayerParameterName=self.FIELD_POINTS,
            type=QgsProcessingParameterField.Numeric,
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_CSV,
            'Output CSV (proxy / measured pairs)',
            fileFilter='CSV files (*.csv)',
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_PLOT,
            'Output scatter plot (PNG)',
            fileFilter='PNG files (*.png)',
            optional=True,
            createByDefault=True,
        ))

    def processAlgorithm(self, parameters, context, feedback):
        proxy_layer = self.parameterAsRasterLayer(parameters, self.WTD_PROXY, context)
        points_layer = self.parameterAsVectorLayer(parameters, self.FIELD_POINTS, context)
        wtd_field = self.parameterAsFields(parameters, self.FIELD_WTD, context)[0]
        out_csv = self.parameterAsFileOutput(parameters, self.OUTPUT_CSV, context)
        out_plot = self.parameterAsFileOutput(parameters, self.OUTPUT_PLOT, context)

        feedback.pushInfo('Reading field measurement points...')
        feedback.setProgress(10)
        xy_list, measured = _read_points(points_layer, wtd_field, numeric=True)
        if not xy_list:
            raise QgsProcessingException('No valid field measurement points found.')
        feedback.pushInfo(f'Found {len(xy_list)} field measurement points.')

        feedback.pushInfo('Sampling WTD proxy raster at field points...')
        feedback.setProgress(30)
        proxy_values = _sample_raster_at_points(proxy_layer.source(), xy_list)

        pairs = [(p, m) for p, m in zip(proxy_values, measured) if not np.isnan(p)]
        if len(pairs) < 2:
            raise QgsProcessingException(
                f'Only {len(pairs)} valid proxy/measured pair(s) found — need at least 2.'
            )
        feedback.pushInfo(f'{len(pairs)} valid pairs after removing nodata.')

        proxy_arr = np.array([p for p, _ in pairs])
        measured_arr = np.array([m for _, m in pairs])

        feedback.pushInfo('Running linear regression...')
        feedback.setProgress(55)
        slope, intercept = np.polyfit(proxy_arr, measured_arr, 1)
        predicted = slope * proxy_arr + intercept

        ss_res = np.sum((measured_arr - predicted) ** 2)
        ss_tot = np.sum((measured_arr - measured_arr.mean()) ** 2)
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        rmse = float(np.sqrt(np.mean((measured_arr - predicted) ** 2)))

        sign = '+' if intercept >= 0 else '-'
        equation = f'WTD = {slope:.4f} x proxy {sign} {abs(intercept):.4f}'
        feedback.pushInfo(f'Regression: {equation}')
        feedback.pushInfo(f'R² = {r_squared:.4f},  RMSE = {rmse:.4f}')

        feedback.pushInfo('Writing CSV...')
        feedback.setProgress(70)
        with open(out_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['wtd_proxy', 'wtd_measured', 'wtd_fitted'])
            for p, m, pr in zip(proxy_arr, measured_arr, predicted):
                writer.writerow([f'{p:.6f}', f'{m:.6f}', f'{pr:.6f}'])

        results = {self.OUTPUT_CSV: out_csv}

        if out_plot:
            if HAS_MATPLOTLIB:
                feedback.pushInfo('Writing scatter plot...')
                feedback.setProgress(85)
                fig, ax = plt.subplots(figsize=(7, 5))
                ax.scatter(proxy_arr, measured_arr, color='steelblue',
                           alpha=0.75, zorder=3, label='Field points')
                x_line = np.array([proxy_arr.min(), proxy_arr.max()])
                ax.plot(x_line, slope * x_line + intercept, color='firebrick',
                        linewidth=2,
                        label=f'{equation}\nR² = {r_squared:.3f},  RMSE = {rmse:.3f}')
                ax.set_xlabel('WTD proxy')
                ax.set_ylabel('Measured WTD')
                ax.set_title('WTD Proxy Calibration')
                ax.legend()
                fig.tight_layout()
                fig.savefig(out_plot, dpi=150)
                plt.close(fig)
                results[self.OUTPUT_PLOT] = out_plot
            else:
                feedback.pushWarning('matplotlib not available — scatter plot not produced.')

        feedback.setProgress(100)
        return results


# ======================================================================
# Classification Validation
# ======================================================================

class HuHoLaClassificationValidationAlgorithm(QgsProcessingAlgorithm):

    CLASS_RASTER = 'CLASS_RASTER'
    FIELD_POINTS = 'FIELD_POINTS'
    FIELD_CLASS = 'FIELD_CLASS'
    OUTPUT_CONFUSION = 'OUTPUT_CONFUSION'
    OUTPUT_METRICS = 'OUTPUT_METRICS'
    OUTPUT_REPORT = 'OUTPUT_REPORT'

    LABELS = [1, 2, 3]
    LABEL_NAMES = {1: 'Hollow', 2: 'Hummock', 3: 'Lawn'}

    def name(self):
        return 'huhola_classification_validation'

    def displayName(self):
        return 'Classification Validation'

    def group(self):
        return 'HuHoLa'

    def groupId(self):
        return 'huhola'

    def shortHelpString(self):
        return (
            'Validates the HuHoLa classification raster against field-observed\n'
            'microtopography classes.\n\n'
            'Inputs:\n'
            '  Classification raster — HuHoLa output (1=Hollow, 2=Hummock, 3=Lawn)\n'
            '  Field observation points — shapefile with ground-truth classes\n'
            '  Field name — column containing observed class values (1 / 2 / 3)\n\n'
            'Outputs:\n'
            '  Confusion matrix CSV (rows = observed, columns = predicted)\n'
            '  Metrics CSV (overall accuracy, Cohen\'s kappa)\n'
            '  Classification report (per-class precision, recall, F1-score, support)'
        )

    def createInstance(self):
        return HuHoLaClassificationValidationAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.CLASS_RASTER, 'HuHoLa classification raster'
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.FIELD_POINTS,
            'Field observation points',
            types=[QgsProcessing.TypeVectorPoint],
        ))
        self.addParameter(QgsProcessingParameterField(
            self.FIELD_CLASS,
            'Field with observed class (1=Hollow, 2=Hummock, 3=Lawn)',
            parentLayerParameterName=self.FIELD_POINTS,
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_CONFUSION,
            'Output confusion matrix CSV',
            fileFilter='CSV files (*.csv)',
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_METRICS,
            'Output metrics CSV (accuracy, kappa)',
            fileFilter='CSV files (*.csv)',
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_REPORT,
            'Output classification report (text)',
            fileFilter='Text files (*.txt)',
        ))

    def processAlgorithm(self, parameters, context, feedback):
        class_layer = self.parameterAsRasterLayer(parameters, self.CLASS_RASTER, context)
        points_layer = self.parameterAsVectorLayer(parameters, self.FIELD_POINTS, context)
        class_field = self.parameterAsFields(parameters, self.FIELD_CLASS, context)[0]
        out_confusion = self.parameterAsFileOutput(parameters, self.OUTPUT_CONFUSION, context)
        out_metrics = self.parameterAsFileOutput(parameters, self.OUTPUT_METRICS, context)
        out_report = self.parameterAsFileOutput(parameters, self.OUTPUT_REPORT, context)

        feedback.pushInfo('Reading field observation points...')
        feedback.setProgress(10)
        xy_list, observed = _read_points(points_layer, class_field, numeric=False)
        if not xy_list:
            raise QgsProcessingException('No valid observation points found.')
        feedback.pushInfo(f'Found {len(xy_list)} observation points.')

        feedback.pushInfo('Sampling classification raster at observation points...')
        feedback.setProgress(30)
        predicted_raw = _sample_raster_at_points(class_layer.source(), xy_list)

        pairs = []
        for obs, pred in zip(observed, predicted_raw):
            if np.isnan(pred):
                continue
            pred_int = int(round(pred))
            if obs not in self.LABELS or pred_int not in self.LABELS:
                continue
            pairs.append((obs, pred_int))

        if not pairs:
            raise QgsProcessingException('No valid observation/prediction pairs found.')
        feedback.pushInfo(f'{len(pairs)} valid pairs after filtering.')
        feedback.setProgress(50)

        y_true = np.array([p[0] for p in pairs])
        y_pred = np.array([p[1] for p in pairs])

        # Confusion matrix: rows=observed (actual), columns=predicted
        n = len(self.LABELS)
        label_idx = {l: i for i, l in enumerate(self.LABELS)}
        cm = np.zeros((n, n), dtype=int)
        for t, p in zip(y_true, y_pred):
            cm[label_idx[t], label_idx[p]] += 1

        total = int(cm.sum())
        row_sums = cm.sum(axis=1)
        col_sums = cm.sum(axis=0)
        accuracy = float(np.diag(cm).sum()) / total if total > 0 else 0.0

        pe = float((row_sums * col_sums).sum()) / (total ** 2) if total > 0 else 0.0
        kappa = (accuracy - pe) / (1.0 - pe) if pe < 1.0 else 0.0

        feedback.pushInfo(f'Overall accuracy: {accuracy:.4f}')
        feedback.pushInfo(f"Cohen's kappa:    {kappa:.4f}")
        feedback.setProgress(70)

        # Per-class metrics
        class_metrics = {}
        for i, label in enumerate(self.LABELS):
            tp = cm[i, i]
            fp = int(col_sums[i]) - tp
            fn = int(row_sums[i]) - tp
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2 * precision * recall / (precision + recall)
                  if (precision + recall) > 0 else 0.0)
            class_metrics[label] = dict(
                name=self.LABEL_NAMES[label],
                precision=precision,
                recall=recall,
                f1=f1,
                support=int(row_sums[i]),
            )

        # Confusion matrix CSV
        with open(out_confusion, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(
                ['observed \\ predicted'] + [self.LABEL_NAMES[l] for l in self.LABELS]
            )
            for i, label in enumerate(self.LABELS):
                writer.writerow([self.LABEL_NAMES[label]] + list(cm[i]))

        # Metrics CSV
        with open(out_metrics, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['metric', 'value'])
            writer.writerow(['overall_accuracy', f'{accuracy:.4f}'])
            writer.writerow(['cohen_kappa', f'{kappa:.4f}'])
            writer.writerow(['n_samples', total])

        # Classification report (text)
        w = 12
        lines = [
            'HuHoLa Classification Validation',
            '=' * 52,
            f'{"Class":<{w}} {"Precision":>10} {"Recall":>10} {"F1-score":>10} {"Support":>8}',
            '-' * 52,
        ]
        for label in self.LABELS:
            m = class_metrics[label]
            lines.append(
                f'{m["name"]:<{w}} {m["precision"]:>10.4f} {m["recall"]:>10.4f} '
                f'{m["f1"]:>10.4f} {m["support"]:>8}'
            )
        lines += [
            '-' * 52,
            f'{"accuracy":<{w}} {"":>10} {"":>10} {accuracy:>10.4f} {total:>8}',
            f'{"kappa":<{w}} {"":>10} {"":>10} {kappa:>10.4f} {total:>8}',
        ]
        with open(out_report, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

        feedback.setProgress(100)
        return {
            self.OUTPUT_CONFUSION: out_confusion,
            self.OUTPUT_METRICS: out_metrics,
            self.OUTPUT_REPORT: out_report,
        }
