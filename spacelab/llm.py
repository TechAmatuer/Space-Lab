"""Local LLM access through Ollama. Every caller has a non-AI fallback, so jobs still work if Ollama is off."""
import base64
import json
import re
import time
import urllib.request

from . import config


class LLM:
    def __init__(self, cfg=None, log=print):
        cfg = cfg or config.load()
        self.c = cfg['llm']
        self.host = self.c['host'].rstrip('/')
        self.log = log
        self._models = None
        self.used = []  # which model answered, for the run summary

    def _post(self, path, payload, timeout=300):
        req = urllib.request.Request(self.host + path, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))

    def models(self):
        if self._models is None:
            try:
                with urllib.request.urlopen(self.host + '/api/tags', timeout=5) as r:
                    self._models = [m['name'] for m in json.loads(r.read()).get('models', [])]
            except Exception:
                self._models = []
        return self._models

    def _pick(self, kind):
        have = self.models()
        names = self.c.get(kind) or self.c['writer']
        out = []
        for n in names:
            if n in have or (':' not in n and any(h.split(':')[0] == n for h in have)):
                out.append(n)
        return out

    @staticmethod
    def _clean(text):
        text = re.sub(r'<think>.*?</think>', '', text or '', flags=re.S)
        return text.strip()

    def vision(self, prompt, image_paths, json_mode=False, max_tokens=400):
        """Ask a vision-capable local model (Gemma 4) about images. Returns text/dict or None."""
        imgs = []
        for pth in image_paths:
            try:
                with open(pth, 'rb') as f:
                    imgs.append(base64.b64encode(f.read()).decode())
            except OSError:
                return None
        return self.chat(prompt, kind='vision', json_mode=json_mode, max_tokens=max_tokens, images=imgs, temperature=0.1)

    def chat(self, prompt, system=None, kind='writer', json_mode=False, max_tokens=700, temperature=0.3, images=None, num_ctx=8192):
        """Return the model's text (or parsed dict when json_mode), or None if no local model answered."""
        msgs = []
        if system:
            msgs.append({'role': 'system', 'content': system})
        msgs.append({'role': 'user', 'content': prompt, **({'images': images} if images else {})})
        for model in self._pick(kind):
            for think in (False, None):
                payload = {'model': model, 'messages': msgs, 'stream': False, 'keep_alive': '15m',
                           'options': {'temperature': temperature, 'num_predict': max_tokens, 'num_ctx': num_ctx}}
                if think is False:
                    payload['think'] = False
                else:
                    payload['options']['num_predict'] = max_tokens * 4  # thinking models need room
                if json_mode:
                    payload['format'] = 'json'
                try:
                    t0 = time.time()
                    r = self._post('/api/chat', payload)
                    text = self._clean((r.get('message') or {}).get('content', ''))
                    if not text:
                        continue
                    if json_mode:
                        m = re.search(r'\{.*\}', text, flags=re.S)
                        if not m:
                            continue
                        val = json.loads(m.group(0))
                    else:
                        val = text
                    self.used.append(model)
                    self.log(f'  llm {model} answered in {time.time() - t0:.1f}s')
                    return val
                except Exception as e:
                    self.log(f'  llm {model} failed: {type(e).__name__}: {str(e)[:150]}')
                    continue
        return None

    def embed(self, texts):
        """Return a list of vectors (or None). Uses /api/embed, falls back to /api/embeddings."""
        model = self.c.get('embed') or 'bge-m3'
        if not any(m.split(':')[0] == model.split(':')[0] for m in self.models()):
            return None
        out = []
        try:
            for i in range(0, len(texts), 24):
                r = self._post('/api/embed', {'model': model, 'input': texts[i:i + 24], 'keep_alive': '15m'})
                out.extend(r['embeddings'])
            return out
        except Exception as e:
            self.log(f'  embed batch failed ({e}); trying one at a time')
        try:
            out = []
            for t in texts:
                r = self._post('/api/embeddings', {'model': model, 'prompt': t})
                out.append(r['embedding'])
            return out
        except Exception as e:
            self.log(f'  embed failed: {e}')
            return None
