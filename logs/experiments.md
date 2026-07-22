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
