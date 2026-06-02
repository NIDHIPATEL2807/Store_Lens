import time


def call_with_retry(fn, *args, max_retries: int = 6, base_delay: float = 10.0, **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff on rate-limit / quota errors."""
    try:
        import openai as _openai
        _rate_errors = (_openai.RateLimitError,)
    except ImportError:
        _rate_errors = ()

    delay = base_delay
    for attempt in range(1, max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except _rate_errors as exc:
            if attempt == max_retries:
                raise
            print(f"  [RATE LIMIT] {exc.__class__.__name__} — retrying in {delay:.0f}s "
                  f"(attempt {attempt}/{max_retries})")
            time.sleep(delay)
            delay = min(delay * 2, 120.0)
        except Exception as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("429", "quota", "rate", "exhausted", "too many")):
                if attempt == max_retries:
                    raise
                print(f"  [RATE LIMIT] {exc} — retrying in {delay:.0f}s "
                      f"(attempt {attempt}/{max_retries})")
                time.sleep(delay)
                delay = min(delay * 2, 120.0)
            else:
                raise
