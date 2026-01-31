# TC7: Issue Found via Ruff

## Tool Used
`uv run ruff check . --select=ALL`

## Issue Details
- **File**: `dashboard/app.py`
- **Line**: 86
- **Code**: `UP041`
- **Message**: Replace aliased errors with `TimeoutError`

## Current Code
```python
except asyncio.TimeoutError:
    logger.warning("WebSocket shutdown timed out after 5s")
```

## Expected Fix
```python
except TimeoutError:
    logger.warning("WebSocket shutdown timed out after 5s")
```

## Explanation
In Python 3.11+, `asyncio.TimeoutError` is an alias for the built-in `TimeoutError`.
Ruff recommends using the simpler, modern form for better readability and consistency.

## Evidence
```
$ uv run ruff check . --select=UP041
dashboard/app.py:86:12: UP041 Replace aliased errors with `TimeoutError`
```
