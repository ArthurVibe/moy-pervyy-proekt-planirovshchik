'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { resolve } = require('node:path');
const p = require('../prototype/script.js');

const NOW = '2026-10-20T09:00:00.000Z';
const empty = () => ({ schemaVersion: 1, tasks: [] });
const fields = (title, dueDate = '', dueTime = '') => ({ title, dueDate, dueTime });
const task = (id, overrides = {}) => ({ id, title: `Задача ${id}`, dueDate: null, dueTime: null,
  completed: false, createdAt: NOW, updatedAt: NOW, ...overrides });
const stateWith = (...tasks) => ({ schemaVersion: 1, tasks });
const localNoon = () => new Date(2026, 9, 20, 12, 0, 0);

test('AT-01: missing key is empty, read does not write or touch unrelated keys', () => {
  const calls = [];
  const repo = p.createRepository(() => ({ getItem: key => { calls.push(key); return null; },
    setItem: () => assert.fail('Unexpected write') }));
  assert.deepEqual(repo.load(), empty());
  assert.deepEqual(calls, ['whats-up-bro.storage']);
});

test('AT-02, AT-08: creation normalizes spaces without inventing a deadline', () => {
  const original = empty();
  const result = p.addTask(original, fields('  Купить\n  хлеб  '), 'one', NOW);
  assert.deepEqual(original, empty());
  assert.deepEqual(result.tasks, [task('one', { title: 'Купить хлеб' })]);
});

test('AT-03: date-only and timed task keep precisely the supplied fields', () => {
  const dated = p.addTask(empty(), fields('Дело', '2026-10-21'), 'a', NOW);
  assert.equal(dated.tasks[0].dueDate, '2026-10-21');
  assert.equal(dated.tasks[0].dueTime, null);
  assert.equal(p.addTask(empty(), fields('Звонок', '2026-10-21', '10:05'), 'b', NOW).tasks[0].dueTime, '10:05');
});

test('AT-10: blank titles are rejected', () => {
  for (const title of ['', ' ', '\t\n']) {
    assert.throws(() => p.addTask(empty(), fields(title), 'a', NOW), error => error.fields.title === 'Введите название задачи');
  }
});

test('AT-10: 200 code points accepted, 201 rejected, emoji is not two', () => {
  for (const character of ['я', '😀']) {
    assert.equal(p.validateFields(fields(character.repeat(200)), []).errors.title, undefined);
    assert.ok(p.validateFields(fields(character.repeat(201)), []).errors.title);
  }
});

test('AT-11: uniqueness ignores case and whitespace, not punctuation, ё or compatibility Unicode', () => {
  const tasks = [task('a', { title: 'Купить хлеб' })];
  assert.ok(p.validateFields(fields(' КУПИТЬ\t хлеб '), tasks).errors.title);
  assert.equal(p.validateFields(fields('Купить хлеб!'), tasks).errors.title, undefined);
  assert.notEqual(p.titleKey('всё'), p.titleKey('все'));
  assert.notEqual(p.titleKey('Ａ'), p.titleKey('A'));
});

test('AT-12: completed title remains reserved, deletion frees it', () => {
  const state = stateWith(task('a', { title: 'Купить хлеб', completed: true }));
  assert.match(p.validateFields(fields('купить хлеб'), state.tasks).errors.title, /среди выполненных/);
  assert.equal(p.validateFields(fields('купить хлеб'), p.removeTask(state, 'a').tasks).errors.title, undefined);
});

test('AT-13, AT-14: editing ignores own id but not other active/completed records', () => {
  const state = stateWith(task('a'), task('b', { completed: true }));
  assert.equal(p.editTask(state, 'a', fields('ЗАДАЧА A'), NOW).tasks[0].title, 'ЗАДАЧА A');
  assert.throws(() => p.editTask(state, 'a', fields('Задача b'), NOW), error => /среди выполненных/.test(error.fields.title));
});

test('AT-15: moving a completed task keeps identity, createdAt and status', () => {
  const state = stateWith(task('a', { dueDate: '2026-10-20', dueTime: '11:30', completed: true }));
  const later = '2026-10-21T08:00:00.000Z';
  const moved = p.editTask(state, 'a', fields('Задача a', '2026-10-23', '11:30'), later);
  assert.deepEqual(moved.tasks[0], { ...state.tasks[0], dueDate: '2026-10-23', updatedAt: later });
  assert.equal(state.tasks[0].dueDate, '2026-10-20');
});

test('AT-16: removing deadline clears both nullable fields, time alone invalid', () => {
  const state = stateWith(task('a', { dueDate: '2026-10-20', dueTime: '11:30' }));
  const updated = p.editTask(state, 'a', fields('Задача a'), NOW).tasks[0];
  assert.equal(updated.dueDate, null);
  assert.equal(updated.dueTime, null);
  assert.ok(p.validateFields(fields('Дело', '', '11:00'), []).errors.dueTime);
});

test('AT-18: no-op edit returns original state, preserves updatedAt', () => {
  const state = stateWith(task('a'));
  assert.equal(p.editTask(state, 'a', fields(' Задача   a '), '2026-10-21T00:00:00.000Z'), state);
});

test('AT-19, AT-20: toggle is reversible and updates only status/timestamp', () => {
  const state = stateWith(task('a', { dueDate: '2026-10-20', dueTime: '12:30' }));
  const updatedAt = '2026-10-20T12:00:00.000Z';
  const completed = p.toggleTask(state, 'a', updatedAt);
  assert.deepEqual(completed.tasks[0], { ...state.tasks[0], completed: true, updatedAt });
  assert.equal(p.toggleTask(completed, 'a', updatedAt).tasks[0].completed, false);
  assert.equal(state.tasks[0].completed, false);
});

test('AT-22: delete affects only selected id, never mutates source', () => {
  const state = stateWith(task('a'), task('b'));
  assert.deepEqual(p.removeTask(state, 'a').tasks, [state.tasks[1]]);
  assert.equal(state.tasks.length, 2);
  assert.throws(() => p.removeTask(state, 'missing'), error => error.code === 'missing');
});

test('AT-04, AT-05: deadline boundary, undated/completed and date-only overdue rules', () => {
  const now = localNoon();
  for (const [dueDate, dueTime, completed, expected] of [
    [null, null, false, false], ['2026-10-19', null, false, true],
    ['2026-10-20', null, false, false], ['2026-10-20', '11:59', false, true],
    ['2026-10-20', '12:00', false, true], ['2026-10-20', '12:01', false, false],
    ['2026-10-21', '00:00', false, false], ['2026-10-19', '00:00', true, false]
  ]) assert.equal(p.isOverdue(task('a', { dueDate, dueTime, completed }), now), expected);
});

test('AT-06, AT-07, AT-37: groups, chronological sorting, ties and weekdays', () => {
  const tasks = [task('undated'), task('future', { dueDate: '2026-10-22' }),
    task('tomorrow', { dueDate: '2026-10-21' }), task('no-time', { dueDate: '2026-10-20' }),
    task('late', { dueDate: '2026-10-20', dueTime: '14:00' }),
    task('early-b', { dueDate: '2026-10-20', dueTime: '13:00' }),
    task('early-a', { dueDate: '2026-10-20', dueTime: '13:00' }),
    task('today-overdue', { dueDate: '2026-10-20', dueTime: '12:00' }),
    task('yesterday', { dueDate: '2026-10-19' }), task('done', { completed: true })];
  const original = JSON.stringify(tasks);
  const view = p.groupTasks(tasks, localNoon());
  assert.deepEqual(view.groups.map(group => group.label), ['Просрочено', 'Сегодня · Вт, 20.10.2026',
    'Завтра · Ср, 21.10.2026', 'Чт, 22.10.2026', 'Без даты']);
  assert.deepEqual(view.groups[0].tasks.map(item => item.id), ['yesterday', 'today-overdue']);
  assert.deepEqual(view.groups[1].tasks.map(item => item.id), ['early-a', 'early-b', 'late', 'no-time']);
  assert.deepEqual(view.completed.map(item => item.id), ['done']);
  assert.equal(view.groups.flatMap(group => group.tasks).length + view.completed.length, tasks.length);
  assert.equal(JSON.stringify(tasks), original);
});

test('AT-07: createdAt tie-break and completed updatedAt sorting', () => {
  const older = '2026-10-19T00:00:00.000Z';
  const view = p.groupTasks([task('z', { createdAt: older }), task('a'),
    task('done-a', { completed: true, updatedAt: older }), task('done-b', { completed: true })], localNoon());
  assert.deepEqual(view.groups[0].tasks.map(item => item.id), ['z', 'a']);
  assert.deepEqual(view.completed.map(item => item.id), ['done-b', 'done-a']);
});

test('AT-40: calendar/date range includes leap days, rejects invalid and out-of-range dates', () => {
  for (const date of ['2000-01-01', '2099-12-31', '2024-02-29']) assert.equal(p.isDueDate(date), true, date);
  for (const date of ['1999-12-31', '2100-01-01', '2026-02-29', '2026-04-31', '2026-00-01', '2026-01-00', '2026-1-1', null]) {
    assert.equal(p.isDueDate(date), false, String(date));
    assert.ok(p.validateFields(fields('Дело', date ?? 'bad'), []).errors.dueDate);
  }
});

test('AT-40: exact minute format accepts bounds, rejects seconds and 24:00', () => {
  for (const value of ['00:00', '23:59', '12:30']) assert.equal(p.isTime(value), true);
  for (const value of ['24:00', '10:60', '9:00', '10:00:01', '', null]) assert.equal(p.isTime(value), false);
});

test('AT-09, AT-21: serialization round-trip preserves all nullable fields', () => {
  const state = stateWith(task('a'), task('b', { dueDate: '2026-10-22', dueTime: '23:59', completed: true }));
  assert.deepEqual(p.parseStorage(JSON.stringify(state)), state);
});

test('storage rejects malformed JSON, schema/version/fields and duplicate identities/titles', () => {
  const bad = [undefined, '', '{', 'null', '[]', '{"schemaVersion":1}',
    JSON.stringify({ ...empty(), extra: true }),
    JSON.stringify(stateWith({ ...task('a'), extra: true })),
    JSON.stringify(stateWith(task('a'), task('a', { title: 'Другое дело' }))),
    JSON.stringify(stateWith(task('a'), task('b', { title: 'ЗАДАЧА A', completed: true })))];
  for (const raw of bad) assert.throws(() => p.parseStorage(raw));
  assert.throws(() => p.parseStorage('{"schemaVersion":2,"tasks":[]}'), error => error.code === 'version');
});

test('storage rejects noncanonical titles, types, deadlines and timestamps', () => {
  for (const changes of [{ title: ' ' }, { title: ' Задача a' }, { title: 12 }, { id: '' },
    { completed: 'false' }, { dueTime: '10:00' }, { dueDate: '2026-02-29' },
    { createdAt: '2026-02-30T09:00:00.000Z' }, { updatedAt: 'bad' }, { dueDate: undefined }]) {
    assert.throws(() => p.validateState(stateWith(task('a', changes))));
  }
});

test('AT-41: moving system clock backwards does not corrupt metadata', () => {
  const state = stateWith(task('a'));
  const earlier = '2026-10-19T00:00:00.000Z';
  const changed = p.editTask(state, 'a', fields('Правка'), earlier);
  assert.equal(changed.tasks[0].updatedAt, earlier);
  assert.equal(changed.tasks[0].createdAt, NOW);
  assert.equal(p.validateState(changed), changed);
});

test('repository publishes candidate only after successful storage write', () => {
  let raw = null;
  let fail = false;
  const repo = p.createRepository(() => ({ getItem: () => raw,
    setItem(key, value) { if (fail) throw Object.assign(new Error(), { name: 'QuotaExceededError' }); raw = value; } }));
  const initial = repo.load();
  const candidate = p.addTask(initial, fields('Дело'), 'a', NOW);
  fail = true;
  assert.throws(() => repo.commit(candidate), error => error.code === 'quota');
  assert.equal(repo.getState(), initial);
  assert.equal(raw, null);
  fail = false;
  repo.commit(candidate);
  assert.equal(repo.getState(), candidate);
  assert.deepEqual(JSON.parse(raw), candidate);
});

test('read access and generic write failures are explicit and do not mutate confirmed state', () => {
  const denied = p.createRepository(() => { throw new Error('denied'); });
  assert.throws(() => denied.load(), error => error.code === 'read');
  assert.equal(denied.getState(), null);
  const repo = p.createRepository(() => ({ getItem: () => null, setItem: () => { throw new Error('denied'); } }));
  const state = repo.load();
  assert.throws(() => repo.commit(p.addTask(state, fields('Дело'), 'a', NOW)), error => error.code === 'write');
  assert.equal(repo.getState(), state);
});

test('invalid candidate is not written', () => {
  let writes = 0;
  const repo = p.createRepository(() => ({ getItem: () => null, setItem: () => { writes++; } }));
  repo.load();
  assert.throws(() => repo.commit(stateWith(task('a', { dueDate: '2100-01-01' }))));
  assert.equal(writes, 0);
});

test('UUID has a secure fallback and an explicit unsupported failure', () => {
  assert.equal(p.createId({ randomUUID: () => 'native-uuid' }), 'native-uuid');
  assert.match(p.createId({ getRandomValues: array => array.fill(0) }), /^00000000-0000-4000-8000-000000000000$/);
  assert.throws(() => p.createId({}), error => error.code === 'id');
});

test('calendar grouping uses local date in UTC-negative and UTC-positive zones', () => {
  const modulePath = resolve(__dirname, '../prototype/script.js');
  for (const timezone of ['America/Los_Angeles', 'Pacific/Kiritimati']) {
    const script = `const p=require(${JSON.stringify(modulePath)}); console.log(p.localDateKey(new Date(2026,9,20,0,5)));`;
    assert.equal(execFileSync(process.execPath, ['-e', script], { env: { ...process.env, TZ: timezone }, encoding: 'utf8' }).trim(), '2026-10-20');
  }
});
