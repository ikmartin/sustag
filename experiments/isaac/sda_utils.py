"""
Utilities for querying the USDA Soil Data Access (SDA) REST API and visualizing results.

SDA provides tabular soil survey data via SQL over HTTP. The main entry point is
`query_sda`, which accepts any valid SDA SQL string. The higher-level helpers
(`get_location_data_from_point`, `get_tables_from_point`) translate a lon/lat point
into map unit keys and return pre-built result tables.

SDA SQL reference: https://sdmdataaccess.sc.egov.usda.gov/QueryHelp.aspx

Documentation written by Claude, seems correct to me (Isaac)
"""

import requests
import pandas as pd
import plotly.express as px
import folium

pd.set_option('display.max_colwidth', None)
pd.set_option('display.max_columns', None)  # show all columns
pd.set_option('display.width', None)        # don't wrap output

def query_sda(sql):
    """Submit a SQL query to the SDA REST API and return results as a DataFrame.

    Args:
        sql: Valid SDA SQL string.

    Returns:
        DataFrame with one column per selected field, or an empty DataFrame if
        the query returns no rows.

    Raises:
        requests.HTTPError: On a non-2xx response.
        ValueError: If the response body is empty (usually a malformed query).
    """
    url = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"
    payload = {"query": sql, "format": "json+columnname"}
    
    response = requests.post(url, data=payload)
    response.raise_for_status()
    
    if not response.text:
        raise ValueError("Empty response from SDA — check your SQL for errors")
    
    data = response.json()
    
    # The response wraps results in a 'Table' key (or 'Table1', 'Table2'... for multiple result sets)
    # First row is column names when using json+columnname
    table = data.get("Table", [])
    if not table:
        return pd.DataFrame()
    
    columns = table[0]
    rows = table[1:]
    return pd.DataFrame(rows, columns=columns)

def get_location_data_from_point(lat, lon):
    """Return map unit metadata for the soil map unit at the given WGS-84 point.

    Args:
        lon: Longitude in decimal degrees (WGS-84).
        lat: Latitude in decimal degrees (WGS-84).

    Returns:
        DataFrame with columns: mukey, muname, areasymbol.
    """
    sql = f"""
    SELECT mu.mukey, mu.muname, l.areasymbol
    FROM mapunit mu
    JOIN legend l ON mu.lkey = l.lkey
    WHERE mu.mukey IN (
        SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84(
            'POINT({lon} {lat})'
        )
    )
    """
    return query_sda(sql)

def get_tables_from_point(lat, lon):
    """Return three soil data tables for the dominant map unit at a WGS-84 point.

    Resolves the map unit key via `get_location_data_from_point`, then queries
    crop yield, horizon, and restriction data for all components in that map unit.

    Args:
        lon: Longitude in decimal degrees (WGS-84).
        lat: Latitude in decimal degrees (WGS-84).

    Returns:
        Tuple of (crop_df, horizons_df, restrictions_df), each a DataFrame joined
        with component name and percent composition.
    """
    mukey = get_location_data_from_point(lat, lon).iloc[0]["mukey"]
    print(mukey)

    # crop data 
    crop_sql = f"""
        SELECT co.compname, co.comppct_r, cc.*
        FROM component co
        JOIN cocropyld cc ON co.cokey = cc.cokey
        WHERE co.mukey = '{mukey}'"""

    # horizons
    horizons_sql = f"""
        SELECT co.compname, co.comppct_r, ch.*
        FROM component co
        JOIN chorizon ch ON co.cokey = ch.cokey
        WHERE co.mukey = '{mukey}'"""

    # Restrictions data
    restrictions_sql = f"""
        SELECT co.compname, co.comppct_r, cr.*
        FROM component co
        JOIN corestrictions cr ON co.cokey = cr.cokey
        WHERE co.mukey = '{mukey}'"""
    return query_sda(crop_sql), query_sda(horizons_sql), query_sda(restrictions_sql)


def plot_point_px(lat, lon):
    """Display a Plotly scatter map centered on the given point. Shows inline in Jupyter."""
    df = pd.DataFrame({"lat": [lat], "lon": [lon]})
    fig = px.scatter_map(df, lat="lat", lon="lon", zoom=13)
    fig.show()

def plot_point(lat, lon, zoom=13):
    """Return a Folium map centered on the given point with a marker.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        zoom: Initial zoom level (default 13).

    Returns:
        folium.Map instance (renders inline in Jupyter).
    """
    m = folium.Map(location=[lat, lon], zoom_start=zoom)
    folium.Marker([lat, lon]).add_to(m)
    return m

def summarize_crop_yields(crop_df):
    """Aggregate crop yield data by crop name, summing irrigated and non-irrigated yields.

    Args:
        crop_df: DataFrame as returned by the crop query in `get_tables_from_point`.

    Returns:
        DataFrame indexed by cropname with columns: yldunits, nonirryield_r, irryield_r.
    """
    crop_df.nonirryield_r = crop_df.nonirryield_r.apply(lambda x: float(x))
    crop_df = crop_df.drop(columns=["cocropyldkey", "cokey", "vasoiprdgrp", "irryield_h", "irryield_l", "nonirryield_l", "nonirryield_h", "comppct_r", "compname", "cropprodindex"])
    agg_crop = crop_df.groupby("cropname").agg(
        yldunits=("yldunits", "first"),
        nonirryield_r =("nonirryield_r", "sum"),
        irryield_r =("irryield_r", "sum")
    )

    return agg_crop