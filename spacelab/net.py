"""Small HTTP helpers with retries, polite pacing and certifi-backed TLS (works with python.org and uv Pythons)."""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

UA = 'SpaceLab/1.0 (personal citizen-science pipeline; low request rate)'
_ctx = None
_last = {}


def ssl_ctx():
    global _ctx
    if _ctx is None:
        try:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _ctx = ssl.create_default_context()
    return _ctx


def _pace(url, min_gap):
    host = urllib.parse.urlsplit(url).netloc
    wait = _last.get(host, 0) + min_gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.time()


def fetch(url, params=None, data=None, timeout=60, retries=3, headers=None, min_gap=1.0):
    """GET (or POST when data is given). Returns bytes. Raises the last error after retries."""
    if params:
        url = url + ('&' if '?' in url else '?') + urllib.parse.urlencode(params, doseq=True)
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else urllib.parse.urlencode(data, doseq=True).encode()
    h = {'User-Agent': UA, 'Accept-Encoding': 'identity'}
    h.update(headers or {})
    err = None
    for attempt in range(retries):
        _pace(url, min_gap)
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (400, 401, 403, 404):
                break
            time.sleep(3 * (attempt + 1))
        except Exception as e:  # timeouts, resets
            err = e
            time.sleep(3 * (attempt + 1))
    raise err


def fetch_json(url, params=None, **kw):
    return json.loads(fetch(url, params=params, **kw).decode('utf-8', 'replace'))


def fetch_text(url, params=None, **kw):
    return fetch(url, params=params, **kw).decode('utf-8', 'replace')


def reachable(url, timeout=12):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
            r.read(256)
            return True, f'HTTP {r.status} in {time.time() - t0:.1f}s'
    except urllib.error.HTTPError as e:
        # The server answered, so the network path works.
        return e.code < 500, f'HTTP {e.code}'
    except Exception as e:
        return False, f'{type(e).__name__}: {str(e)[:120]}'
