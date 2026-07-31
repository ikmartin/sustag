# Experiment log

_Latest run per experiment. Full append-only history: logs/experiments.jsonl_

### 26 [reg]
**Question.** Do the local-catchment cat_* COMID features (cat_tiles92/bfi/contact) help or hurt the shipped recipe's transfer? They show high gain but ~0 permutation importance -- likely site fingerprints.

**Winner.** `with_cat` — lofo_r2 = 0.3651
<sub>2026-07-22T06:04Z · full · 45 sites · git 8aa7fac</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill | spearman | between_r2 | within_r2 | macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| with_cat ⭐ | 45 | 13 | 120004 | 58 | 0.3585 | 0.3651 | 4.541 | -8.284 | 0.6527 | 0.0771 | 0.4504 | 0.3586 |
| no_cat | 45 | 13 | 120004 | 55 | 0.3854 | 0.3446 | 4.445 | -7.888 | 0.6593 | 0.1301 | 0.4663 | 0.3928 |

_Top features (winner):_ rest_of_state_nitrate_lag1, tile_frac_basin, max_dist_to_sensor, log_basin_area, mean_dist_to_sensor, cat_tiles92, roll_n_avg_except_this7d, tot_tiles92

### 26c [clf]
**Question.** Do the local-catchment cat_* COMID features (cat_tiles92/bfi/contact) help or hurt the shipped recipe's transfer? They show high gain but ~0 permutation importance -- likely site fingerprints.

**Winner.** `no_cat` — lofo_auc = 0.8223, +0.0022 vs with_cat
<sub>2026-07-22T07:08Z · full · 45 sites · git 8aa7fac</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | loso_prauc | lofo_prauc | loso_f1 | lofo_f1 | loso_prauc_lift | loso_f2 | loso_mcc | loso_recall_at_far | lofo_prauc_lift | lofo_f2 | lofo_mcc | lofo_recall_at_far | brier | persist_skill | base | between_rate_r2 | macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| no_cat ⭐ | 45 | 13 | 120004 | 73 | 0.8202 | 0.8223 | 0.6823 | 0.661 | 0.6231 | 0.6258 | 2.475 | 0.7115 | 0.4815 | 0.5486 | 2.397 | 0.7252 | 0.4689 | 0.5129 | 0.1494 | -4.449 | 0.2757 | 0.04394 | 0.8953 |
| with_cat | 45 | 13 | 120004 | 76 | 0.8229 | 0.8201 | 0.6837 | 0.6569 | 0.6337 | 0.6202 | 2.479 | 0.7113 | 0.4922 | 0.5584 | 2.382 | 0.7264 | 0.4623 | 0.5129 | 0.1462 | -4.332 | 0.2757 | 0.1154 | 0.9008 |

_Top features (winner):_ rest_of_state_nitrate_lag1, rest_of_state_nitrate_lag2, tile_frac_basin, mean_dist_to_sensor, tot_tiles92, log_basin_area, tot_contact, lon

### 24 [reg]
**Question.** Do the static basin attributes close the between-site gap: base vs +AgTile vs +COMID vs +both, on a small covariate set (so the static features have room to matter)?

**Winner.** `base+agtile` — lofo_r2 = 0.2944, +0.0048 vs base
<sub>2026-07-22T17:03Z · full · 45 sites · git 8aa7fac</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | rmse | persist_skill | spearman | between_r2 | within_r2 | macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base+agtile ⭐ | 45 | 13 | 120004 | 47 | 0.3065 | 0.2944 | 4.721 | -9.058 | 0.6137 | 0.1129 | 0.3651 | 0.296 |
| base | 45 | 13 | 120004 | 45 | 0.2727 | 0.2896 | 4.835 | -9.551 | 0.609 | -0.0006753 | 0.3653 | 0.1767 |
| base+comid | 45 | 13 | 120004 | 51 | 0.3034 | 0.2695 | 4.732 | -9.093 | 0.5993 | 0.0658 | 0.3748 | 0.1964 |
| base+both | 45 | 13 | 120004 | 53 | 0.3157 | 0.2597 | 4.69 | -8.915 | 0.6139 | 0.08633 | 0.3766 | 0.242 |

_Top features (winner):_ tile_frac_basin, log_basin_area, mean_dist_to_sensor, max_dist_to_sensor, lon, lat, Alfalfa_b0, doy_sin

### 24c [clf]
**Question.** Do the static basin attributes close the between-site gap: base vs +AgTile vs +COMID vs +both, on a small covariate set (so the static features have room to matter)?

**Winner.** `base+both` — lofo_auc = 0.8177, +0.0102 vs base
<sub>2026-07-22T17:59Z · full · 45 sites · git 8aa7fac</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | loso_prauc | lofo_prauc | loso_f1 | lofo_f1 | loso_prauc_lift | loso_f2 | loso_mcc | loso_recall_at_far | lofo_prauc_lift | lofo_f2 | lofo_mcc | lofo_recall_at_far | brier | persist_skill | base | between_rate_r2 | macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base+both ⭐ | 45 | 13 | 120004 | 53 | 0.8098 | 0.8177 | 0.6223 | 0.6014 | 0.6154 | 0.6264 | 2.257 | 0.7189 | 0.4489 | 0.4888 | 2.181 | 0.7306 | 0.4643 | 0.4863 | 0.1588 | -4.794 | 0.2757 | -0.08499 | 0.8911 |
| base+agtile | 45 | 13 | 120004 | 47 | 0.8209 | 0.8174 | 0.6296 | 0.5905 | 0.6239 | 0.6162 | 2.283 | 0.7301 | 0.4646 | 0.5025 | 2.142 | 0.7365 | 0.4513 | 0.4831 | 0.155 | -4.652 | 0.2757 | 0.04756 | 0.8842 |
| base+comid | 45 | 13 | 120004 | 51 | 0.8103 | 0.8163 | 0.6193 | 0.5991 | 0.6101 | 0.6222 | 2.246 | 0.7212 | 0.4417 | 0.4832 | 2.173 | 0.7285 | 0.4581 | 0.4806 | 0.1606 | -4.86 | 0.2757 | -0.07831 | 0.8839 |
| base | 45 | 13 | 120004 | 45 | 0.8146 | 0.8074 | 0.6019 | 0.5974 | 0.6193 | 0.6063 | 2.183 | 0.727 | 0.455 | 0.4768 | 2.167 | 0.7238 | 0.4352 | 0.4624 | 0.1607 | -4.86 | 0.2757 | -0.03212 | 0.8855 |

_Top features (winner):_ cat_tiles92, tot_tiles92, tile_frac_basin, lon, Hay_Pasture_b1, mean_dist_to_sensor, log_basin_area, tot_contact

### 30 [reg]
**Question.** Which feature geometry maximizes transfer skill under the shipping encodings (crops/surplus as per-bucket normalized AND whole-basin exp-decay): jointly, which distance-bucket edges and which exp-decay length lam?

**Winner.** `e8k_l20k` — lofo_r2 = 0.3452, +0.0386 vs e50k_l2k
<sub>2026-07-28T07:33Z · full · 123 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 | lofo_within_r2 | lofo_macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|
| e8k_l20k ⭐ | 122 | 30 | 180611 | 78 | 0.468 | 0.3452 | 4.395 | 0.1769 | 0.3438 | 0.0704 |
| e8k_l2k | 122 | 30 | 180611 | 78 | 0.4621 | 0.3399 | 4.413 | 0.223 | 0.3394 | 0.0583 |
| e8k_30k_l5k | 122 | 30 | 180611 | 104 | 0.4457 | 0.3397 | 4.414 | 0.2399 | 0.3216 | 0.07251 |
| e8k_30k_l100k | 122 | 30 | 180611 | 104 | 0.4473 | 0.3391 | 4.416 | 0.2354 | 0.3231 | 0.06158 |
| e8k_30k_l50k | 122 | 30 | 180611 | 104 | 0.4453 | 0.3387 | 4.417 | 0.211 | 0.3231 | 0.06887 |
| e5k_20k_l20k | 122 | 30 | 180611 | 104 | 0.4258 | 0.3384 | 4.418 | 0.219 | 0.3353 | 0.0596 |
| e8k_30k_l20k | 122 | 30 | 180611 | 104 | 0.4438 | 0.3378 | 4.42 | 0.2296 | 0.3219 | 0.07066 |
| e5k_20k_l100k | 122 | 30 | 180611 | 104 | 0.424 | 0.3373 | 4.422 | 0.1792 | 0.3429 | 0.1109 |
| e8k_30k_l2k | 122 | 30 | 180611 | 104 | 0.4451 | 0.3355 | 4.428 | 0.2427 | 0.3233 | 0.05814 |
| e8k_loff | 122 | 30 | 180611 | 78 | 0.4758 | 0.3334 | 4.435 | 0.1737 | 0.3348 | 0.06179 |
| e5k_20k_l5k | 122 | 30 | 180611 | 104 | 0.4187 | 0.333 | 4.436 | 0.1993 | 0.3285 | 0.06644 |
| e5k_20k_l2k | 122 | 30 | 180611 | 104 | 0.4285 | 0.3317 | 4.44 | 0.1651 | 0.3411 | 0.0816 |
| e8k_30k_loff | 122 | 30 | 180611 | 104 | 0.4464 | 0.3316 | 4.441 | 0.2104 | 0.3278 | 0.04327 |
| e8k_l5k | 122 | 30 | 180611 | 78 | 0.4463 | 0.3278 | 4.453 | 0.2046 | 0.3383 | 0.05044 |
| e8k_l50k | 122 | 30 | 180611 | 78 | 0.4669 | 0.3236 | 4.467 | 0.1077 | 0.341 | 0.08854 |
| e5k_20k_l50k | 122 | 30 | 180611 | 104 | 0.4367 | 0.3214 | 4.475 | 0.1411 | 0.3354 | 0.06482 |
| e5k_20k_loff | 122 | 30 | 180611 | 104 | 0.4359 | 0.3162 | 4.492 | 0.1527 | 0.3388 | 0.09378 |
| ewhole_l100k | 122 | 30 | 180611 | 52 | 0.4593 | 0.3162 | 4.492 | 0.2093 | 0.3298 | 0.04092 |
| ewhole_l5k | 122 | 30 | 180611 | 52 | 0.4278 | 0.3151 | 4.495 | 0.2003 | 0.3347 | 0.01448 |
| e50k_l5k | 122 | 30 | 180611 | 78 | 0.4336 | 0.314 | 4.499 | 0.207 | 0.3382 | 0.01409 |
| e8k_l100k | 122 | 30 | 180611 | 78 | 0.4749 | 0.3139 | 4.499 | 0.123 | 0.3361 | 0.1283 |
| e50k_l50k | 122 | 30 | 180611 | 78 | 0.4471 | 0.3133 | 4.501 | 0.1203 | 0.3428 | 0.01371 |
| ewhole_loff | 122 | 30 | 180611 | 52 | 0.4717 | 0.309 | 4.515 | 0.1947 | 0.3295 | 0.05297 |
| e50k_l2k | 122 | 30 | 180611 | 78 | 0.4375 | 0.3066 | 4.523 | 0.1516 | 0.3485 | 0.04419 |
| e20k_l5k | 122 | 30 | 180611 | 78 | 0.4231 | 0.3036 | 4.533 | 0.1511 | 0.3295 | 0.06585 |
| e50k_l20k | 122 | 30 | 180611 | 78 | 0.444 | 0.303 | 4.535 | 0.1024 | 0.3417 | -0.01549 |
| ewhole_l20k | 122 | 30 | 180611 | 52 | 0.4533 | 0.302 | 4.538 | 0.1333 | 0.3272 | 0.02087 |
| e20k_l100k | 122 | 30 | 180611 | 78 | 0.4357 | 0.3015 | 4.54 | 0.1308 | 0.3247 | 0.06315 |
| ewhole_l50k | 122 | 30 | 180611 | 52 | 0.4571 | 0.3013 | 4.541 | 0.1533 | 0.3293 | 0.03872 |
| ewhole_l2k | 122 | 30 | 180611 | 52 | 0.4219 | 0.3009 | 4.542 | 0.1629 | 0.336 | -0.009178 |
| e20k_l50k | 122 | 30 | 180611 | 78 | 0.4387 | 0.3006 | 4.543 | 0.12 | 0.3262 | 0.07186 |
| e50k_l100k | 122 | 30 | 180611 | 78 | 0.4544 | 0.2986 | 4.549 | 0.1168 | 0.3444 | 0.02579 |
| e20k_l20k | 122 | 30 | 180611 | 78 | 0.4424 | 0.2973 | 4.553 | 0.08429 | 0.3283 | 0.02981 |
| e50k_loff | 122 | 30 | 180611 | 78 | 0.4499 | 0.2972 | 4.554 | 0.1138 | 0.3424 | 0.0303 |
| e20k_l2k | 122 | 30 | 180611 | 78 | 0.4146 | 0.2941 | 4.564 | 0.08481 | 0.3384 | 0.04328 |
| e20k_loff | 122 | 30 | 180611 | 78 | 0.4441 | 0.2859 | 4.59 | 0.09823 | 0.3273 | 0.04925 |

_Top features (winner):_ tile_frac_basin, cat_tiles92, pct_corn_e2_b1, tot_tiles92, mean_dist_to_sensor, lat, doy_sin, pct_hay_pasture_e2_b1

### 30c [clf]
**Question.** Which feature geometry maximizes transfer skill under the shipping encodings (crops/surplus as per-bucket normalized AND whole-basin exp-decay): jointly, which distance-bucket edges and which exp-decay length lam?

**Winner.** `e8k_l20k` — lofo_auc = 0.8487, +0.0065 vs e4k_8k_l2k
<sub>2026-07-28T10:25Z · full · 123 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | lofo_prauc_lift | lofo_recall_at_beta | lofo_fdr_at_beta | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| e8k_l20k ⭐ | 122 | 30 | 180611 | 78 | 0.8885 | 0.8487 | 2.74 | 0.8821 | 0.5851 | 0.1299 | 0.2328 | 0.291 | 0.8754 |
| e8k_l100k | 122 | 30 | 180611 | 78 | 0.8902 | 0.8468 | 2.728 | 0.8653 | 0.5769 | 0.1305 | 0.2328 | 0.2832 | 0.874 |
| e4k_8k_l20k | 122 | 30 | 180611 | 104 | 0.8858 | 0.8465 | 2.713 | 0.8801 | 0.5854 | 0.1309 | 0.2328 | 0.2664 | 0.8757 |
| e8k_l2k | 122 | 30 | 180611 | 78 | 0.8857 | 0.8464 | 2.736 | 0.8893 | 0.5966 | 0.1299 | 0.2328 | 0.2823 | 0.8729 |
| e5k_20k_l100k | 122 | 30 | 180611 | 104 | 0.8861 | 0.8457 | 2.673 | 0.887 | 0.591 | 0.1315 | 0.2328 | 0.2206 | 0.8718 |
| e5k_20k_loff | 122 | 30 | 180611 | 104 | 0.8878 | 0.8454 | 2.675 | 0.8848 | 0.5903 | 0.1311 | 0.2328 | 0.2231 | 0.8683 |
| e5k_20k_l50k | 122 | 30 | 180611 | 104 | 0.8855 | 0.8454 | 2.679 | 0.8854 | 0.5895 | 0.1314 | 0.2328 | 0.2301 | 0.8669 |
| e8k_l50k | 122 | 30 | 180611 | 78 | 0.8903 | 0.8452 | 2.705 | 0.8667 | 0.5797 | 0.1313 | 0.2328 | 0.2361 | 0.8709 |
| e4k_8k_loff | 122 | 30 | 180611 | 104 | 0.8874 | 0.8445 | 2.692 | 0.8737 | 0.5876 | 0.1324 | 0.2328 | 0.1984 | 0.8791 |
| e5k_20k_l2k | 122 | 30 | 180611 | 104 | 0.8809 | 0.8441 | 2.711 | 0.8762 | 0.5888 | 0.1313 | 0.2328 | 0.2392 | 0.8698 |
| e8k_l5k | 122 | 30 | 180611 | 78 | 0.886 | 0.8439 | 2.724 | 0.8857 | 0.5989 | 0.1313 | 0.2328 | 0.2655 | 0.8773 |
| e5k_20k_l5k | 122 | 30 | 180611 | 104 | 0.8813 | 0.8438 | 2.735 | 0.8764 | 0.5871 | 0.1308 | 0.2328 | 0.2475 | 0.8717 |
| e8k_loff | 122 | 30 | 180611 | 78 | 0.8907 | 0.8435 | 2.721 | 0.881 | 0.5982 | 0.1308 | 0.2328 | 0.2677 | 0.8738 |
| e5k_20k_l20k | 122 | 30 | 180611 | 104 | 0.8829 | 0.8434 | 2.679 | 0.8843 | 0.5924 | 0.132 | 0.2328 | 0.2215 | 0.8709 |
| e4k_8k_l50k | 122 | 30 | 180611 | 104 | 0.8864 | 0.843 | 2.653 | 0.8662 | 0.5796 | 0.1328 | 0.2328 | 0.2164 | 0.8823 |
| e4k_8k_l2k | 122 | 30 | 180611 | 104 | 0.8826 | 0.8422 | 2.684 | 0.8866 | 0.5983 | 0.133 | 0.2328 | 0.2286 | 0.8801 |
| e4k_8k_l5k | 122 | 30 | 180611 | 104 | 0.8821 | 0.8419 | 2.701 | 0.8751 | 0.5952 | 0.1328 | 0.2328 | 0.2429 | 0.8818 |
| e4k_8k_l100k | 122 | 30 | 180611 | 104 | 0.888 | 0.8399 | 2.665 | 0.8846 | 0.6047 | 0.1341 | 0.2328 | 0.2197 | 0.8773 |
| e4k_8k_20k_l100k | 122 | 30 | 180611 | 130 | 0.8765 | 0.8396 | 2.648 | 0.9012 | 0.6071 | 0.1341 | 0.2328 | 0.169 | 0.8676 |
| e4k_l50k | 122 | 30 | 180611 | 78 | 0.8824 | 0.838 | 2.582 | 0.8929 | 0.6018 | 0.1357 | 0.2328 | 0.136 | 0.877 |
| e4k_8k_20k_loff | 122 | 30 | 180611 | 130 | 0.8793 | 0.8376 | 2.65 | 0.9006 | 0.6112 | 0.1344 | 0.2328 | 0.1852 | 0.8661 |
| e4k_8k_20k_l2k | 122 | 30 | 180611 | 130 | 0.8722 | 0.837 | 2.656 | 0.9062 | 0.6154 | 0.1343 | 0.2328 | 0.2051 | 0.869 |
| e4k_8k_20k_l20k | 122 | 30 | 180611 | 130 | 0.8763 | 0.8365 | 2.651 | 0.8947 | 0.6084 | 0.135 | 0.2328 | 0.209 | 0.8657 |
| e4k_8k_20k_l5k | 122 | 30 | 180611 | 130 | 0.8697 | 0.8354 | 2.667 | 0.8662 | 0.5948 | 0.1341 | 0.2328 | 0.2226 | 0.865 |
| e4k_l2k | 122 | 30 | 180611 | 78 | 0.8768 | 0.8335 | 2.611 | 0.8816 | 0.6018 | 0.1358 | 0.2328 | 0.1858 | 0.8757 |
| e4k_l100k | 122 | 30 | 180611 | 78 | 0.8802 | 0.8332 | 2.56 | 0.8943 | 0.61 | 0.1377 | 0.2328 | 0.1106 | 0.8782 |
| e4k_loff | 122 | 30 | 180611 | 78 | 0.882 | 0.8329 | 2.563 | 0.8852 | 0.6037 | 0.1382 | 0.2328 | 0.08448 | 0.8813 |
| e4k_l5k | 122 | 30 | 180611 | 78 | 0.8752 | 0.8329 | 2.635 | 0.9021 | 0.6213 | 0.1351 | 0.2328 | 0.197 | 0.8788 |
| e4k_l20k | 122 | 30 | 180611 | 78 | 0.8794 | 0.8328 | 2.577 | 0.8661 | 0.5874 | 0.1369 | 0.2328 | 0.1489 | 0.8821 |
| e4k_8k_20k_l50k | 122 | 30 | 180611 | 130 | 0.8768 | 0.8324 | 2.596 | 0.8845 | 0.6055 | 0.1371 | 0.2328 | 0.1411 | 0.8667 |
| ewhole_l2k | 122 | 30 | 180611 | 52 | 0.8733 | 0.8294 | 2.64 | 0.8979 | 0.6238 | 0.1359 | 0.2328 | 0.2231 | 0.8711 |
| ewhole_l5k | 122 | 30 | 180611 | 52 | 0.8739 | 0.8278 | 2.633 | 0.8598 | 0.5985 | 0.137 | 0.2328 | 0.1947 | 0.8706 |
| ewhole_l100k | 122 | 30 | 180611 | 52 | 0.8779 | 0.8267 | 2.537 | 0.8936 | 0.6186 | 0.1413 | 0.2328 | 0.1013 | 0.8741 |
| ewhole_l50k | 122 | 30 | 180611 | 52 | 0.8806 | 0.8253 | 2.527 | 0.875 | 0.6081 | 0.1415 | 0.2328 | 0.09184 | 0.8799 |
| ewhole_loff | 122 | 30 | 180611 | 52 | 0.8819 | 0.8215 | 2.527 | 0.8837 | 0.6213 | 0.1422 | 0.2328 | 0.06995 | 0.8746 |
| ewhole_l20k | 122 | 30 | 180611 | 52 | 0.8749 | 0.819 | 2.509 | 0.877 | 0.616 | 0.1436 | 0.2328 | 0.07879 | 0.8742 |

_Top features (winner):_ pct_corn_e3_b1, tile_frac_basin, cat_tiles92, doy_sin, tot_tiles92, lon, pct_nonag_e3_b0, mean_dist_to_sensor

### 31 [reg]
**Question.** Is weather more useful bucketed by distance or aggregated over the whole basin -- and is any difference due to spatial resolution or to the per-bucket travel-time lag?

**Winner.** `whole_lag` — lofo_r2 = 0.3122, +0.0116 vs bucket_lag
<sub>2026-07-28T10:43Z · full · 123 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 | lofo_within_r2 | lofo_macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|
| whole_lag ⭐ | 122 | 30 | 180611 | 61 | 0.4451 | 0.3122 | 4.505 | 0.1224 | 0.3457 | -0.03181 |
| bucket_nolag | 122 | 30 | 180611 | 78 | 0.4508 | 0.3108 | 4.509 | 0.1545 | 0.3396 | 0.04244 |
| whole_nolag | 122 | 30 | 180611 | 61 | 0.4437 | 0.309 | 4.515 | 0.1193 | 0.349 | 0.01648 |
| bucket_lag | 122 | 30 | 180611 | 78 | 0.4478 | 0.3006 | 4.543 | 0.1208 | 0.338 | 0.004561 |

_Top features (winner):_ tile_frac_basin, cat_tiles92, pct_corn_fixed_b0, mean_dist_to_sensor, tot_tiles92, pct_corn_fixed_b1, doy_sin, tot_contact

### 31c [clf]
**Question.** Is weather more useful bucketed by distance or aggregated over the whole basin -- and is any difference due to spatial resolution or to the per-bucket travel-time lag?

**Winner.** `bucket_nolag` — lofo_auc = 0.8441, +0.0005 vs bucket_lag
<sub>2026-07-28T11:03Z · full · 123 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_auc | lofo_prauc_lift | lofo_recall_at_beta | lofo_fdr_at_beta | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bucket_nolag ⭐ | 122 | 30 | 180611 | 104 | 0.8827 | 0.8441 | 2.682 | 0.8935 | 0.5971 | 0.133 | 0.2328 | 0.2175 | 0.8798 |
| whole_lag | 122 | 30 | 180611 | 70 | 0.8844 | 0.8441 | 2.688 | 0.897 | 0.6016 | 0.1321 | 0.2328 | 0.2171 | 0.8783 |
| bucket_lag | 122 | 30 | 180611 | 104 | 0.8831 | 0.8436 | 2.7 | 0.8853 | 0.5932 | 0.132 | 0.2328 | 0.2312 | 0.875 |
| whole_nolag | 122 | 30 | 180611 | 70 | 0.8837 | 0.8417 | 2.683 | 0.8848 | 0.5985 | 0.1324 | 0.2328 | 0.2406 | 0.8881 |

_Top features (winner):_ pct_corn_fixed_b2, tile_frac_basin, cat_tiles92, lon, doy_sin, tot_tiles92, surplus_kgha_fixed_b2, doy_cos

### 32 [reg]
**Question.** Does long-run basin composition -- the multi-year mean crop/surplus shares, their year-to-year std, and a corn-rotation index -- improve the BETWEEN-site metrics over the shipped recipe, which only ever sees one year of land cover at a time?

**Winner.** `longrun_mean` — lofo_r2 = 0.4489, +0.0387 vs base
<sub>2026-07-31T04:08Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 | lofo_within_r2 | lofo_macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|
| longrun_mean ⭐ | 116 | 30 | 175973 | 81 | 0.5264 | 0.4489 | 4.033 | 0.4337 | 0.405 | 0.2021 |
| longrun_both_cell | 116 | 30 | 175973 | 111 | 0.5101 | 0.44 | 4.065 | 0.3685 | 0.4075 | 0.2066 |
| longrun_both | 116 | 30 | 175973 | 111 | 0.509 | 0.4368 | 4.077 | 0.3808 | 0.4061 | 0.2033 |
| base | 116 | 30 | 175973 | 54 | 0.5184 | 0.4102 | 4.172 | 0.3509 | 0.4001 | 0.162 |
| longrun_sd_rot_cell | 116 | 30 | 175973 | 84 | 0.4769 | 0.3927 | 4.234 | 0.1721 | 0.4121 | 0.1744 |
| longrun_sd_rot | 116 | 30 | 175973 | 84 | 0.4728 | 0.3779 | 4.285 | 0.1406 | 0.4072 | 0.1807 |

_Top features (winner):_ surplus_kgha_mean_b1, pct_corn_mean_b1, pct_corn_mean_b2, pct_small_grains_mean_b2, roll_n_avg_except_this7d, surplus_kgha_mean_b2, pct_corn_mean_b0, tile_frac_basin

### 32c [clf]
**Question.** Does long-run basin composition -- the multi-year mean crop/surplus shares, their year-to-year std, and a corn-rotation index -- improve the BETWEEN-site metrics over the shipped recipe, which only ever sees one year of land cover at a time?

**Winner.** `longrun_both_cell` — lofo_prauc = 0.6821, +0.0582 vs base
<sub>2026-07-31T06:57Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift | lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| longrun_both_cell ⭐ | 116 | 30 | 175973 | 119 | 0.8905 | 0.6821 | 0.8693 | 2.934 | 0.8852 | 0.5502 | 0.1175 | 0.2325 | 0.406 | 0.8815 |
| longrun_mean | 116 | 30 | 175973 | 89 | 0.8935 | 0.6774 | 0.872 | 2.914 | 0.8853 | 0.5398 | 0.1167 | 0.2325 | 0.4038 | 0.8791 |
| longrun_both | 116 | 30 | 175973 | 119 | 0.8895 | 0.6742 | 0.8683 | 2.9 | 0.8721 | 0.5364 | 0.1184 | 0.2325 | 0.3873 | 0.882 |
| longrun_sd_rot_cell | 116 | 30 | 175973 | 92 | 0.8815 | 0.6554 | 0.8539 | 2.819 | 0.8671 | 0.5708 | 0.1226 | 0.2325 | 0.3309 | 0.8813 |
| longrun_sd_rot | 116 | 30 | 175973 | 92 | 0.8784 | 0.6552 | 0.8546 | 2.818 | 0.8694 | 0.5706 | 0.1225 | 0.2325 | 0.3305 | 0.8821 |
| base | 116 | 30 | 175973 | 62 | 0.8877 | 0.6239 | 0.8404 | 2.684 | 0.8648 | 0.5887 | 0.129 | 0.2325 | 0.2153 | 0.8821 |

_Top features (winner):_ surplus_kgha_sd_b2, pct_hay_pasture_mean_b2, surplus_kgha_mean_b2, rest_of_state_nitrate_lag1, pct_corn_mean_b2, rest_of_state_nitrate_lag3, surplus_kgha_mean_b1, roll_n_avg_except_this7d

### 33 [reg]
**Question.** Does the nitrate of the hydrologically-connected monitored neighbors -- BOTH the nearest upstream and the nearest downstream, on the reach graph -- predict a site beyond the statewide rest_of_state scalar the recipe already carries, and does any of it survive being made causal?

**Winner.** `nbr_all_no_lag0` — lofo_r2 = 0.5097, +0.0608 vs base
<sub>2026-07-31T04:42Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 | lofo_within_r2 | lofo_macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|
| nbr_all_no_lag0 ⭐ | 116 | 30 | 175973 | 95 | 0.539 | 0.5097 | 3.804 | 0.5181 | 0.4471 | 0.3287 |
| nbr_all | 116 | 30 | 175973 | 97 | 0.5348 | 0.5078 | 3.811 | 0.4907 | 0.4518 | 0.3363 |
| nbr_level | 116 | 30 | 175973 | 83 | 0.5352 | 0.4971 | 3.853 | 0.5026 | 0.4439 | 0.3251 |
| nbr_lagged | 116 | 30 | 175973 | 85 | 0.535 | 0.4941 | 3.864 | 0.5054 | 0.4407 | 0.2985 |
| nbr_concurrent | 116 | 30 | 175973 | 83 | 0.5455 | 0.4939 | 3.865 | 0.4674 | 0.438 | 0.295 |
| nbr_2hop | 116 | 30 | 175973 | 87 | 0.5357 | 0.4938 | 3.865 | 0.5062 | 0.4364 | 0.2957 |
| nbr_lagged_down_only | 116 | 30 | 175973 | 83 | 0.5301 | 0.4757 | 3.934 | 0.4643 | 0.4217 | 0.281 |
| nbr_lagged_up_only | 116 | 30 | 175973 | 83 | 0.516 | 0.4557 | 4.008 | 0.4431 | 0.4145 | 0.2368 |
| nbr_coverage | 116 | 30 | 175973 | 87 | 0.5197 | 0.4515 | 4.023 | 0.4658 | 0.4061 | 0.2311 |
| base | 116 | 30 | 175973 | 81 | 0.5264 | 0.4489 | 4.033 | 0.4337 | 0.405 | 0.2021 |

_Top features (winner):_ surplus_kgha_mean_b1, pct_corn_mean_b2, pct_small_grains_mean_b2, rest_of_state_nitrate_lag1, tile_frac_basin, nbr_level_lag1_down, pct_soybeans_mean_b2, surplus_kgha_mean_b2

### 34 [reg]
**Question.** With ONE either-direction neighbor picked by drainage scale, and its direction carried as a separate flag, does the neighbor's nitrate predict a site beyond the statewide rest_of_state scalar, and does any of it survive being made causal?

**Winner.** `nbr_all` — lofo_r2 = 0.51, +0.0611 vs base
<sub>2026-07-31T05:09Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 | lofo_within_r2 | lofo_macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|
| nbr_all ⭐ | 116 | 30 | 175973 | 90 | 0.5374 | 0.51 | 3.803 | 0.513 | 0.4519 | 0.2734 |
| nbr_all_no_lag0 | 116 | 30 | 175973 | 89 | 0.5391 | 0.4986 | 3.847 | 0.4849 | 0.4515 | 0.3103 |
| nbr_2hop | 116 | 30 | 175973 | 84 | 0.5353 | 0.4977 | 3.85 | 0.4788 | 0.4457 | 0.2838 |
| nbr_concurrent | 116 | 30 | 175973 | 82 | 0.5396 | 0.4959 | 3.857 | 0.4685 | 0.4439 | 0.2741 |
| nbr_level | 116 | 30 | 175973 | 82 | 0.5374 | 0.4927 | 3.87 | 0.4983 | 0.4436 | 0.3045 |
| nbr_lagged | 116 | 30 | 175973 | 83 | 0.5371 | 0.4832 | 3.905 | 0.4657 | 0.4318 | 0.2674 |
| nbr_coverage | 116 | 30 | 175973 | 85 | 0.5249 | 0.4614 | 3.987 | 0.4832 | 0.4154 | 0.2354 |
| base | 116 | 30 | 175973 | 81 | 0.5264 | 0.4489 | 4.033 | 0.4337 | 0.405 | 0.2021 |

_Top features (winner):_ surplus_kgha_mean_b1, pct_corn_mean_b2, rest_of_state_nitrate_lag1, pct_small_grains_mean_b2, pct_corn_mean_b1, nbr_level_lag1, pct_soybeans_mean_b2, roll_n_avg_except_this7d

### 35 [reg]
**Question.** Same single either-direction neighbor as 34, but with direction folded into the SIGN of the distance instead of a separate flag: does one signed column encode direction as well as the magnitude-plus-flag pair does?

**Winner.** `nbr_all` — lofo_r2 = 0.5057, +0.0568 vs base
<sub>2026-07-31T05:36Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 | lofo_within_r2 | lofo_macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|
| nbr_all ⭐ | 116 | 30 | 175973 | 89 | 0.5411 | 0.5057 | 3.82 | 0.4941 | 0.4578 | 0.3118 |
| nbr_all_no_lag0 | 116 | 30 | 175973 | 88 | 0.5343 | 0.5047 | 3.823 | 0.4993 | 0.4481 | 0.2789 |
| nbr_2hop | 116 | 30 | 175973 | 84 | 0.5353 | 0.4977 | 3.85 | 0.4788 | 0.4457 | 0.2838 |
| nbr_concurrent | 116 | 30 | 175973 | 82 | 0.5396 | 0.4959 | 3.857 | 0.4685 | 0.4439 | 0.2741 |
| nbr_level | 116 | 30 | 175973 | 82 | 0.5374 | 0.4927 | 3.87 | 0.4983 | 0.4436 | 0.3045 |
| nbr_lagged | 116 | 30 | 175973 | 83 | 0.5371 | 0.4832 | 3.905 | 0.4657 | 0.4318 | 0.2674 |
| nbr_coverage | 116 | 30 | 175973 | 84 | 0.522 | 0.4507 | 4.027 | 0.4366 | 0.4121 | 0.1807 |
| base | 116 | 30 | 175973 | 81 | 0.5264 | 0.4489 | 4.033 | 0.4337 | 0.405 | 0.2021 |

_Top features (winner):_ surplus_kgha_mean_b1, rest_of_state_nitrate_lag1, pct_corn_mean_b2, nbr_level_lag1, pct_small_grains_mean_b2, pct_corn_mean_b1, surplus_kgha_mean_b2, tile_frac_basin

### 33c [clf]
**Question.** Does the nitrate of the hydrologically-connected monitored neighbors -- BOTH the nearest upstream and the nearest downstream, on the reach graph -- predict a site beyond the statewide rest_of_state scalar the recipe already carries, and does any of it survive being made causal?

**Winner.** `nbr_all_no_lag0` — lofo_prauc = 0.7555, +0.0825 vs base
<sub>2026-07-31T07:37Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift | lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nbr_all_no_lag0 ⭐ | 116 | 30 | 175973 | 130 | 0.8993 | 0.7555 | 0.8957 | 3.249 | 0.8589 | 0.4649 | 0.1012 | 0.2325 | 0.5334 | 0.8923 |
| nbr_all | 116 | 30 | 175973 | 132 | 0.8993 | 0.752 | 0.8962 | 3.234 | 0.8575 | 0.4529 | 0.1017 | 0.2325 | 0.5267 | 0.8949 |
| nbr_level | 116 | 30 | 175973 | 118 | 0.8997 | 0.7425 | 0.891 | 3.194 | 0.8597 | 0.4687 | 0.1039 | 0.2325 | 0.4962 | 0.8912 |
| nbr_concurrent | 116 | 30 | 175973 | 118 | 0.8983 | 0.7337 | 0.8884 | 3.156 | 0.8732 | 0.4877 | 0.1062 | 0.2325 | 0.5022 | 0.8971 |
| nbr_2hop | 116 | 30 | 175973 | 122 | 0.8991 | 0.7314 | 0.888 | 3.146 | 0.871 | 0.4961 | 0.1066 | 0.2325 | 0.4901 | 0.8938 |
| nbr_lagged | 116 | 30 | 175973 | 120 | 0.8976 | 0.7311 | 0.8888 | 3.145 | 0.8864 | 0.5062 | 0.1063 | 0.2325 | 0.5072 | 0.8984 |
| nbr_lagged_down_only | 116 | 30 | 175973 | 118 | 0.8974 | 0.7105 | 0.8824 | 3.056 | 0.8717 | 0.5062 | 0.1096 | 0.2325 | 0.4607 | 0.891 |
| nbr_lagged_up_only | 116 | 30 | 175973 | 118 | 0.8906 | 0.6967 | 0.8744 | 2.997 | 0.8948 | 0.546 | 0.1152 | 0.2325 | 0.4254 | 0.8912 |
| nbr_coverage | 116 | 30 | 175973 | 122 | 0.8883 | 0.6854 | 0.8723 | 2.948 | 0.888 | 0.5462 | 0.1162 | 0.2325 | 0.4057 | 0.883 |
| base | 116 | 30 | 175973 | 116 | 0.8909 | 0.6729 | 0.868 | 2.895 | 0.8784 | 0.5422 | 0.1185 | 0.2325 | 0.3939 | 0.8797 |

_Top features (winner):_ surplus_kgha_sd_b2, nbr_level_lag1_down, pct_hay_pasture_mean_b2, surplus_kgha_mean_b2, rest_of_state_nitrate_lag3, rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, pct_corn_mean_b2

### 34c [clf]
**Question.** With ONE either-direction neighbor picked by drainage scale, and its direction carried as a separate flag, does the neighbor's nitrate predict a site beyond the statewide rest_of_state scalar, and does any of it survive being made causal?

**Winner.** `nbr_all` — lofo_prauc = 0.7349, +0.0620 vs base
<sub>2026-07-31T08:10Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift | lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nbr_all ⭐ | 116 | 30 | 175973 | 125 | 0.8918 | 0.7349 | 0.8873 | 3.161 | 0.8553 | 0.4747 | 0.1066 | 0.2325 | 0.4687 | 0.8945 |
| nbr_all_no_lag0 | 116 | 30 | 175973 | 124 | 0.891 | 0.732 | 0.8855 | 3.149 | 0.8534 | 0.4741 | 0.1073 | 0.2325 | 0.4461 | 0.8911 |
| nbr_level | 116 | 30 | 175973 | 117 | 0.8855 | 0.7058 | 0.8802 | 3.036 | 0.8622 | 0.4969 | 0.1111 | 0.2325 | 0.4184 | 0.8878 |
| nbr_concurrent | 116 | 30 | 175973 | 117 | 0.8857 | 0.6959 | 0.8777 | 2.994 | 0.8766 | 0.5208 | 0.1131 | 0.2325 | 0.4181 | 0.8977 |
| nbr_lagged | 116 | 30 | 175973 | 118 | 0.8865 | 0.6901 | 0.874 | 2.968 | 0.891 | 0.5424 | 0.1147 | 0.2325 | 0.3886 | 0.8961 |
| nbr_2hop | 116 | 30 | 175973 | 119 | 0.8859 | 0.6894 | 0.8741 | 2.965 | 0.8967 | 0.5497 | 0.1148 | 0.2325 | 0.4003 | 0.8976 |
| nbr_coverage | 116 | 30 | 175973 | 120 | 0.8902 | 0.6734 | 0.8688 | 2.897 | 0.8834 | 0.5479 | 0.1184 | 0.2325 | 0.376 | 0.8808 |
| base | 116 | 30 | 175973 | 116 | 0.8909 | 0.6729 | 0.868 | 2.895 | 0.8784 | 0.5422 | 0.1185 | 0.2325 | 0.3939 | 0.8797 |

_Top features (winner):_ surplus_kgha_sd_b2, nbr_level_lag1, surplus_kgha_mean_b2, rest_of_state_nitrate_lag3, pct_hay_pasture_mean_b2, rest_of_state_nitrate_lag1, roll_n_avg_except_this7d, surplus_kgha_mean_b1

### 35c [clf]
**Question.** Same single either-direction neighbor as 34, but with direction folded into the SIGN of the distance instead of a separate flag: does one signed column encode direction as well as the magnitude-plus-flag pair does?

**Winner.** `nbr_all` — lofo_prauc = 0.7306, +0.0577 vs base
<sub>2026-07-31T08:40Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift | lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nbr_all ⭐ | 116 | 30 | 175973 | 124 | 0.8902 | 0.7306 | 0.885 | 3.143 | 0.8545 | 0.4791 | 0.108 | 0.2325 | 0.4509 | 0.8929 |
| nbr_all_no_lag0 | 116 | 30 | 175973 | 123 | 0.891 | 0.7263 | 0.8838 | 3.124 | 0.8539 | 0.4831 | 0.1086 | 0.2325 | 0.4345 | 0.8904 |
| nbr_level | 116 | 30 | 175973 | 117 | 0.8855 | 0.7058 | 0.8802 | 3.036 | 0.8622 | 0.4969 | 0.1111 | 0.2325 | 0.4184 | 0.8878 |
| nbr_concurrent | 116 | 30 | 175973 | 117 | 0.8857 | 0.6959 | 0.8777 | 2.994 | 0.8766 | 0.5208 | 0.1131 | 0.2325 | 0.4181 | 0.8977 |
| nbr_lagged | 116 | 30 | 175973 | 118 | 0.8865 | 0.6901 | 0.874 | 2.968 | 0.891 | 0.5424 | 0.1147 | 0.2325 | 0.3886 | 0.8961 |
| nbr_2hop | 116 | 30 | 175973 | 119 | 0.8859 | 0.6894 | 0.8741 | 2.965 | 0.8967 | 0.5497 | 0.1148 | 0.2325 | 0.4003 | 0.8976 |
| nbr_coverage | 116 | 30 | 175973 | 119 | 0.8896 | 0.6741 | 0.8679 | 2.9 | 0.8783 | 0.5441 | 0.1187 | 0.2325 | 0.3806 | 0.8844 |
| base | 116 | 30 | 175973 | 116 | 0.8909 | 0.6729 | 0.868 | 2.895 | 0.8784 | 0.5422 | 0.1185 | 0.2325 | 0.3939 | 0.8797 |

_Top features (winner):_ surplus_kgha_sd_b2, nbr_level_lag1, surplus_kgha_mean_b2, rest_of_state_nitrate_lag1, pct_hay_pasture_mean_b2, roll_n_avg_except_this7d, pct_corn_mean_b2, surplus_kgha_mean_b1

### 36 [reg]
**Question.** On the FULL containment graph, does picking each direction's reach neighbor by distance -- nearest along the network -- beat exp 33's immediate-graph, drainage-scale pick, holding every column, arm and fold identical?

**Winner.** `nbr_all` — lofo_r2 = 0.5149, +0.0660 vs base
<sub>2026-07-31T06:20Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_r2 | lofo_r2 | lofo_rmse | lofo_between_r2 | lofo_within_r2 | lofo_macro_r2 |
|---|---|---|---|---|---|---|---|---|---|---|
| nbr_all ⭐ | 116 | 30 | 175973 | 97 | 0.5524 | 0.5149 | 3.784 | 0.5361 | 0.4484 | 0.3426 |
| nbr_all_no_lag0 | 116 | 30 | 175973 | 95 | 0.5467 | 0.512 | 3.795 | 0.5233 | 0.45 | 0.3303 |
| nbr_level | 116 | 30 | 175973 | 83 | 0.5431 | 0.5063 | 3.817 | 0.5222 | 0.448 | 0.2958 |
| nbr_lagged | 116 | 30 | 175973 | 85 | 0.5399 | 0.4989 | 3.846 | 0.5144 | 0.4429 | 0.3015 |
| nbr_2hop | 116 | 30 | 175973 | 87 | 0.5424 | 0.4968 | 3.854 | 0.4909 | 0.4479 | 0.3015 |
| nbr_concurrent | 116 | 30 | 175973 | 83 | 0.5452 | 0.4949 | 3.861 | 0.458 | 0.4409 | 0.3209 |
| nbr_lagged_down_only | 116 | 30 | 175973 | 83 | 0.5373 | 0.4756 | 3.934 | 0.4523 | 0.4307 | 0.2527 |
| nbr_lagged_up_only | 116 | 30 | 175973 | 83 | 0.5114 | 0.4578 | 4 | 0.4471 | 0.4145 | 0.206 |
| nbr_coverage | 116 | 30 | 175973 | 87 | 0.5216 | 0.4547 | 4.012 | 0.4627 | 0.4068 | 0.2146 |
| base | 116 | 30 | 175973 | 81 | 0.5264 | 0.4489 | 4.033 | 0.4337 | 0.405 | 0.2021 |

_Top features (winner):_ nbr_level_lag1_down, pct_corn_mean_b2, surplus_kgha_mean_b1, roll_n_avg_except_this7d, pct_small_grains_mean_b2, rest_of_state_nitrate_lag1, surplus_kgha_mean_b2, pct_soybeans_mean_b2

### 36c [clf]
**Question.** On the FULL containment graph, does picking each direction's reach neighbor by distance -- nearest along the network -- beat exp 33's immediate-graph, drainage-scale pick, holding every column, arm and fold identical?

**Winner.** `nbr_all_no_lag0` — lofo_prauc = 0.7524, +0.0794 vs base
<sub>2026-07-31T09:09Z · full · 116 sites · git 048ef1d</sub>

**Results.**

| recipe | n_sites | n_families | n_rows | n_feat | loso_auc | lofo_prauc | lofo_auc | lofo_prauc_lift | lofo_recall_at_f2 | lofo_fdr_at_f2 | lofo_brier | base | lofo_between_rate_r2 | lofo_macro_auc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nbr_all_no_lag0 ⭐ | 116 | 30 | 175973 | 130 | 0.9011 | 0.7524 | 0.8965 | 3.236 | 0.853 | 0.4577 | 0.1022 | 0.2325 | 0.5092 | 0.893 |
| nbr_all | 116 | 30 | 175973 | 132 | 0.9015 | 0.7487 | 0.8949 | 3.22 | 0.8549 | 0.462 | 0.1024 | 0.2325 | 0.5036 | 0.8959 |
| nbr_level | 116 | 30 | 175973 | 118 | 0.8995 | 0.7449 | 0.8906 | 3.204 | 0.8617 | 0.4752 | 0.1033 | 0.2325 | 0.4834 | 0.8914 |
| nbr_2hop | 116 | 30 | 175973 | 122 | 0.9005 | 0.7412 | 0.8925 | 3.188 | 0.8831 | 0.505 | 0.1043 | 0.2325 | 0.5105 | 0.898 |
| nbr_lagged | 116 | 30 | 175973 | 120 | 0.8988 | 0.7388 | 0.8912 | 3.178 | 0.8813 | 0.5044 | 0.1045 | 0.2325 | 0.5044 | 0.8982 |
| nbr_concurrent | 116 | 30 | 175973 | 118 | 0.8994 | 0.735 | 0.889 | 3.162 | 0.8734 | 0.49 | 0.1054 | 0.2325 | 0.4964 | 0.8984 |
| nbr_lagged_down_only | 116 | 30 | 175973 | 118 | 0.8987 | 0.7206 | 0.884 | 3.1 | 0.8797 | 0.5178 | 0.108 | 0.2325 | 0.4754 | 0.8899 |
| nbr_lagged_up_only | 116 | 30 | 175973 | 118 | 0.8901 | 0.6912 | 0.8743 | 2.973 | 0.8828 | 0.5344 | 0.1155 | 0.2325 | 0.4153 | 0.8922 |
| nbr_coverage | 116 | 30 | 175973 | 122 | 0.8894 | 0.687 | 0.8719 | 2.955 | 0.8773 | 0.5375 | 0.1169 | 0.2325 | 0.4043 | 0.8839 |
| base | 116 | 30 | 175973 | 116 | 0.8909 | 0.6729 | 0.868 | 2.895 | 0.8784 | 0.5422 | 0.1185 | 0.2325 | 0.3939 | 0.8797 |

_Top features (winner):_ nbr_level_lag1_down, surplus_kgha_sd_b2, surplus_kgha_mean_b2, rest_of_state_nitrate_lag3, nbr_n_down, pct_hay_pasture_mean_b2, rest_of_state_nitrate_lag1, roll_n_avg_except_this7d
