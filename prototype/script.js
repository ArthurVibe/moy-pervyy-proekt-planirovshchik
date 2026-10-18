'use strict';

(() => {
  const STORAGE_KEY = 'whats-up-bro.storage';
  const MIN_DATE = '2000-01-01';
  const MAX_DATE = '2099-12-31';
  const DAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
  const TASK_KEYS = ['id', 'title', 'dueDate', 'dueTime', 'completed', 'createdAt', 'updatedAt'];

  class PlannerError extends Error {
    constructor(code, message, fields = {}) {
      super(message);
      this.name = 'PlannerError';
      this.code = code;
      this.fields = fields;
    }
  }

  const normalizeTitle = value => value.trim().replace(/\s+/gu, ' ');
  const titleKey = value => normalizeTitle(value).toLocaleLowerCase('ru-RU');
  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const pad = n => String(n).padStart(2, '0');

  function isCalendarDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const [year, month, day] = value.split('-').map(Number);
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const lengths = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= lengths[month - 1];
  }

  const isDueDate = value => isCalendarDate(value) && value >= MIN_DATE && value <= MAX_DATE;
  const isTime = value => typeof value === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
  const isTimestamp = value => typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value) &&
    Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;

  function validateFields(raw, tasks, excludeId = null) {
    const title = normalizeTitle(typeof raw.title === 'string' ? raw.title : '');
    const dueDate = raw.dueDate === '' || raw.dueDate == null ? null : raw.dueDate;
    const dueTime = raw.dueTime === '' || raw.dueTime == null ? null : raw.dueTime;
    const errors = {};
    if (!title) errors.title = 'Введите название задачи';
    else if (Array.from(title).length > 200) errors.title = 'Название должно содержать не больше 200 символов';
    else {
      const duplicate = tasks.find(task => task.id !== excludeId && titleKey(task.title) === titleKey(title));
      if (duplicate) errors.title = duplicate.completed
        ? 'Задача с таким названием уже есть среди выполненных. Верните её в работу или измените название'
        : 'Задача с таким названием уже есть. Измените название или отредактируйте существующую';
    }
    if (dueDate !== null && !isCalendarDate(dueDate)) errors.dueDate = 'Укажите корректную дату';
    else if (dueDate !== null && !isDueDate(dueDate)) errors.dueDate = 'Выберите дату с 01.01.2000 по 31.12.2099';
    if (dueTime !== null && dueDate === null) errors.dueTime = 'Для времени сначала укажите дату';
    else if (dueTime !== null && !isTime(dueTime)) errors.dueTime = 'Укажите время в формате ЧЧ:ММ';
    return { values: { title, dueDate, dueTime }, errors };
  }

  function exactKeys(object, keys) {
    return object !== null && typeof object === 'object' && !Array.isArray(object) &&
      Object.keys(object).length === keys.length && keys.every(key => Object.hasOwn(object, key));
  }

  function validateState(state) {
    if (state && typeof state === 'object' && Object.hasOwn(state, 'schemaVersion') && state.schemaVersion !== 1) {
      throw new PlannerError('version', 'Эта версия данных не поддерживается. Данные не изменены');
    }
    const invalid = () => { throw new PlannerError('data', 'Не удалось прочитать сохранённые задачи. Данные не изменены'); };
    if (!exactKeys(state, ['schemaVersion', 'tasks']) || state.schemaVersion !== 1 || !Array.isArray(state.tasks)) invalid();
    const ids = new Set();
    const titles = new Set();
    for (const task of state.tasks) {
      if (!exactKeys(task, TASK_KEYS) || typeof task.id !== 'string' || !task.id.trim() || ids.has(task.id) ||
          typeof task.title !== 'string' || task.title !== normalizeTitle(task.title) || !task.title ||
          Array.from(task.title).length > 200 || titles.has(titleKey(task.title)) ||
          (task.dueDate !== null && !isDueDate(task.dueDate)) ||
          (task.dueTime !== null && (!isTime(task.dueTime) || task.dueDate === null)) ||
          typeof task.completed !== 'boolean' || !isTimestamp(task.createdAt) || !isTimestamp(task.updatedAt)) invalid();
      ids.add(task.id);
      titles.add(titleKey(task.title));
    }
    return state;
  }

  function parseStorage(raw) {
    if (raw === null) return { schemaVersion: 1, tasks: [] };
    let state;
    try { state = JSON.parse(raw); }
    catch { throw new PlannerError('data', 'Не удалось прочитать сохранённые задачи. Данные не изменены'); }
    return validateState(state);
  }

  function createRepository(getStorage) {
    let state = null;
    let lastLoadedRaw = null;
    let recoveryRaw = null;
    let loaded = false;
    let conflicted = false;
    const read = () => {
      try { return getStorage().getItem(STORAGE_KEY); }
      catch { throw new PlannerError('read', 'Хранилище браузера недоступно. Сохранение задач невозможно'); }
    };
    function assertCurrent() {
      if (!loaded) throw new PlannerError('read', 'Хранилище браузера недоступно. Сохранение задач невозможно');
      if (conflicted || read() !== lastLoadedRaw) {
        conflicted = true;
        throw new PlannerError('conflict', 'Данные изменены в другой вкладке. Загрузите актуальный список. Несохранённый ввод будет потерян');
      }
    }
    return {
      getState: () => state,
      getRecoveryRaw: () => recoveryRaw,
      assertCurrent,
      load() {
        loaded = false;
        recoveryRaw = null;
        const raw = read();
        recoveryRaw = raw;
        const parsed = parseStorage(raw);
        state = parsed;
        lastLoadedRaw = raw;
        conflicted = false;
        // Mark ready only after the entire snapshot passed validation.
        loaded = true;
        return state;
      },
      commit(candidate) {
        validateState(candidate);
        assertCurrent();
        const serialized = JSON.stringify(candidate);
        try { getStorage().setItem(STORAGE_KEY, serialized); }
        catch (error) {
          if (error.name === 'QuotaExceededError') {
            throw new PlannerError('quota', 'В хранилище браузера недостаточно места. Изменения не сохранены');
          }
          throw new PlannerError('write', 'Не удалось сохранить изменения. Данные не изменены. Повторите попытку');
        }
        state = candidate;
        lastLoadedRaw = serialized;
        return state;
      }
    };
  }

  const millisecondsToNextMinute = timestamp => 60000 - ((timestamp % 60000 + 60000) % 60000);

  function checkedValues(state, fields, excludeId) {
    const result = validateFields(fields, state.tasks, excludeId);
    if (Object.keys(result.errors).length) throw new PlannerError('validation', '', result.errors);
    return result.values;
  }

  function findTask(state, id) {
    const task = state.tasks.find(item => item.id === id);
    if (!task) throw new PlannerError('missing', 'Задача не найдена. Повторите загрузку');
    return task;
  }

  function addTask(state, fields, id, now) {
    const values = checkedValues(state, fields);
    const task = { id, ...values, completed: false, createdAt: now, updatedAt: now };
    return { schemaVersion: 1, tasks: [...state.tasks, task] };
  }

  function editTask(state, id, fields, now) {
    const task = findTask(state, id);
    const values = checkedValues(state, fields, id);
    if (Object.keys(values).every(key => values[key] === task[key])) return state;
    return { schemaVersion: 1, tasks: state.tasks.map(item => item.id === id ? { ...item, ...values, updatedAt: now } : item) };
  }

  function toggleTask(state, id, now) {
    findTask(state, id);
    return { schemaVersion: 1, tasks: state.tasks.map(item => item.id === id ? { ...item, completed: !item.completed, updatedAt: now } : item) };
  }

  function removeTask(state, id) {
    findTask(state, id);
    return { schemaVersion: 1, tasks: state.tasks.filter(item => item.id !== id) };
  }

  const localDateKey = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

  function formatDate(value) {
    const [year, month, day] = value.split('-').map(Number);
    const weekday = DAYS[new Date(year, month - 1, day, 12).getDay()];
    return `${weekday}, ${pad(day)}.${pad(month)}.${year}`;
  }

  function isOverdue(task, now) {
    if (task.completed || !task.dueDate) return false;
    const today = localDateKey(now);
    return task.dueDate < today || (task.dueDate === today && task.dueTime !== null && task.dueTime <= `${pad(now.getHours())}:${pad(now.getMinutes())}`);
  }

  function compareActive(a, b) {
    return compare(a.dueDate ?? '\uffff', b.dueDate ?? '\uffff') ||
      compare(a.dueTime ?? '\uffff', b.dueTime ?? '\uffff') || compare(a.createdAt, b.createdAt) || compare(a.id, b.id);
  }

  function groupTasks(tasks, now) {
    const today = localDateKey(now);
    const tomorrow = localDateKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 12));
    const buckets = new Map();
    for (const task of tasks.filter(item => !item.completed).sort(compareActive)) {
      const key = isOverdue(task, now) ? 'overdue' : task.dueDate === null ? 'undated' : task.dueDate;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(task);
    }
    const keys = [...buckets.keys()].sort((a, b) =>
      compare(a === 'overdue' ? 0 : a === 'undated' ? 2 : 1, b === 'overdue' ? 0 : b === 'undated' ? 2 : 1) || compare(a, b));
    const groups = keys.map(key => ({
      key,
      label: key === 'overdue' ? 'Просрочено' : key === 'undated' ? 'Без даты' :
        `${key === today ? 'Сегодня · ' : key === tomorrow ? 'Завтра · ' : ''}${formatDate(key)}`,
      tasks: buckets.get(key)
    }));
    const completed = tasks.filter(task => task.completed).sort((a, b) =>
      compare(b.updatedAt, a.updatedAt) || compare(a.createdAt, b.createdAt) || compare(a.id, b.id));
    return { groups, completed };
  }

  function createId(cryptoApi) {
    if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID();
    if (typeof cryptoApi?.getRandomValues !== 'function') {
      throw new PlannerError('id', 'Браузер не поддерживает безопасное создание задач');
    }
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  const api = { STORAGE_KEY, PlannerError, normalizeTitle, titleKey, isCalendarDate, isDueDate, isTime,
    validateFields, validateState, parseStorage, createRepository, addTask, editTask, toggleTask,
    removeTask, localDateKey, formatDate, isOverdue, groupTasks, createId, millisecondsToNextMinute };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  if (typeof document !== 'undefined') initApp();

  function initApp() {
    const byId = id => document.getElementById(id);
    const form = byId('task-form');
    const fields = { title: byId('task-title'), dueDate: byId('task-date'), dueTime: byId('task-time') };
    const errors = { title: byId('title-error'), dueDate: byId('date-error'), dueTime: byId('time-error') };
    const repository = createRepository(() => window.localStorage);
    const groupsElement = byId('task-groups');
    const completed = byId('completed');
    const completedList = byId('completed-list');
    const status = byId('status');
    const alert = byId('alert');
    const cards = new Map();
    let ready = false;
    let conflict = false;
    let editingId = null;
    let draft = null;
    let submitting = false;
    let composing = false;
    let statusTimer;
    let clockTimer;
    let loadRevision = 0;
    let lastRenderSignature = '';
    const canMutate = () => ready && !conflict;

    function clearStatus() {
      clearTimeout(statusTimer);
      status.textContent = '';
    }

    function message(text, isError = false, keepAlert = false) {
      clearStatus();
      if (isError) alert.textContent = text;
      else {
        if (!keepAlert) alert.textContent = '';
        status.textContent = text;
        statusTimer = setTimeout(() => { status.textContent = ''; }, 4000);
      }
    }

    function fieldError(key, text = '') {
      errors[key].textContent = text;
      errors[key].hidden = !text;
      if (text) fields[key].setAttribute('aria-invalid', 'true');
      else fields[key].removeAttribute('aria-invalid');
    }

    function clearErrors() { Object.keys(fields).forEach(key => fieldError(key)); }
    const readFields = () => Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value]));

    function syncControls() {
      const hasDate = Boolean(fields.dueDate.value);
      byId('form-fields').disabled = !ready;
      Object.values(fields).forEach(input => { input.readOnly = conflict; });
      fields.dueTime.disabled = !hasDate;
      byId('time-hint').hidden = hasDate;
      byId('clear-date').disabled = !canMutate() || (!hasDate && !fields.dueTime.value);
      byId('save-task').disabled = !canMutate() || submitting;
      byId('cancel-edit').disabled = !canMutate();
    }

    function syncDate() {
      if (!fields.dueDate.value) fields.dueTime.value = '';
      syncControls();
    }

    function fillFields(values) {
      Object.entries(fields).forEach(([key, input]) => { input.value = values[key] ?? ''; });
      syncDate();
    }

    function handleError(error) {
      if (error.code === 'validation') {
        Object.entries(error.fields).forEach(([key, text]) => fieldError(key, text));
        fields[Object.keys(fields).find(key => error.fields[key])].focus();
        return;
      }
      if (error.code === 'conflict') {
        conflict = true;
        byId('reload-current').hidden = false;
        syncControls();
        render();
      }
      message(error instanceof PlannerError ? error.message : 'Не удалось выполнить действие. Повторите попытку', true);
    }

    function element(tag, className, text) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function visibleCards() {
      return [...document.querySelectorAll('.task-card')].filter(card =>
        card.getClientRects().length && (!completed.contains(card) || completed.open));
    }

    function neighboringId(id) {
      const ids = visibleCards().map(card => card.dataset.taskId);
      const index = ids.indexOf(id);
      return ids[index + 1] ?? ids[index - 1] ?? null;
    }

    function focusCard(id, action, scroll = false, openCompleted = false) {
      const card = cards.get(id);
      if (!card) return false;
      if (completed.contains(card)) {
        if (openCompleted) completed.open = true;
        if (!completed.open) return false;
      }
      const control = card.querySelector(`[data-action="${action}"]`);
      if (!control || control.disabled || !card.getClientRects().length) return false;
      control.focus({ preventScroll: true });
      if (scroll) control.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      return true;
    }

    function focusNeighbor(id, afterCompletion = false) {
      if (id && focusCard(id, 'toggle', true)) return;
      if (afterCompletion && !completed.hidden) byId('completed-heading').focus({ preventScroll: true });
      else fields.title.focus({ preventScroll: true });
    }

    function createCard(id) {
      const card = element('li', 'task-card');
      card.dataset.taskId = id;
      const checkLabel = element('label', 'task-check');
      const checkbox = element('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.action = 'toggle';
      checkbox.addEventListener('change', () => {
        if (!canMutate() || editingId !== null) return;
        const task = findTask(repository.getState(), id);
        const neighbor = neighboringId(id);
        clearStatus();
        try {
          repository.commit(toggleTask(repository.getState(), id, new Date().toISOString()));
          render();
          message(task.completed ? 'Задача возвращена в работу' : 'Задача выполнена');
          if (task.completed) focusCard(id, 'toggle', true);
          else focusNeighbor(neighbor, true);
        } catch (error) { checkbox.checked = task.completed; handleError(error); }
      });
      checkLabel.append(checkbox);
      const content = element('div', 'task-content');
      content.append(element('p', 'task-title'), element('p', 'task-meta'), element('span', 'overdue-label', 'Просрочено'));
      const actions = element('div', 'task-actions');
      const edit = element('button', 'secondary', 'Изменить');
      edit.type = 'button';
      edit.dataset.action = 'edit';
      edit.addEventListener('click', () => {
        if (!canMutate() || editingId !== null) return;
        clearStatus();
        try {
          repository.assertCurrent();
          const task = findTask(repository.getState(), id);
          draft = readFields();
          editingId = id;
          clearErrors();
          fillFields(task);
          byId('form-heading').textContent = 'Редактирование задачи';
          byId('save-task').textContent = 'Сохранить';
          byId('cancel-edit').hidden = false;
          render();
          fields.title.focus();
        } catch (error) { handleError(error); }
      });
      const remove = element('button', 'secondary', 'Удалить');
      remove.type = 'button';
      remove.dataset.action = 'remove';
      remove.addEventListener('click', () => {
        if (!canMutate() || editingId !== null) return;
        const task = findTask(repository.getState(), id);
        clearStatus();
        if (!window.confirm(`Удалить задачу «${task.title}»? Это действие нельзя отменить.`)) return;
        const neighbor = neighboringId(id);
        try {
          repository.commit(removeTask(repository.getState(), id));
          render();
          message('Задача удалена');
          focusNeighbor(neighbor);
        } catch (error) { handleError(error); }
      });
      actions.append(edit, remove);
      card.append(checkLabel, content, actions);
      return card;
    }

    function updateCard(card, task, now) {
      card.classList.toggle('is-completed', task.completed);
      const checkbox = card.querySelector('[data-action="toggle"]');
      checkbox.checked = task.completed;
      checkbox.setAttribute('aria-label', `${task.completed ? 'Вернуть в работу' : 'Выполнить задачу'}: ${task.title}`);
      card.querySelector('.task-title').textContent = task.title;
      card.querySelector('.task-meta').textContent = task.dueDate
        ? `${formatDate(task.dueDate)} · ${task.dueTime ?? 'Без времени'}` : 'Без даты';
      card.querySelector('.overdue-label').hidden = !isOverdue(task, now);
      for (const [action, label] of [['edit', 'Изменить'], ['remove', 'Удалить']]) {
        card.querySelector(`[data-action="${action}"]`).setAttribute('aria-label', `${label} задачу: ${task.title}`);
      }
      card.querySelectorAll('input, button').forEach(control => { control.disabled = !canMutate() || editingId !== null; });
    }

    function render() {
      if (!ready) {
        groupsElement.replaceChildren();
        completedList.replaceChildren();
        cards.clear();
        lastRenderSignature = '';
        completed.hidden = true;
        byId('empty-state').hidden = true;
        return;
      }
      const now = new Date();
      const view = groupTasks(repository.getState().tasks, now);
      const signature = JSON.stringify({ view, editingId, conflict });
      if (signature === lastRenderSignature) return;
      lastRenderSignature = signature;
      const active = document.activeElement;
      const focusedCard = active?.closest('.task-card');
      const focusId = focusedCard?.dataset.taskId;
      const focusAction = active?.dataset.action;
      const scrollX = window.scrollX;
      const scrollY = window.scrollY;
      const alive = new Set();
      const getCard = task => {
        if (!cards.has(task.id)) cards.set(task.id, createCard(task.id));
        const card = cards.get(task.id);
        updateCard(card, task, now);
        alive.add(task.id);
        return card;
      };
      const sections = view.groups.map(group => {
        const section = element('section', 'task-group');
        section.dataset.group = group.key;
        section.append(element('h2', '', group.label));
        const list = element('ul', 'task-list');
        group.tasks.forEach(task => list.append(getCard(task)));
        section.append(list);
        return section;
      });
      groupsElement.replaceChildren(...sections);
      completedList.replaceChildren(...view.completed.map(getCard));
      for (const id of cards.keys()) if (!alive.has(id)) cards.delete(id);
      completed.hidden = view.completed.length === 0;
      if (completed.hidden) completed.open = false;
      byId('completed-heading').textContent = `Выполненные · ${view.completed.length}`;
      byId('empty-state').hidden = view.groups.length !== 0;
      byId('empty-state').textContent = view.completed.length
        ? 'Все задачи выполнены. Можно добавить новую' : 'Пока нет задач. Добавьте первую выше.';
      if (focusId && focusAction) focusCard(focusId, focusAction);
      window.scrollTo(scrollX, scrollY);
    }

    function finishEdit() {
      const previousId = editingId;
      editingId = null;
      clearErrors();
      fillFields(draft);
      draft = null;
      byId('form-heading').textContent = 'Новая задача';
      byId('save-task').textContent = 'Добавить';
      byId('cancel-edit').hidden = true;
      render();
      focusCard(previousId, 'edit', true, true);
    }

    function hideRaw(focus = false) {
      byId('raw-data-panel').hidden = true;
      byId('raw-data').value = '';
      if (focus) byId('copy-raw').focus({ preventScroll: true });
    }

    function load(userInitiated = false) {
      loadRevision++;
      ready = false;
      conflict = false;
      editingId = null;
      draft = null;
      clearStatus();
      clearErrors();
      hideRaw();
      fillFields({});
      completed.open = false;
      byId('form-heading').textContent = 'Новая задача';
      byId('save-task').textContent = 'Добавить';
      byId('cancel-edit').hidden = true;
      byId('reload-current').hidden = true;
      byId('copy-raw').hidden = true;
      byId('copy-raw').disabled = false;
      try {
        repository.load();
        ready = true;
        byId('retry-load').hidden = true;
        alert.textContent = '';
      } catch (error) {
        byId('retry-load').hidden = false;
        byId('copy-raw').hidden = typeof repository.getRecoveryRaw() !== 'string';
        handleError(error);
      }
      syncControls();
      render();
      if (ready && userInitiated) fields.title.focus();
    }

    function refresh() {
      if (!ready || conflict) return;
      try { repository.assertCurrent(); }
      catch (error) { handleError(error); }
      render();
    }

    function scheduleClock() {
      clearTimeout(clockTimer);
      if (document.hidden) return;
      clockTimer = setTimeout(() => {
        refresh();
        scheduleClock();
      }, millisecondsToNextMinute(Date.now()));
    }

    async function copyRaw() {
      const raw = repository.getRecoveryRaw();
      if (typeof raw !== 'string') return;
      const revision = loadRevision;
      byId('copy-raw').disabled = true;
      clearStatus();
      try {
        if (typeof navigator.clipboard?.writeText !== 'function') throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(raw);
        if (revision === loadRevision) message('Исходные данные скопированы', false, true);
      } catch {
        if (revision === loadRevision) {
          byId('raw-data-panel').hidden = false;
          byId('raw-data').value = raw;
          byId('raw-data').focus();
          byId('raw-data').select();
        }
      } finally {
        if (revision === loadRevision) byId('copy-raw').disabled = false;
      }
    }

    form.addEventListener('compositionstart', () => { composing = true; });
    form.addEventListener('compositionend', () => { composing = false; });
    form.addEventListener('keydown', event => {
      if (event.key === 'Enter' && (composing || event.isComposing)) event.preventDefault();
    });
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!canMutate() || submitting || composing) return;
      clearStatus();
      submitting = true;
      syncControls();
      clearErrors();
      try {
        const raw = readFields();
        const validation = validateFields(raw, repository.getState().tasks, editingId);
        if (fields.dueDate.validity.badInput) validation.errors.dueDate = 'Укажите корректную дату';
        if (fields.dueTime.validity.badInput || fields.dueTime.validity.stepMismatch) validation.errors.dueTime = 'Укажите время в формате ЧЧ:ММ';
        if (Object.keys(validation.errors).length) throw new PlannerError('validation', '', validation.errors);
        repository.assertCurrent();
        const state = repository.getState();
        const now = new Date().toISOString();
        const candidate = editingId === null
          ? addTask(state, raw, createId(window.crypto), now) : editTask(state, editingId, raw, now);
        if (candidate === state) { message('Изменений нет'); finishEdit(); }
        else {
          repository.commit(candidate);
          if (editingId !== null) { message('Изменения сохранены'); finishEdit(); }
          else {
            fields.title.value = '';
            fields.dueTime.value = '';
            render();
            message('Задача добавлена');
            fields.title.focus({ preventScroll: true });
          }
        }
      } catch (error) { handleError(error); }
      finally { submitting = false; syncControls(); }
    });
    Object.entries(fields).forEach(([key, input]) => input.addEventListener('input', () => {
      clearStatus();
      fieldError(key);
      if (key === 'dueDate') { syncDate(); fieldError('dueTime'); }
    }));
    byId('clear-date').addEventListener('click', () => {
      clearStatus();
      fields.dueDate.value = '';
      syncDate();
      fieldError('dueDate');
      fieldError('dueTime');
    });
    byId('cancel-edit').addEventListener('click', () => { clearStatus(); finishEdit(); });
    byId('retry-load').addEventListener('click', () => load(true));
    byId('reload-current').addEventListener('click', () => load(true));
    byId('copy-raw').addEventListener('click', copyRaw);
    byId('hide-raw').addEventListener('click', () => hideRaw(true));
    window.addEventListener('storage', event => {
      if (event.key !== STORAGE_KEY && event.key !== null) return;
      try { if (event.storageArea && event.storageArea !== window.localStorage) return; }
      catch (error) { handleError(new PlannerError('read', 'Хранилище браузера недоступно. Сохранение задач невозможно')); return; }
      refresh();
    });
    const resume = () => { refresh(); scheduleClock(); };
    window.addEventListener('focus', resume);
    window.addEventListener('pageshow', resume);
    window.addEventListener('pagehide', () => clearTimeout(clockTimer));
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) clearTimeout(clockTimer);
      else resume();
    });
    load();
    scheduleClock();
  }
})();
