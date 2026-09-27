"""trendbot research package.

A small, fully auditable, standard-library-only reference implementation of the
EMA9/21 + RSI + volume + 200-EMA-regime long-only strategy, its mandatory risk
rules, a walk-forward validation harness, and a logistic-regression filter layer.

Nothing in this package targets or optimises a win rate. The target metric is
positive expectancy (average R per trade) that survives walk-forward validation
with controlled drawdown. Research tool only, not financial advice.
"""
