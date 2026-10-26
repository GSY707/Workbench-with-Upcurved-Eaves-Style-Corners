'use strict';

const assert = require('node:assert/strict');
const { bezierControlsFromSegment } = require('../layout.js');

function evaluate(coefficients, dx) {
  const [a, b, c, d] = coefficients;
  return ((a * dx + b) * dx + c) * dx + d;
}

function evaluateBezier(points, t) {
  const [p0, p1, p2, p3] = points;
  const u = 1 - t;
  const weights = [u ** 3, 3 * u ** 2 * t, 3 * u * t ** 2, t ** 3];
  return [0, 1].map(axis => points.reduce((sum, point, index) => sum + weights[index] * point[axis], 0));
}

const oldFitShape = {
  knots_x_mm: [-7.25, -4.1, 2.65, 18.4],
  segments: [
    { coeffs_descending_in_dx_mm: [0.013, -0.11, 0.42, 7.3] },
    { coeffs_descending_in_dx_mm: [-0.004, 0.085, -1.25, 8.1] },
    { coeffs_descending_in_dx_mm: [0.0007, -0.021, 0.34, -2.6] }
  ]
};

let samples = 0;
for (let segmentIndex = 0; segmentIndex < oldFitShape.segments.length; segmentIndex += 1) {
  const x0 = oldFitShape.knots_x_mm[segmentIndex];
  const x1 = oldFitShape.knots_x_mm[segmentIndex + 1];
  const h = x1 - x0;
  const coefficients = oldFitShape.segments[segmentIndex].coeffs_descending_in_dx_mm;
  const y0 = evaluate(coefficients, 0);
  const y1 = evaluate(coefficients, h);
  const controls = bezierControlsFromSegment(x0, x1, y0, y1, coefficients);

  assert.deepEqual(controls[0], [x0, y0]);
  assert.deepEqual(controls[3], [x1, y1]);
  for (const t of [0, 0.03, 0.17, 0.4, 0.63, 0.91, 1]) {
    const actual = evaluateBezier(controls, t);
    const dx = h * t;
    const expected = [x0 + dx, evaluate(coefficients, dx)];
    for (let axis = 0; axis < 2; axis += 1) {
      const tolerance = 2e-10 * Math.max(1, Math.abs(expected[axis]));
      assert.ok(Math.abs(actual[axis] - expected[axis]) <= tolerance,
        `segment ${segmentIndex + 1}, t=${t}, axis=${axis}: ${actual[axis]} != ${expected[axis]}`);
    }
    samples += 1;
  }
}

assert.throws(() => bezierControlsFromSegment(0, 0, 1, 1, [0, 0, 0, 1]), /无效/);
assert.throws(() => bezierControlsFromSegment(0, 1, 1, 2, [0, 0, Infinity, 1]), /无效/);
console.log(`Bezier 与旧格式分段三次多项式一致：${samples} 个采样点通过。`);
