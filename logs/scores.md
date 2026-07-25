# CLF Model 8 

| | |
|-|-|
| **Timestamp:** | [2026-07-23T21:22:03Z] | 
|**Recipe:** | recipe_CLF  |
|**# Features:** | 65|
| **List of Features:** | rest_of_state_nitrate_lag1, pct_corn_b2, tile_frac_basin, mean_dist_to_sensor, rest_of_state_nitrate_lag2, max_dist_to_sensor, pct_corn_b1, tot_tiles92, pct_nonag_b0, log_basin_area, tot_contact, lon, lat, pct_nonag_b2, pct_hay_pasture_b2, pct_nonag_b1, tot_bfi, pct_alfalfa_b0, cat_contact, surplus_kgha_norm_b2_x, pct_small_grains_b0, pct_other_b2, pct_alfalfa_b2, doy_sin, surplus_kgha_norm_b0_x, pct_corn_b0, tile_frac_ag, pct_soybeans_b0, pct_fallow_b2, pct_other_b0, doy_cos, pct_hay_pasture_b0, surplus_kgha_norm_b1_y, pct_soybeans_b2, pct_fallow_b1, surplus_kgha_norm_b0_y, pct_hay_pasture_b1, pct_small_grains_b2, surplus_kgha_norm_b2_y, surplus_kgha_norm_b1_x, pct_alfalfa_b1, pct_fallow_b0, pct_other_b1, pct_soybeans_b1, pct_small_grains_b1, fuel_moisture_1000h_b2, doy_sin2, evapotranspiration_b1, fuel_moisture_1000h_b1, doy_cos2, evapotranspiration_b0, fuel_moisture_1000h_b0, evapotranspiration_b2, energy_release_b0, max_rel_humidity_b1, burning_index_b2, burning_index_b0, energy_release_b2, energy_release_b1, max_rel_humidity_b2, max_rel_humidity_b0, precip_in_1d_b0, precip_in_1d_b2, precip_in_1d_b1, burning_index_b1  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | loso_prauc | lofo_prauc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 79 | 20 | 157318 | 65 | 0.8804 | 0.8439 | 0.7565 | 0.6694 |

| loso_f1 | lofo_f1 | loso_prauc_lift | loso_f2 | loso_mcc | loso_recall_at_far | lofo_prauc_lift | lofo_f2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.6842 | 0.6381 | 2.9510 | 0.7554 | 0.5758 | 0.6659 | 2.6113 | 0.7317 |

| lofo_mcc | lofo_recall_at_far | brier | persist_skill | base | between_rate_r2 | macro_auc |
| --- | --- | --- | --- | --- | --- | --- |
| 0.5033 | 0.5648 | 0.1142 | -3.1148 | 0.2564 | 0.5020 | 0.9067 |

---

# REG Model 7 

| | |
|-|-|
| **Timestamp:** | [2026-07-22T22:23:55Z] | 
|**Recipe:** | recipe_REG  |
|**# Features:** | 35|
| **List of Features:** | rest_of_state_nitrate_lag1, mean_dist_to_sensor, tile_frac_basin, max_dist_to_sensor, roll_n_avg_except_this7d, tot_tiles92, lat, tot_contact, log_basin_area, fuel_moisture_1000h_b1, rest_of_state_nitrate_lag2, lon, tot_bfi, Hay_Pasture_b1, Nonag_b0, tile_frac_ag, total_kg_N_b1, Alfalfa_b1, Hay_Pasture_b0, Other_b0, Alfalfa_b0, Corn_b0, doy_sin, Soybeans_b0, Corn_b1, Fallow_b0, Small_Grains_b1, Fallow_b1, Other_b1, Small_Grains_b0, Nonag_b1, total_kg_N_b0, doy_cos, fuel_moisture_1000h_b0, Soybeans_b1  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 79 | 20 | 157318 | 35 | 0.4255 | 0.3227 | 4.2134 | -6.7256 |

| spearman | between_r2 | within_r2 | macro_r2 |
| --- | --- | --- | --- |
| 0.6896 | 0.2859 | 0.4275 | 0.3391 |

---

# CLF Model 6 

| | |
|-|-|
| **Timestamp:** | [2026-07-22T21:47:27Z] | 
|**Recipe:** | recipe_CLF  |
|**# Features:** | 20|
| **List of Features:** | rest_of_state_nitrate_lag1, mean_dist_to_sensor, tile_frac_basin, max_dist_to_sensor, rest_of_state_nitrate_lag2, tot_tiles92, lon, tot_contact, lat, log_basin_area, tot_bfi, tile_frac_ag, doy_cos, doy_sin, total_kg_N_b2, total_kg_N_b0, fuel_moisture_1000h_b2, total_kg_N_b1, fuel_moisture_1000h_b1, fuel_moisture_1000h_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | loso_prauc | lofo_prauc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 79 | 20 | 157318 | 20 | 0.8685 | 0.8383 | 0.7229 | 0.6301 |

| loso_f1 | lofo_f1 | loso_prauc_lift | loso_f2 | loso_mcc | loso_recall_at_far | lofo_prauc_lift | lofo_f2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.6711 | 0.6354 | 2.8199 | 0.7418 | 0.5517 | 0.6354 | 2.4581 | 0.7277 |

| lofo_mcc | lofo_recall_at_far | brier | persist_skill | base | between_rate_r2 | macro_auc |
| --- | --- | --- | --- | --- | --- | --- |
| 0.4997 | 0.5545 | 0.1198 | -3.3186 | 0.2564 | 0.2995 | 0.9024 |

---

# REG Model 5 

| | |
|-|-|
| **Timestamp:** | [2026-07-21] | 
|**Recipe:** | recipe_REG  |
|**# Features:** | 58|
| **List of Features:** | rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, cat_tiles92, tile_frac_basin, mean_dist_to_sensor, max_dist_to_sensor, rest_of_state_nitrate_lag2, tot_tiles92, log_basin_area, tot_contact, cat_bfi, fuel_moisture_1000h_b1, lat, lon, Hay_Pasture_b1, tot_bfi, Nonag_b0, cat_contact, rest_of_state_nitrate_lag3, total_kg_N_b1, surplus_kgha_b1, tile_frac_ag, Hay_Pasture_b0, Alfalfa_b1, Other_b0, Alfalfa_b0, Soybeans_b0, Corn_b0, rest_of_state_nitrate_lag5, doy_sin, Corn_b1, surplus_kgha_b0, Fallow_b1, Fallow_b0, Small_Grains_b1, total_kg_N_b0, doy_cos, Other_b1, Small_Grains_b0, fuel_moisture_1000h_b0, Nonag_b1, Soybeans_b1, max_temp_b1, min_temp_b1, solar_rad_b1, min_temp_b0, vpd_b1, solar_rad_b0, evapotranspiration_b1, evapotranspiration_b0, max_temp_b0, max_rel_humidity_b1, min_rel_humidity_b0, precip_in_1d_b1, min_rel_humidity_b1, max_rel_humidity_b0, vpd_b0, precip_in_1d_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 79 | 20 | 157318 | 58 | 0.4248 | 0.3199 | 4.2158 | -6.7399 |

| spearman | between_r2 | within_r2 | macro_r2 |
| --- | --- | --- | --- |
| 0.6946 | 0.3442 | 0.4243 | 0.3044 |

---

# CLF Model 4 

| | |
|-|-|
| **Timestamp:** | [2026-07-21] | 
|**Recipe:** | recipe_CLF  |
|**# Features:** | 76|
| **List of Features:** | rest_of_state_nitrate_lag1, cat_tiles92, tile_frac_basin, rest_of_state_nitrate_lag2, mean_dist_to_sensor, tot_contact, lon, max_dist_to_sensor, tot_tiles92, log_basin_area, lat, Nonag_b1, rest_of_state_nitrate_lag3, Nonag_b2, Alfalfa_b2, cat_bfi, tot_bfi, Hay_Pasture_b2, cat_contact, Nonag_b0, Corn_b2, Soybeans_b2, Corn_b0, tile_frac_ag, Alfalfa_b0, rest_of_state_nitrate_lag5, Other_b0, Soybeans_b0, Hay_Pasture_b1, Small_Grains_b2, total_kg_N_b2, Corn_b1, Fallow_b0, surplus_kgha_b2, Small_Grains_b0, Other_b2, Hay_Pasture_b0, Soybeans_b1, surplus_kgha_b0, doy_sin, doy_cos, Alfalfa_b1, total_kg_N_b1, total_kg_N_b0, Fallow_b2, surplus_kgha_b1, Fallow_b1, fuel_moisture_1000h_b2, min_temp_b1, solar_rad_b1, Other_b1, evapotranspiration_b1, Small_Grains_b1, solar_rad_b2, min_temp_b2, min_temp_b0, solar_rad_b0, fuel_moisture_1000h_b1, evapotranspiration_b0, max_temp_b1, max_temp_b0, fuel_moisture_1000h_b0, evapotranspiration_b2, max_temp_b2, min_rel_humidity_b2, vpd_b0, min_rel_humidity_b0, max_rel_humidity_b2, vpd_b2, precip_in_1d_b0, max_rel_humidity_b1, min_rel_humidity_b1, vpd_b1, max_rel_humidity_b0, precip_in_1d_b2, precip_in_1d_b1  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | loso_prauc | lofo_prauc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 79 | 20 | 157318 | 76 | 0.8410 | 0.8094 | 0.6947 | 0.6157 |

| loso_f1 | lofo_f1 | loso_prauc_lift | loso_f2 | loso_mcc | loso_recall_at_far | lofo_prauc_lift | lofo_f2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.6430 | 0.5933 | 2.7099 | 0.7146 | 0.5244 | 0.6043 | 2.4016 | 0.6999 |

| lofo_mcc | lofo_recall_at_far | brier | persist_skill | base | between_rate_r2 | macro_auc |
| --- | --- | --- | --- | --- | --- | --- |
| 0.4382 | 0.4911 | 0.1302 | -3.6976 | 0.2564 | 0.2297 | 0.9015 |

---

# CLF Model 3 

| | |
|-|-|
| **Timestamp:** | [2026-07-04] | 
|**Recipe:** | recipe_CLF  |
|**# Features:** | 68|
| **List of Features:** | rest_of_state_nitrate_lag1, rest_of_state_nitrate_lag2, lat, mean_dist_to_sensor, max_dist_to_sensor, lon, rest_of_state_nitrate_lag3, Corn_b0, log_basin_area, Hay_Pasture_b2, Hay_Pasture_b0, Corn_b1, Corn_b2, rest_of_state_nitrate_lag5, Alfalfa_b0, Nonag_b1, Soybeans_b0, Nonag_b2, Nonag_b0, Small_Grains_b0, Alfalfa_b2, Soybeans_b2, surplus_kgha_b2, Soybeans_b1, surplus_kgha_b1, total_kg_N_b2, Other_b0, total_kg_N_b1, Hay_Pasture_b1, Alfalfa_b1, Small_Grains_b2, surplus_kgha_b0, doy_sin, doy_cos, total_kg_N_b0, Other_b2, Fallow_b1, Other_b1, fuel_moisture_1000h_b2, Fallow_b2, solar_rad_b1, Fallow_b0, Small_Grains_b1, min_temp_b1, evapotranspiration_b1, min_temp_b2, min_temp_b0, fuel_moisture_1000h_b1, solar_rad_b2, evapotranspiration_b0, solar_rad_b0, max_temp_b0, fuel_moisture_1000h_b0, max_temp_b1, evapotranspiration_b2, max_temp_b2, min_rel_humidity_b2, min_rel_humidity_b0, vpd_b0, max_rel_humidity_b1, vpd_b1, max_rel_humidity_b2, vpd_b2, max_rel_humidity_b0, precip_in_1d_b2, precip_in_1d_b0, min_rel_humidity_b1, precip_in_1d_b1  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | loso_prauc | lofo_prauc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 80 | 19 | 160074 | 68 | 0.8430 | 0.8197 | 0.6841 | 0.6341 |

| loso_f1 | lofo_f1 | brier | persist_skill | base | between_rate_r2 | macro_auc |
| --- | --- | --- | --- | --- | --- | --- |
| 0.6375 | 0.6113 | 0.1368 | -3.9490 | 0.2627 | 0.2375 | 0.9004 |

---

# REG Model 2 

| | |
|-|-|
| **Timestamp:** | [2026-07-03] | 
|**Recipe:** | recipe_REG  |
|**# Features:** | 50|
| **List of Features:** | rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, lat, mean_dist_to_sensor, log_basin_area, max_dist_to_sensor, lon, fuel_moisture_1000h_b1, rest_of_state_nitrate_lag2, Nonag_b0, Corn_b0, rest_of_state_nitrate_lag3, Alfalfa_b0, Hay_Pasture_b1, total_kg_N_b1, surplus_kgha_b1, Alfalfa_b1, Other_b0, Hay_Pasture_b0, doy_sin, Corn_b1, Soybeans_b1, Soybeans_b0, rest_of_state_nitrate_lag5, surplus_kgha_b0, Other_b1, fuel_moisture_1000h_b0, Nonag_b1, total_kg_N_b0, Small_Grains_b0, Fallow_b1, Fallow_b0, Small_Grains_b1, doy_cos, max_temp_b1, min_temp_b1, evapotranspiration_b0, min_temp_b0, solar_rad_b1, evapotranspiration_b1, solar_rad_b0, vpd_b1, max_temp_b0, max_rel_humidity_b1, min_rel_humidity_b1, vpd_b0, min_rel_humidity_b0, precip_in_1d_b1, max_rel_humidity_b0, precip_in_1d_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 80 | 19 | 160074 | 50 | 0.3800 | 0.3280 | 4.3575 | -7.3928 |

| spearman | between_r2 | within_r2 | macro_r2 |
| --- | --- | --- | --- |
| 0.6464 | 0.2094 | 0.4187 | 0.2445 |

---

# CLF Model 1 

| | |
|-|-|
| **Timestamp:** | [2026-07-02] | 
|**Recipe:** | recipe_CLF  |
|**# Features:** | 49|
| **List of Features:** | rest_of_state_nitrate_lag1, rest_of_state_nitrate_lag2, lat, mean_dist_to_sensor, max_dist_to_sensor, rest_of_state_nitrate_lag3, lon, Nonag_b0, Corn_b0, log_basin_area, Soybeans_b0, Hay_Pasture_b1, Corn_b1, Nonag_b1, Alfalfa_b1, rest_of_state_nitrate_lag5, Hay_Pasture_b0, surplus_kgha_b0, Soybeans_b1, Alfalfa_b0, Other_b0, total_kg_N_b1, Other_b1, surplus_kgha_b1, total_kg_N_b0, doy_sin, Fallow_b1, Fallow_b0, Small_Grains_b1, doy_cos, solar_rad_b0, evapotranspiration_b0, Small_Grains_b0, fuel_moisture_1000h_b1, min_temp_b0, fuel_moisture_1000h_b0, solar_rad_b1, min_temp_b1, max_temp_b0, max_temp_b1, evapotranspiration_b1, min_rel_humidity_b1, max_rel_humidity_b1, vpd_b1, vpd_b0, min_rel_humidity_b0, precip_in_1d_b1, max_rel_humidity_b0, precip_in_1d_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | loso_prauc | lofo_prauc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 80 | 19 | 160074 | 49 | 0.8358 | 0.8066 | 0.6759 | 0.6249 |

| loso_f1 | lofo_f1 | brier | persist_skill | base | between_rate_r2 | macro_auc |
| --- | --- | --- | --- | --- | --- | --- |
| 0.6279 | 0.5976 | 0.1385 | -4.0123 | 0.2627 | 0.2201 | 0.8938 |

---

# REG Model 0 

| | |
|-|-|
| **Timestamp:** | [2026-07-01] | 
|**Recipe:** | recipe_REG  |
|**# Features:** | 50|
| **List of Features:** | rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, lat, mean_dist_to_sensor, log_basin_area, max_dist_to_sensor, lon, fuel_moisture_1000h_b1, rest_of_state_nitrate_lag2, Nonag_b0, Corn_b0, rest_of_state_nitrate_lag3, Alfalfa_b0, Hay_Pasture_b1, total_kg_N_b1, surplus_kgha_b1, Alfalfa_b1, Other_b0, Hay_Pasture_b0, doy_sin, Corn_b1, Soybeans_b1, Soybeans_b0, rest_of_state_nitrate_lag5, surplus_kgha_b0, Other_b1, fuel_moisture_1000h_b0, Nonag_b1, total_kg_N_b0, Small_Grains_b0, Fallow_b1, Fallow_b0, Small_Grains_b1, doy_cos, max_temp_b1, min_temp_b1, evapotranspiration_b0, min_temp_b0, solar_rad_b1, evapotranspiration_b1, solar_rad_b0, vpd_b1, max_temp_b0, max_rel_humidity_b1, min_rel_humidity_b1, vpd_b0, min_rel_humidity_b0, precip_in_1d_b1, max_rel_humidity_b0, precip_in_1d_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 80 | 19 | 160074 | 50 | 0.3800 | 0.3280 | 4.3575 | -7.3928 |

| spearman | between_r2 | within_r2 | macro_r2 |
| --- | --- | --- | --- |
| 0.6464 | 0.2094 | 0.4187 | 0.2445 |

