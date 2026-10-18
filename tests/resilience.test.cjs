'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { resolve } = require('node:path');
const p = require('../prototype/script.js');
const stamp = '2026-10-20T09:00:00.000Z';
const empty = () => ({ schemaVersion: 1, tasks: [] });
const fields = title => ({ title, dueDate: '', dueTime: '' });

function fixture(raw = null) {
  const store = { raw, writes: 0, denyRead: false, denyWrite: false };
  const repo = p.createRepository(() => ({
    getItem(key) { assert.equal(key, p.STORAGE_KEY); if (store.denyRead) throw new Error(); return store.raw; },
    setItem(key, value) {
      assert.equal(key, p.STORAGE_KEY);
      if (store.denyWrite) throw Object.assign(new Error(), { name: 'QuotaExceededError' });
      store.writes++;
      store.raw = value;
    }
  }));
  return { store, repo };
}

test('AT-29: stale writer cannot overwrite an external addition; conflict lasts until explicit reload', () => {
  const { store, repo } = fixture();
  const before = repo.load();
  const candidate = p.addTask(before, fields('Моя задача'), 'a', stamp);
  const external = p.addTask(empty(), fields('Другая вкладка'), 'b', stamp);
  store.raw = JSON.stringify(external);
  assert.throws(() => repo.commit(candidate), error => error.code === 'conflict');
  assert.equal(repo.getState(), before);
  assert.deepEqual(JSON.parse(store.raw), external);
  assert.equal(store.writes, 0);
  store.raw = null;
  assert.throws(() => repo.assertCurrent(), error => error.code === 'conflict');
  repo.load();
  assert.doesNotThrow(() => repo.assertCurrent());
});

test('AT-29: removal of existing key is a conflict, not permission to recreate old data', () => {
  const data = p.addTask(empty(), fields('Сохранено'), 'a', stamp);
  const { store, repo } = fixture(JSON.stringify(data));
  repo.load();
  store.raw = null;
  assert.throws(() => repo.commit(p.toggleTask(data, 'a', stamp)), error => error.code === 'conflict');
  assert.equal(store.raw, null);
  assert.equal(store.writes, 0);
});

test('AT-29: successful commits advance the exact comparison snapshot', () => {
  const { store, repo } = fixture();
  repo.load();
  repo.commit(p.addTask(repo.getState(), fields('Первая'), 'a', stamp));
  repo.commit(p.addTask(repo.getState(), fields('Вторая'), 'b', stamp));
  assert.equal(store.writes, 2);
  assert.doesNotThrow(() => repo.assertCurrent());
  assert.equal(repo.getState().tasks.length, 2);
});

test('AT-29: comparison uses original raw string, including whitespace', () => {
  const compact = JSON.stringify(empty());
  const { store, repo } = fixture(compact);
  repo.load();
  store.raw = ' ' + compact;
  assert.throws(() => repo.assertCurrent(), error => error.code === 'conflict');
});

test('AT-26/27: failed preflight read never publishes a candidate or writes', () => {
  const { store, repo } = fixture();
  const before = repo.load();
  store.denyRead = true;
  assert.throws(() => repo.commit(p.addTask(before, fields('Новая'), 'a', stamp)), error => error.code === 'read');
  assert.equal(repo.getState(), before);
  assert.equal(store.writes, 0);
  store.denyRead = false;
  assert.doesNotThrow(() => repo.commit(p.addTask(before, fields('Повтор'), 'b', stamp)));
});

test('AT-27: failed write does not advance comparison snapshot, retry succeeds', () => {
  const { store, repo } = fixture();
  const before = repo.load();
  const next = p.addTask(before, fields('Повтор'), 'a', stamp);
  store.denyWrite = true;
  assert.throws(() => repo.commit(next), error => error.code === 'quota');
  assert.equal(repo.getState(), before);
  assert.equal(store.raw, null);
  store.denyWrite = false;
  repo.commit(next);
  assert.equal(store.writes, 1);
});

test('AT-28/39: exact unreadable JSON/version/fields are retained for recovery', () => {
  for (const raw of ['', '  broken\n', '{"schemaVersion":2,"tasks":[]}',
    '{"schemaVersion":1,"tasks":[],"unknown":true}']) {
    const { store, repo } = fixture(raw);
    assert.throws(() => repo.load());
    assert.equal(repo.getRecoveryRaw(), raw);
    assert.equal(store.raw, raw);
    assert.equal(store.writes, 0);
    assert.throws(() => repo.commit(empty()), error => error.code === 'read');
  }
});

test('AT-26/39: failed read cannot offer a stale recovery snapshot', () => {
  const { store, repo } = fixture('broken');
  assert.throws(() => repo.load());
  assert.equal(repo.getRecoveryRaw(), 'broken');
  store.denyRead = true;
  assert.throws(() => repo.load(), error => error.code === 'read');
  assert.equal(repo.getRecoveryRaw(), null);
});

test('AT-28: every record validated, duplicate id/title and unknown fields invalidate entire snapshot', () => {
  const first = p.addTask(empty(), fields('Задача'), 'a', stamp).tasks[0];
  for (const tasks of [
    [first, { ...first, id: 'b', dueDate: '2026-02-29' }],
    [first, { ...first, title: 'Другая' }],
    [first, { ...first, id: 'b', completed: true }],
    [{ ...first, futureField: 'keep-me' }]
  ]) {
    const raw = JSON.stringify({ schemaVersion: 1, tasks });
    const { repo, store } = fixture(raw);
    assert.throws(() => repo.load(), error => error.code === 'data');
    assert.equal(store.raw, raw);
    assert.equal(repo.getRecoveryRaw(), raw);
    assert.equal(store.writes, 0);
  }
});

test('AT-23: minute scheduler is aligned, does not spin at exact boundaries', () => {
  for (const [value, expected] of [[0, 60000], [1, 59999], [59999, 1], [60000, 60000], [-1, 1]]) {
    assert.equal(p.millisecondsToNextMinute(value), expected);
  }
});

test('AT-23/25: day-only task changes at midnight, month/year and leap day boundaries', () => {
  for (const [year, month, day, due, tomorrow] of [
    [2026, 11, 31, '2026-12-31', '2027-01-01'],
    [2026, 0, 31, '2026-01-31', '2026-02-01'],
    [2024, 1, 28, '2024-02-28', '2024-02-29']
  ]) {
    const task = { id: 'a', title: 'Дело', dueDate: due, dueTime: null, completed: false, createdAt: stamp, updatedAt: stamp };
    assert.equal(p.isOverdue(task, new Date(year, month, day, 23, 59)), false);
    assert.equal(p.isOverdue(task, new Date(year, month, day + 1, 0, 0)), true);
    const next = { ...task, dueDate: tomorrow };
    assert.match(p.groupTasks([next], new Date(year, month, day, 12)).groups[0].label, /^Завтра · /);
  }
});

test('AT-25: tomorrow follows calendar over 23/25 hour DST days', () => {
  const modulePath = resolve(__dirname, '../prototype/script.js');
  for (const [month, day, dueDate] of [[2, 28, '2026-03-29'], [9, 24, '2026-10-25']]) {
    const script = `const p=require(${JSON.stringify(modulePath)}); const t={id:'a',title:'Дело',dueDate:${JSON.stringify(dueDate)},dueTime:null,completed:false,createdAt:'${stamp}',updatedAt:'${stamp}'}; console.log(p.groupTasks([t],new Date(2026,${month},${day},12)).groups[0].label);`;
    const output = execFileSync(process.execPath, ['-e', script], { env: { ...process.env, TZ: 'Europe/Berlin' }, encoding: 'utf8' });
    assert.match(output, /^Завтра · /);
  }
});
