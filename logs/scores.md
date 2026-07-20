# REG Model 0

Recipe: recipe_REG  
Features: rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, lat, mean_dist_to_sensor, log_basin_area, max_dist_to_sensor, lon, fuel_moisture_1000h_b1, rest_of_state_nitrate_lag2, Nonag_b0, Corn_b0, rest_of_state_nitrate_lag3, Alfalfa_b0, Hay_Pasture_b1, total_kg_N_b1, surplus_kgha_b1, Alfalfa_b1, Other_b0, Hay_Pasture_b0, doy_sin, Corn_b1, Soybeans_b1, Soybeans_b0, rest_of_state_nitrate_lag5, surplus_kgha_b0, Other_b1, fuel_moisture_1000h_b0, Nonag_b1, total_kg_N_b0, Small_Grains_b0, Fallow_b1, Fallow_b0, Small_Grains_b1, doy_cos, max_temp_b1, min_temp_b1, evapotranspiration_b0, min_temp_b0, solar_rad_b1, evapotranspiration_b1, solar_rad_b0, vpd_b1, max_temp_b0, max_rel_humidity_b1, min_rel_humidity_b1, vpd_b0, min_rel_humidity_b0, precip_in_1d_b1, max_rel_humidity_b0, precip_in_1d_b0  
Scores:

| n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill | spearman | between_r2 | within_r2 | macro_r2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 80 | 19 | 160074 | 50 | 0.3800 | 0.3280 | 4.3575 | -7.3928 | 0.6464 | 0.2094 | 0.4187 | 0.2445 |

---

# CLF Model 1

Recipe: recipe_CLF  
Features: rest_of_state_nitrate_lag1, rest_of_state_nitrate_lag2, lat, mean_dist_to_sensor, max_dist_to_sensor, rest_of_state_nitrate_lag3, lon, Nonag_b0, Corn_b0, log_basin_area, Soybeans_b0, Hay_Pasture_b1, Corn_b1, Nonag_b1, Alfalfa_b1, rest_of_state_nitrate_lag5, Hay_Pasture_b0, surplus_kgha_b0, Soybeans_b1, Alfalfa_b0, Other_b0, total_kg_N_b1, Other_b1, surplus_kgha_b1, total_kg_N_b0, doy_sin, Fallow_b1, Fallow_b0, Small_Grains_b1, doy_cos, solar_rad_b0, evapotranspiration_b0, Small_Grains_b0, fuel_moisture_1000h_b1, min_temp_b0, fuel_moisture_1000h_b0, solar_rad_b1, min_temp_b1, max_temp_b0, max_temp_b1, evapotranspiration_b1, min_rel_humidity_b1, max_rel_humidity_b1, vpd_b1, vpd_b0, min_rel_humidity_b0, precip_in_1d_b1, max_rel_humidity_b0, precip_in_1d_b0  
Scores:

| n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | loso_prauc | lofo_prauc | loso_f1 | lofo_f1 | brier | persist_skill | base | between_rate_r2 | macro_auc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 80 | 19 | 160074 | 49 | 0.8358 | 0.8066 | 0.6759 | 0.6249 | 0.6279 | 0.5976 | 0.1385 | -4.0123 | 0.2627 | 0.2201 | 0.8938 |

