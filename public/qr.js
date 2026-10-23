/* QR codes for meeting join links, drawn as SVG. No library: the server is
   self-hosted and the phones in the room may have no internet.

   Byte mode, error correction level M, versions 1-10 (up to 213 bytes, far
   more than a join link). Follows ISO/IEC 18004; tests/qr_test.py decodes
   every version and mask with OpenCV's QR reader.

   VMQR.svg(text)            -> '<svg ...>' string
   VMQR.matrix(text, mask?)  -> {size, version, mask, modules[row][col]} */
(function (root) {
  'use strict';
  // Level M: [total codewords, EC codewords per block, [[blocks, data codewords each], ...]]
  var M = [null,
    [26, 10, [[1, 16]]], [44, 16, [[1, 28]]], [70, 26, [[1, 44]]], [100, 18, [[2, 32]]],
    [134, 24, [[2, 43]]], [172, 16, [[4, 27]]], [196, 18, [[4, 31]]],
    [242, 22, [[2, 38], [2, 39]]], [292, 22, [[3, 36], [2, 37]]], [346, 26, [[4, 43], [1, 44]]]];
  var ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34],
    [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]];

  // GF(256), polynomial 0x11D
  var EXP = new Array(512), LOG = new Array(256);
  (function () { var x = 1; for (var i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 256) x ^= 0x11D; }
    for (i = 255; i < 512; i++) EXP[i] = EXP[i - 255]; })();
  function mul(a, b) { return a && b ? EXP[LOG[a] + LOG[b]] : 0; }
  function rsGenerator(n) {
    var g = [1];
    for (var i = 0; i < n; i++) {
      var next = new Array(g.length + 1).fill(0);
      for (var j = 0; j < g.length; j++) { next[j] ^= g[j]; next[j + 1] ^= mul(g[j], EXP[i]); }
      g = next;
    }
    return g;     // g[0] = 1, highest degree first
  }
  function rsRemainder(data, n) {
    var g = rsGenerator(n), r = data.concat(new Array(n).fill(0));
    for (var i = 0; i < data.length; i++) {
      var c = r[i];
      if (c) for (var j = 1; j < g.length; j++) r[i + j] ^= mul(g[j], c);
    }
    return r.slice(data.length);
  }

  function utf8(s) {
    var out = [], b = unescape(encodeURIComponent(s));
    for (var i = 0; i < b.length; i++) out.push(b.charCodeAt(i));
    return out;
  }

  function dataCodewords(v) { return M[v][2].reduce(function (s, g) { return s + g[0] * g[1]; }, 0); }

  function encode(bytes) {
    for (var v = 1; v <= 10; v++) {
      var cc = v < 10 ? 8 : 16, cap = dataCodewords(v) * 8;
      if (4 + cc + bytes.length * 8 <= cap) break;
    }
    if (v > 10) throw new Error('QR: text too long');
    var bits = [];
    function put(val, n) { for (var i = n - 1; i >= 0; i--) bits.push((val >>> i) & 1); }
    put(4, 4); put(bytes.length, cc);
    bytes.forEach(function (b) { put(b, 8); });
    put(0, Math.min(4, cap - bits.length));
    while (bits.length % 8) bits.push(0);
    var words = [];
    for (var i = 0; i < bits.length; i += 8) {
      var w = 0; for (var j = 0; j < 8; j++) w = (w << 1) | bits[i + j];
      words.push(w);
    }
    for (var pad = 0xEC; words.length < cap / 8; pad ^= 0xEC ^ 0x11) words.push(pad);
    // Split into blocks, add error correction, interleave.
    var ecn = M[v][1], blocks = [], k = 0;
    M[v][2].forEach(function (g) { for (var b = 0; b < g[0]; b++) { blocks.push(words.slice(k, k + g[1])); k += g[1]; } });
    var ecs = blocks.map(function (b) { return rsRemainder(b, ecn); });
    var out = [], maxLen = Math.max.apply(null, blocks.map(function (b) { return b.length; }));
    for (i = 0; i < maxLen; i++) blocks.forEach(function (b) { if (i < b.length) out.push(b[i]); });
    for (i = 0; i < ecn; i++) ecs.forEach(function (e) { out.push(e[i]); });
    return {version: v, codewords: out};
  }

  function build(version, codewords, mask) {
    var size = 17 + 4 * version, mod = [], fn = [];
    for (var y = 0; y < size; y++) { mod.push(new Array(size).fill(false)); fn.push(new Array(size).fill(false)); }
    function set(x, y, dark) { mod[y][x] = dark; fn[y][x] = true; }
    for (var i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
    [[3, 3], [size - 4, 3], [3, size - 4]].forEach(function (c) {
      for (var dy = -4; dy <= 4; dy++) for (var dx = -4; dx <= 4; dx++) {
        var xx = c[0] + dx, yy = c[1] + dy, d = Math.max(Math.abs(dx), Math.abs(dy));
        if (xx >= 0 && xx < size && yy >= 0 && yy < size) set(xx, yy, d !== 2 && d !== 4);
      }
    });
    var al = ALIGN[version], last = al.length - 1;
    for (i = 0; i < al.length; i++) for (var j = 0; j < al.length; j++) {
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) continue;
      for (var ay = -2; ay <= 2; ay++) for (var ax = -2; ax <= 2; ax++)
        set(al[i] + ax, al[j] + ay, Math.max(Math.abs(ax), Math.abs(ay)) !== 1);
    }
    drawFormat(0);
    if (version >= 7) {
      var rem = version;
      for (i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25);
      var vb = (version << 12) | rem;
      for (i = 0; i < 18; i++) {
        var bit = ((vb >>> i) & 1) === 1, a = size - 11 + i % 3, b = Math.floor(i / 3);
        set(a, b, bit); set(b, a, bit);
      }
    }
    function drawFormat(m) {
      var data = (0 << 3) | m, r = data;        // level M = 00
      for (var i = 0; i < 10; i++) r = (r << 1) ^ ((r >>> 9) * 0x537);
      var bits = ((data << 10) | r) ^ 0x5412;
      function g(i) { return ((bits >>> i) & 1) === 1; }
      for (i = 0; i <= 5; i++) set(8, i, g(i));
      set(8, 7, g(6)); set(8, 8, g(7)); set(7, 8, g(8));
      for (i = 9; i < 15; i++) set(14 - i, 8, g(i));
      for (i = 0; i < 8; i++) set(size - 1 - i, 8, g(i));
      for (i = 8; i < 15; i++) set(8, size - 15 + i, g(i));
      set(8, size - 8, true);
    }
    // Data, in the two-column zigzag from the bottom right.
    var n = 0, total = codewords.length * 8;
    for (var right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (var vert = 0; vert < size; vert++) for (j = 0; j < 2; j++) {
        var x = right - j, up = ((right + 1) & 2) === 0, yy2 = up ? size - 1 - vert : vert;
        if (!fn[yy2][x] && n < total) { mod[yy2][x] = ((codewords[n >>> 3] >>> (7 - (n & 7))) & 1) === 1; n++; }
      }
    }
    var masks = [
      function (x, y) { return (x + y) % 2 === 0; }, function (x, y) { return y % 2 === 0; },
      function (x) { return x % 3 === 0; }, function (x, y) { return (x + y) % 3 === 0; },
      function (x, y) { return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; },
      function (x, y) { return x * y % 2 + x * y % 3 === 0; },
      function (x, y) { return (x * y % 2 + x * y % 3) % 2 === 0; },
      function (x, y) { return ((x + y) % 2 + x * y % 3) % 2 === 0; }];
    function apply(m) {
      for (var y = 0; y < size; y++) for (var x = 0; x < size; x++)
        if (!fn[y][x] && masks[m](x, y)) mod[y][x] = !mod[y][x];
    }
    if (mask === undefined || mask === null) {
      var best = 0, bestScore = Infinity;
      for (var m = 0; m < 8; m++) {
        apply(m); drawFormat(m);
        var s = penalty(mod, size);
        if (s < bestScore) { best = m; bestScore = s; }
        apply(m);
      }
      mask = best;
    }
    apply(mask); drawFormat(mask);
    return {size: size, version: version, mask: mask, modules: mod};
  }

  function penalty(mod, size) {
    var score = 0, x, y, dark = 0;
    function line(get) {
      var s = 0, run = 1, seq = [];
      for (var i = 0; i < size; i++) {
        var c = get(i);
        seq.push(c ? 1 : 0);
        if (i > 0) {
          if (c === get(i - 1)) { run++; if (run === 5) s += 3; else if (run > 5) s += 1; }
          else run = 1;
        }
      }
      // finder-like 1011101 with four light modules on either side
      var str = '0000' + seq.join('') + '0000';
      for (var k = 0; k + 11 <= str.length; k++) {
        var w = str.substr(k, 11);
        if (w === '00001011101' || w === '10111010000') s += 40;
      }
      return s;
    }
    for (y = 0; y < size; y++) score += line(function (i) { return mod[y][i]; });
    for (x = 0; x < size; x++) score += line(function (i) { return mod[i][x]; });
    for (y = 0; y < size - 1; y++) for (x = 0; x < size - 1; x++) {
      var c = mod[y][x];
      if (c === mod[y][x + 1] && c === mod[y + 1][x] && c === mod[y + 1][x + 1]) score += 3;
    }
    for (y = 0; y < size; y++) for (x = 0; x < size; x++) if (mod[y][x]) dark++;
    var k = Math.ceil(Math.abs(dark * 20 - size * size * 10) / (size * size)) - 1;
    return score + Math.max(0, k) * 10;
  }

  function matrix(text, mask) {
    var e = encode(utf8(text));
    return build(e.version, e.codewords, mask);
  }

  function svg(text, opts) {
    opts = opts || {};
    var q = matrix(text), border = 4, n = q.size + border * 2, d = '';
    for (var y = 0; y < q.size; y++) for (var x = 0; x < q.size; x++)
      if (q.modules[y][x]) d += 'M' + (x + border) + ',' + (y + border) + 'h1v1h-1z';
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + n + ' ' + n + '"' +
      (opts.size ? ' width="' + opts.size + '" height="' + opts.size + '"' : '') +
      ' shape-rendering="crispEdges" role="img" aria-label="QR code">' +
      '<rect width="100%" height="100%" fill="#fff"/><path d="' + d + '" fill="#000"/></svg>';
  }

  var api = {matrix: matrix, svg: svg};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.VMQR = api;
})(this);
