# fixture-holdout

**Never used for prompt tuning** (specs.md §12, §14.11). When a prompt
changes, run it here to confirm coverage-cell count actually moved rather than
overfitting to the tuning set. Only the regression harness should read
`bugs.yaml`.
