# README Cross Validation

Basins linked by a river are highly correlated, hence naive random test/train splits would result in leaking data. The field thinks about this already. The first link below seems to be a canonical reference, the one below discusses a slightly laxxed strategy.

- [Cross-validation strategies for data with temporal, spatial, hierarchical, or phylogenetic structure](https://nsojournals.onlinelibrary.wiley.com/doi/full/10.1111/ecog.02881)
- [Spatial leave-one-out cross-validation for variable selection in the presence of spatial autocorrelation](https://onlinelibrary.wiley.com/doi/10.1111/geb.12161)

Strict strategy: make graph of site network. Hold out connected components (this is our LOFO strategy).

More physical: even if two sensors are connected, if they are far apart, they aren't highly correlated. Instead, hold out one family of mutually correlated sites, with some correlation threshold defining it.

An even better strategy (likely): abandon independence as the goal, and instead match the CV nearest-neighbor distance distribution to the one the model will face at prediction time. The aim is to resemble the prediction situation in difficulty, not to maximize separation.