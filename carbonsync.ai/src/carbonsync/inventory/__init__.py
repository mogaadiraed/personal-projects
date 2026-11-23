"""GHG inventory beyond the daily budget perimeter.

* ``scope2``: Scope 2 by both GHG Protocol methods: location-based (grid average, the daily figure) and
  market-based (contractual instruments + residual mix), as ESRS E1-6 asks.
* ``scope3``: Scope 3 screening of the 15 GHG Protocol categories: calculated, estimated or excluded, with
  the method, the source of each factor and the reason for each exclusion.
* ``breakdown``: emissions by plant zone and item from allocation keys, so each reduction has an owner.

All three are pure functions of the daily emissions table and config.yaml (no database writes): a settings
change shows at once, without rerunning the pipeline.
"""
