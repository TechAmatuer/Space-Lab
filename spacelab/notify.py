"""Optional Discord alerts through a channel webhook (Server Settings → Integrations → Webhooks → New Webhook → Copy URL)."""
import json
import urllib.request

from . import config, net

OK_PREFIX = ('https://discord.com/api/webhooks/', 'https://discordapp.com/api/webhooks/',
             'https://ptb.discord.com/api/webhooks/', 'https://canary.discord.com/api/webhooks/')


def valid(url):
    return isinstance(url, str) and url.startswith(OK_PREFIX)


def send(text, url=None):
    """Post text to the Discord channel. Returns (ok, message). Splits long text into 1,900-character parts."""
    url = url or config.load().get('discord_webhook') or ''
    if not valid(url):
        return False, 'no valid Discord webhook set'
    parts, cur = [], ''
    for line in (text or '').splitlines():
        if len(cur) + len(line) + 1 > 1900:
            parts.append(cur)
            cur = ''
        cur += line + '\n'
    if cur.strip():
        parts.append(cur)
    try:
        for p in parts[:4]:
            req = urllib.request.Request(url, data=json.dumps({'content': p, 'username': 'Space Lab',
                                                                 'allowed_mentions': {'parse': []}}).encode(),
                                         headers={'Content-Type': 'application/json', 'User-Agent': net.UA})
            with urllib.request.urlopen(req, timeout=20, context=net.ssl_ctx()) as r:
                r.read()
        return True, 'sent'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
