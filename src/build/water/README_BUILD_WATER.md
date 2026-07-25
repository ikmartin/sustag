# README for `make_water.py`

Here's how it works.

`fetch_sites.py` -> produces site list from various sources of data, produces raw_site_list.csv, modeled after the current fetch_site list.

`download.py` -> checks that raw data for all sites in raw_site_list.csv is present, downloads what isn't present and what can't be downloaded and is missing is flagged/reported. Should store raw/IWQIS and raw/USGS for now, and should use chunking for datasets which cannot be downloaded (non-API datasets like IWQIS will need to be git commited)

`filter_sites.py` -> utilities for filtering sites. Lifespan and sparsity filters should be applied automatically, quality filters need a mechanism for human review and should have a high false positive rate to ensure all actual bad sites are caught.

- Produces a small json file called filtered_sites.json with three lists: 
  - good_sites : [] list of good sites
  - flagged_sites : list of sites which trip the QUALITY review (not the sparsity/data length filters) and 
  - short_sites : list of sites which are trip the sparsity/data length filters.
  - good_sites = [all sites from raw_site_list.csv] - [short_sites] - KNOWN_BAD. Here, KNOWN_BAD is a preset list of sites at the top of the file.
- For the human review process, simply print one large table of nitrate graphs, one row per site, with a nitrate timeseries and any other useful graphs that illustrate site quality. Each row should also have a table of site statistics.
- The last line of the command line printout of this step should print the full list of sites flagged for quality (in python list format so it can be copy and pasted) with instructions to manually modify KNOWN_BAD in the python file and then to rerun filter_sites.py.

`make_water.py` -> make the basins for the sites in good_sites using the raw data downloaded by download.py. Stores data in processed/water. The data should be stored already aggregated to daily max.

This is a significant rework of the water pipeline and requires substantial human review. Because of this, make_data should be renamed make_features.py, and it should NOT call make_water. The new build pipeline is
1. the make water pipeline described above
2. the make features pipeline (current make_data but it makes everything EXCEPT water)

Draft a detailed plan for this. I envision the human review process using something similar to the basin_editor -- perhaps the widget can be launched in a data review mode, a special mode used to manually review pieces of the data set. For now, leave the widget untouched and use the human review mechanism specified above, but flag this as a future item.