// Space Lab extras: fun and learning features for the public page.
// Rules (see EXTRAS.md): only edit this file and extras.css. Read data with SpaceLab.data; never fetch other sites
// except the ones listed in EXTRAS.md; never store anything except in this visitor's own browser (localStorage).
(function () {
  'use strict';
  var S = window.SpaceLab;
  if (!S) return;

  // Safe localStorage helper — wrapped in try/catch, sl_ prefix only.
  var SL = {
    get: function (k, def) { try { var v = localStorage.getItem('sl_' + k); return v === null ? def : JSON.parse(v); } catch (_) { return def; } },
    set: function (k, v) { try { localStorage.setItem('sl_' + k, JSON.stringify(v)); } catch (_) {} },
  };

  // ── Task 1: Glossary tooltips ──────────────────────────────────────────────
  var GL = {
    'transit': 'A transit happens when a planet passes directly between its star and us, briefly dimming the starlight.',
    'light curve': "A plot showing how bright an object gets dimmer and brighter over time.",
    'period': 'The length of one orbit — the time it takes a planet to circle its star.',
    'depth': 'How much the star dims during a transit, measured in parts per million (ppm).',
    'ppm': 'Parts per million — a fraction. One ppm is one part of a million.',
    'SNR': 'Signal-to-noise ratio: how clearly the transit signal stands above random noise.',
    'BLS': 'Box Least Squares — the algorithm that searches light curves for repeating dips.',
    'centroid': 'The brightness-weighted center of a star\'s image. Shifts during a dip hint at a false positive.',
    'false-positive probability': 'How likely it is that the signal comes from something other than a planet.',
    'FPP': 'False-Positive Probability — scores how likely a signal is a false alarm.',
    'eclipsing binary': 'Two stars orbiting each other. One passing in front of the other mimics a planet transit.',
    'TESS': 'The Transiting Exoplanet Survey Satellite, a NASA telescope that watches 24-degree sky patches for two weeks.',
    'TIC': 'The TESS Input Catalog — over a billion stars with positions and brightness measurements.',
    'ExoFOP': 'Exoplanet Forecast and Observation Planner, a NASA tool showing all available data for a star.',
    'precovery': 'Finding old images that show an object before it was officially discovered.',
    'near-Earth asteroid': 'An asteroid whose orbit brings it close to Earth — potentially hazardous.',
    'Palermo scale': 'A logarithmic scale from minus infinity to plus eight; below -2 means no cause for concern.',
    'Torino scale': 'A zero-to-ten scale used to communicate asteroid impact risk to the public.',
    'lunar distance': 'One lunar distance — about 384,000 kilometers, the average Earth-Moon distance.',
    'LD': 'Lunar distance — roughly 384,000 km (238,000 miles).',
    'mid-transit time': 'The exact moment a planet is halfway through its transit across its star.',
    'O-C': 'Observed minus Calculated — the difference between actual and predicted transit timing.',
    'ephemeris': "A planet's predicted schedule of future transits, refined each new observation.",
    'airmass': 'How much atmosphere starlight passes through before reaching the telescope.',
    'arXiv': 'A free online repository where physicists and astronomers post preprints.',
    'Earth radii': "A planet's size expressed as a multiple of Earth's radius.",
    'star': 'A giant ball of hot gas producing energy through nuclear fusion in its core.',
    'planet': "An object orbiting a star, big enough to be round but not triggering nuclear fusion.",
  };

  // Build regex with all glossary terms ordered longest-first.
  var _glTerms = Object.keys(GL);
  _glTerms.sort(function (a, b) { return b.length - a.length; });
  var _glRegex = new RegExp('\\b(?:' + _glTerms.map(function (t) {
    return t.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
  }).join('|') + ')\\b', 'gi');

  // Wrap first occurrence of each glossary term in a card.
  function wrapGlossaryEl(el) {
    var tag = el.tagName.toLowerCase();
    if (['a','button','input','select','textarea','script','style','abbr'].indexOf(tag) >= 0) return false;
    if (el.querySelector && el.querySelector('.sl-gl')) return true;

    var wrappedAny = false;
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    var textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);

    for (var ni = 0; ni < textNodes.length; ni++) {
      if (wrappedAny) break;
      var node = textNodes[ni];
      var text = node.textContent;
      _glRegex.lastIndex = 0;
      var match = _glRegex.exec(text);
      if (!match) continue;

      var term = match[0];
      var beforeIdx = match.index;
      var afterIdx = beforeIdx + term.length;
      var parent = node.parentNode;
      if (!parent || parent.tagName.toLowerCase() === 'abbr') continue;

      var beforeText = text.slice(0, beforeIdx);
      var afterText = text.slice(afterIdx);
      var abbr = document.createElement('abbr');
      abbr.className = 'sl-gl';
      abbr.title = GL[term] || '';
      abbr.textContent = term;
      parent.insertBefore(abbr, node);
      if (beforeText) parent.insertBefore(document.createTextNode(beforeText), abbr);
      if (afterText) parent.insertBefore(document.createTextNode(afterText), abbr);
      wrappedAny = true;
      break;
    }
    return wrappedAny;
  }

  // Glossary popup.
  var _popupEl = document.createElement('div');
  _popupEl.id = 'sl-glossary-popup';
  _popupEl.innerHTML = '<button class="close" title="Close">x</button><span id="sl-glossary-text"></span>';
  document.body.appendChild(_popupEl);

  _popupEl.addEventListener('click', function (e) {
    if (e.target.classList.contains('close') || e.target === _popupEl) _popupEl.style.display = 'none';
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') _popupEl.style.display = 'none';
  });

  document.addEventListener('click', function (e) {
    var gl = e.target.closest('.sl-gl');
    if (!gl) return;
    e.preventDefault();
    _popupEl.querySelector('#sl-glossary-text').textContent = gl.title || '';
    _popupEl.style.display = 'block';
  });

  S.onRender(function (tab, el) {
    try {
      if (tab !== 'tess') return;
      var cards = el.querySelectorAll('.card');
      for (var ci = 0; ci < cards.length; ci++) wrapGlossaryEl(cards[ci]);
    } catch (_) {}
  });


  // ── Task 2: "How big is it?" size comparison SVG ────────────────────────

  S.onRender(function (tab) {
    try {
      if (tab !== 'tess') return;
    } catch (_) {}
    var infoDivs = document.querySelectorAll('#v-tess div.small');
    for (var di = 0; di < infoDivs.length; di++) {
      var div = infoDivs[di];
      var rpMatch = div.textContent.match(/([\d.]+)\s*[xX]\s*Earth/i);
      if (!rpMatch) continue;
      var rp = parseFloat(rpMatch[1]);

      var R_EARTH = 1.0, R_NEPTUNE = 3.9, R_JUPITER = 11.2;
      var maxR = Math.max(rp, R_JUPITER);
      var SCALE = 100 / maxR;
      var CX = 60, CY = 50;
      var svgNS = 'http://www.w3.org/2000/svg';

      var svg = document.createElementNS(svgNS, 'svg');
      svg.setAttribute('width', '120');
      svg.setAttribute('height', String(Math.round(maxR * SCALE + 20)));
      svg.style.cssText = 'margin:6px auto;display:block;width:100%;max-width:140px;';

      function addCircle(r, cxVal, fill, stroke) {
        var c = document.createElementNS(svgNS, 'circle');
        c.setAttribute('cx', String(cxVal));
        c.setAttribute('cy', String(CY));
        c.setAttribute('r', String(r * SCALE));
        c.setAttribute('fill', fill || 'none');
        if (stroke) c.setAttribute('stroke', stroke);
        svg.appendChild(c);
        var t = document.createElementNS(svgNS, 'text');
        t.setAttribute('x', String(cxVal));
        t.setAttribute('y', String(CY + r * SCALE + 8));
        t.setAttribute('text-anchor', 'middle');
        t.setAttribute('font-size', '9');
        t.setAttribute('fill', '#8a99a9');
        t.textContent = r === R_EARTH ? 'Earth' : r === R_JUPITER ? 'Jupiter' : '~'+r.toFixed(1);
        svg.appendChild(t);
      }

      addCircle(R_EARTH, CX - 30, '#4db3d3');
      addCircle(rp, CX + 30, null, '#ff8c42');
      addCircle(R_NEPTUNE, CX - 60, null, '#9085e9');
      addCircle(R_JUPITER, CX + 60, null, '#f2b632');

      var wrap = document.createElement('div');
      wrap.style.cssText = 'text-align:center;';
      var pEl = document.createElement('p');
      pEl.className = 'small muted';
      pEl.textContent = 'if it is a planet, here is how big:';
      wrap.appendChild(pEl);
      wrap.insertBefore(svg, pEl.nextSibling);
      div.parentNode.insertBefore(wrap, div.nextSibling);
    }
  });


  // ── Task 3: Listen to a transit (Web Audio) ─────────────────────────────

  var _audioCtx = null;
  var _audioPlaying = false;
  var _audioStop = null;

  S.onRender(function (tab) {
    try {
      if (tab !== 'tess') return;
    } catch (_) {}
    var rows = document.querySelectorAll('#v-tess .card > div.row');
    for (var ri = 0; ri < rows.length; ri++) {
      var row = rows[ri];
      var btns = row.querySelectorAll('[data-lc]');
      if (!btns.length) continue;
      if (row.querySelector('.sl-listen-btn')) continue;

      var listenBtn = document.createElement('button');
      listenBtn.className = 'b sl-listen-btn';
      listenBtn.textContent = '\uD83D\uDD0A Listen';
      listenBtn.title = 'Exaggerated audio: 1 orbit = 1 second. A real planet is silent.';
      listenBtn.addEventListener('click', function () {
        if (_audioPlaying) { stopTransitAudio(); return; }
        playTransitAudio(btns[0]);
      });
      row.insertBefore(listenBtn, btns[0].nextSibling);
    }
  });

  function playTransitAudio(btn) {
    try {
      if (_audioCtx && _audioCtx.state !== 'closed') stopTransitAudio();
      var AudioC = window.AudioContext || window.webkitAudioContext;
      if (!AudioC) { return; }
      _audioCtx = new AudioC();
      var ctx = _audioCtx;

      var period = parseFloat(btn.dataset.p);
      var duration = parseFloat(btn.dataset.dur);
      var depth = parseFloat(btn.dataset.dep);
      if (isNaN(period)) period = 3;
      if (isNaN(duration)) duration = 0.1;
      if (isNaN(depth)) depth = 0.001;

      var durSec = Math.min(Math.max(period, 0.3), 2);
      var dipDur = (duration / period) * durSec;
      var maxDip = Math.min(depth * 50, 0.8);

      var gainNode = ctx.createGain();
      gainNode.connect(ctx.destination);
      gainNode.gain.value = 0.3;

      var osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.value = 440;
      osc.connect(gainNode);
      osc.start();

      var numTransits = Math.max(3, Math.round(durSec / Math.max(dipDur, 0.02)));
      var totalSec = numTransits * durSec + 1;
      var sr = ctx.sampleRate || 44100;
      var steps = Math.floor(totalSec * sr);

      for (var si = 0; si < steps; si++) {
        var tSec = si / sr;
        var gainVal = 0.3;
        for (var tr = 0; tr < numTransits; tr++) {
          var baseTime = tr * durSec;
          var relT = (tSec - baseTime) / durSec;
          if (Math.abs(relT - 0.5) < dipDur / (2 * durSec)) {
            var inDip = Math.cos((relT - 0.5) / (dipDur / 2) * Math.PI / 2);
            gainVal *= (1 - maxDip * Math.max(0, inDip));
          }
        }
        gainNode.gain.setValueAtTime(gainVal, tSec);
      }

      _audioPlaying = true;
      _audioStop = setTimeout(function () {
        osc.stop(); ctx.close(); _audioCtx = null; _audioPlaying = false;
        document.querySelectorAll('.sl-listen-btn.playing').forEach(function (b) { b.classList.remove('playing'); });
      }, totalSec * 1000);

      listenBtn.classList.add('playing');
    } catch (_) {}
  }

  function stopTransitAudio() {
    try {
      clearTimeout(_audioStop);
      if (_audioCtx) { _audioCtx.close(); _audioCtx = null; }
      _audioPlaying = false;
    } catch (_) {}
  }


  // ── Task 4: Basic / Expert switch ────────────────────────────────────────

  (function initLevelToggle() {
    try {
      var stored = localStorage.getItem('sl_level');
      if (!stored) localStorage.setItem('sl_level', 'basic');
    } catch (_) {}

    var level = SL.get('sl_level', 'basic');
    if (level === 'expert') document.body.dataset.level = 'expert';

    var nav = document.querySelector('nav.tabs');
    if (!nav) return;
    var toggle = document.createElement('button');
    toggle.className = 'sl-level-toggle';
    toggle.textContent = level === 'basic' ? 'Basic' : 'Expert';
    toggle.title = 'Toggle Basic/Expert view. Hides or shows detailed numbers.';
    toggle.addEventListener('click', function () {
      var cur = document.body.dataset.level;
      var next = cur === 'expert' ? 'basic' : 'expert';
      document.body.dataset.level = next;
      toggle.textContent = next === 'basic' ? 'Basic' : 'Expert';
      try { localStorage.setItem('sl_level', next); } catch (_) {}
    });
    nav.appendChild(toggle);
  })();


  // ── Task 5: "Planet or not?" review game (real candidates) ───────────────

  function _renderReviewTab(el, data) {
    var cands = (data && data.tess && data.tess.cands || []).filter(function (c) { return c.ai; });
    if (!cands.length) { el.innerHTML = '<div class="card empty">No AI-reviewed candidates ready for you yet.</div>'; return; }

    var idx = SL.get('sl_review_idx', 0) % cands.length;
    var c = cands[idx];
    var reviews = SL.get('sl_reviews', []);

    el.innerHTML = '<div class="sl-review-card">' +
      '<h3>Planet or not?</h3>' +
      '<p class="small muted">Step 1 of 3: Look at the diagnostic plot below. Does it look like a planet transit, an eclipsing binary (two stars), noise, or are you unsure?</p>' +
      (c.plot ? '<img class="plot" src="' + S.esc(c.plot) + '" alt="diagnostic plot for TIC ' + S.esc(String(c.tic)) + '">' : '<p class="muted">No plot available.</p>') +
      '<div class="sl-verdicts" id="sl-vd">' +
        '<button data-v="planet">\uD83E\uDEF0 Planet-like</button>' +
        '<button data-v="binary">\u2B50 Two stars</button>' +
        '<button data-v="noise">\uD83D\uDCCA Noise</button>' +
        '<button data-v="unsure">\uD83E\uDD14 Not sure</button>' +
      '</div>' +
      '<p class="small muted" style="margin-top:8px">TIC ' + S.esc(String(c.tic)) + ' \u00B7 ' + S.esc(c.status || 'new') + ' \u00B7 depth ' + Math.round((c.depth||0)*1e6) + ' ppm \u00B7 ' + S.num(c.rp, 1) + ' R\u2295</p>' +
    '</div>';

    el.querySelectorAll('#sl-vd button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var verdict = btn.dataset.v;
        reviews.push({ tic: c.tic, idx: idx, verdict: verdict, at: Date.now() / 1000 });
        SL.set('sl_reviews', reviews);
        SL.set('sl_review_idx', (idx + 1) % cands.length);

        var aiV = c.ai && c.ai.verdict || '?';
        var flags = (c.flags || []).map(S.esc).join(', ') || 'none';
        btn.closest('.sl-review-card').innerHTML = '<div class="sl-result">' +
          '<h3>You said: ' + S.esc(verdict === 'planet' ? 'Planet-like' : verdict === 'binary' ? 'Two stars' : verdict === 'noise' ? 'Noise' : 'Not sure') + '</h3>' +
          '<p>The AI and automated checks agree:</p>' +
          '<ul class="checks">' +
            '<li>AI verdict: ' + S.esc(aiV) + '</li>' +
            (flags !== 'none' ? '<li>Flags: ' + flags + '</li>' : '') +
          '</ul>' +
          '<p style="margin-top:12px"><button class="b primary" id="sl-next-review">Next candidate \u2192</button></p>' +
          '<p style="margin-top:6px;font-size:12px;color:#8a99a9">' + reviews.length + ' reviews done so far.</p>' +
        '</div>';
      });
    });

    var nextBtn = el.querySelector('#sl-next-review');
    if (nextBtn) nextBtn.addEventListener('click', function () {
      SL.set('sl_review_idx', (SL.get('sl_review_idx', 0) + 1) % cands.length);
      el.innerHTML = '';
      _renderReviewTab(el, data);
    });
  }

  S.addTab('review', 'Review', _renderReviewTab);


  // ── Task 6: "Is the asteroid there?" image review ────────────────────────

  function _renderAsteroidReview(el, data) {
    var cands = data && data.asteroids && data.asteroids.cands || [];
    if (!cands.length) {
      el.innerHTML += '<div class="card"><p class="small muted">No precovery sightings waiting right now. Try again after the next pipeline run.</p></div>';
      return;
    }

    var idx = SL.get('sl_astr_idx', 0) % cands.length;
    var a = cands[idx];

    el.innerHTML += '<h2>Precovery image review</h2>' +
      '<div class="sl-asteroid-review">' +
        '<p class="small muted">The asteroid should be the dot that moves between frames. Look at the animated GIF:</p>' +
        (a.gif ? '<img class="blink" src="' + S.esc(a.gif) + '" alt="blink for ' + S.esc(a.des) + '">' : '') +
        (a.png ? '<br><img class="plot" src="' + S.esc(a.png) + '" alt="cutouts" style="margin-top:8px">' : '') +
        '<p class="small muted">' + S.esc(a.des) + ' \u00B7 Night: ' + S.esc(String(a.night)) + ' \u00B7 Archive: ' + S.esc(a.archive || '?') + '</p>' +
        '<div class="sl-verdicts" id="sl-avd">' +
          '<button data-v="real">\uD83D\uDD34 Moving dot</button>' +
          '<button data-v="nothing">\u26AA Nothing there</button>' +
          '<button data-v="star">\u272A Star/defect</button>' +
          '<button data-v="unsure">\uD83E\uDD14 Not sure</button>' +
        '</div>' +
        '<p style="margin-top:12px"><button class="b primary" id="sl-next-astr">Next asteroid \u2192</button></p>' +
      '</div>';

    el.querySelectorAll('#sl-avd button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var verdict = btn.dataset.v;
        var astrReviews = SL.get('sl_astr_reviews', []);
        astrReviews.push({ des: a.des, night: a.night, verdict: verdict, at: Date.now() / 1000 });
        SL.set('sl_astr_reviews', astrReviews);
        SL.set('sl_astr_idx', (idx + 1) % cands.length);

        btn.closest('.sl-asteroid-review').innerHTML = '<div class="sl-result">' +
          '<h3>Thank you! You said: ' + S.esc(verdict === 'real' ? 'Moving dot' : verdict === 'nothing' ? 'Nothing there' : verdict === 'star' ? 'Star/defect' : 'Not sure') + '</h3>' +
          '<p>If confirmed, these old positions extend the asteroid\'s observed orbit and help scientists refine impact-risk estimates.</p>' +
          '<p style="margin-top:12px"><button class="b primary" id="sl-next-astr-2">Next asteroid \u2192</button></p>' +
        '</div>';

        // Also update next button to work from result view.
        var nBtn = el.querySelector('#sl-next-astr-2');
        if (nBtn) nBtn.addEventListener('click', function () {
          SL.set('sl_astr_idx', (SL.get('sl_astr_idx', 0) + 1) % cands.length);
          el.innerHTML = '';
          _renderAsteroidReview(el, data);
        });
      });
    });

    var nextBtn = el.querySelector('#sl-next-astr');
    if (nextBtn) nextBtn.addEventListener('click', function () {
      SL.set('sl_astr_idx', (SL.get('sl_astr_idx', 0) + 1) % cands.length);
      el.innerHTML = '';
      _renderAsteroidReview(el, data);
    });
  }

  S.addTab('review-astr', 'Asteroids', _renderAsteroidReview);


  // ── Task 7: Find-the-period practice game ───────────────────────────────

  function _renderPracticeTab(el, data) {
    var trueP = 1 + Math.random() * 7;
    var nDays = 27, dt = 0.5 / 24;
    var depthVal = 0.005 + Math.random() * 0.015;
    var noiseVal = 0.003 + Math.random() * 0.007;

    function rand(seed) {
      var s = seed;
      return function () { s = (s * 9301 + 49297) % 233280; return s / 233280; };
    }
    var rng = rand(42);
    var tArr = [], yArr = [];
    for (var pi = 0; pi < nDays / dt; pi++) {
      var tVal = pi * dt;
      var base = 1 + (rng() - 0.5) * noiseVal * 2;
      var ph = ((tVal % trueP) / trueP);
      if (ph < depthVal || ph > 1 - depthVal) base -= depthVal;
      tArr.push(tVal); yArr.push(base);
    }

    el.innerHTML = '<div style="max-width:640px;margin:0 auto">' +
      '<h3>Find the planet\'s period</h3>' +
      '<p class="small muted">This is practice with made-up data. No real planets are involved.</p>' +
      '<canvas id="sl-canvas"></canvas>' +
      '<div style="height:120px;border:1px solid #24303d;border-radius:8px;margin-top:8px" id="sl-folded-view"></div>' +
      '<p class="sl-step"><label>Try a period: <input type="range" id="sl-slider" min="0.5" max="10" step="0.01" value="3"> <span id="sl-val" class="mono">3.00</span> days</label></p>' +
      '<div style="text-align:center"><button class="b primary" id="sl-new-star">New star</button> <span id="sl-status" class="small muted"></span></div>' +
    '</div>';

    var canvas = el.querySelector('#sl-canvas');
    var cx2 = canvas.getContext('2d');
    canvas.width = Math.min(600, el.clientWidth - 40);
    canvas.height = 200;

    function drawCurve(ctx, arr) {
      if (!arr.length) return;
      ctx.fillStyle = '#0c1218';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      var W = canvas.width, H = canvas.height;
      var step = Math.max(1, Math.floor(arr.length / (W / 3)));
      for (var di2 = 0; di2 < arr.length; di2 += step) {
        var xVal = (di2 / arr.length) * W;
        var yVal = H - ((arr[di2] - 0.9) / 0.25) * H;
        ctx.fillStyle = '#8a99a9';
        ctx.fillRect(xVal, Math.min(H - 1, Math.max(0, yVal)), 2, 2);
      }
    }

    function foldAndDraw(period) {
      var W = canvas.width, H = canvas.height - 40;
      var nBins = 60;
      var bins = [];
      for (var bi = 0; bi < nBins; bi++) bins.push({sum: 0, count: 0});
      for (var fi = 0; fi < tArr.length; fi++) {
        var ph2 = ((tArr[fi] % period) / period);
        var bi2 = Math.floor(ph2 * nBins) % nBins;
        bins[bi2].sum += yArr[fi];
        bins[bi2].count++;
      }
      cx2.fillStyle = '#0c1218';
      cx2.fillRect(0, 0, W, H + 40);
      var binW = W / nBins;
      for (var gi = 0; gi < nBins; gi++) {
        var avg = bins[gi].count ? bins[gi].sum / bins[gi].count : 1;
        var yH = H - ((avg - 0.9) / 0.25) * H;
        cx2.fillStyle = '#ff8c42';
        cx2.fillRect(gi * binW, Math.min(H, Math.max(0, yH)), binW - 1, Math.max(0, H - yH));
      }
    }

    drawCurve(cx2, yArr);
    foldAndDraw(trueP);

    var slider = el.querySelector('#sl-slider');
    var status = el.querySelector('#sl-status');
    var tries = 0;

    slider.addEventListener('input', function () {
      tries++;
      var val = parseFloat(slider.value);
      el.querySelector('#sl-val').textContent = val.toFixed(2);
      foldAndDraw(val);
      if (Math.abs(val - trueP) / trueP < 0.01) {
        status.textContent = 'You found it! Period \u2248 ' + trueP.toFixed(3) + ' days (' + tries + ' tries).';
      } else {
        var diff = ((val - trueP) / trueP * 100).toFixed(1);
        status.textContent = (diff > 0 ? '+' : '') + diff + '% off \u2014 ' + (Math.abs(diff) < 5 ? 'getting closer!' : 'try again');
      }
    });

    el.querySelector('#sl-new-star').addEventListener('click', function () {
      trueP = 1 + Math.random() * 7;
      tries = 0;
      status.textContent = 'New star generated. Can you find the period?';
      slider.value = String(trueP);
      slider.dispatchEvent(new Event('input'));
    });

    // Track practice finds for badges.
    var foundCount = SL.get('sl_practice_found', 0);
    const origInput = slider.oninput;
    let foundThisOne = false;
    slider.addEventListener('change', function () {
      if (Math.abs(parseFloat(slider.value) - trueP) / trueP < 0.01 && tries <= 5 && !foundThisOne) {
        foundThisOne = true;
        SL.set('sl_practice_found', foundCount + 1);
      }
    });
  }

  S.addTab('practice', 'Practice', _renderPracticeTab);


  // ── Task 8: Postcard / light-curve art ───────────────────────────────────

  S.onRender(function (tab) {
    try {
      if (tab !== 'tess') return;
    } catch (_) {}
    var cards = document.querySelectorAll('#v-tess .card');
    for (var ci3 = 0; ci3 < cards.length; ci3++) {
      var card = cards[ci3];
      if (card.querySelector('.sl-postcard-btn')) continue;
      var ticMatch = card.id.match(/tic-(\d+)/);
      if (!ticMatch) continue;

      // Find candidate data.
      var cands2 = S.data && S.data.tess && S.data.tess.cands || [];
      var cand2 = null;
      for (var vi = 0; vi < cands2.length; vi++) {
        if (String(cands2[vi].tic) === ticMatch[1]) { cand2 = cands2[vi]; break; }
      }
      if (!cand2 || !cand2.period) continue;

      var btn3 = document.createElement('button');
      btn3.className = 'b sl-postcard-btn';
      btn3.textContent = '\uD83D\uDBC0 Postcard';
      btn3.title = 'Save a postcard for TIC ' + ticMatch[1];
      btn3.addEventListener('click', function () {
        var canvas2 = document.createElement('canvas');
        canvas2.width = 1080; canvas2.height = 1080;
        var cx3 = canvas2.getContext('2d');

        // Background.
        cx3.fillStyle = '#0c1218';
        cx3.fillRect(0, 0, 1080, 1080);

        // Glowing ring (folded light curve).
        var cxr = 540, cyr = 500, r2 = 300;
        var numPts = 200;
        var dp = cand2.depth || 0.001;
        for (var wi = 0; wi < numPts; wi++) {
          var angle = (wi / numPts) * Math.PI * 2;
          var inTr = Math.abs(Math.sin(angle)) < 0.15;
          var flux = inTr ? 1 - dp : 1;
          var px = cxr + r2 * Math.cos(angle) * flux;
          var py = cyr + r2 * Math.sin(angle) * flux;

          cx3.beginPath();
          cx3.arc(px, py, 3, 0, Math.PI * 2);
          cx3.fillStyle = 'rgba(77,179,211,' + (inTr ? '0.8)' : '0.3)');
          cx3.fill();
        }

        // Outline ring.
        cx3.beginPath();
        cx3.arc(cxr, cyr, r2, 0, Math.PI * 2);
        cx3.strokeStyle = 'rgba(77,179,211,0.3)';
        cx3.lineWidth = 1;
        cx3.stroke();

        cx3.fillStyle = '#e1e8ee';
        cx3.font = 'bold 24px sans-serif';
        cx3.textAlign = 'center';
        cx3.fillText('TIC ' + ticMatch[1], cxr, cyr - r2 - 30);

        cx3.font = '16px sans-serif';
        cx3.fillStyle = '#8a99a9';
        cx3.fillText('orbit every ' + cand2.period.toFixed(4) + ' days', cxr, cyr - r2 - 8);
        cx3.fillText('~' + (cand2.rp||0).toFixed(1) + ' Earth radii if planet', cxr, cyr - r2 + 12);

        cx3.font = '12px sans-serif';
        cx3.fillStyle = '#5f6f82';
        cx3.fillText('Space Lab \u00B7 citizen science', cxr, cyr + r2 + 30);

        var link = document.createElement('a');
        link.download = 'tic-' + ticMatch[1] + '-postcard.png';
        link.href = canvas2.toDataURL('image/png');
        link.click();
      });

      var rowEl = card.querySelector('.row');
      if (rowEl) rowEl.insertBefore(btn3, rowEl.firstChild);
      else card.insertBefore(btn3, card.firstChild);
    }
  });


  // ── Task 9: "My list" (favorites) ────────────────────────────────────────

  S.onRender(function (tab) {
    try {
      if (tab !== 'tess') return;
    } catch (_) {}
    var rows2 = document.querySelectorAll('#v-tess .card > div.row');
    for (var ri2 = 0; ri2 < rows2.length; ri2++) {
      var row3 = rows2[ri2];
      if (row3.querySelector('.sl-star-link')) continue;
      var ticMatch2 = row3.closest('.card') && row3.closest('.card').id.match(/tic-(\d+)/);
      if (!ticMatch2) continue;

      var starEl = document.createElement('span');
      starEl.className = 'sl-star-link';
      starEl.textContent = '\u2606';
      starEl.title = 'Add to My list';

      var favs = SL.get('sl_favs', []);
      if (favs.indexOf(ticMatch2[1]) >= 0) { starEl.textContent = '\u2605'; starEl.classList.add('on'); }

      starEl.addEventListener('click', function () {
        var idx2 = favs.indexOf(ticMatch2[1]);
        if (idx2 >= 0) { favs.splice(idx2, 1); starEl.textContent = '\u2606'; starEl.classList.remove('on'); starEl.title = 'Add to My list'; }
        else { favs.push(ticMatch2[1]); starEl.textContent = '\u2605'; starEl.classList.add('on'); starEl.title = 'Remove from My list'; }
        SL.set('sl_favs', favs);
      });
      row3.insertBefore(starEl, row3.firstChild);
    }

    // Also add "My list" tab button that shows favorites.
    if (tab === 'tess' && !document.querySelector('#sl-mylist-tab')) {
      var mylistBtn = document.createElement('button');
      mylistBtn.id = 'sl-mylist-tab';
      mylistBtn.className = 'b';
      mylistBtn.textContent = '\u2605 My list (' + favs.length + ')';
      mylistBtn.title = 'Show your saved candidates.';
      mylistBtn.addEventListener('click', function () {
        var favList = SL.get('sl_favs', []);
        if (!favList.length) { alert('Your "My list" is empty. Click the \u2606 on any candidate to save it.'); return; }
        var cands3 = S.data && S.data.tess && S.data.tess.cands || [];
        var savedText = favList.slice(0, 10).map(function (tic) {
          var found = null;
          for (var fi2 = 0; fi2 < cands3.length; fi2++) {
            if (String(cands3[fi2].tic) === tic) { found = cands3[fi2]; break; }
          }
          return 'TIC ' + tic + (found ? ' (' + S.num(found.rp, 1) + ' R\u2295)' : '');
        }).join('\n');
        alert('Your saved candidates:\n\n' + savedText);
      });
      var nav2 = document.querySelector('nav.tabs');
      if (nav2) nav2.insertBefore(mylistBtn, nav2.lastChild);
    }
  });


  // ── Task 10: "How we checked this signal" walkthrough ────────────────────

  S.onRender(function (tab) {
    try {
      if (tab !== 'tess') return;
    } catch (_) {}
    var cards2 = document.querySelectorAll('#v-tess .card');
    for (var ci4 = 0; ci4 < cards2.length; ci4++) {
      var card3 = cards2[ci4];
      if (card3.querySelector('.sl-walk-btn')) continue;
      var ticMatch3 = card3.id.match(/tic-(\d+)/);
      if (!ticMatch3) continue;

      var walkBtn = document.createElement('button');
      walkBtn.className = 'b sl-walk-btn';
      walkBtn.textContent = '\uD83D\uDD0D Walk me through it';
      walkBtn.title = 'See how we checked this signal, step by step.';
      walkBtn.addEventListener('click', function () {
        var bodyEl = modal('<div class="sl-step"><h3>Step 1/5: The Light Curve</h3><p>We downloaded the star\'s brightness over time from NASA\'s TESS archive. Each dot is the average brightness over 30 minutes.</p></div>' +
          '<div class="sl-step"><h3>Step 2/5: The Period Search</h3><p>A computer slides a box-shaped dip across every possible period (0.5 to 20 days). The one with the deepest, most consistent signal wins.</p></div>' +
          '<div class="sl-step"><h3>Step 3/5: The Folded Dip</h3><p>We lined up every transit on top of each other. A real planet gives a clean U-shape; noise is random.</p></div>' +
          '<div class="sl-step"><h3>Step 4/5: Odd vs Even Dips</h3><p>If odd and even dips have very different depths, the star might be an eclipsing binary at twice the period.</p></div>' +
          '<div class="sl-step"><h3>Step 5/5: The Centroid Test</h3><p>We checked if the dip signal comes from the target star or a nearby impostor. Shifts toward another star suggest a false positive.</p></div>');
      });
      var rowEl2 = card3.querySelector('.row');
      if (rowEl2) rowEl2.insertBefore(walkBtn, rowEl2.firstChild);
    }
  });


  // ── Task 11: Personal badges (localStorage only) ────────────────────────

  function _checkBadges() {
    var badges = SL.get('sl_badges', {});
    var reviews2 = SL.get('sl_reviews', []);
    var astrReviews = SL.get('sl_astr_reviews', []);
    var changed = false;

    if (!badges['first_review'] && reviews2.length >= 1) { badges['first_review'] = Date.now(); changed = true; }
    if (!badges['ten_reviews'] && reviews2.length >= 10) { badges['ten_reviews'] = Date.now(); changed = true; }
    if (!badges['reviewer'] && reviews2.length >= 25) { badges['reviewer'] = Date.now(); changed = true; }
    if (!badges['astr_reviewer'] && astrReviews.length >= 1) { badges['astr_reviewer'] = Date.now(); changed = true; }
    if (!badges['practice_found'] && SL.get('sl_practice_found', 0) >= 1) { badges['practice_found'] = Date.now(); changed = true; }

    if (changed) SL.set('sl_badges', badges);
    return badges;
  }

  // Show badge counter in the page header next to tabs.
  (function initBadges() {
    var badges2 = _checkBadges();
    var earnedCount = Object.keys(badges2).filter(function (k) { return k !== 'first_review' && k !== 'ten_reviews' && k !== 'reviewer' && k !== 'astr_reviewer' && k !== 'practice_found'; }).length;
    // Just the earned-count badges for now.
    var totalEarned = 0;
    for (var bk in badges2) { if (badges2[bk]) totalEarned++; }

    if (totalEarned > 0) {
      var nav3 = document.querySelector('nav.tabs');
      if (nav3) {
        var badgeDot = document.createElement('span');
        badgeDot.className = 'n';
        badgeDot.textContent = '\uD83C\uDF96 \u00D7 ' + totalEarned;
        nav3.insertBefore(badgeDot, nav3.lastChild);
      }
    }
  })();

})();
