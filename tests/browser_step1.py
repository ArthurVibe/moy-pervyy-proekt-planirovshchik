from datetime import datetime, timezone
from pathlib import Path
import os
import platform
from tempfile import TemporaryDirectory, gettempdir
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('BRO_TEST_BASE_URL', 'http://127.0.0.1:8765/').rstrip('/') + '/'
CHROMIUM = os.environ.get('BRO_TEST_CHROMIUM', '/usr/bin/chromium')
KEY = 'whats-up-bro.storage'
PINNED = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
OUT = Path(gettempdir()) / 'whats-up-bro-step1'
OUT.mkdir(parents=True, exist_ok=True)
CASES = []

def case(name):
    def register(fn):
        CASES.append((name, fn))
        return fn
    return register

def state(page):
    return page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', KEY)

def writes(page):
    return page.evaluate('window.__plannerWrites.length')

def add(page, title, date='', time=''):
    page.locator('#task-title').fill(title)
    page.locator('#task-date').fill(date)
    if time:
        page.locator('#task-time').fill(time)
    page.get_by_role('button', name='Добавить', exact=True).click()

def card(page, title):
    return page.locator('.task-card').filter(has=page.get_by_text(title, exact=True))

def edit(page, title):
    card(page, title).get_by_role('button', name=f'Изменить задачу: {title}', exact=True).click()

def save(page):
    page.get_by_role('button', name='Сохранить', exact=True).click()

def confirm_next(page, accept):
    page.once('dialog', lambda dialog: dialog.accept() if accept else dialog.dismiss())

@case('AT-01: empty storage, disabled time, no write, no automatic date')
def initial(page):
    expect(page.locator('#empty-state')).to_have_text('Пока нет задач. Добавьте первую выше.')
    assert state(page) is None and writes(page) == 0
    expect(page.locator('#task-date')).to_have_value('')
    expect(page.locator('#task-time')).to_be_disabled()
    expect(page.locator('#completed')).to_be_hidden()

@case('AT-02/08/09: normalization, undated task, keyboard focus and reload')
def create_reload(page):
    add(page, '  Купить   хлеб  ')
    data = state(page)
    assert len(data['tasks']) == 1
    assert data['tasks'][0]['title'] == 'Купить хлеб'
    assert data['tasks'][0]['dueDate'] is None and data['tasks'][0]['dueTime'] is None
    expect(page.locator('[data-group="undated"] .task-title')).to_have_text('Купить хлеб')
    expect(page.locator('#task-title')).to_be_focused()
    page.reload()
    assert state(page) == data
    expect(card(page, 'Купить хлеб')).to_be_visible()
    expect(page.locator('#task-title')).to_have_value('')
    expect(page.locator('#task-date')).to_have_value('')

@case('AT-03..07/37: group order, overdue boundary, weekday labels and sorting')
def groups(page):
    for args in [('Без срока', '', ''), ('Четверг', '2026-10-22', ''), ('Завтра', '2026-10-21', ''),
                 ('Без времени', '2026-10-20', ''), ('Позднее', '2026-10-20', '15:00'),
                 ('Раньше', '2026-10-20', '13:00'), ('В срок', '2026-10-20', '12:00'),
                 ('До срока', '2026-10-20', '11:59'), ('Вчера', '2026-10-19', '')]:
        add(page, *args)
    assert page.locator('.task-group h2').all_text_contents() == [
        'Просрочено', 'Сегодня · Вт, 20.10.2026', 'Завтра · Ср, 21.10.2026', 'Чт, 22.10.2026', 'Без даты']
    assert page.locator('[data-group="overdue"] .task-title').all_text_contents() == ['Вчера', 'До срока', 'В срок']
    assert page.locator('[data-group="2026-10-20"] .task-title').all_text_contents() == ['Раньше', 'Позднее', 'Без времени']
    expect(card(page, 'Четверг').locator('.task-meta')).to_have_text('Чт, 22.10.2026 · Без времени')
    assert page.locator('.task-card').count() == len(state(page)['tasks']) == 9
    page.screenshot(path=str(OUT / 'step1-desktop.png'), full_page=True)

@case('AT-08/09: repeated date, cleared time/title, blank form after reload')
def fast_input(page):
    add(page, 'Первое', '2026-10-22', '15:00')
    expect(page.locator('#task-title')).to_have_value('')
    expect(page.locator('#task-time')).to_have_value('')
    expect(page.locator('#task-date')).to_have_value('2026-10-22')
    page.locator('#task-title').fill('Второе')
    page.locator('#task-title').press('Enter')
    assert [(t['dueDate'], t['dueTime']) for t in state(page)['tasks']] == [('2026-10-22', '15:00'), ('2026-10-22', None)]
    page.reload()
    expect(page.locator('#task-date')).to_have_value('')
    assert len(state(page)['tasks']) == 2

@case('AT-10: blanks, 200/201 codepoints and retained input')
def title_limits(page):
    for value in ['', '   ', 'я' * 201]:
        page.locator('#task-title').fill(value)
        page.get_by_role('button', name='Добавить', exact=True).click()
        expect(page.locator('#title-error')).to_be_visible()
        expect(page.locator('#task-title')).to_have_value(value)
        expect(page.locator('#task-title')).to_be_focused()
        assert state(page) is None
    add(page, '😀' * 200)
    assert len(state(page)['tasks']) == 1
    expect(page.locator('#title-error')).to_be_hidden()

@case('AT-11..14: duplicates, own id and completed reservation')
def duplicates(page):
    add(page, 'Купить хлеб')
    add(page, '  КУПИТЬ   ХЛЕБ ')
    expect(page.locator('#title-error')).to_contain_text('уже есть')
    assert len(state(page)['tasks']) == 1
    edit(page, 'Купить хлеб')
    page.locator('#task-title').fill('КУПИТЬ ХЛЕБ')
    save(page)
    original_id = state(page)['tasks'][0]['id']
    card(page, 'КУПИТЬ ХЛЕБ').get_by_role('checkbox').click()
    add(page, 'купить хлеб')
    expect(page.locator('#title-error')).to_contain_text('среди выполненных')
    add(page, 'Другое')
    edit(page, 'Другое')
    page.locator('#task-title').fill('купить хлеб')
    save(page)
    expect(page.locator('#title-error')).to_contain_text('среди выполненных')
    assert len(state(page)['tasks']) == 2
    page.get_by_role('button', name='Отмена', exact=True).click()
    expect(page.locator('#completed')).not_to_have_attribute('open', '')
    page.locator('#completed-heading').click()
    confirm_next(page, True)
    card(page, 'КУПИТЬ ХЛЕБ').get_by_role('button', name='Удалить задачу: КУПИТЬ ХЛЕБ', exact=True).click()
    add(page, 'купить хлеб')
    assert len(state(page)['tasks']) == 2
    assert all(t['id'] != original_id for t in state(page)['tasks'])

@case('AT-15..18: editing identity, date-only move, clear, cancel/draft and no-op writes')
def editing(page):
    add(page, 'Звонок', '2026-10-21', '11:30')
    before = state(page)['tasks'][0]
    page.locator('#task-title').fill('Черновик')
    page.locator('#task-date').fill('2026-10-23')
    page.locator('#task-time').fill('16:30')
    edit(page, 'Звонок')
    expect(card(page, 'Звонок').get_by_role('checkbox')).to_be_disabled()
    expect(card(page, 'Звонок').get_by_role('button', name='Удалить задачу: Звонок', exact=True)).to_be_disabled()
    page.locator('#task-date').fill('2026-10-22')
    expect(page.locator('#task-time')).to_have_value('11:30')
    save(page)
    after = state(page)['tasks'][0]
    assert (after['id'], after['createdAt'], after['completed'], after['dueTime']) == (before['id'], before['createdAt'], False, '11:30')
    assert after['dueDate'] == '2026-10-22'
    expect(page.locator('#task-title')).to_have_value('Черновик')
    expect(page.locator('#task-date')).to_have_value('2026-10-23')
    expect(page.locator('#task-time')).to_have_value('16:30')
    count = writes(page)
    edit(page, 'Звонок')
    page.locator('#task-title').fill('Не сохранять')
    page.get_by_role('button', name='Отмена', exact=True).click()
    assert writes(page) == count and state(page)['tasks'][0] == after
    expect(page.locator('#task-title')).to_have_value('Черновик')
    edit(page, 'Звонок')
    save(page)
    expect(page.locator('#status')).to_have_text('Изменений нет')
    assert writes(page) == count and state(page)['tasks'][0] == after
    edit(page, 'Звонок')
    page.get_by_role('button', name='Без даты', exact=True).click()
    expect(page.locator('#task-time')).to_be_disabled()
    expect(page.locator('#task-time')).to_have_value('')
    save(page)
    assert state(page)['tasks'][0]['dueDate'] is None and state(page)['tasks'][0]['dueTime'] is None
    expect(page.locator('[data-group="undated"] .task-title')).to_have_text('Звонок')
    saved = state(page)
    page.reload()
    assert state(page) == saved
    expect(card(page, 'Звонок')).to_be_visible()

@case('AT-19..21: complete, collapse, edit completed, reload and return to overdue')
def completion(page):
    add(page, 'Завершить', '2026-10-19')
    card(page, 'Завершить').get_by_role('checkbox').click()
    assert state(page)['tasks'][0]['completed'] is True
    expect(page.locator('#completed-heading')).to_have_text('Выполненные · 1')
    assert not page.locator('#completed').evaluate('(e) => e.open')
    expect(page.locator('#empty-state')).to_have_text('Все задачи выполнены. Можно добавить новую')
    page.locator('#completed-heading').click()
    edit(page, 'Завершить')
    page.locator('#task-title').fill('Исправлено')
    save(page)
    assert state(page)['tasks'][0]['completed'] is True
    saved = state(page)
    page.reload()
    assert state(page) == saved and not page.locator('#completed').evaluate('(e) => e.open')
    page.locator('#completed-heading').click()
    card(page, 'Исправлено').get_by_role('checkbox').click()
    assert state(page)['tasks'][0]['completed'] is False
    expect(page.locator('[data-group="overdue"] .task-title')).to_have_text('Исправлено')
    expect(page.locator('#completed')).to_be_hidden()
    page.reload()
    assert state(page)['tasks'][0]['completed'] is False

@case('AT-22: confirm dismiss/accept, only selected deletion and reload')
def deletion(page):
    add(page, 'Удалить')
    add(page, 'Оставить')
    before = state(page)
    count = writes(page)
    confirm_next(page, False)
    card(page, 'Удалить').get_by_role('button', name='Удалить задачу: Удалить', exact=True).click()
    assert state(page) == before and writes(page) == count
    confirm_next(page, True)
    card(page, 'Удалить').get_by_role('button', name='Удалить задачу: Удалить', exact=True).click()
    assert [t['title'] for t in state(page)['tasks']] == ['Оставить']
    page.reload()
    assert [t['title'] for t in state(page)['tasks']] == ['Оставить']
    expect(card(page, 'Оставить')).to_be_visible()

@case('AT-30: HTML-like title survives reload as text without execution')
def text_safety(page):
    title = '<img src=x onerror=window.__injected=true>'
    add(page, title)
    page.reload()
    expect(page.locator('.task-title')).to_have_text(title)
    assert page.locator('.task-title img').count() == 0
    assert page.evaluate('window.__injected') is None

@case('AT-31: double click and IME composition do not create duplicates')
def keyboard(page):
    page.locator('#task-title').fill('Двойной клик')
    page.get_by_role('button', name='Добавить', exact=True).dblclick()
    assert len(state(page)['tasks']) == 1
    page.locator('#task-title').fill('После композиции')
    page.locator('#task-title').dispatch_event('compositionstart')
    page.locator('#task-title').press('Enter')
    assert len(state(page)['tasks']) == 1
    page.locator('#task-title').dispatch_event('compositionend')
    page.locator('#task-title').press('Enter')
    assert len(state(page)['tasks']) == 2

@case('AT-40: form range errors, calendar bounds and native date validity')
def date_limits(page):
    for date in ['1999-12-31', '2100-01-01']:
        add(page, 'Границы', date)
        expect(page.locator('#date-error')).to_contain_text('01.01.2000')
        expect(page.locator('#task-date')).to_have_value(date)
        assert state(page) is None
    for n, date in enumerate(['2000-01-01', '2099-12-31', '2024-02-29']):
        add(page, f'Разрешено {n}', date)
    assert len(state(page)['tasks']) == 3
    assert page.locator('#task-date').evaluate("(e) => {e.value = '2026-02-29'; return e.value}") == ''

@case('AT-41: real browser state survives clock reversal and reload')
def clock_reversal(page):
    add(page, 'До перевода часов')
    before = state(page)['tasks'][0]
    page.clock.set_fixed_time(datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc))
    edit(page, 'До перевода часов')
    page.locator('#task-title').fill('После перевода часов')
    save(page)
    after = state(page)['tasks'][0]
    assert after['id'] == before['id'] and after['createdAt'] == before['createdAt']
    assert after['updatedAt'] == '2026-10-19T08:00:00.000Z'
    page.reload()
    assert state(page)['tasks'][0] == after
    expect(card(page, 'После перевода часов')).to_be_visible()

@case('step-1 storage safety: failed writes retain input, status and record on toggle/delete')
def failures(page):
    add(page, 'Сохранено')
    before = state(page)
    page.evaluate('''() => {window.__workingSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function() {throw new DOMException('full','QuotaExceededError')};}''')
    add(page, 'Не сохранено', '2026-10-22', '10:00')
    expect(page.locator('#alert')).to_contain_text('недостаточно места')
    expect(page.locator('#task-title')).to_have_value('Не сохранено')
    assert state(page) == before
    card(page, 'Сохранено').get_by_role('checkbox').click()
    expect(card(page, 'Сохранено').get_by_role('checkbox')).not_to_be_checked()
    confirm_next(page, True)
    card(page, 'Сохранено').get_by_role('button', name='Удалить задачу: Сохранено', exact=True).click()
    expect(card(page, 'Сохранено')).to_be_visible()
    assert state(page) == before
    page.evaluate('() => { Storage.prototype.setItem = window.__workingSetItem; }')
    page.get_by_role('button', name='Добавить', exact=True).click()
    assert len(state(page)['tasks']) == 2

@case('step-1 storage safety: malformed data is retained, empty state not fabricated')
def invalid_storage(page):
    page.evaluate('(key) => localStorage.setItem(key, "broken-json")', KEY)
    page.reload()
    expect(page.locator('#alert')).to_contain_text('Не удалось прочитать')
    expect(page.locator('#save-task')).to_be_disabled()
    expect(page.locator('#empty-state')).to_be_hidden()
    assert page.evaluate('(key) => localStorage.getItem(key)', KEY) == 'broken-json'
    page.evaluate('(key) => localStorage.removeItem(key)', KEY)
    page.get_by_role('button', name='Повторить загрузку', exact=True).click()
    expect(page.locator('#save-task')).to_be_enabled()
    expect(page.locator('#empty-state')).to_be_visible()

@case('basic layout at 320/390/768/1440 px, no clipping of long title')
def layout(page):
    add(page, 'Д' * 200, '2026-10-22', '12:30')
    assert len(state(page)['tasks']) == 1
    for width in [320, 390, 768, 1440]:
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        expect(page.get_by_role('button', name='Добавить', exact=True)).to_be_visible()
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(OUT / 'step1-mobile.png'), full_page=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=CHROMIUM, headless=True, args=['--no-sandbox'])
    print(f'Chromium {browser.version}, {platform.system()}, {BASE}', flush=True)
    for name, fn in CASES:
        context = browser.new_context(timezone_id='Europe/Moscow', locale='ru-RU', viewport={'width':1280,'height':1000})
        page = context.new_page()
        page.set_default_timeout(8000)
        errors = []
        external = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: external.append(request.url) if not request.url.startswith(BASE) else None)
        page.add_init_script('''window.__plannerWrites = [];
          const original = Storage.prototype.setItem;
          Storage.prototype.setItem = function(key, value) {
            original.call(this, key, value);
            if (key === 'whats-up-bro.storage') window.__plannerWrites.push(value);
          };''')
        page.clock.install(time=PINNED)
        page.goto(BASE)
        try:
            fn(page)
            assert not errors, errors
            assert not external, external
            print('PASS ' + name, flush=True)
        except Exception:
            page.screenshot(path=str(OUT / 'step1-browser-failure.png'), full_page=True)
            (OUT / 'step1-browser-failure.html').write_text(page.content())
            print('FAIL ' + name, flush=True)
            raise
        finally:
            context.close()
    browser.close()
    print(f'Passed {len(CASES)} browser scenarios. No page errors or external requests.', flush=True)

# Check disk-backed persistence across a full browser shutdown, not just reload.
with sync_playwright() as playwright:
    with TemporaryDirectory(prefix='bro-profile-') as profile:
        for launch in range(2):
            context = playwright.chromium.launch_persistent_context(profile, executable_path=CHROMIUM,
                headless=True, args=['--no-sandbox'], locale='ru-RU', timezone_id='Europe/Moscow')
            try:
                page = context.pages[0]
                page.goto(BASE)
                if launch == 0:
                    add(page, 'Проверка перезапуска', '2026-10-22', '10:30')
                    saved = state(page)
                    assert saved is not None
                else:
                    assert state(page) == saved
                    expect(page.locator('.task-title')).to_have_text('Проверка перезапуска')
                    expect(page.locator('#task-date')).to_have_value('')
            finally:
                context.close()
    print('PASS localStorage survives full browser close/relaunch (persistent profile).', flush=True)
