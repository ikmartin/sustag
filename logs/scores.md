# CLF Model 27 

| | |
|-|-|
| **Timestamp:** | [2026-08-01T18:19:24Z] | 
|**Recipe:** | island_CLF  |
|**Name:** | treat_drop_island_CLF  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 116|
| **List of Features:** | surplus_kgha_sd_b2, rest_of_state_nitrate_lag1, surplus_kgha_mean_b2, rest_of_state_nitrate_lag3, roll_n_avg_except_this7d, pct_small_grains_mean_b2, surplus_kgha_mean_b1, pct_hay_pasture_sd_b1, pct_corn_mean_b1, pct_hay_pasture_mean_b2, roll_n_avg_except_this14d, tile_frac_basin, pct_hay_pasture_sd_b2, pct_alfalfa_sd_b2, pct_corn_mean_b0, pct_corn_mean_b2, tot_tiles92, surplus_kgha_mean_b0, pct_other_sd_b1, pct_hay_pasture_mean_b1, pct_alfalfa_mean_b2, pct_soybeans_mean_b1, pct_soybeans_sd_b0, pct_fallow_mean_b0, pct_fallow_sd_b0, tile_frac_ag, pct_fallow_sd_b1, lon, pct_nonag_b0, pct_corn_b0, pct_fallow_mean_b1, pct_nonag_sd_b1, pct_small_grains_mean_b0, pct_nonag_mean_b0, roll_n_avg_except_this60d, pct_nonag_sd_b0, pct_soybeans_mean_b0, pct_nonag_mean_b1, Alfalfa, tot_contact, pct_alfalfa_sd_b1, doy_sin, Nonag, surplus_kgha_sd_b0, lat, pct_alfalfa_mean_b0, pct_small_grains_sd_b2, pct_other_mean_b1, pct_small_grains_mean_b1, pct_other_sd_b0, pct_corn_sd_b2, pct_hay_pasture_sd_b0, surplus_kgha_sd_b1, Hay_Pasture, pct_fallow_mean_b2, pct_corn_b1, pct_other_b1, surplus_kgha_norm_b0, pct_small_grains_sd_b0, mean_dist_to_sensor, pct_hay_pasture_b2, pct_soybeans_b0, pct_other_mean_b0, log_basin_area, pct_corn_sd_b0, fuel_moisture_1000h_b2, Corn, pct_corn_sd_b1, pct_soybeans_mean_b2, surplus_kgha_norm_b2, pct_soybeans_b2, doy_sin2, Fallow, pct_soybeans_b1, pct_other_mean_b2, fuel_moisture_1000h_b1, doy_cos, pct_small_grains_sd_b1, surplus_kgha_expT_b0, pct_alfalfa_b2, pct_other_sd_b2, pct_fallow_b0, surplus_kgha_expT_b1, pct_nonag_sd_b2, surplus_kgha_norm_b1, pct_nonag_mean_b2, Small_Grains, pct_hay_pasture_mean_b0, pct_corn_b2, Soybeans, pct_fallow_b2, pct_other_b2, pct_hay_pasture_b0, fuel_moisture_1000h_b0, pct_fallow_b1, Other, pct_nonag_b2, pct_alfalfa_mean_b1, pct_soybeans_sd_b1, pct_alfalfa_b0, cat_contact, pct_nonag_b1, surplus_kgha_expT_b2, pct_small_grains_b0, pct_fallow_sd_b2, max_dist_to_sensor, tot_bfi, cat_bfi, pct_hay_pasture_b1, pct_small_grains_b1, pct_other_b0, pct_alfalfa_b1, pct_small_grains_b2, pct_alfalfa_sd_b0, pct_soybeans_sd_b2, doy_cos2  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 109 | 28 | 169407 | 116 | 0.9023 | 0.7017 | 0.8726 | 2.9678 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.9019 | 0.5509 | 0.1168 | 0.2364 | 0.4826 | 0.8865 |

### Decision thresholds:

Base rate 0.2364 (in the scored rows) — 'never alarm' is 76.4% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8847 | 0.1383 | 0.0888 | 0.9112 | 0.7931 | 0.0042 | 3.8537 |
| 0.5 | 0.5888 | 0.4602 | 0.2392 | 0.7608 | 0.8381 | 0.0448 | 3.2175 |
| 1.0 | 0.2691 | 0.6980 | 0.4093 | 0.5907 | 0.8142 | 0.1498 | 2.4980 |
| 1.5 | 0.1278 | 0.8468 | 0.5064 | 0.4936 | 0.7584 | 0.2690 | 2.0877 |
| 1.8 | 0.0937 | 0.8890 | 0.5393 | 0.4607 | 0.7277 | 0.3222 | 1.9486 |
| 2.0 | 0.0833 | 0.9019 | 0.5509 | 0.4491 | 0.7152 | 0.3425 | 1.8995 |
| 2.2 | 0.0833 | 0.9019 | 0.5509 | 0.4491 | 0.7152 | 0.3425 | 1.8995 |
| 2.5 | 0.0720 | 0.9140 | 0.5672 | 0.4328 | 0.6965 | 0.3709 | 1.8306 |
| 2.8 | 0.0565 | 0.9324 | 0.5939 | 0.4061 | 0.6615 | 0.4223 | 1.7173 |
| 3.0 | 0.0564 | 0.9326 | 0.5944 | 0.4056 | 0.6610 | 0.4232 | 1.7155 |
| 3.5 | 0.0326 | 0.9640 | 0.6475 | 0.3525 | 0.5727 | 0.5484 | 1.4906 |
| 4.0 | 0.0257 | 0.9741 | 0.6659 | 0.3341 | 0.5349 | 0.6011 | 1.4131 |
| 4.5 | 0.0196 | 0.9836 | 0.6845 | 0.3155 | 0.4915 | 0.6608 | 1.3344 |
| 5.0 | 0.0196 | 0.9836 | 0.6845 | 0.3155 | 0.4915 | 0.6608 | 1.3344 |

---

# CLF Model 26 

| | |
|-|-|
| **Timestamp:** | [2026-08-01T18:17:00Z] | 
|**Recipe:** | island_CLF  |
|**Name:** | treat_all_island_CLF  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 116|
| **List of Features:** | surplus_kgha_sd_b2, surplus_kgha_mean_b2, rest_of_state_nitrate_lag1, rest_of_state_nitrate_lag3, surplus_kgha_mean_b1, roll_n_avg_except_this7d, tile_frac_basin, pct_corn_mean_b1, pct_hay_pasture_sd_b1, pct_small_grains_mean_b2, surplus_kgha_mean_b0, pct_corn_mean_b2, roll_n_avg_except_this14d, tot_tiles92, pct_soybeans_sd_b0, pct_hay_pasture_mean_b2, pct_hay_pasture_sd_b2, pct_alfalfa_sd_b2, pct_hay_pasture_mean_b1, pct_fallow_mean_b0, pct_soybeans_mean_b1, pct_fallow_sd_b1, tile_frac_ag, pct_nonag_mean_b0, pct_corn_mean_b0, pct_nonag_mean_b1, pct_fallow_mean_b1, pct_other_sd_b1, pct_small_grains_mean_b0, pct_fallow_sd_b0, lon, pct_nonag_sd_b1, pct_hay_pasture_b2, pct_soybeans_mean_b0, pct_nonag_b0, log_basin_area, surplus_kgha_sd_b0, roll_n_avg_except_this60d, tot_contact, pct_nonag_mean_b2, doy_sin, pct_fallow_mean_b2, Nonag, pct_soybeans_mean_b2, pct_alfalfa_sd_b1, pct_alfalfa_mean_b2, pct_corn_sd_b1, pct_alfalfa_mean_b0, pct_nonag_sd_b0, pct_hay_pasture_sd_b0, surplus_kgha_sd_b1, pct_alfalfa_mean_b1, pct_nonag_b1, pct_other_mean_b1, pct_other_sd_b0, pct_corn_b1, pct_small_grains_sd_b0, mean_dist_to_sensor, pct_corn_b0, pct_corn_sd_b0, pct_small_grains_mean_b1, Alfalfa, pct_other_b1, pct_soybeans_b0, pct_small_grains_sd_b2, pct_soybeans_sd_b1, surplus_kgha_norm_b1, pct_other_mean_b0, surplus_kgha_norm_b0, max_dist_to_sensor, fuel_moisture_1000h_b2, lat, pct_nonag_sd_b2, Corn, pct_small_grains_sd_b1, doy_sin2, pct_corn_sd_b2, doy_cos, surplus_kgha_norm_b2, Small_Grains, fuel_moisture_1000h_b0, Fallow, pct_alfalfa_b2, pct_fallow_b0, pct_hay_pasture_b0, pct_other_b0, pct_nonag_b2, surplus_kgha_expT_b1, pct_alfalfa_sd_b0, pct_soybeans_b1, Hay_Pasture, surplus_kgha_expT_b0, Other, fuel_moisture_1000h_b1, pct_hay_pasture_mean_b0, pct_fallow_sd_b2, pct_other_sd_b2, pct_alfalfa_b0, pct_other_b2, pct_soybeans_b2, cat_contact, pct_hay_pasture_b1, pct_soybeans_sd_b2, surplus_kgha_expT_b2, pct_other_mean_b2, tot_bfi, pct_fallow_b2, pct_fallow_b1, pct_small_grains_b0, pct_small_grains_b2, pct_corn_b2, cat_bfi, Soybeans, pct_small_grains_b1, pct_alfalfa_b1, doy_cos2  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 116 | 0.8900 | 0.6874 | 0.8675 | 2.9568 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.8905 | 0.5589 | 0.1172 | 0.2325 | 0.4180 | 0.8794 |

### Decision thresholds:

Base rate 0.2325 (in the scored rows) — 'never alarm' is 76.8% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8793 | 0.1382 | 0.1210 | 0.8790 | 0.7952 | 0.0058 | 3.7811 |
| 0.5 | 0.5215 | 0.4862 | 0.2695 | 0.7305 | 0.8388 | 0.0543 | 3.1420 |
| 1.0 | 0.2532 | 0.6924 | 0.4075 | 0.5925 | 0.8178 | 0.1443 | 2.5484 |
| 1.5 | 0.1386 | 0.8175 | 0.4927 | 0.5073 | 0.7730 | 0.2405 | 2.1821 |
| 1.8 | 0.0953 | 0.8725 | 0.5408 | 0.4592 | 0.7315 | 0.3112 | 1.9753 |
| 2.0 | 0.0815 | 0.8905 | 0.5589 | 0.4411 | 0.7122 | 0.3418 | 1.8973 |
| 2.2 | 0.0727 | 0.9014 | 0.5711 | 0.4289 | 0.6981 | 0.3635 | 1.8450 |
| 2.5 | 0.0632 | 0.9137 | 0.5854 | 0.4146 | 0.6800 | 0.3908 | 1.7832 |
| 2.8 | 0.0578 | 0.9205 | 0.5945 | 0.4055 | 0.6678 | 0.4087 | 1.7444 |
| 3.0 | 0.0578 | 0.9205 | 0.5945 | 0.4055 | 0.6678 | 0.4087 | 1.7444 |
| 3.5 | 0.0316 | 0.9553 | 0.6559 | 0.3441 | 0.5662 | 0.5517 | 1.4799 |
| 4.0 | 0.0257 | 0.9655 | 0.6737 | 0.3263 | 0.5285 | 0.6038 | 1.4035 |
| 4.5 | 0.0179 | 0.9798 | 0.6998 | 0.3002 | 0.4643 | 0.6918 | 1.2913 |
| 5.0 | 0.0179 | 0.9798 | 0.6998 | 0.3002 | 0.4643 | 0.6918 | 1.2913 |

---

# REG Model 25 

| | |
|-|-|
| **Timestamp:** | [2026-08-01T18:09:12Z] | 
|**Recipe:** | island_REG  |
|**Name:** | treat_drop_island_REG  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 81|
| **List of Features:** | surplus_kgha_mean_b1, surplus_kgha_mean_b2, roll_n_avg_except_this7d, pct_corn_mean_b0, pct_corn_mean_b1, rest_of_state_nitrate_lag1, tot_tiles92, tile_frac_basin, pct_fallow_mean_b1, pct_nonag_mean_b1, pct_soybeans_mean_b2, rest_of_state_nitrate_lag3, pct_hay_pasture_mean_b1, pct_hay_pasture_mean_b2, pct_small_grains_mean_b1, tile_frac_ag, surplus_kgha_mean_b0, tot_contact, lon, pct_hay_pasture_mean_b0, pct_corn_mean_b2, pct_fallow_mean_b0, pct_soybeans_mean_b1, pct_nonag_mean_b0, roll_n_avg_except_this14d, pct_corn_b0, pct_fallow_mean_b2, pct_small_grains_mean_b2, tot_bfi, pct_alfalfa_mean_b0, pct_other_mean_b1, pct_small_grains_mean_b0, cat_contact, pct_other_mean_b2, pct_nonag_b1, pct_alfalfa_mean_b1, pct_soybeans_mean_b0, cat_bfi, pct_corn_b1, surplus_kgha_norm_b2, pct_alfalfa_mean_b2, pct_nonag_b0, pct_hay_pasture_b0, lat, fuel_moisture_1000h_b2, roll_n_avg_except_this60d, pct_nonag_mean_b2, pct_other_b0, pct_fallow_b0, pct_hay_pasture_b2, surplus_kgha_norm_b1, pct_other_mean_b0, pct_alfalfa_b2, pct_soybeans_b0, pct_soybeans_b1, surplus_kgha_expT_b1, pct_other_b1, surplus_kgha_expT_b2, pct_corn_b2, pct_small_grains_b2, surplus_kgha_expT_b0, surplus_kgha_norm_b0, pct_alfalfa_b0, pct_hay_pasture_b1, pct_fallow_b2, log_basin_area, doy_sin2, mean_dist_to_sensor, pct_alfalfa_b1, pct_small_grains_b0, pct_nonag_b2, pct_fallow_b1, pct_other_b2, doy_sin, pct_soybeans_b2, pct_small_grains_b1, max_dist_to_sensor, fuel_moisture_1000h_b1, fuel_moisture_1000h_b0, doy_cos, doy_cos2  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 109 | 28 | 169407 | 81 | 0.5638 | 0.4926 | 3.8091 | 0.5585 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.4447 | 0.1836 |

---

# REG Model 24 

| | |
|-|-|
| **Timestamp:** | [2026-08-01T18:07:23Z] | 
|**Recipe:** | island_REG  |
|**Name:** | treat_all_island_REG  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 81|
| **List of Features:** | surplus_kgha_mean_b1, pct_corn_mean_b0, roll_n_avg_except_this7d, surplus_kgha_mean_b2, pct_corn_mean_b1, tile_frac_basin, pct_soybeans_mean_b2, rest_of_state_nitrate_lag1, tot_tiles92, pct_fallow_mean_b1, pct_small_grains_mean_b2, tile_frac_ag, pct_hay_pasture_mean_b1, rest_of_state_nitrate_lag3, pct_fallow_mean_b0, surplus_kgha_mean_b0, pct_hay_pasture_mean_b2, lon, pct_nonag_mean_b1, tot_contact, pct_corn_mean_b2, pct_small_grains_mean_b1, pct_nonag_mean_b2, pct_soybeans_mean_b1, pct_small_grains_mean_b0, pct_fallow_mean_b2, pct_hay_pasture_mean_b0, tot_bfi, pct_other_mean_b2, roll_n_avg_except_this14d, pct_soybeans_mean_b0, pct_nonag_mean_b0, pct_small_grains_b2, pct_corn_b0, pct_hay_pasture_b2, pct_alfalfa_mean_b0, pct_alfalfa_mean_b2, cat_bfi, pct_corn_b1, fuel_moisture_1000h_b2, pct_other_mean_b1, pct_hay_pasture_b0, surplus_kgha_norm_b2, cat_contact, pct_nonag_b0, pct_alfalfa_b2, pct_other_b0, pct_soybeans_b2, roll_n_avg_except_this60d, surplus_kgha_norm_b1, pct_nonag_b1, log_basin_area, pct_alfalfa_b0, lat, surplus_kgha_expT_b2, pct_corn_b2, pct_alfalfa_mean_b1, pct_other_b1, pct_soybeans_b0, pct_soybeans_b1, pct_other_b2, pct_fallow_b0, pct_hay_pasture_b1, pct_other_mean_b0, surplus_kgha_expT_b0, surplus_kgha_expT_b1, pct_small_grains_b0, pct_fallow_b2, max_dist_to_sensor, doy_sin, pct_alfalfa_b1, mean_dist_to_sensor, doy_sin2, surplus_kgha_norm_b0, pct_nonag_b2, pct_small_grains_b1, pct_fallow_b1, fuel_moisture_1000h_b1, fuel_moisture_1000h_b0, doy_cos, doy_cos2  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 81 | 0.5042 | 0.4183 | 4.1434 | 0.3672 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.3967 | 0.1920 |

---

# CLF Model 23 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T05:18:20Z] | 
|**Recipe:** | recipe_CLF  |
|**Name:** | 0729_clf_no_exp_surplus  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 144|
| **List of Features:** | surplus_kgha_sd_b2, rest_of_state_nitrate_lag3, surplus_kgha_mean_b2, pct_hay_pasture_mean_b2, pct_corn_mean_b2, rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, pct_hay_pasture_sd_b2, roll_n_avg_except_this14d, pct_alfalfa_sd_b2, surplus_kgha_mean_b1, tile_frac_basin, surplus_kgha_mean_b0, pct_corn_mean_b0, surplus_kgha_sd_b1, pct_small_grains_mean_b2, pct_hay_pasture_sd_b0, pct_alfalfa_mean_b2, pct_corn_mean_b1, pct_soybeans_sd_b0, pct_nonag_b0, pct_fallow_mean_b1, pct_fallow_sd_b0, Nonag_b0, pct_nonag_mean_b2, tot_tiles92, tile_frac_ag, pct_small_grains_sd_b1, lon, pct_hay_pasture_sd_b1, pct_fallow_mean_b0, pct_nonag_mean_b0, pct_hay_pasture_mean_b1, surplus_kgha_sd_b0, pct_hay_pasture_b2, pct_corn_b0, max_dist_to_sensor, pct_small_grains_mean_b1, lat, pct_small_grains_mean_b0, pct_nonag_sd_b2, Hay_Pasture_b2, pct_nonag_sd_b1, pct_nonag_mean_b1, pct_soybeans_mean_b2, pct_soybeans_mean_b1, pct_fallow_sd_b1, pct_other_sd_b0, pct_small_grains_sd_b2, pct_other_mean_b0, pct_alfalfa_mean_b0, doy_sin, pct_other_mean_b1, pct_other_b1, pct_nonag_sd_b0, pct_alfalfa_mean_b1, pct_soybeans_mean_b0, mean_dist_to_sensor, tot_contact, pct_alfalfa_sd_b0, surplus_kgha_norm_b2, pct_corn_b1, pct_small_grains_sd_b0, Alfalfa_b1, fuel_moisture_1000h_b2, pct_small_grains_b2, pct_other_sd_b1, pct_fallow_sd_b2, pct_fallow_mean_b2, pct_alfalfa_sd_b1, doy_cos, evapotranspiration_b1, pct_soybeans_sd_b1, Fallow_b2, Other_b1, pct_nonag_b2, pct_hay_pasture_mean_b0, pct_alfalfa_b1, Fallow_b1, surplus_kgha_norm_b0, pct_nonag_b1, Corn_b1, pct_hay_pasture_b1, cat_bfi, Hay_Pasture_b0, pct_soybeans_b0, pct_corn_sd_b0, doy_sin2, pct_hay_pasture_b0, pct_fallow_b0, Soybeans_b0, fuel_moisture_1000h_b1, pct_small_grains_b1, log_basin_area, Nonag_b1, surplus_kgha_norm_b1, pct_soybeans_b2, pct_other_b2, pct_soybeans_b1, pct_corn_b2, Soybeans_b2, Corn_b0, pct_small_grains_b0, pct_fallow_b2, Hay_Pasture_b1, pct_other_b0, pct_alfalfa_b0, Small_Grains_b2, Alfalfa_b0, Soybeans_b1, pct_fallow_b1, Small_Grains_b0, pct_alfalfa_b2, Alfalfa_b2, pct_other_mean_b2, pct_soybeans_sd_b2, fuel_moisture_1000h_b0, evapotranspiration_b0, Fallow_b0, Small_Grains_b1, Corn_b2, Nonag_b2, roll_n_avg_except_this60d, tot_bfi, pct_other_sd_b2, pct_corn_sd_b1, Other_b0, cat_contact, Other_b2, evapotranspiration_b2, pct_corn_sd_b2, doy_cos2, precip_in_1d_b2, burning_index_b2, max_rel_humidity_b2, burning_index_b1, energy_release_b1, max_rel_humidity_b1, energy_release_b0, precip_in_1d_b0, precip_in_1d_b1, burning_index_b0, max_rel_humidity_b0, energy_release_b2  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 144 | 0.8886 | 0.6710 | 0.8667 | 2.8862 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.8619 | 0.5288 | 0.1195 | 0.2325 | 0.3671 | 0.8884 |

### Decision thresholds:

Base rate 0.2325 (in the scored rows) — 'never alarm' is 76.8% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8672 | 0.1345 | 0.1383 | 0.8617 | 0.7938 | 0.0065 | 3.7064 |
| 0.5 | 0.5440 | 0.4565 | 0.2796 | 0.7204 | 0.8325 | 0.0537 | 3.0986 |
| 1.0 | 0.1768 | 0.7696 | 0.4502 | 0.5498 | 0.7999 | 0.1909 | 2.3649 |
| 1.5 | 0.1414 | 0.8124 | 0.4757 | 0.5243 | 0.7850 | 0.2233 | 2.2552 |
| 1.8 | 0.1219 | 0.8321 | 0.4960 | 0.5040 | 0.7706 | 0.2481 | 2.1677 |
| 2.0 | 0.0944 | 0.8619 | 0.5288 | 0.4712 | 0.7431 | 0.2929 | 2.0269 |
| 2.2 | 0.0703 | 0.8943 | 0.5655 | 0.4345 | 0.7048 | 0.3526 | 1.8689 |
| 2.5 | 0.0598 | 0.9099 | 0.5844 | 0.4156 | 0.6816 | 0.3875 | 1.7878 |
| 2.8 | 0.0458 | 0.9326 | 0.6147 | 0.3853 | 0.6385 | 0.4507 | 1.6574 |
| 3.0 | 0.0456 | 0.9329 | 0.6150 | 0.3850 | 0.6379 | 0.4514 | 1.6560 |
| 3.5 | 0.0320 | 0.9571 | 0.6531 | 0.3469 | 0.5711 | 0.5458 | 1.4921 |
| 4.0 | 0.0260 | 0.9664 | 0.6708 | 0.3292 | 0.5344 | 0.5964 | 1.4161 |
| 4.5 | 0.0172 | 0.9830 | 0.7006 | 0.2994 | 0.4612 | 0.6969 | 1.2877 |
| 5.0 | 0.0147 | 0.9875 | 0.7102 | 0.2898 | 0.4345 | 0.7330 | 1.2466 |

---

# REG Model 22 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T05:17:08Z] | 
|**Recipe:** | recipe_REG  |
|**Name:** | 0729_reg_no_exp_surplus  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 117|
| **List of Features:** | surplus_kgha_mean_b1, pct_corn_mean_b1, pct_corn_mean_b2, rest_of_state_nitrate_lag1, pct_small_grains_mean_b2, pct_soybeans_mean_b2, tile_frac_ag, roll_n_avg_except_this7d, surplus_kgha_mean_b2, pct_corn_mean_b0, tile_frac_basin, pct_fallow_mean_b0, tot_contact, pct_hay_pasture_mean_b1, tot_tiles92, pct_hay_pasture_mean_b2, rest_of_state_nitrate_lag3, surplus_kgha_mean_b0, pct_small_grains_mean_b1, pct_fallow_mean_b2, pct_nonag_mean_b2, pct_fallow_mean_b1, roll_n_avg_except_this14d, pct_alfalfa_mean_b0, pct_alfalfa_mean_b2, pct_other_mean_b2, cat_bfi, pct_other_mean_b1, pct_hay_pasture_mean_b0, pct_nonag_mean_b1, lon, pct_hay_pasture_b2, pct_corn_b2, Small_Grains_b2, pct_small_grains_b2, pct_nonag_mean_b0, lat, pct_nonag_b0, cat_contact, pct_corn_b0, pct_soybeans_mean_b0, tot_bfi, pct_soybeans_mean_b1, fuel_moisture_1000h_b2, Other_b0, pct_corn_b1, Soybeans_b2, Corn_b1, mean_dist_to_sensor, pct_other_b0, surplus_kgha_norm_b2, Hay_Pasture_b1, max_dist_to_sensor, pct_other_b1, pct_hay_pasture_b0, pct_soybeans_b1, Nonag_b1, Corn_b2, Soybeans_b0, Corn_b0, pct_alfalfa_b0, pct_alfalfa_b1, Soybeans_b1, surplus_kgha_norm_b1, Fallow_b0, Hay_Pasture_b0, pct_alfalfa_mean_b1, pct_other_mean_b0, pct_small_grains_b0, pct_other_b2, pct_soybeans_b2, pct_soybeans_b0, Alfalfa_b2, roll_n_avg_except_this60d, Nonag_b2, doy_sin, pct_alfalfa_b2, fuel_moisture_1000h_b1, Alfalfa_b0, Alfalfa_b1, Hay_Pasture_b2, pct_fallow_b2, pct_small_grains_mean_b0, pct_hay_pasture_b1, Fallow_b2, Small_Grains_b1, pct_nonag_b2, Other_b1, pct_fallow_b1, pct_small_grains_b1, Nonag_b0, pct_fallow_b0, Small_Grains_b0, pct_nonag_b1, surplus_kgha_norm_b0, log_basin_area, Fallow_b1, doy_sin2, Other_b2, fuel_moisture_1000h_b0, doy_cos, evapotranspiration_b2, evapotranspiration_b0, evapotranspiration_b1, doy_cos2, energy_release_b0, burning_index_b2, energy_release_b2, precip_in_1d_b2, energy_release_b1, burning_index_b1, max_rel_humidity_b1, max_rel_humidity_b2, precip_in_1d_b0, precip_in_1d_b1, burning_index_b0, max_rel_humidity_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 117 | 0.5042 | 0.4483 | 4.0354 | 0.4389 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.4102 | 0.2189 |

---

# CLF Model 21 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T04:46:15Z] | 
|**Recipe:** | recipe_CLF  |
|**Name:** | 0729_clf_crop_exp_no_bucket  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 131|
| **List of Features:** | pct_corn_mean_b2, surplus_kgha_sd_b2, surplus_kgha_mean_b2, rest_of_state_nitrate_lag1, pct_hay_pasture_mean_b2, roll_n_avg_except_this7d, pct_hay_pasture_sd_b2, rest_of_state_nitrate_lag3, surplus_kgha_mean_b1, roll_n_avg_except_this14d, pct_small_grains_mean_b2, pct_corn_mean_b0, tile_frac_basin, pct_alfalfa_mean_b2, pct_nonag_mean_b0, pct_hay_pasture_mean_b1, pct_alfalfa_sd_b2, tot_tiles92, pct_fallow_mean_b1, surplus_kgha_sd_b1, tile_frac_ag, surplus_kgha_mean_b0, pct_hay_pasture_sd_b0, pct_corn_mean_b1, pct_nonag_mean_b2, pct_soybeans_sd_b0, pct_soybeans_mean_b0, pct_fallow_sd_b0, lon, pct_small_grains_sd_b1, pct_fallow_mean_b0, pct_corn_b0, max_dist_to_sensor, pct_small_grains_mean_b1, pct_nonag_sd_b1, pct_other_mean_b0, pct_nonag_b0, pct_soybeans_mean_b1, surplus_kgha_sd_b0, lat, pct_fallow_sd_b1, pct_corn_sd_b0, pct_other_sd_b0, pct_soybeans_mean_b2, tot_contact, pct_small_grains_mean_b0, pct_hay_pasture_b2, pct_alfalfa_mean_b0, pct_nonag_sd_b0, pct_nonag_mean_b1, pct_hay_pasture_mean_b0, Nonag, pct_other_mean_b1, doy_sin, mean_dist_to_sensor, pct_other_b1, surplus_kgha_norm_b2, pct_corn_sd_b2, pct_alfalfa_sd_b0, doy_cos, fuel_moisture_1000h_b2, pct_other_sd_b1, pct_small_grains_sd_b2, pct_corn_b1, surplus_kgha_expT_b0, pct_corn_b2, pct_alfalfa_mean_b1, Small_Grains, pct_fallow_mean_b2, pct_soybeans_sd_b1, pct_small_grains_b2, fuel_moisture_1000h_b1, pct_nonag_sd_b2, cat_bfi, pct_fallow_sd_b2, pct_hay_pasture_b1, doy_sin2, pct_small_grains_sd_b0, pct_alfalfa_b1, Alfalfa, pct_soybeans_sd_b2, pct_alfalfa_sd_b1, surplus_kgha_expT_b1, surplus_kgha_norm_b0, Fallow, pct_soybeans_b2, Corn, surplus_kgha_norm_b1, pct_hay_pasture_b0, pct_other_b2, pct_hay_pasture_sd_b1, pct_other_mean_b2, pct_small_grains_b1, pct_fallow_b0, pct_nonag_b1, Other, evapotranspiration_b0, pct_fallow_b2, pct_corn_sd_b1, pct_soybeans_b0, tot_bfi, pct_soybeans_b1, pct_alfalfa_b0, pct_fallow_b1, pct_nonag_b2, Soybeans, evapotranspiration_b1, pct_other_sd_b2, pct_alfalfa_b2, Hay_Pasture, surplus_kgha_expT_b2, log_basin_area, pct_small_grains_b0, cat_contact, pct_other_b0, fuel_moisture_1000h_b0, evapotranspiration_b2, roll_n_avg_except_this60d, doy_cos2, precip_in_1d_b2, burning_index_b2, burning_index_b1, max_rel_humidity_b2, burning_index_b0, energy_release_b1, energy_release_b2, energy_release_b0, precip_in_1d_b1, max_rel_humidity_b1, max_rel_humidity_b0, precip_in_1d_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 131 | 0.8896 | 0.6765 | 0.8691 | 2.9097 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.8760 | 0.5408 | 0.1177 | 0.2325 | 0.3810 | 0.8816 |

### Decision thresholds:

Base rate 0.2325 (in the scored rows) — 'never alarm' is 76.8% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8564 | 0.1441 | 0.1410 | 0.8590 | 0.7955 | 0.0072 | 3.6948 |
| 0.5 | 0.5246 | 0.4750 | 0.2767 | 0.7233 | 0.8357 | 0.0550 | 3.1114 |
| 1.0 | 0.2112 | 0.7377 | 0.4246 | 0.5754 | 0.8125 | 0.1649 | 2.4751 |
| 1.5 | 0.1410 | 0.8130 | 0.4791 | 0.5209 | 0.7827 | 0.2265 | 2.2404 |
| 1.8 | 0.1102 | 0.8480 | 0.5121 | 0.4879 | 0.7577 | 0.2696 | 2.0985 |
| 2.0 | 0.0880 | 0.8760 | 0.5408 | 0.4592 | 0.7313 | 0.3125 | 1.9751 |
| 2.2 | 0.0732 | 0.8951 | 0.5625 | 0.4375 | 0.7081 | 0.3486 | 1.8819 |
| 2.5 | 0.0575 | 0.9182 | 0.5910 | 0.4090 | 0.6725 | 0.4019 | 1.7593 |
| 2.8 | 0.0517 | 0.9279 | 0.6036 | 0.3964 | 0.6547 | 0.4280 | 1.7050 |
| 3.0 | 0.0483 | 0.9330 | 0.6112 | 0.3888 | 0.6435 | 0.4442 | 1.6726 |
| 3.5 | 0.0386 | 0.9469 | 0.6360 | 0.3640 | 0.6030 | 0.5012 | 1.5656 |
| 4.0 | 0.0287 | 0.9622 | 0.6639 | 0.3361 | 0.5494 | 0.5757 | 1.4458 |
| 4.5 | 0.0166 | 0.9827 | 0.7020 | 0.2980 | 0.4579 | 0.7010 | 1.2820 |
| 5.0 | 0.0158 | 0.9842 | 0.7049 | 0.2951 | 0.4497 | 0.7123 | 1.2692 |

---

# REG Model 20 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T04:45:17Z] | 
|**Recipe:** | recipe_REG  |
|**Name:** | 0729_reg_crop_exp_no_bucket  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 104|
| **List of Features:** | surplus_kgha_mean_b1, pct_corn_mean_b1, pct_corn_mean_b2, rest_of_state_nitrate_lag1, pct_small_grains_mean_b2, surplus_kgha_mean_b2, pct_hay_pasture_mean_b1, tile_frac_basin, pct_corn_mean_b0, roll_n_avg_except_this7d, tot_tiles92, pct_soybeans_mean_b2, rest_of_state_nitrate_lag3, roll_n_avg_except_this14d, tot_contact, pct_nonag_mean_b2, tile_frac_ag, pct_fallow_mean_b0, surplus_kgha_mean_b0, pct_hay_pasture_mean_b2, pct_alfalfa_mean_b2, pct_fallow_mean_b1, pct_fallow_mean_b2, pct_alfalfa_mean_b0, cat_bfi, lon, pct_small_grains_mean_b1, pct_other_mean_b2, pct_nonag_mean_b0, pct_hay_pasture_mean_b0, pct_small_grains_b2, lat, pct_alfalfa_mean_b1, pct_corn_b2, pct_soybeans_mean_b0, pct_soybeans_mean_b1, pct_hay_pasture_b2, pct_other_mean_b1, pct_corn_b1, mean_dist_to_sensor, fuel_moisture_1000h_b2, pct_nonag_b0, cat_contact, pct_other_b0, pct_nonag_mean_b1, pct_nonag_b1, pct_other_mean_b0, tot_bfi, surplus_kgha_expT_b2, pct_corn_b0, surplus_kgha_norm_b2, pct_alfalfa_b0, pct_hay_pasture_b0, max_dist_to_sensor, pct_soybeans_b2, pct_other_b1, pct_hay_pasture_b1, pct_fallow_b0, Soybeans, surplus_kgha_norm_b1, pct_soybeans_b0, Fallow, pct_small_grains_mean_b0, pct_nonag_b2, pct_alfalfa_b2, Hay_Pasture, surplus_kgha_expT_b1, pct_soybeans_b1, fuel_moisture_1000h_b1, Nonag, Other, Alfalfa, pct_fallow_b2, surplus_kgha_expT_b0, doy_sin, pct_small_grains_b1, pct_other_b2, pct_alfalfa_b1, roll_n_avg_except_this60d, Small_Grains, pct_small_grains_b0, Corn, surplus_kgha_norm_b0, log_basin_area, pct_fallow_b1, doy_sin2, doy_cos, fuel_moisture_1000h_b0, evapotranspiration_b2, evapotranspiration_b1, evapotranspiration_b0, energy_release_b2, energy_release_b0, doy_cos2, burning_index_b2, energy_release_b1, precip_in_1d_b2, max_rel_humidity_b1, burning_index_b0, burning_index_b1, precip_in_1d_b0, max_rel_humidity_b2, precip_in_1d_b1, max_rel_humidity_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 104 | 0.5201 | 0.4507 | 4.0263 | 0.4543 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.4035 | 0.2360 |

---

# CLF Model 19 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T00:57:29Z] | 
|**Recipe:** | recipe_CLF  |
|**Name:** | 0729_clf_no_norm_crops  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 123|
| **List of Features:** | surplus_kgha_sd_b2, surplus_kgha_mean_b2, rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, pct_corn_mean_b2, rest_of_state_nitrate_lag3, pct_hay_pasture_mean_b2, surplus_kgha_mean_b1, roll_n_avg_except_this14d, pct_corn_mean_b0, tile_frac_basin, pct_alfalfa_sd_b2, pct_alfalfa_mean_b2, pct_soybeans_sd_b0, pct_hay_pasture_mean_b1, pct_hay_pasture_sd_b2, pct_corn_mean_b1, pct_nonag_mean_b0, pct_small_grains_mean_b2, pct_hay_pasture_sd_b0, pct_fallow_mean_b1, pct_nonag_mean_b2, tot_tiles92, surplus_kgha_mean_b0, pct_small_grains_sd_b1, surplus_kgha_sd_b1, tile_frac_ag, pct_small_grains_mean_b1, pct_nonag_sd_b1, pct_fallow_sd_b0, pct_nonag_mean_b1, Hay_Pasture_b2, pct_fallow_mean_b0, pct_other_mean_b0, surplus_kgha_sd_b0, pct_other_sd_b0, pct_small_grains_mean_b0, pct_fallow_sd_b1, tot_contact, pct_nonag_sd_b0, pct_small_grains_sd_b0, Nonag_b0, lon, pct_alfalfa_mean_b0, pct_soybeans_mean_b2, pct_hay_pasture_sd_b1, pct_nonag_sd_b2, doy_sin, pct_soybeans_mean_b0, pct_soybeans_mean_b1, max_dist_to_sensor, pct_alfalfa_mean_b1, mean_dist_to_sensor, pct_other_mean_b2, lat, pct_hay_pasture_mean_b0, pct_small_grains_sd_b2, pct_other_sd_b1, fuel_moisture_1000h_b2, pct_corn_sd_b0, Nonag_b1, pct_alfalfa_sd_b1, pct_other_mean_b1, Other_b1, Alfalfa_b1, doy_cos, pct_fallow_sd_b2, surplus_kgha_expT_b0, pct_fallow_mean_b2, surplus_kgha_norm_b2, pct_soybeans_sd_b2, surplus_kgha_norm_b1, surplus_kgha_expT_b1, pct_alfalfa_sd_b0, Fallow_b2, pct_corn_sd_b1, evapotranspiration_b1, Alfalfa_b2, cat_bfi, fuel_moisture_1000h_b1, doy_sin2, Soybeans_b2, Corn_b1, Corn_b0, Fallow_b1, Soybeans_b0, pct_soybeans_sd_b1, surplus_kgha_norm_b0, Hay_Pasture_b0, Hay_Pasture_b1, tot_bfi, fuel_moisture_1000h_b0, Small_Grains_b2, Small_Grains_b0, Other_b0, evapotranspiration_b0, Nonag_b2, Alfalfa_b0, roll_n_avg_except_this60d, Soybeans_b1, Fallow_b0, Small_Grains_b1, Corn_b2, pct_corn_sd_b2, surplus_kgha_expT_b2, cat_contact, log_basin_area, pct_other_sd_b2, Other_b2, doy_cos2, evapotranspiration_b2, burning_index_b2, max_rel_humidity_b2, precip_in_1d_b2, burning_index_b1, burning_index_b0, energy_release_b1, precip_in_1d_b0, energy_release_b0, max_rel_humidity_b0, energy_release_b2, precip_in_1d_b1, max_rel_humidity_b1  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 123 | 0.8855 | 0.6652 | 0.8643 | 2.8611 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.8735 | 0.5439 | 0.1210 | 0.2325 | 0.3123 | 0.8900 |

### Decision thresholds:

Base rate 0.2325 (in the scored rows) — 'never alarm' is 76.8% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8416 | 0.1483 | 0.1618 | 0.8382 | 0.7953 | 0.0087 | 3.6056 |
| 0.5 | 0.4534 | 0.5084 | 0.3129 | 0.6871 | 0.8319 | 0.0701 | 2.9555 |
| 1.0 | 0.1918 | 0.7456 | 0.4406 | 0.5594 | 0.8044 | 0.1779 | 2.4064 |
| 1.5 | 0.1201 | 0.8288 | 0.4992 | 0.5008 | 0.7681 | 0.2503 | 2.1539 |
| 1.8 | 0.1087 | 0.8414 | 0.5110 | 0.4890 | 0.7587 | 0.2663 | 2.1035 |
| 2.0 | 0.0831 | 0.8735 | 0.5439 | 0.4561 | 0.7284 | 0.3156 | 1.9616 |
| 2.2 | 0.0681 | 0.8934 | 0.5662 | 0.4338 | 0.7041 | 0.3532 | 1.8660 |
| 2.5 | 0.0600 | 0.9051 | 0.5811 | 0.4189 | 0.6860 | 0.3803 | 1.8019 |
| 2.8 | 0.0572 | 0.9094 | 0.5870 | 0.4130 | 0.6784 | 0.3916 | 1.7764 |
| 3.0 | 0.0394 | 0.9386 | 0.6288 | 0.3712 | 0.6161 | 0.4816 | 1.5966 |
| 3.5 | 0.0295 | 0.9588 | 0.6574 | 0.3426 | 0.5626 | 0.5574 | 1.4735 |
| 4.0 | 0.0219 | 0.9748 | 0.6837 | 0.3163 | 0.5043 | 0.6382 | 1.3606 |
| 4.5 | 0.0219 | 0.9748 | 0.6838 | 0.3162 | 0.5041 | 0.6385 | 1.3603 |
| 5.0 | 0.0174 | 0.9823 | 0.7005 | 0.2995 | 0.4618 | 0.6958 | 1.2884 |

---

# REG Model 18 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T00:56:57Z] | 
|**Recipe:** | recipe_REG  |
|**Name:** | 0729_reg_no_norm_crops  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 96|
| **List of Features:** | surplus_kgha_mean_b1, pct_corn_mean_b1, pct_corn_mean_b2, rest_of_state_nitrate_lag1, pct_small_grains_mean_b2, pct_corn_mean_b0, surplus_kgha_mean_b2, roll_n_avg_except_this7d, tile_frac_basin, tot_tiles92, pct_hay_pasture_mean_b1, pct_soybeans_mean_b2, surplus_kgha_mean_b0, rest_of_state_nitrate_lag3, tile_frac_ag, pct_fallow_mean_b0, pct_nonag_mean_b2, pct_fallow_mean_b2, pct_alfalfa_mean_b0, tot_contact, pct_hay_pasture_mean_b2, cat_bfi, lon, roll_n_avg_except_this14d, pct_nonag_mean_b1, pct_fallow_mean_b1, pct_hay_pasture_mean_b0, lat, pct_nonag_mean_b0, pct_other_mean_b2, pct_small_grains_mean_b1, pct_soybeans_mean_b0, pct_alfalfa_mean_b2, pct_other_mean_b1, pct_small_grains_mean_b0, Small_Grains_b2, pct_soybeans_mean_b1, Corn_b1, Hay_Pasture_b1, max_dist_to_sensor, pct_other_mean_b0, Corn_b2, surplus_kgha_expT_b2, cat_contact, fuel_moisture_1000h_b2, surplus_kgha_norm_b2, mean_dist_to_sensor, tot_bfi, Other_b0, Corn_b0, Hay_Pasture_b2, doy_sin, Alfalfa_b1, Fallow_b0, Other_b1, Hay_Pasture_b0, fuel_moisture_1000h_b1, surplus_kgha_expT_b1, Soybeans_b2, surplus_kgha_norm_b1, Soybeans_b0, Alfalfa_b0, Nonag_b0, Soybeans_b1, pct_alfalfa_mean_b1, Nonag_b1, Alfalfa_b2, Fallow_b2, surplus_kgha_expT_b0, Nonag_b2, Small_Grains_b0, Fallow_b1, Small_Grains_b1, surplus_kgha_norm_b0, Other_b2, log_basin_area, fuel_moisture_1000h_b0, roll_n_avg_except_this60d, doy_sin2, evapotranspiration_b2, doy_cos, evapotranspiration_b0, evapotranspiration_b1, energy_release_b0, doy_cos2, burning_index_b2, precip_in_1d_b2, energy_release_b1, energy_release_b2, max_rel_humidity_b1, burning_index_b1, precip_in_1d_b0, max_rel_humidity_b2, max_rel_humidity_b0, burning_index_b0, precip_in_1d_b1  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 96 | 0.4811 | 0.4464 | 4.0423 | 0.4604 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.4203 | 0.2429 |

---

# CLF Model 17 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T00:36:35Z] | 
|**Recipe:** | recipe_CLF  |
|**Name:** | 0729_clf_no_exp_crops  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 123|
| **List of Features:** | surplus_kgha_sd_b2, surplus_kgha_mean_b2, roll_n_avg_except_this7d, rest_of_state_nitrate_lag1, rest_of_state_nitrate_lag3, pct_corn_mean_b2, pct_hay_pasture_mean_b2, surplus_kgha_mean_b1, roll_n_avg_except_this14d, tile_frac_basin, pct_alfalfa_mean_b2, pct_alfalfa_sd_b2, pct_corn_mean_b0, pct_hay_pasture_sd_b2, pct_small_grains_mean_b2, pct_fallow_mean_b1, pct_nonag_mean_b0, tot_tiles92, pct_hay_pasture_sd_b0, pct_soybeans_sd_b0, surplus_kgha_sd_b1, pct_small_grains_sd_b1, tile_frac_ag, pct_fallow_mean_b0, pct_nonag_mean_b2, surplus_kgha_mean_b0, pct_corn_mean_b1, pct_fallow_sd_b0, pct_small_grains_mean_b0, pct_nonag_b0, pct_hay_pasture_b2, pct_small_grains_mean_b1, pct_nonag_sd_b1, lon, pct_soybeans_mean_b1, pct_corn_b0, surplus_kgha_sd_b0, tot_contact, lat, pct_fallow_sd_b1, max_dist_to_sensor, pct_corn_sd_b0, pct_hay_pasture_sd_b1, pct_soybeans_mean_b2, pct_soybeans_mean_b0, pct_nonag_mean_b1, pct_other_mean_b0, pct_other_sd_b0, pct_nonag_sd_b0, pct_alfalfa_mean_b0, pct_other_mean_b1, doy_sin, pct_alfalfa_mean_b1, pct_other_b1, pct_other_mean_b2, surplus_kgha_norm_b2, pct_small_grains_b2, fuel_moisture_1000h_b2, pct_hay_pasture_mean_b0, pct_fallow_sd_b2, pct_fallow_mean_b2, log_basin_area, pct_other_sd_b1, surplus_kgha_norm_b1, pct_alfalfa_sd_b0, pct_alfalfa_sd_b1, fuel_moisture_1000h_b1, doy_cos, pct_hay_pasture_mean_b1, pct_nonag_sd_b2, pct_small_grains_sd_b0, pct_hay_pasture_b0, pct_alfalfa_b1, doy_sin2, mean_dist_to_sensor, cat_bfi, pct_corn_b1, surplus_kgha_expT_b0, pct_hay_pasture_b1, pct_soybeans_sd_b2, pct_small_grains_b1, surplus_kgha_expT_b1, pct_soybeans_b0, pct_small_grains_sd_b2, pct_corn_b2, evapotranspiration_b1, pct_fallow_b1, pct_other_b2, surplus_kgha_norm_b0, pct_fallow_b2, pct_soybeans_sd_b1, pct_soybeans_b2, pct_other_sd_b2, tot_bfi, pct_nonag_b2, evapotranspiration_b0, pct_corn_sd_b2, pct_fallow_b0, pct_nonag_b1, pct_alfalfa_b0, surplus_kgha_expT_b2, pct_alfalfa_b2, pct_corn_sd_b1, pct_other_b0, pct_small_grains_b0, pct_soybeans_b1, evapotranspiration_b2, roll_n_avg_except_this60d, fuel_moisture_1000h_b0, cat_contact, doy_cos2, precip_in_1d_b2, burning_index_b2, max_rel_humidity_b2, burning_index_b1, energy_release_b2, max_rel_humidity_b1, energy_release_b0, energy_release_b1, max_rel_humidity_b0, burning_index_b0, precip_in_1d_b1, precip_in_1d_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 123 | 0.8896 | 0.6709 | 0.8655 | 2.8859 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.8621 | 0.5353 | 0.1193 | 0.2325 | 0.3550 | 0.8802 |

### Decision thresholds:

Base rate 0.2325 (in the scored rows) — 'never alarm' is 76.8% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8417 | 0.1576 | 0.1486 | 0.8514 | 0.7978 | 0.0083 | 3.6622 |
| 0.5 | 0.5100 | 0.4820 | 0.2862 | 0.7138 | 0.8346 | 0.0585 | 3.0703 |
| 1.0 | 0.1843 | 0.7579 | 0.4469 | 0.5531 | 0.8013 | 0.1855 | 2.3789 |
| 1.5 | 0.1374 | 0.8113 | 0.4838 | 0.5162 | 0.7793 | 0.2303 | 2.2203 |
| 1.8 | 0.1060 | 0.8455 | 0.5181 | 0.4819 | 0.7527 | 0.2754 | 2.0728 |
| 2.0 | 0.0923 | 0.8621 | 0.5353 | 0.4647 | 0.7371 | 0.3008 | 1.9990 |
| 2.2 | 0.0670 | 0.8974 | 0.5723 | 0.4277 | 0.6969 | 0.3638 | 1.8396 |
| 2.5 | 0.0492 | 0.9275 | 0.6069 | 0.3931 | 0.6502 | 0.4337 | 1.6908 |
| 2.8 | 0.0492 | 0.9275 | 0.6069 | 0.3931 | 0.6502 | 0.4337 | 1.6908 |
| 3.0 | 0.0444 | 0.9358 | 0.6181 | 0.3819 | 0.6330 | 0.4587 | 1.6428 |
| 3.5 | 0.0388 | 0.9445 | 0.6325 | 0.3675 | 0.6091 | 0.4925 | 1.5806 |
| 4.0 | 0.0231 | 0.9693 | 0.6803 | 0.3197 | 0.5133 | 0.6248 | 1.3751 |
| 4.5 | 0.0229 | 0.9695 | 0.6807 | 0.3193 | 0.5124 | 0.6260 | 1.3735 |
| 5.0 | 0.0123 | 0.9901 | 0.7206 | 0.2794 | 0.4041 | 0.7734 | 1.2020 |

---

# REG Model 16 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T00:35:32Z] | 
|**Recipe:** | recipe_REG  |
|**Name:** | 0729_reg_no_exp_crops  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 96|
| **List of Features:** | surplus_kgha_mean_b1, pct_corn_mean_b1, pct_small_grains_mean_b2, rest_of_state_nitrate_lag1, pct_corn_mean_b2, surplus_kgha_mean_b2, pct_soybeans_mean_b2, roll_n_avg_except_this7d, tile_frac_basin, tot_tiles92, pct_hay_pasture_mean_b1, pct_corn_mean_b0, pct_hay_pasture_mean_b2, tile_frac_ag, surplus_kgha_mean_b0, rest_of_state_nitrate_lag3, pct_fallow_mean_b0, tot_contact, pct_fallow_mean_b2, pct_nonag_mean_b2, pct_fallow_mean_b1, roll_n_avg_except_this14d, pct_small_grains_mean_b0, pct_small_grains_mean_b1, pct_alfalfa_mean_b2, pct_nonag_mean_b1, lat, pct_hay_pasture_mean_b0, pct_small_grains_b2, pct_corn_b2, pct_alfalfa_mean_b1, pct_soybeans_mean_b0, pct_other_mean_b1, lon, pct_corn_b1, pct_hay_pasture_b2, pct_nonag_mean_b0, pct_nonag_b0, pct_soybeans_mean_b1, cat_bfi, fuel_moisture_1000h_b2, pct_alfalfa_mean_b0, pct_corn_b0, mean_dist_to_sensor, cat_contact, surplus_kgha_expT_b2, pct_hay_pasture_b1, pct_other_mean_b2, tot_bfi, pct_other_b1, pct_fallow_b0, pct_other_mean_b0, fuel_moisture_1000h_b1, pct_hay_pasture_b0, surplus_kgha_norm_b2, doy_sin, pct_other_b0, surplus_kgha_norm_b1, pct_soybeans_b0, pct_soybeans_b2, pct_alfalfa_b2, surplus_kgha_expT_b0, pct_small_grains_b0, pct_fallow_b2, pct_other_b2, pct_soybeans_b1, pct_alfalfa_b0, pct_alfalfa_b1, pct_small_grains_b1, surplus_kgha_expT_b1, max_dist_to_sensor, pct_nonag_b1, pct_nonag_b2, log_basin_area, pct_fallow_b1, surplus_kgha_norm_b0, roll_n_avg_except_this60d, doy_sin2, doy_cos, evapotranspiration_b0, fuel_moisture_1000h_b0, evapotranspiration_b2, evapotranspiration_b1, energy_release_b1, burning_index_b2, energy_release_b2, doy_cos2, max_rel_humidity_b1, burning_index_b1, energy_release_b0, precip_in_1d_b2, max_rel_humidity_b2, precip_in_1d_b0, max_rel_humidity_b0, burning_index_b0, precip_in_1d_b1  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 96 | 0.5243 | 0.4531 | 4.0178 | 0.4669 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.4101 | 0.2061 |

---

# CLF Model 15 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T00:05:19Z] | 
|**Recipe:** | recipe_CLF  |
|**Name:** | 0729_clf_no_weather_bucket_yes_other_bucket  |
|**Notes:** | weather is very slightly better bucketed than non-bucketed  |
|**True Lofo:** | F|
|**# Features:** | 135|
| **List of Features:** | surplus_kgha_sd_b2, surplus_kgha_mean_b2, roll_n_avg_except_this7d, rest_of_state_nitrate_lag1, pct_hay_pasture_mean_b2, pct_corn_mean_b2, rest_of_state_nitrate_lag3, roll_n_avg_except_this14d, pct_alfalfa_sd_b2, surplus_kgha_mean_b1, surplus_kgha_mean_b0, tile_frac_basin, pct_corn_mean_b0, surplus_kgha_sd_b1, pct_small_grains_mean_b2, pct_nonag_mean_b0, pct_hay_pasture_mean_b1, pct_soybeans_sd_b0, tile_frac_ag, pct_fallow_mean_b0, pct_alfalfa_mean_b2, pct_hay_pasture_sd_b0, pct_fallow_mean_b1, pct_corn_mean_b1, tot_tiles92, pct_hay_pasture_sd_b2, pct_fallow_sd_b0, pct_hay_pasture_b2, pct_nonag_mean_b2, pct_nonag_b0, pct_nonag_sd_b1, pct_small_grains_mean_b1, Nonag_b0, pct_corn_b0, lon, pct_small_grains_sd_b1, doy_sin, surplus_kgha_sd_b0, pct_small_grains_mean_b0, pct_small_grains_sd_b2, pct_soybeans_mean_b0, pct_soybeans_mean_b2, pct_nonag_mean_b1, pct_alfalfa_mean_b1, max_dist_to_sensor, lat, Hay_Pasture_b2, pct_alfalfa_mean_b0, surplus_kgha_norm_b2, pct_soybeans_mean_b1, pct_fallow_sd_b1, pct_small_grains_sd_b0, pct_hay_pasture_sd_b1, pct_other_sd_b0, tot_contact, pct_nonag_sd_b0, pct_other_b1, pct_nonag_sd_b2, pct_alfalfa_sd_b0, pct_other_mean_b1, Soybeans_b0, pct_other_mean_b0, pct_hay_pasture_mean_b0, energy_release, Nonag_b1, mean_dist_to_sensor, pct_small_grains_b2, pct_fallow_sd_b2, pct_fallow_mean_b2, Alfalfa_b1, doy_cos, surplus_kgha_norm_b1, fuel_moisture_1000h, pct_corn_sd_b0, pct_other_sd_b1, pct_nonag_b2, pct_hay_pasture_b0, Corn_b1, pct_corn_b2, tot_bfi, pct_other_mean_b2, pct_alfalfa_b1, pct_alfalfa_sd_b1, Fallow_b2, surplus_kgha_expT_b0, cat_bfi, Other_b1, surplus_kgha_norm_b0, doy_sin2, surplus_kgha_expT_b1, pct_corn_b1, Soybeans_b2, pct_hay_pasture_b1, pct_alfalfa_b0, Small_Grains_b2, pct_soybeans_sd_b1, pct_other_b2, log_basin_area, roll_n_avg_except_this60d, pct_small_grains_b1, Hay_Pasture_b0, Hay_Pasture_b1, pct_soybeans_b2, pct_fallow_b2, pct_nonag_b1, pct_fallow_b1, Corn_b0, pct_soybeans_sd_b2, Small_Grains_b0, pct_soybeans_b0, pct_soybeans_b1, Fallow_b1, evapotranspiration, Soybeans_b1, pct_fallow_b0, Nonag_b2, surplus_kgha_expT_b2, pct_alfalfa_b2, Alfalfa_b2, pct_other_b0, Fallow_b0, Alfalfa_b0, pct_small_grains_b0, Small_Grains_b1, Other_b2, pct_corn_sd_b1, pct_corn_sd_b2, Corn_b2, Other_b0, pct_other_sd_b2, cat_contact, doy_cos2, burning_index, precip_in_1d, max_rel_humidity  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 135 | 0.8898 | 0.6738 | 0.8677 | 2.8982 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.8727 | 0.5367 | 0.1189 | 0.2325 | 0.3682 | 0.8925 |

### Decision thresholds:

Base rate 0.2325 (in the scored rows) — 'never alarm' is 76.8% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8451 | 0.1553 | 0.1454 | 0.8546 | 0.7975 | 0.0080 | 3.6761 |
| 0.5 | 0.5194 | 0.4728 | 0.2841 | 0.7159 | 0.8338 | 0.0568 | 3.0792 |
| 1.0 | 0.1815 | 0.7711 | 0.4462 | 0.5538 | 0.8023 | 0.1882 | 2.3821 |
| 1.5 | 0.1491 | 0.8076 | 0.4712 | 0.5288 | 0.7880 | 0.2180 | 2.2747 |
| 1.8 | 0.1142 | 0.8446 | 0.5071 | 0.4929 | 0.7618 | 0.2632 | 2.1200 |
| 2.0 | 0.0901 | 0.8727 | 0.5367 | 0.4633 | 0.7354 | 0.3062 | 1.9930 |
| 2.2 | 0.0775 | 0.8886 | 0.5550 | 0.4450 | 0.7164 | 0.3357 | 1.9140 |
| 2.5 | 0.0609 | 0.9111 | 0.5841 | 0.4159 | 0.6819 | 0.3876 | 1.7890 |
| 2.8 | 0.0526 | 0.9239 | 0.6017 | 0.3983 | 0.6578 | 0.4228 | 1.7133 |
| 3.0 | 0.0476 | 0.9317 | 0.6132 | 0.3868 | 0.6408 | 0.4474 | 1.6639 |
| 3.5 | 0.0345 | 0.9550 | 0.6476 | 0.3524 | 0.5815 | 0.5317 | 1.5157 |
| 4.0 | 0.0275 | 0.9656 | 0.6676 | 0.3324 | 0.5412 | 0.5873 | 1.4300 |
| 4.5 | 0.0230 | 0.9728 | 0.6818 | 0.3182 | 0.5090 | 0.6315 | 1.3685 |
| 5.0 | 0.0151 | 0.9862 | 0.7107 | 0.2893 | 0.4337 | 0.7337 | 1.2446 |

---

# REG Model 14 

| | |
|-|-|
| **Timestamp:** | [2026-07-30T00:04:18Z] | 
|**Recipe:** | recipe_REG  |
|**Name:** | 0729_reg_no_weather_bucket_yes_other_bucket  |
|**Notes:** | weather is very slightly better bucketed than non-bucketed  |
|**True Lofo:** | F|
|**# Features:** | 108|
| **List of Features:** | surplus_kgha_mean_b1, pct_corn_mean_b1, pct_corn_mean_b2, rest_of_state_nitrate_lag1, pct_small_grains_mean_b2, surplus_kgha_mean_b2, roll_n_avg_except_this7d, tile_frac_basin, pct_soybeans_mean_b2, pct_corn_mean_b0, tile_frac_ag, pct_hay_pasture_mean_b2, pct_hay_pasture_mean_b1, tot_contact, pct_fallow_mean_b0, tot_tiles92, pct_fallow_mean_b2, rest_of_state_nitrate_lag3, surplus_kgha_mean_b0, pct_nonag_mean_b2, pct_small_grains_mean_b1, pct_fallow_mean_b1, pct_hay_pasture_b2, lon, lat, roll_n_avg_except_this14d, cat_bfi, pct_alfalfa_mean_b0, pct_hay_pasture_mean_b0, pct_small_grains_b2, pct_nonag_mean_b0, pct_corn_b2, pct_other_mean_b2, pct_alfalfa_mean_b2, Small_Grains_b2, pct_corn_b1, pct_other_mean_b1, pct_soybeans_mean_b0, pct_soybeans_mean_b1, pct_corn_b0, mean_dist_to_sensor, pct_other_mean_b0, pct_nonag_b0, cat_contact, tot_bfi, pct_nonag_mean_b1, pct_other_b1, surplus_kgha_norm_b2, Soybeans_b2, Hay_Pasture_b1, pct_other_b0, fuel_moisture_1000h, Soybeans_b0, Other_b0, surplus_kgha_expT_b2, Other_b1, pct_alfalfa_b2, Alfalfa_b0, pct_hay_pasture_b0, pct_alfalfa_b0, pct_nonag_b2, pct_soybeans_b2, Fallow_b2, max_dist_to_sensor, Corn_b0, pct_small_grains_b0, Corn_b1, pct_soybeans_b1, Small_Grains_b1, Fallow_b0, Hay_Pasture_b0, Hay_Pasture_b2, Alfalfa_b2, Nonag_b0, pct_fallow_b0, surplus_kgha_expT_b0, Nonag_b1, surplus_kgha_norm_b1, pct_soybeans_b0, Corn_b2, pct_alfalfa_mean_b1, pct_alfalfa_b1, pct_other_b2, log_basin_area, Soybeans_b1, surplus_kgha_expT_b1, pct_hay_pasture_b1, doy_sin, Nonag_b2, pct_fallow_b2, Alfalfa_b1, pct_small_grains_b1, surplus_kgha_norm_b0, pct_small_grains_mean_b0, pct_fallow_b1, Fallow_b1, pct_nonag_b1, Small_Grains_b0, Other_b2, roll_n_avg_except_this60d, doy_sin2, doy_cos, evapotranspiration, energy_release, doy_cos2, burning_index, max_rel_humidity, precip_in_1d  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 108 | 0.4922 | 0.4489 | 4.0331 | 0.4317 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.4075 | 0.1895 |

---

# CLF Model 13 

| | |
|-|-|
| **Timestamp:** | [2026-07-29T23:32:47Z] | 
|**Recipe:** | recipe_CLF  |
|**Name:** | 0729_clf_no_bucket  |
|**Notes:** | the last clf run but with no bucketing on the linearly aggregated features. Does worse, marginally.  |
|**True Lofo:** | F|
|**# Features:** | 63|
| **List of Features:** | pct_corn_mean, rest_of_state_nitrate_lag1, rest_of_state_nitrate_lag3, roll_n_avg_except_this7d, pct_small_grains_mean, roll_n_avg_except_this14d, surplus_kgha_mean, tile_frac_basin, surplus_kgha_sd, pct_nonag_mean, pct_small_grains_sd, mean_dist_to_sensor, pct_hay_pasture_sd, pct_hay_pasture_mean, pct_corn_sd, pct_fallow_mean, max_dist_to_sensor, lon, tile_frac_ag, Nonag, log_basin_area, pct_soybeans_sd, pct_alfalfa_sd, pct_fallow_sd, tot_tiles92, pct_other_mean, lat, pct_alfalfa_mean, pct_soybeans_mean, Hay_Pasture, pct_nonag_sd, Small_Grains, doy_sin, cat_bfi, pct_other, surplus_kgha_norm, pct_fallow, fuel_moisture_1000h, tot_contact, surplus_kgha_expT, doy_cos, Corn, Fallow, pct_nonag, tot_bfi, pct_other_sd, pct_corn, Alfalfa, pct_small_grains, pct_alfalfa, doy_sin2, Other, pct_soybeans, Soybeans, evapotranspiration, cat_contact, pct_hay_pasture, roll_n_avg_except_this60d, doy_cos2, energy_release, burning_index, precip_in_1d, max_rel_humidity  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 63 | 0.8866 | 0.6625 | 0.8597 | 2.8496 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.8907 | 0.5682 | 0.1223 | 0.2325 | 0.2898 | 0.8904 |

### Decision thresholds:

Base rate 0.2325 (in the scored rows) — 'never alarm' is 76.8% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8805 | 0.1526 | 0.1458 | 0.8542 | 0.7969 | 0.0079 | 3.6741 |
| 0.5 | 0.5956 | 0.4545 | 0.2767 | 0.7233 | 0.8328 | 0.0527 | 3.1113 |
| 1.0 | 0.2225 | 0.7257 | 0.4585 | 0.5415 | 0.7934 | 0.1861 | 2.3294 |
| 1.5 | 0.1242 | 0.8359 | 0.5237 | 0.4763 | 0.7482 | 0.2784 | 2.0488 |
| 1.8 | 0.0936 | 0.8745 | 0.5554 | 0.4446 | 0.7168 | 0.3309 | 1.9122 |
| 2.0 | 0.0825 | 0.8907 | 0.5682 | 0.4318 | 0.7021 | 0.3550 | 1.8574 |
| 2.2 | 0.0707 | 0.9081 | 0.5866 | 0.4134 | 0.6791 | 0.3903 | 1.7782 |
| 2.5 | 0.0611 | 0.9235 | 0.6035 | 0.3965 | 0.6554 | 0.4258 | 1.7055 |
| 2.8 | 0.0540 | 0.9349 | 0.6169 | 0.3831 | 0.6349 | 0.4560 | 1.6479 |
| 3.0 | 0.0483 | 0.9436 | 0.6286 | 0.3714 | 0.6155 | 0.4838 | 1.5974 |
| 3.5 | 0.0389 | 0.9569 | 0.6507 | 0.3493 | 0.5755 | 0.5400 | 1.5024 |
| 4.0 | 0.0284 | 0.9726 | 0.6788 | 0.3212 | 0.5157 | 0.6226 | 1.3815 |
| 4.5 | 0.0235 | 0.9795 | 0.6924 | 0.3076 | 0.4827 | 0.6678 | 1.3232 |
| 5.0 | 0.0233 | 0.9798 | 0.6930 | 0.3070 | 0.4812 | 0.6698 | 1.3207 |

---

# REG Model 12 

| | |
|-|-|
| **Timestamp:** | [2026-07-29T23:31:54Z] | 
|**Recipe:** | recipe_REG  |
|**Name:** | 0729_reg_no_bucket  |
|**Notes:** | the last reg run but with no bucketing. REG likes buckets, it is worse here.  |
|**True Lofo:** | F|
|**# Features:** | 54|
| **List of Features:** | pct_corn_mean, pct_small_grains_mean, rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, surplus_kgha_mean, pct_fallow_mean, rest_of_state_nitrate_lag3, pct_hay_pasture_mean, tile_frac_basin, mean_dist_to_sensor, pct_nonag_mean, tot_tiles92, log_basin_area, tile_frac_ag, max_dist_to_sensor, cat_bfi, tot_contact, pct_soybeans_mean, lat, roll_n_avg_except_this14d, pct_corn, lon, pct_other_mean, cat_contact, tot_bfi, pct_alfalfa_mean, pct_other, Soybeans, Nonag, Hay_Pasture, pct_small_grains, Fallow, pct_fallow, surplus_kgha_norm, fuel_moisture_1000h, pct_nonag, pct_hay_pasture, pct_alfalfa, surplus_kgha_expT, pct_soybeans, Other, Corn, Small_Grains, Alfalfa, doy_sin, roll_n_avg_except_this60d, doy_cos, doy_sin2, energy_release, evapotranspiration, doy_cos2, burning_index, max_rel_humidity, precip_in_1d  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 54 | 0.5038 | 0.4076 | 4.1815 | 0.3725 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.4033 | 0.2465 |

---

# REG Model 11 

| | |
|-|-|
| **Timestamp:** | [2026-07-29T22:52:03Z] | 
|**Recipe:** | recipe_REG  |
|**Name:** | recipe_REG2  |
|**Notes:** | a baseline recipe test for current feature engineering, trying to determine what features should be bucketed and whether including exp veresions of crop and surplus is a good idea.  |
|**True Lofo:** | F|
|**# Features:** | 120|
| **List of Features:** | surplus_kgha_mean_b1, pct_corn_mean_b1, rest_of_state_nitrate_lag1, pct_small_grains_mean_b2, surplus_kgha_mean_b2, pct_corn_mean_b2, pct_corn_mean_b0, tile_frac_basin, pct_soybeans_mean_b2, roll_n_avg_except_this7d, tile_frac_ag, pct_hay_pasture_mean_b1, tot_tiles92, pct_nonag_mean_b2, pct_fallow_mean_b0, rest_of_state_nitrate_lag3, pct_hay_pasture_mean_b2, pct_fallow_mean_b2, tot_contact, pct_alfalfa_mean_b2, lon, surplus_kgha_mean_b0, pct_hay_pasture_mean_b0, roll_n_avg_except_this14d, pct_small_grains_mean_b1, pct_alfalfa_mean_b0, cat_bfi, pct_hay_pasture_b2, pct_corn_b2, pct_fallow_mean_b1, pct_other_mean_b2, mean_dist_to_sensor, lat, pct_small_grains_b2, Small_Grains_b2, pct_nonag_mean_b0, pct_corn_b0, pct_soybeans_mean_b0, cat_contact, pct_nonag_b0, pct_other_mean_b0, Hay_Pasture_b1, tot_bfi, surplus_kgha_expT_b2, pct_alfalfa_mean_b1, pct_soybeans_mean_b1, pct_nonag_mean_b1, Corn_b1, Soybeans_b2, pct_other_b0, pct_corn_b1, fuel_moisture_1000h_b2, pct_hay_pasture_b0, Fallow_b0, Corn_b0, Other_b0, pct_other_b1, surplus_kgha_norm_b2, pct_small_grains_mean_b0, max_dist_to_sensor, Hay_Pasture_b0, pct_other_mean_b1, pct_soybeans_b1, pct_alfalfa_b0, Alfalfa_b1, pct_soybeans_b2, pct_hay_pasture_b1, pct_small_grains_b0, pct_nonag_b1, Alfalfa_b2, Soybeans_b0, surplus_kgha_norm_b1, Alfalfa_b0, surplus_kgha_expT_b0, pct_alfalfa_b1, pct_fallow_b0, pct_other_b2, Fallow_b2, Soybeans_b1, pct_soybeans_b0, fuel_moisture_1000h_b1, pct_fallow_b2, pct_alfalfa_b2, pct_nonag_b2, Nonag_b2, doy_sin, Small_Grains_b1, Nonag_b1, Other_b1, surplus_kgha_expT_b1, Nonag_b0, pct_small_grains_b1, surplus_kgha_norm_b0, pct_fallow_b1, Corn_b2, Hay_Pasture_b2, log_basin_area, Small_Grains_b0, Fallow_b1, Other_b2, doy_sin2, roll_n_avg_except_this60d, fuel_moisture_1000h_b0, doy_cos, evapotranspiration_b0, evapotranspiration_b2, evapotranspiration_b1, doy_cos2, energy_release_b2, energy_release_b0, burning_index_b2, energy_release_b1, burning_index_b1, precip_in_1d_b2, max_rel_humidity_b1, max_rel_humidity_b2, precip_in_1d_b0, burning_index_b0, precip_in_1d_b1, max_rel_humidity_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 120 | 0.4979 | 0.4522 | 4.0208 | 0.4383 |

| lofo_within_r2 | lofo_macro_r2 |
| --- | --- |
| 0.4107 | 0.1892 |

---

# CLF Model 10 

| | |
|-|-|
| **Timestamp:** | [2026-07-29T22:20:21Z] | 
|**Recipe:** | recipe_CLF  |
|**Name:** | recipe_CLF2  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 147|
| **List of Features:** | surplus_kgha_sd_b2, pct_hay_pasture_mean_b2, surplus_kgha_mean_b2, roll_n_avg_except_this7d, rest_of_state_nitrate_lag1, roll_n_avg_except_this14d, pct_corn_mean_b2, rest_of_state_nitrate_lag3, surplus_kgha_mean_b1, pct_small_grains_mean_b2, tile_frac_basin, pct_alfalfa_sd_b2, pct_corn_mean_b0, surplus_kgha_mean_b0, pct_nonag_mean_b0, surplus_kgha_sd_b1, pct_hay_pasture_sd_b2, pct_fallow_mean_b1, pct_hay_pasture_sd_b0, pct_hay_pasture_mean_b1, pct_corn_mean_b1, pct_nonag_mean_b2, tot_tiles92, tile_frac_ag, pct_soybeans_sd_b0, pct_alfalfa_mean_b2, pct_small_grains_mean_b1, pct_fallow_mean_b0, pct_corn_b0, max_dist_to_sensor, pct_soybeans_mean_b1, pct_small_grains_sd_b1, Nonag_b0, pct_soybeans_mean_b0, pct_fallow_sd_b0, surplus_kgha_sd_b0, pct_nonag_b0, Hay_Pasture_b2, pct_other_sd_b0, lon, pct_hay_pasture_b2, lat, pct_nonag_mean_b1, doy_sin, pct_fallow_sd_b1, pct_other_mean_b0, pct_nonag_sd_b1, pct_soybeans_mean_b2, pct_alfalfa_mean_b1, pct_small_grains_mean_b0, pct_nonag_sd_b0, pct_other_mean_b1, pct_other_b1, tot_contact, pct_alfalfa_mean_b0, pct_fallow_sd_b2, pct_hay_pasture_mean_b0, surplus_kgha_norm_b2, pct_soybeans_b1, Nonag_b1, pct_other_mean_b2, fuel_moisture_1000h_b2, pct_alfalfa_sd_b1, pct_small_grains_sd_b0, pct_corn_sd_b0, pct_small_grains_b2, Other_b1, Soybeans_b0, mean_dist_to_sensor, Alfalfa_b1, pct_small_grains_sd_b2, pct_soybeans_sd_b1, pct_nonag_b2, surplus_kgha_expT_b0, pct_alfalfa_sd_b0, surplus_kgha_norm_b1, pct_corn_sd_b2, doy_cos, pct_nonag_sd_b2, pct_hay_pasture_sd_b1, fuel_moisture_1000h_b1, Soybeans_b1, pct_alfalfa_b1, pct_other_sd_b1, surplus_kgha_expT_b1, Hay_Pasture_b1, Fallow_b2, pct_hay_pasture_b1, doy_sin2, pct_fallow_mean_b2, Hay_Pasture_b0, pct_other_b2, pct_small_grains_b1, surplus_kgha_norm_b0, pct_nonag_b1, pct_hay_pasture_b0, pct_soybeans_b0, pct_fallow_b1, pct_corn_b1, Corn_b0, pct_soybeans_b2, Corn_b1, pct_corn_b2, Alfalfa_b2, surplus_kgha_expT_b2, cat_bfi, pct_alfalfa_b0, evapotranspiration_b0, evapotranspiration_b1, pct_fallow_b2, Small_Grains_b2, pct_small_grains_b0, pct_soybeans_sd_b2, Alfalfa_b0, pct_corn_sd_b1, Small_Grains_b0, Fallow_b0, pct_other_b0, Fallow_b1, pct_fallow_b0, log_basin_area, fuel_moisture_1000h_b0, Soybeans_b2, tot_bfi, Other_b0, Nonag_b2, pct_alfalfa_b2, pct_other_sd_b2, roll_n_avg_except_this60d, Small_Grains_b1, Corn_b2, Other_b2, cat_contact, precip_in_1d_b2, evapotranspiration_b2, doy_cos2, burning_index_b2, max_rel_humidity_b2, burning_index_b0, burning_index_b1, energy_release_b1, max_rel_humidity_b1, precip_in_1d_b0, energy_release_b2, energy_release_b0, max_rel_humidity_b0, precip_in_1d_b1  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 116 | 30 | 175973 | 147 | 0.8870 | 0.6779 | 0.8686 | 2.9159 |

| lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
| --- | --- | --- | --- | --- | --- |
| 0.8580 | 0.5223 | 0.1183 | 0.2325 | 0.3737 | 0.8852 |

### Decision thresholds:

Base rate 0.2325 (in the scored rows) — 'never alarm' is 76.8% accurate. Lift is precision ÷ base rate at that point.

| beta | tau | recall | fdr | precision | accuracy | fpr | lift |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.8185 | 0.1947 | 0.1505 | 0.8495 | 0.8048 | 0.0104 | 3.6540 |
| 0.5 | 0.5170 | 0.4814 | 0.2863 | 0.7137 | 0.8345 | 0.0585 | 3.0701 |
| 1.0 | 0.1810 | 0.7744 | 0.4501 | 0.5499 | 0.8002 | 0.1920 | 2.3653 |
| 1.5 | 0.1433 | 0.8134 | 0.4768 | 0.5232 | 0.7843 | 0.2246 | 2.2504 |
| 1.8 | 0.1276 | 0.8284 | 0.4905 | 0.5095 | 0.7747 | 0.2416 | 2.1913 |
| 2.0 | 0.0998 | 0.8580 | 0.5223 | 0.4777 | 0.7489 | 0.2841 | 2.0548 |
| 2.2 | 0.0639 | 0.9072 | 0.5769 | 0.4231 | 0.6908 | 0.3747 | 1.8198 |
| 2.5 | 0.0596 | 0.9140 | 0.5850 | 0.4150 | 0.6805 | 0.3902 | 1.7851 |
| 2.8 | 0.0508 | 0.9274 | 0.6030 | 0.3970 | 0.6556 | 0.4267 | 1.7075 |
| 3.0 | 0.0420 | 0.9418 | 0.6240 | 0.3760 | 0.6232 | 0.4733 | 1.6175 |
| 3.5 | 0.0378 | 0.9489 | 0.6358 | 0.3642 | 0.6030 | 0.5017 | 1.5666 |
| 4.0 | 0.0287 | 0.9636 | 0.6622 | 0.3378 | 0.5523 | 0.5723 | 1.4528 |
| 4.5 | 0.0219 | 0.9752 | 0.6840 | 0.3160 | 0.5034 | 0.6395 | 1.3591 |
| 5.0 | 0.0166 | 0.9842 | 0.7028 | 0.2972 | 0.4553 | 0.7049 | 1.2785 |

---

# REG Model 9 

| | |
|-|-|
| **Timestamp:** | [2026-07-23T22:10:41Z] | 
|**Recipe:** | recipe_REG  |
|**Name:** | recipe_REG4  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 50|
| **List of Features:** | rest_of_state_nitrate_lag1, mean_dist_to_sensor, roll_n_avg_except_this7d, pct_hay_pasture_b1, pct_corn_b0, tile_frac_basin, max_dist_to_sensor, tot_tiles92, pct_nonag_b0, rest_of_state_nitrate_lag2, log_basin_area, lat, pct_corn_b1, fuel_moisture_1000h_b1, tot_bfi, tot_contact, lon, tile_frac_ag, cat_contact, surplus_kgha_norm_b1_x, surplus_kgha_norm_b1_y, pct_other_b0, pct_soybeans_b0, pct_alfalfa_b1, pct_soybeans_b1, pct_hay_pasture_b0, doy_sin, surplus_kgha_norm_b0_x, pct_fallow_b0, pct_small_grains_b1, fuel_moisture_1000h_b0, pct_fallow_b1, pct_small_grains_b0, pct_alfalfa_b0, doy_sin2, pct_other_b1, doy_cos, pct_nonag_b1, surplus_kgha_norm_b0_y, evapotranspiration_b1, evapotranspiration_b0, doy_cos2, max_rel_humidity_b1, energy_release_b1, precip_in_1d_b1, energy_release_b0, burning_index_b1, precip_in_1d_b0, max_rel_humidity_b0, burning_index_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 79 | 20 | 157318 | 50 | 0.4228 | 0.3221 | 4.2233 | -6.7717 |

| spearman | between_r2 | within_r2 | macro_r2 |
| --- | --- | --- | --- |
| 0.6949 | 0.1611 | 0.4421 | 0.3680 |

---

# CLF Model 8 

| | |
|-|-|
| **Timestamp:** | [2026-07-23T21:22:03Z] | 
|**Recipe:** | recipe_CLF  |
|**Name:** | recipe_CLF4  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
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
|**Name:** | recipe_REG3  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
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
|**Name:** | recipe_CLF3  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
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
|**Name:** | recipe_REG2  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
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
|**Name:** | recipe_CLF2  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
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
|**Name:** | recipe_CLF2  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
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
|**Name:** | recipe_REG2  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
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
|**Name:** | recipe_CLF2  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
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
|**Name:** | recipe_REG2  |
|**Notes:** | <no notes>  |
|**True Lofo:** | F|
|**# Features:** | 50|
| **List of Features:** | rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, lat, mean_dist_to_sensor, log_basin_area, max_dist_to_sensor, lon, fuel_moisture_1000h_b1, rest_of_state_nitrate_lag2, Nonag_b0, Corn_b0, rest_of_state_nitrate_lag3, Alfalfa_b0, Hay_Pasture_b1, total_kg_N_b1, surplus_kgha_b1, Alfalfa_b1, Other_b0, Hay_Pasture_b0, doy_sin, Corn_b1, Soybeans_b1, Soybeans_b0, rest_of_state_nitrate_lag5, surplus_kgha_b0, Other_b1, fuel_moisture_1000h_b0, Nonag_b1, total_kg_N_b0, Small_Grains_b0, Fallow_b1, Fallow_b0, Small_Grains_b1, doy_cos, max_temp_b1, min_temp_b1, evapotranspiration_b0, min_temp_b0, solar_rad_b1, evapotranspiration_b1, solar_rad_b0, vpd_b1, max_temp_b0, max_rel_humidity_b1, min_rel_humidity_b1, vpd_b0, min_rel_humidity_b0, precip_in_1d_b1, max_rel_humidity_b0, precip_in_1d_b0  | 

### Scores:
| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 80 | 19 | 160074 | 50 | 0.3800 | 0.3280 | 4.3575 | -7.3928 |

| spearman | between_r2 | within_r2 | macro_r2 |
| --- | --- | --- | --- |
| 0.6464 | 0.2094 | 0.4187 | 0.2445 |

