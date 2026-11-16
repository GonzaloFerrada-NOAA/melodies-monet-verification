# melodies-monet-verification

## Python environment
`conda create --name melodies-monet -y -c conda-forge python=3.11 melodies-monet pandas pyyaml monet monetio netcdf4 wrf-python typer pooch regionmask`

Then activate it to install additional packages:

`conda activate melodies-monet`

### Install additional dependencies
- [**pytstack**](https://github.com/noaa-oar-arl/pytspack/releases/tag/v0.1.3) (needed for regridding AERONET data):

    `pip install git+https://github.com/noaa-oar-arl/pytspack.git@v0.1.3`

- [**pyearthsciences**](https://github.com/GonzaloFerrada-NOAA/pyearthsciences) (for plotting):

    `pip install git+https://github.com/GonzaloFerrada-NOAA/pyearthsciences.git`

## Other details

The map and charts are drawn by JavaScript libraries that the page loads from public CDNs when someone opens it, so nothing is installed for them: Leaflet 1.9.4 and Leaflet.markercluster 1.5.3 from unpkg.com, and Plotly.js 2.30.0 from cdn.plot.ly.
Viewers’ browsers need internet access to those CDNs and to the map tile servers (Esri, plus OSM if those layers are used).
Everything else, including all data, is inside the single generated HTML file.
To regenerate it: edit config.yaml, then run python generate_dashboard.py [config.yaml].