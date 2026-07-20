# Executive Summary

**Project members:** Erin Bevilacqua, Xiaoying He, Rajpreet Kaur, Jay Lee, Isaac Martin

**Github Repo:** <https://github.com/Erdos-Projects/summer26-sustainability-of-agriculture>

**Project Objective:** Iowa grows 2.5 billion bushels of corn a year, which demands heavy nitrogen fertilizer; the excess leaches into rivers as nitrate, a Group 2A carcinogen. Iowa's drinking water routinely exceeds the 10 mg/L federal limit — far above the 1–2 mg/L background level and the 3 mg/L threshold now linked to elevated cancer risk — a likely contributor to the state's rising cancer rate. Real-time nitrate sensors are sparse, costly, and shrinking under budget cuts, leaving utilities, regulators, and farmers blind across most of the state. We ask: **can we predict nitrate concentration and violation risk at any Iowa location from publicly available weather and land-use data alone, with no physical sensor?**

**Data preparation and computing workflow:** 
We filtered 162 candidate sensors to 85 high-quality sites (~160,000 daily nitrate records). For each, we computed and hand-validated a drainage basin using one of three distinct methods, then stitched three gridded layers onto it: annual satellite crop cover, annual soil nitrogen surplus, and daily weather. Feature engineering was a major effort, with three findings driving the design:

- Proximity dominates — land near a sensor matters far more than the basin's outer reaches, so we bucketed features by distance and weighted them by exponential decay toward the sensor.
- Geography is a strong proxy — latitude/longitude and basin geometry proved surprisingly predictive, likely standing in for unmeasured local factors like nearby livestock operations.
- Seasonality is real, leakage is a trap — violations are seasonal beyond weather alone (sine/cosine terms helped), and because upstream basins nest inside downstream ones, we held whole basin families together across the train/test split to prevent spatial leakage.

**Statistical Validation and KPIs:** 
Two early dead ends shaped our approach. First, classical time-series forecasting (e.g., Holt-Winters) is nonsensical for our target; it needs a site's own history, which a virtual site lacks. Second, per-site models underperformed even a dummy-mean baseline (negative test R²) for want of data. Pooling all sites into one XGBoost model was the unlock — untuned, it already reached 0.24 R² (regression) and 0.51 PR-AUC (classification) on unseen sites, the benchmark the tuned pipeline was measured against.

Final tuned results below, reported using CV designed to prevent leakage from nested basins: 

- **Classification (violation flagging):** ROC-AUC 0.82, PR-AUC 0.63 against a 26% base rate (2.4× better than chance), and per-site AUC 0.90 — excellent at timing when a basin spikes. A Brier score of 0.137 (vs. a 0.19 base-rate floor) confirms well-calibrated probabilities. Tuned toward recall, it catches 86% of violations at a 59% false-discovery rate, with a knob to trade recall against precision.

- **Regression (concentration):** R² ≈ 0.33 (up from the 0.24 baseline), ~4 mg/L typical error. To account for the high variability in average site nitrate values we report two mean-normalized scores: within-basin R² (0.42) calculates R² after subtracting a site's mean from both the prediction and the actual, and between-basin R² (0.21) compares the predicted and actual site means. These scores therefore reflect that the regressor is good at detecting the direction of changes within a site, but bad at pinning the absolute level of an unseen basin. It's a "when will nitrate spike and roughly how bad will it be" tool.

**Statistical tests and deployment implications:** 
An F-test confirmed nitrate's seasonality survives controlling for rainfall seasonality, ruling out a naive "high in June" shortcut. Gain- and permutation-based importance agree on the backbone: statewide daily nitrate, sensor location/geometry, and near-field corn. 

Key limitations: a sparse sensor fleet, annual crop/soil snapshots that miss within-season change, and extrapolation risk at unfamiliar basins — and nitrate ultimately depends on more than weather and crops. Natural next steps: point-source pollution data (livestock manure, which location may be proxying for), soil/tile-drainage data, fertilizer timing, and graph models exploiting hydrological network structure.

The work is deployed as a web widget (not yet accessible via public URL): drop a pin anywhere in Iowa and it computes the basin, builds the same 68-feature set, and returns predicted-nitrate and violation-probability curves — no local history needed — with a tunable recall/precision knob.

**Executive Impact:**
Nitrate predictions let three groups act sooner and more precisely than the current sparse sensor network allows. Water utilities can begin treatment before a spike hits rather than reacting after the fact. Regulatory agencies can target monitoring and remediation dollars toward high-risk unmonitored locations rather than spreading resources evenly. Farmers can time fertilizer application around forecasted rain to reduce runoff. The model is not a substitute for physical sensors in regulatory compliance contexts, but in a state where budget cuts mean the realistic alternative at most locations is no information at all, an imperfect, well-calibrated prediction represents a substantial upgrade in situational awareness.