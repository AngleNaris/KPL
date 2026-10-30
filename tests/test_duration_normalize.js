// 测试编辑器 app.js 中的时长规整：直接截取程序里的函数运行，不另抄一份实现。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'native-prototype', 'ui', 'app.js'), 'utf8');
const start = source.indexOf('  function seconds(shot) {');
const end = source.indexOf('  const round = value =>');
assert.ok(start > 0 && end > start, 'app.js 中找不到时长函数，测试需要随源码更新');
const {normalizeDurations, DURATION_PATTERN} = new Function(source.slice(start, end) +
  'return {normalizeDurations, DURATION_PATTERN};')();

const model = values => ({shots: values.map((duration_seconds, index) => ({id: 's' + index, duration_seconds}))});
const values = doc => doc.shots.map(shot => shot.duration_seconds);
const total = list => list.reduce((sum, value) => sum + (value.trim() ? Number(value) : 0), 0);

test('24 fps frame durations land on the 0.1 s grid without total drift', () => {
  // 34、19、16、31 帧 @24fps：逐镜四舍五入会累计漂移，累计切点取整不会
  const frames = [34, 19, 16, 31, 18, 12, 41];
  const doc = model(frames.map(f => String(f / 24)));
  const exact = frames.reduce((a, f) => a + f, 0) / 24;
  normalizeDurations(doc);
  for (const value of values(doc)) assert.match(value, DURATION_PATTERN, value);
  assert.ok(Math.abs(total(values(doc)) - Math.round(exact * 10) / 10) < 1e-9, '总长漂移：' + total(values(doc)));
});

test('cut points stay within 0.05 s of the exact cut', () => {
  const raw = ['1.4166666666666667', '0.791666666666666667', '1.208333333333333333', '0.6666666666666667', '2.25'];
  const doc = model(raw);
  normalizeDurations(doc);
  let exact = 0, rounded = 0;
  raw.forEach((value, index) => {
    exact += Number(value); rounded += Number(values(doc)[index]);
    assert.ok(Math.abs(rounded - exact) <= 0.05 + 1e-9, '切点 ' + index + ' 偏 ' + (rounded - exact));
  });
});

test('very short shots keep at least 0.1 s and later cuts absorb the difference', () => {
  const doc = model(['0.02', '0.03', '1']);
  normalizeDurations(doc);
  assert.deepEqual(values(doc), ['0.1', '0.1', '0.9']);
});

test('valid durations, empty and invalid values are left alone', () => {
  const doc = model(['2.5', '3', '', 'abc', '1.2']);
  assert.equal(normalizeDurations(doc), 0);
  assert.deepEqual(values(doc), ['2.5', '3', '', 'abc', '1.2']);
});

test('the count reports only changed shots and output never shows float noise', () => {
  const doc = model(['0.1', '0.2', '0.30000000000000004', '1.75']);
  assert.equal(normalizeDurations(doc), 2);
  for (const value of values(doc)) assert.ok(value.length <= 4, value);
});
