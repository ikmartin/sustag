## List of good water sites

Big basin sites
["WQS0066", "WQS0065", "USGS-05474500", "WQS0020", "USGS-05420500"]

Fixed "WQS0066" and "WQS0067", these are not actually "humongous" basins. The other three truly are huge.

## Basin Notes

Basins are incredibly important to get correct. There are three methods for computing them, each one produces the best results in at least one scenario. I reviewed all sites and manually checked which basin was best for each site by cross referencing with USGS and the IWQIS map + IWQIS notes in `site_location_metadata`. The file `preferred_basin.csv` stores these files. The archived version `preferred_basin_archive.csv` is a copy of this file created after manually reviewing all sites.

## Widget Notes

### `info_panel.py`

Currently graphing code located here. To plot rain, rainfall is aggregated by date and then a mean is taken. This throws away all spatial content in the graph. You could imagine a more useful way to aggregate, for instance, a weighted sum based on distance from the grid point to the site location calculated as flow distance. Rainfall closer to the site should tend to have a bigger impact on the site, me-thinks.

This also is slow for graphing likely. Probably better to pre-aggregate all the data and then call it on demand.