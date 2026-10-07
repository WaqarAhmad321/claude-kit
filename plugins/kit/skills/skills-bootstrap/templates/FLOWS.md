# {Module}: user flows

Each flow lists the calls in the order the client makes them, then the test that proves it.

## {Flow name}
1. `{operationId or METHOD /path/}`: {what the client sends and gets back}
2. `{operationId}`: {next call}

Errors covered: {401, 403, 404, 400, 409, as they apply}. Test: `{journeys/feature.hurl or test id}` | Test: MISSING
