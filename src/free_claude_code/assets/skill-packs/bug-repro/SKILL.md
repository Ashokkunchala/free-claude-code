# Bug repro

Reproduce, isolate, fix, prove.

1. Reproduce first. No fix without a captured failing behavior: the exact command, inputs, expected and actual output.
2. Isolate the root cause to the smallest input or call that still fails. Fixing symptoms leaves the bug alive.
3. Write the regression test BEFORE the patch: it must fail without the fix and pass with it.
4. Keep the patch minimal; drive-by refactors hide regressions from review.
5. Run the full validation suite, not only the new test.
