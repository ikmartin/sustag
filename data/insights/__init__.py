"""Insights: what the data layer actually holds, and how its pieces relate.

Runnable after any pipeline run, not routine:

    python -m data.insights            # everything -> report.md + images/
    python -m data.insights --tier 0   # the exposure accounting alone

THREE TIERS, AND THE THIRD IS DELIBERATELY ABSENT. Tier 0 reconciles what is on disk against what `data.access` exposes -- knowing what we can actually reach. Tier 1 describes each source: coverage, grain, span, cadence, lineage. Tier 2 compares sources within a theme: agreement, redundancy, the within-basin variance ratio. There is no tier measuring predictive power, because that is a property of a TASK rather than of the data, and recording it here would make an inventory into a record of one model's preferences on one cohort at one date.

`report.md` IS ALWAYS GENERATED AND NEVER HAND-EDITED. It is what you read after a pipeline run to see what the data now looks like. The prose that interprets it -- the themes, the do-not-double-count list, what to use when -- is hand-written in the inventory chapter and cites this.

IMPORT DIRECTION: this reads `data.access` and `data.build.config`; neither may read it. It writes no store and gates nothing, so it is not a `data.build.run` stage.
"""
