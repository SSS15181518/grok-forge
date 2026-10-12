# Grok rules you must follow

## Syntax
- `%{PATTERN:field}` captures into `field`; `%{PATTERN:field:int}` or `:float` converts the type. No other types exist.
- `%{PATTERN}` matches without capturing.
- Nested fields use Logstash field references: `%{IP:[source][ip]}`.
- Ad-hoc capture: `(?<field>regex)`; nested names allowed: `(?<[error][type]>[\w.$]+)`.
- Custom reusable patterns go in `pattern_definitions` as NAME -> regex (names are UPPER_SNAKE_CASE) and may use other patterns.

## Regex rules (Oniguruma engine)
- Escape literal special characters: `\[ \] \( \) \. \| \? \* \+ \{ \} \^ \$`. Log lines are full of brackets and dots.
- Spaces in the pattern are literal. If the number of spaces can vary (aligned columns, `WARN  [`), use `\s+`.
- `(?m)` makes `.` match newlines (multi-line events such as stack traces).

## Choosing patterns (most specific that is still safe)
- WORD = `\b\w+\b` (letters/digits/underscore only; stops at `.`, `-`, `/`). Never use WORD for hostnames, paths, users with dots, or ids with dashes.
- NOTSPACE = `\S+` (anything up to the next space) - good default for single tokens.
- DATA = `.*?` (lazy) - only when something follows it to stop at. Never at the end of a pattern.
- GREEDYDATA = `.*` - only for the final free-text part of a line. Use at most once.
- INT / NUMBER / POSINT for numbers; add `:int` / `:float` so values are numeric.
- IP, IPORHOST, HOSTNAME, HOSTPORT, URIPATHPARAM, URI, PATH, UUID, MAC, EMAILADDRESS, USERNAME for those kinds of tokens.
- LOGLEVEL for TRACE/DEBUG/INFO/WARN/WARNING/ERROR/FATAL in any case.
- Timestamps: TIMESTAMP_ISO8601 (2026-10-08T09:12:44Z, 2026-10-08 09:12:44,123), HTTPDATE (08/Oct/2026:09:12:44 +0000), SYSLOGTIMESTAMP (Oct  8 09:12:44), DATESTAMP, TIME. If none fit, define a custom pattern from YEAR, MONTHNUM, MONTHDAY, HOUR, MINUTE, SECOND, MONTH, DAY.
- JAVACLASS for dotted Java class names. QS for a double-quoted string (quotes included); to capture without quotes use `"%{DATA:field}"`.
- Optional parts: `(?: %{INT:bytes:int})?`. Alternatives: `(?:-|%{INT:bytes:int})` for fields that may be a dash.
- Prefer existing whole-line patterns when they fit exactly: COMBINEDAPACHELOG, COMMONAPACHELOG, SYSLOGLINE, SYSLOGBASE, HAPROXYHTTP, etc.

## Quality rules
- Anchor every pattern with `^` and `$` unless intentionally partial.
- One pattern must match ALL sample lines. If lines have different formats, make optional groups or say so in the explanation.
- Field names: prefer Elastic Common Schema names when obvious: `[source][ip]`, `[source][port]`, `[destination][ip]`, `[user][name]`, `[http][request][method]`, `[url][original]`, `[http][response][status_code]`, `[http][response][body][bytes]`, `[log][level]`, `[log][logger]`, `[process][thread][name]`, `[process][pid]`, `[process][name]`, `[host][hostname]`, `[event][duration]`, `[error][message]`, `[user_agent][original]`. Put the raw timestamp in `timestamp` (it will be parsed by the date filter later). Use short snake_case for app-specific fields, nested under a sensible parent like `[app][...]`.
- Do not capture pure punctuation or constant literal words.
