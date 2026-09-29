/* ============================================================================
   Cloud HDR — живой фон: анимированный mesh-градиент (как «Моя волна»).

   Пять цветовых пятен из палитры темы плывут и перетекают друг в друга;
   пространство под ними слегка «течёт», поэтому границ и форм не видно.
   Пока помощник думает или говорит, волна оживает: быстрее и ярче.

   Дёшево: считается в 1/6 размера окна (градиент плавный — разницы не
   видно, браузер растягивает сам), не чаще 30 кадров в секунду, в свёрнутом
   окне стоит. Анимации выключены — один неподвижный кадр. Нет WebGL — на
   месте остаётся прежний мягкий свет из clean.css.
   ========================================================================== */
(function () {
  'use strict';

  const canvas = document.getElementById('mesh');
  const backdrop = canvas && canvas.parentElement;
  if (!canvas) return;

  const SCALE = 1 / 6;
  const FRAME_MS = 1000 / 30;

  const VERT = `
    attribute vec2 p;
    void main() { gl_Position = vec4(p, 0.0, 1.0); }`;

  const FRAG = `
    precision mediump float;
    uniform vec2 res;
    uniform float t;
    uniform float energy;
    uniform float amount;
    uniform vec3 bg;
    uniform vec3 c0, c1, c2, c3, c4;

    // Течение: пространство изгибается двумя слоями волн, и круглые пятна
    // становятся мягкими лентами, как в «Моей волне».
    vec2 flow(vec2 q, float s) {
      q += 0.28 * vec2(sin(q.y * 2.1 + s * 3.1 + sin(q.x * 1.7 - s * 2.0)),
                       cos(q.x * 1.9 - s * 2.7 + cos(q.y * 1.3 + s * 1.9)));
      q += 0.10 * vec2(sin(q.y * 4.3 - s * 4.0), cos(q.x * 3.7 + s * 3.3));
      return q;
    }

    vec2 orbit(float a, float b, float ph, float s, vec2 span) {
      return span * vec2(sin(s * a + ph), cos(s * b + ph * 1.7));
    }

    void main() {
      vec2 uv = gl_FragCoord.xy / res;
      float aspect = res.x / res.y;
      float s = t * 0.07;
      vec2 q = flow((uv - 0.5) * vec2(aspect, 1.0), s);
      vec2 span = vec2(aspect * 0.42, 0.40) * (1.0 + 0.10 * energy);

      vec2 p0 = orbit(0.83, 0.61, 0.0, s, span);
      vec2 p1 = orbit(0.57, 0.97, 2.1, s, span);
      vec2 p2 = orbit(1.11, 0.73, 4.2, s, span);
      vec2 p3 = orbit(0.69, 1.23, 1.3, s, span);
      vec2 p4 = orbit(0.91, 0.52, 5.5, s, span);

      // Цвет — смесь по близости к пятнам (сама «сетка»), плотность — по
      // сумме гауссиан: между пятнами проглядывает фон, текст остаётся читаемым.
      float w0 = exp(-dot(q - p0, q - p0) * 2.4);
      float w1 = exp(-dot(q - p1, q - p1) * 2.8);
      float w2 = exp(-dot(q - p2, q - p2) * 2.2);
      float w3 = exp(-dot(q - p3, q - p3) * 3.0);
      float w4 = exp(-dot(q - p4, q - p4) * 2.6);
      float sum = w0 + w1 + w2 + w3 + w4;
      // Степень делает зоны отчётливыми: у каждого пятна свой цвет, мягкие
      // только переходы. Без неё крупные пятна усреднялись в один оттенок.
      vec4 k = pow(vec4(w0, w1, w2, w3), vec4(4.0));
      float k4 = pow(w4, 4.0);
      vec3 mesh = (c0 * k.x + c1 * k.y + c2 * k.z + c3 * k.w + c4 * k4) / max(k.x + k.y + k.z + k.w + k4, 1e-6);

      float cover = clamp(sum * 1.3, 0.0, 1.0) * amount * (0.85 + 0.35 * energy);
      float vign = smoothstep(1.25, 0.25, length((uv - 0.5) * vec2(aspect * 0.8, 1.0)));
      gl_FragColor = vec4(mix(bg, mesh, clamp(cover * vign, 0.0, 1.0)), 1.0);
    }`;

  let gl = null;
  let program = null;
  let loc = {};
  let running = false;
  let loop = 0;
  let last = 0;
  let clock = 0;
  let energy = 0;
  let palette = null;

  /* ---------------------------------------------------------------- цвета -- */

  const probe = document.createElement('canvas').getContext('2d');
  function rgb(value) {
    probe.fillStyle = '#000';
    probe.fillStyle = (value || '').trim() || '#000';
    const hex = probe.fillStyle;                     // браузер приводит к #rrggbb
    if (hex[0] === '#') return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const m = hex.match(/[\d.]+/g) || [0, 0, 0];
    return m.slice(0, 3).map((v) => Number(v) / 255);
  }
  const mix = (a, b, k) => a.map((v, i) => v + (b[i] - v) * k);

  function readPalette() {
    const css = getComputedStyle(document.documentElement);
    const light = document.documentElement.dataset.theme === 'light' ||
      (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: light)').matches);
    const bg = rgb(css.getPropertyValue('--bg'));
    const a1 = rgb(css.getPropertyValue('--a1'));
    const a2 = rgb(css.getPropertyValue('--a2'));
    const a3 = rgb(css.getPropertyValue('--a3'));
    const sand = rgb('#EACB98');
    return {
      bg,
      // В тёмной теме пятна глубже (закат), в светлой — мягче и светлее.
      colors: light
        ? [a1, a2, rgb('#E0703A'), a3, sand]
        : [a1, rgb('#C2410C'), a3, rgb('#7C2D12'), sand],
      amount: light ? 0.62 : 0.72
    };
  }

  /* ---------------------------------------------------------------- WebGL -- */

  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  }

  function setup() {
    gl = canvas.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false,
                                      powerPreference: 'low-power', preserveDrawingBuffer: false });
    if (!gl) return false;
    program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const p = gl.getAttribLocation(program, 'p');
    gl.enableVertexAttribArray(p);
    gl.vertexAttribPointer(p, 2, gl.FLOAT, false, 0, 0);

    loc = {};
    for (const name of ['res', 't', 'energy', 'amount', 'bg', 'c0', 'c1', 'c2', 'c3', 'c4']) {
      loc[name] = gl.getUniformLocation(program, name);
    }
    applyPalette();
    resize();
    return true;
  }

  function applyPalette() {
    palette = readPalette();
    if (!gl) return;
    gl.uniform3fv(loc.bg, palette.bg);
    palette.colors.forEach((c, i) => gl.uniform3fv(loc['c' + i], c));
    gl.uniform1f(loc.amount, palette.amount);
  }

  function resize() {
    const w = Math.max(64, Math.round(innerWidth * SCALE));
    const h = Math.max(48, Math.round(innerHeight * SCALE));
    if (canvas.width === w && canvas.height === h) return;
    canvas.width = w;
    canvas.height = h;
    if (gl) {
      gl.viewport(0, 0, w, h);
      gl.uniform2f(loc.res, w, h);
    }
  }

  function draw() {
    gl.uniform1f(loc.t, clock);
    gl.uniform1f(loc.energy, energy);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  /* ---------------------------------------------------------------- цикл --- */

  const still = () => document.documentElement.dataset.motion === 'off' ||
    matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Помощник думает или говорит — волна оживает.
  const lively = () => (window.CloudChat && CloudChat.busy) ||
    document.documentElement.dataset.speaking === 'true';

  function frame(now, id) {
    if (!running || id !== loop) return;              // цикл заменён новым
    requestAnimationFrame((ts) => frame(ts, id));
    if (now - last < FRAME_MS) return;
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    energy += ((lively() ? 1 : 0) - energy) * Math.min(dt * 1.6, 1);
    clock += dt * (1 + 1.6 * energy);
    draw();
  }

  function start() {
    if (!gl) return;
    if (still() || document.hidden) {
      running = false;
      draw();                                         // один неподвижный кадр
      return;
    }
    if (running) return;
    running = true;
    last = performance.now();
    const id = ++loop;
    requestAnimationFrame((ts) => frame(ts, id));
  }

  function init() {
    try {
      if (!setup()) return;
    } catch (error) {
      console.warn('Cloud HDR: живой фон недоступен —', error.message);
      gl = null;
      return;
    }
    clock = Math.random() * 60;                       // каждый запуск — своя картинка
    draw();
    backdrop.classList.add('has-mesh');
    requestAnimationFrame(() => canvas.classList.add('on'));
    start();
  }

  addEventListener('resize', () => { if (!gl) return; resize(); if (!running) draw(); });
  document.addEventListener('visibilitychange', () => { running = false; start(); });
  new MutationObserver(() => {
    if (!gl) return;
    applyPalette();
    running = false;
    start();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-motion'] });
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => { if (gl) { applyPalette(); if (!running) draw(); } });

  canvas.addEventListener('webglcontextlost', (event) => { event.preventDefault(); running = false; });
  canvas.addEventListener('webglcontextrestored', () => { gl = null; init(); });

  init();
})();
