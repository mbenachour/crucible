# fixture-c

Golden fixture — memory-safety bugs (specs.md §12). Planted bugs in
`bugs.yaml`; `BUG(<id>)` comments map to manifest entries. Build with `make`
(ASan/UBSan on by default) so PoCs can crash a real binary.
