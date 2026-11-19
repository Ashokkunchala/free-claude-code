# Log correlation

Time-window log analysis and incident timeline synthesis.

1. Fix the time window and the sources before reading anything; an unbounded log search wastes the investigation.
2. Normalize timestamps to one timezone before correlating; note the source timezone of each log.
3. Find the FIRST failure, not the loudest one. Cascading errors after the root cause are noise for diagnosis.
4. Build the timeline from evidence: every entry cites its source, timestamp and a short excerpt. Redact credentials, tokens and personal data before reporting.
