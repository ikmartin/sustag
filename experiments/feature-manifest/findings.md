# Feature Manifest Report

The point of this experiment is to determine which features are useful, which aren't and which are actively harmful. This is to aid in feature engineering. Conclusions at the top, experiment details below that.

## Conclusions
- normed crop aggregation provides large between_r2 boost
- exp-distance decay aggregation provides moderate lofo gains but often hurts between_r2 scores
- weather features (besides fuel) seem to do nothing or hurt -- guessing they're suffering the same aggregation problem, need to normalize by basin area.

**Most important features on their own for CLF:**
- `crops_norm` - gain of $+0.2$ between_rate, that's awesome. No exp weighting, just 
- `surplus_norm` - not as useful, probably nerfed by 2017 cutoff. No exp weighting, just basins
- `surplus_expT` - the exponentially weighted + bucketed version
- `fuel_moisture_1000h`
- `cat_contact` - seems to not hurt

**Most harmful features on their own for CLF:**
- `crops_expF` and `crops_expT` - consistent with the earlier findings that showed these aggegations are bad.

**Features on their own for REG to keep:**
- `fuel_moisture_1000h`
- `roll_n_avg_except_this7d` - likely engineered basin-network features would vastly improve
- `cat_contact` seems fine
- `max_rel_humidity`
- `doy_harmonic2`
- `roll_n_avg_except_this60d`
- `burning_index`
- `solar_rad`

**Features to cut in REG**
- `crops_whole`
- `crops_norm_whole`
- `crops_expT`
- `crops_expF`
- `surplus_norm`
- `max_temp`
- `cat_tiles92`
- poorly aggregated crop features -- these drastically hurt between_r2 ranking, makes sense