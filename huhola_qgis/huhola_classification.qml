<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.16" styleCategories="AllStyleCategories">
  <pipe>
    <provider>
      <resampling enabled="false" maxOversampling="2"
        zoomedInResamplingMethod="nearestNeighbour"
        zoomedOutResamplingMethod="nearestNeighbour"/>
    </provider>
    <rasterrenderer type="paletted" alphaBand="-1" band="1" opacity="1">
      <rasterTransparency/>
      <colorPalette>
        <paletteEntry value="1" color="#3CB0DE" label="Hollow"  alpha="255"/>
        <paletteEntry value="2" color="#D1452A" label="Hummock" alpha="255"/>
        <paletteEntry value="3" color="#B7B8B7" label="Lawn"    alpha="255"/>
      </colorPalette>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
    <huesaturation colorizeOn="0" grayscaleMode="0" saturation="0"/>
    <rasterresampler maxOversampling="2"/>
  </pipe>
  <blendMode>0</blendMode>
</qgis>
