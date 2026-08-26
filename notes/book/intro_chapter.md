# Introduction

This project is broadly called the **cornbelt hydrology project**. It has two objectives at different levels of generality:

1. create a robust system for training and developing models based on publicly available hydrologically-significant data, particularly models aimed at hydrological regression or time-series classification tasks
2. create a *virtual sensor product*: a client-facing application which allows a user to drop a pin anywhere in a specified valid region, snaps the pin to the nearest valid location, and produces hydrologically significant predictions.

The primary aim is objective #2, but in developing it many pieces of #1 — including a robust data layer — will necessarily be built. We therefore build with a forward-looking gaze, anticipating that the data layer will outlive any particular model. When we say "the objective" or "the model" we mean #2.

The premiere model will be a GNN with homogeneous node types, so we may as well predict more than nitrate. Nonetheless the principal performance indicator is *nitrate prediction*, and it is against that literature we gauge performance. **This preference lives in exactly two places**: the AOE is seeded from nitrate sensors (§ D1), and nitrate sits alone in the high-scrutiny channel tier (§ Principles). Everywhere else the pipeline is target-agnostic: it encodes facts about the world, and modeling objectives express preferences over those facts at the last possible moment.

The **valid region** of objective #2 is the AOE restricted to the surface flow network: a pin is valid where it snaps to a reach within the snap tolerance, and the prediction is made for the snapped position.