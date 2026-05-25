# huhola_qgis
QGIS plugin for HuHoLa

Reimplements the [HuHoLa microtopography model](https://github.com/bravemaster3/huhola)
as a native QGIS Processing plugin backed by WhiteboxTools.

**Reference:** Noumonvi, K.D., Havertz, N.H., Bohlin, J., van der Linden, S., Nilsson, M.B. & Peichl, M. (2025). HuHoLa: A novel Hummock-Hollow-Lawn mire microtopography modelling approach. *Ecological Modelling*. [doi.org/10.1016/j.ecolmodel.2025.111001](https://www.sciencedirect.com/science/article/pii/S0304380025001978)

---

## What it does

HuHoLa classifies peatland surface microtopography from a DEM into three classes:

| Value | Class   | Colour  | Description                                    |
|-------|---------|---------|------------------------------------------------|
| 1     | Hollow  | #3CB0DE | Depression filled when running fill-sinks on the original DEM |
| 2     | Hummock | #D1452A | Peak filled when running fill-sinks on the inverted DEM |
| 3     | Lawn    | #B7B8B7 | Area not significantly filled in either pass   |

The plugin produces up to three rasters:

| Output | Type | Description |
|--------|------|-------------|
| Classification | Int16 (1/2/3) | Styled automatically on load (hollow=blue, hummock=red, lawn=grey) |
| Hol-hum | Float64 | `hollow_layer − hummock_layer` computed without fix_flats. Positive = hollow tendency, negative = hummock tendency. Used as a proxy for the depth or height of hollows and hummocks. |
| WTD proxy | Float64 | Same formula but Step 1 (original DEM fill) uses `fix_flats=True`. A relative index for water table depth — must be calibrated against field measurements before use. |

> **WTD proxy calibration:** The WTD proxy is a relative index, not an absolute water table depth. To convert it to actual WTD values (cm), calibrate it against dipwell or piezometer readings using linear regression (`WTD_measured ~ WTD_proxy`). The **WTD Proxy Calibration** tool in the Processing Toolbox does this automatically. See the original [WTD_proxy.ipynb](https://github.com/bravemaster3/huhola/blob/main/Examples/WTD_proxy.ipynb) for a worked example across multiple study sites.

---

## Installation

### 1. Download WhiteboxTools binary

Download the WhiteboxTools executable for your platform from:
https://www.whiteboxgeo.com/download-whiteboxtools/

Extract the archive and note the folder path (e.g. `C:\WhiteboxTools\` or `/opt/WhiteboxTools/`).

No Python packages need to be installed — the plugin calls the WhiteboxTools binary
directly and uses only libraries bundled with QGIS (`osgeo.gdal`, `numpy`).

### 2. Copy the plugin folder into QGIS

Copy the `huhola_qgis/` subfolder (not the repository root) into your QGIS user plugins directory:

- **Windows:** `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
- **macOS/Linux:** `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

The result should look like:

```
plugins/
└── huhola_qgis/
    ├── __init__.py
    ├── metadata.txt
    ├── huhola_plugin.py
    ├── huhola_provider.py
    ├── huhola_algorithm.py
    ├── huhola_classification.qml
    └── icon.png
```

### 3. Enable the plugin in QGIS

1. Open QGIS.
2. Go to **Plugins → Manage and Install Plugins…**
3. Search for **HuHoLa** and check the box to enable it.
4. The algorithm appears in the **Processing Toolbox** under **HuHoLa → HuHoLa Microtopography Classification**.

---

## Running the algorithm

| Parameter | Description |
|-----------|-------------|
| Input DEM | Any raster layer loaded in QGIS |
| Depression fill algorithm | Wang & Liu (default), Simple, or Planchon & Darboux — matches the fill method options from the original HuHoLa package |
| Fill threshold (m) | Pixels where the fill difference is below this value are classified as lawn instead of hollow/hummock. Default: 0.04 m |
| WhiteboxTools executable folder | Path to the folder containing the WhiteboxTools binary. Saved automatically after the first run. |
| Enable WTD proxy output | If checked, also writes the WTD proxy raster |
| Apply fix_flats when filling DEM | Applies `fix_flats=True` to the original DEM fill (Step 1) **only when WTD proxy output is enabled**. Has no effect when WTD proxy is disabled. The inverted DEM fill (Step 2) never uses fix_flats, per paper section 2.2.3. Default: enabled. |
| Flat increment (m) | Elevation increment added per flat cell when fix_flats is enabled. Only used when fix_flats is checked. Default: 0.001 m (per WTD_proxy.ipynb). |
| Output classification raster | Output path for the 3-class raster |
| Output hol-hum raster | Height/depth index (no fix_flats). Optional, enabled by default. |
| Output WTD proxy raster | Output path for the continuous WTD proxy raster (optional) |

The classification raster is styled automatically on load. The hol-hum and WTD proxy rasters load unstyled — apply a diverging colour ramp manually (e.g. Spectral) to visualise them.

---

## Algorithm summary

The algorithm runs in one of two modes depending on whether the WTD proxy is requested.

**Classification only** (`fix_flats=False`):

1. Replace nodata → 0 for WhiteboxTools compatibility.
2. Fill depressions in the original DEM (`fix_flats=False`) → **hollow layer** = `filled_DEM − DEM`.
3. Invert: `DEM_inv = max(DEM) − DEM`.
4. Fill depressions in the inverted DEM (`fix_flats=False`) → **hummock layer** = `filled_inv − DEM_inv`.
5. Combined layer: `hol_hum = hollow_layer − hummock_layer`.
6. Classify: pixels where `hol_hum > threshold` → hollow (1); `hol_hum < −threshold` → hummock (2); otherwise → lawn (3). A secondary override resolves pixels where both individual layers are positive (larger magnitude wins).

**With WTD proxy** (`fix_flats=True` in the original DEM fill only, per paper section 2.2.3):

Steps are the same as above except the original DEM fill (step 2) is re-run with `fix_flats=True` and the user-defined `flat_increment` (default 0.001 m), which introduces a small elevation gradient across flat areas to produce a hydrologically consistent drainage surface. The inverted DEM fill (step 4) never uses `fix_flats`. The resulting `hol_hum` surface from this second run is the **WTD proxy** — a continuous relative index that can be calibrated to actual water table depth via linear regression against field measurements.

---

## Validation and calibration tools

Two additional algorithms are available under **HuHoLa** in the Processing Toolbox.

### WTD Proxy Calibration

Regresses field-measured water table depths against the WTD proxy raster to produce a calibration equation.

| Input | Description |
|-------|-------------|
| WTD proxy raster | Output of the main algorithm with fix_flats=True |
| Field measurement points | Point shapefile with dipwell / piezometer readings |
| Field with measured WTD | Numeric attribute containing measured WTD values |

| Output | Description |
|--------|-------------|
| CSV | Proxy value, measured WTD, and fitted value per point |
| Scatter plot (PNG) | Data points and regression line with equation, R² and RMSE |

The regression equation (`WTD_measured = slope × WTD_proxy + intercept`) printed to the log can then be applied to the entire WTD proxy raster using the QGIS Raster Calculator to produce a calibrated WTD map.

### Classification Validation

Compares the classification raster against field-observed classes at point locations.

| Input | Description |
|-------|-------------|
| Classification raster | HuHoLa output (1=Hollow, 2=Hummock, 3=Lawn) |
| Field observation points | Point shapefile with ground-truth class labels |
| Field with observed class | Attribute containing class values (1, 2 or 3) |

| Output | Description |
|--------|-------------|
| Confusion matrix CSV | Rows = observed class, columns = predicted class |
| Metrics CSV | Overall accuracy and Cohen's kappa |
| Classification report | Per-class precision, recall, F1-score and support |

---

## Links

| Resource | URL |
|----------|-----|
| This plugin (huhola_qgis) | https://github.com/bravemaster3/huhola_qgis |
| Original HuHoLa Python package | https://github.com/bravemaster3/huhola |
| Paper (Noumonvi et al. 2025) | https://doi.org/10.1016/j.ecolmodel.2025.111001 |
| WhiteboxTools download | https://www.whiteboxgeo.com/download-whiteboxtools/ |

---

## License

Same as the original HuHoLa package.
