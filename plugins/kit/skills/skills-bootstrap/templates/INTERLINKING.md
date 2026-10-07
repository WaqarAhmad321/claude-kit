# {Module}: edges to other modules

One edge per line: what links, through which symbol, and what breaks if it changes.

- FK `{Model}.{field}` → `{other module}.{Model}` ({on_delete}): {what depends on it}
- Signal `{sender}.{signal}` → `{module}/signals.py:{receiver}` → {effect}
- Cache key `{key pattern}`: written by `{symbol}`, invalidated by `{symbol}`
- Import: `{module}/{file}.py:{symbol}` calls `{other module}/{file}.py:{symbol}`
- Client limit: {a shipped client relies on this field, status or behaviour}
