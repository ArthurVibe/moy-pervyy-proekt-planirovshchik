"""Step 2 browser checks. Run with a static server; requires developer-side Playwright."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir
import json
import os
import platform
import re
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('BRO_TEST_BASE_URL', 'http://127.0.0.1:8765/').rstrip('/') + '/'
CHROMIUM = os.environ.get('BRO_TEST_CHROMIUM', '/usr/bin/chromium')
KEY = 'whats-up-bro.storage'
OUT = Path(gettempdir()) / 'whats-up-bro-step2'
OUT.mkdir(parents=True, exist_ok=True)
CASES = []
STAMP = '2026-10-20T08:59:30.000Z'

def case(name):
    def register(fn): CASES.append((name, fn)); return fn
    return register

def state(page): return page.evaluate('(k) => JSON.parse(localStorage.getItem(k))', KEY)
def raw(page): return page.evaluate('(k) => localStorage.getItem(k)', KEY)
def writes(page): return page.evaluate('window.__writes')
def card(page, title): return page.locator('.task-card').filter(has=page.get_by_text(title, exact=True))
def add(page, title, date='', time=''):
    page.locator('#task-title').fill(title)
    page.locator('#task-date').fill(date)
    if time: page.locator('#task-time').fill(time)
    page.get_by_role('button', name='Добавить', exact=True).click()

def task(title, date=None, time=None, completed=False, id=None):
    return dict(id=id or title, title=title, dueDate=date, dueTime=time, completed=completed, createdAt=STAMP, updatedAt=STAMP)

def seed(page, tasks):
    page.evaluate('([k,v]) => localStorage.setItem(k, JSON.stringify(v))', [KEY, dict(schemaVersion=1, tasks=tasks)])
    page.reload()

def corrupt(page, value):
    page.evaluate('([k,v]) => localStorage.setItem(k,v)', [KEY, value])
    page.reload()

@case('AT-26: denied localStorage getter blocks mutations; retry recovers saved records')
def denied_access(page):
    add(page, 'Не потерять')
    saved = raw(page)
    page.add_init_script('''window.__storageDescriptor=Object.getOwnPropertyDescriptor(window,'localStorage');
      Object.defineProperty(window,'localStorage',{configurable:true,get(){throw new DOMException('denied','SecurityError')}});''')
    page.reload()
    expect(page.locator('#alert')).to_contain_text('Хранилище браузера недоступно')
    expect(page.locator('#save-task')).to_be_disabled()
    expect(page.locator('#copy-raw')).to_be_hidden()
    expect(page.locator('#empty-state')).to_be_hidden()
    page.evaluate("Object.defineProperty(window,'localStorage',window.__storageDescriptor)")
    assert raw(page) == saved
    page.locator('#retry-load').click()
    expect(card(page, 'Не потерять')).to_be_visible()
    assert writes(page) == 0

@case('AT-28: invalid JSON/schema/records block all mutations without losing the exact source')
def invalid_snapshots(page):
    item = task('Исходная')
    variants = ['', ' broken\n', 'null', '[]', '{"schemaVersion":2,"tasks":[]}',
        json.dumps(dict(schemaVersion=1,tasks=[],unknown=True)),
        json.dumps(dict(schemaVersion=1,tasks=[item,dict(item,title='Другое')])),
        json.dumps(dict(schemaVersion=1,tasks=[item,dict(item,id='other',completed=True)])),
        json.dumps(dict(schemaVersion=1,tasks=[dict(item,dueTime='10:00')])),
        json.dumps(dict(schemaVersion=1,tasks=[dict(item,dueDate='2026-02-29')]))]
    for value in variants:
        corrupt(page, value)
        expect(page.locator('#alert')).not_to_be_empty()
        expect(page.locator('#save-task')).to_be_disabled()
        expect(page.locator('#copy-raw')).to_be_visible()
        expect(page.locator('#empty-state')).to_be_hidden()
        assert raw(page) == value and writes(page) == 0

@case('AT-39: actual Clipboard API copies exact invalid source and leaves the error visible')
def clipboard_actual(page):
    value = '  {"broken": "данные"}\n'
    corrupt(page, value)
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=BASE.rstrip('/'))
    page.bring_to_front()
    page.locator('#copy-raw').click()
    expect(page.locator('#status')).to_have_text('Исходные данные скопированы')
    assert page.evaluate('navigator.clipboard.readText()') == value
    expect(page.locator('#alert')).to_contain_text('Не удалось прочитать')
    page.clock.run_for(5000)
    expect(page.locator('#status')).to_be_empty()
    expect(page.locator('#alert')).not_to_be_empty()
    assert raw(page) == value and writes(page) == 0

@case('AT-39: denied/unavailable clipboard exposes readonly source, no HTML execution')
def clipboard_fallback(page):
    value = '<img src=x onerror=window.bad=true>\n' + 'я' * 4000
    for mode in ['denied', 'unavailable']:
        corrupt(page, value)
        page.evaluate("""mode => Object.defineProperty(navigator,'clipboard',{configurable:true,
          value:mode==='denied'?{writeText:async()=>{throw new Error('denied')}}:undefined})""", mode)
        page.locator('#copy-raw').click()
        expect(page.locator('#raw-data')).to_be_visible()
        expect(page.locator('#raw-data')).to_have_value(value)
        expect(page.locator('#raw-data')).to_be_focused()
        assert page.locator('#raw-data').get_attribute('readonly') is not None
        assert page.evaluate('window.bad') is None
        expect(page.locator('#status')).to_be_empty()
        expect(page.locator('#alert')).not_to_be_empty()
        assert raw(page) == value and writes(page) == 0
        page.locator('#hide-raw').click()
        expect(page.locator('#raw-data-panel')).to_be_hidden()
        expect(page.locator('#copy-raw')).to_be_focused()

@case('AT-39: late clipboard completion cannot announce old data after successful reload')
def late_clipboard(page):
    corrupt(page, 'broken')
    page.evaluate("""() => Object.defineProperty(navigator,'clipboard',{configurable:true,
      value:{writeText:()=>new Promise(resolve=>{window.finishCopy=resolve})}})""")
    page.locator('#copy-raw').click()
    expect(page.locator('#copy-raw')).to_be_disabled()
    page.evaluate('(k) => localStorage.removeItem(k)', KEY)
    page.locator('#retry-load').click()
    page.evaluate('() => {window.finishCopy();}')
    expect(page.locator('#copy-raw')).to_be_hidden()
    expect(page.locator('#raw-data-panel')).to_be_hidden()
    expect(page.locator('#status')).to_be_empty()
    expect(page.locator('#save-task')).to_be_enabled()

@case('AT-27: failed edit preserves draft, failed toggle/delete preserve records, retry succeeds')
def write_failures(page):
    for error_name in ['QuotaExceededError', 'SecurityError']:
        seed(page, [task('Сохранённая', '2026-10-22', '10:00')])
        before = raw(page)
        page.evaluate("""name => {const original=Storage.prototype.setItem; window.allowWrite=false;
          Storage.prototype.setItem=function(k,v){if(k==='whats-up-bro.storage'&&!window.allowWrite)
            throw new DOMException('denied',name); return original.call(this,k,v)};}""", error_name)
        card(page,'Сохранённая').get_by_role('checkbox').click()
        expect(card(page,'Сохранённая').get_by_role('checkbox')).not_to_be_checked()
        assert raw(page) == before
        page.once('dialog', lambda d: d.accept())
        card(page,'Сохранённая').get_by_role('button',name='Удалить задачу: Сохранённая',exact=True).click()
        assert raw(page) == before
        card(page,'Сохранённая').get_by_role('button',name='Изменить задачу: Сохранённая',exact=True).click()
        page.locator('#task-title').fill('Изменённая')
        page.locator('#task-date').fill('2026-10-23')
        page.get_by_role('button',name='Сохранить',exact=True).click()
        expect(page.locator('#form-heading')).to_have_text('Редактирование задачи')
        expect(page.locator('#task-title')).to_have_value('Изменённая')
        expect(page.locator('#task-date')).to_have_value('2026-10-23')
        assert raw(page) == before and writes(page) == 0
        assert page.evaluate("!!document.activeElement.closest('form')")
        page.clock.run_for(5000)
        expect(page.locator('#alert')).not_to_be_empty()
        page.evaluate('window.allowWrite=true')
        page.get_by_role('button',name='Сохранить',exact=True).click()
        expect(page.locator('#alert')).to_be_empty()
        assert state(page)['tasks'][0]['title'] == 'Изменённая'

@case('AT-26/27: failed preflight read refuses save, retains input and allows retry')
def preflight_read(page):
    add(page, 'Ранее сохранено')
    before = raw(page)
    page.evaluate("""() => {const original=Storage.prototype.getItem; window.denyRead=true;
      Storage.prototype.getItem=function(k){if(k==='whats-up-bro.storage'&&window.denyRead)
        throw new DOMException('denied','SecurityError');return original.call(this,k)};}""")
    add(page, 'Повторить', '2026-10-21')
    expect(page.locator('#alert')).to_contain_text('Хранилище браузера недоступно')
    expect(page.locator('#task-title')).to_have_value('Повторить')
    expect(page.locator('#copy-raw')).to_be_hidden()
    page.evaluate('window.denyRead=false')
    assert raw(page) == before
    page.locator('#save-task').click()
    assert len(state(page)['tasks']) == 2

@case('AT-29: real second tab, unrelated keys ignored, pending input retained until explicit reload')
def second_tab(page):
    add(page, 'Первая')
    page.locator('#task-title').fill('Мой черновик')
    other = page.context.new_page()
    try:
        other.goto(BASE)
        other.evaluate("localStorage.setItem('unrelated','value')")
        expect(page.locator('#reload-current')).to_be_hidden()
        add(other,'Из другой вкладки')
        expect(page.locator('#alert')).to_contain_text('другой вкладке')
        expect(page.locator('#task-title')).to_have_value('Мой черновик')
        expect(page.locator('#task-title')).to_have_attribute('readonly','')
        expect(page.locator('#save-task')).to_be_disabled()
        for button in page.locator('.task-card button').all(): expect(button).to_be_disabled()
        expect(page.locator('#task-title')).to_be_enabled()
        page.locator('#task-title').focus()
        assert page.locator('#task-title').evaluate('(e) => {e.select(); return e.selectionEnd}') == len('Мой черновик')
        page.locator('#reload-current').click()
        expect(page.locator('#task-title')).to_have_value('')
        expect(page.locator('#reload-current')).to_be_hidden()
        expect(page.locator('#save-task')).to_be_enabled()
        assert page.locator('.task-card').count() == 2
    finally: other.close()

@case('AT-29: external removal, clear and invalid source all require reload without overwriting')
def external_removal(page):
    for operation in ['remove', 'clear', 'invalid']:
        seed(page,[task('Была задача')])
        page.locator('#task-title').fill('Не потерять черновик')
        other=page.context.new_page()
        try:
            other.goto(BASE)
            other.evaluate("""([key,op])=>{if(op==='remove')localStorage.removeItem(key);
              else if(op==='clear')localStorage.clear(); else localStorage.setItem(key,'broken');} """, [KEY,operation])
            expect(page.locator('#reload-current')).to_be_visible()
            expect(page.locator('#task-title')).to_have_value('Не потерять черновик')
            assert raw(page) == ('broken' if operation=='invalid' else None)
            page.locator('#reload-current').click()
            expect(page.locator('#task-title')).to_have_value('')
            if operation=='invalid':
                expect(page.locator('#copy-raw')).to_be_visible()
                expect(page.locator('#save-task')).to_be_disabled()
            else: expect(page.locator('#empty-state')).to_be_visible()
        finally: other.close()

@case('AT-29: preflight detects changes with no storage event and prevents no-op edit loss')
def silent_conflict(page):
    for no_op in [False, True]:
        seed(page,[task('Исходная')])
        if no_op: card(page,'Исходная').get_by_role('button',name='Изменить задачу: Исходная',exact=True).click()
        else: page.locator('#task-title').fill('Черновик')
        external = json.dumps(dict(schemaVersion=1,tasks=[task('Извне')]),ensure_ascii=False)
        page.evaluate('([k,v])=>localStorage.setItem(k,v)', [KEY,external])
        page.locator('#save-task').click()
        expect(page.locator('#reload-current')).to_be_visible()
        assert raw(page) == external
        expect(page.locator('#task-title')).to_have_value('Исходная' if no_op else 'Черновик')

@case('AT-29: focus/visibility catches an otherwise unannounced storage change')
def focus_check(page):
    add(page,'Было')
    page.evaluate('(k)=>localStorage.removeItem(k)', KEY)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.locator('#reload-current')).to_be_visible()

@case('AT-23: minute crossing preserves draft, selection, field errors, storage and metadata')
def minute_draft(page):
    seed(page,[task('Наступит','2026-10-20','12:00')])
    page.locator('#task-title').fill('Мой черновик')
    page.locator('#task-date').fill('1999-12-31')
    page.locator('#save-task').click()
    expect(page.locator('#date-error')).to_be_visible()
    page.locator('#task-title').focus()
    page.locator('#task-title').evaluate('(e)=>e.setSelectionRange(2,7)')
    before=raw(page)
    page.clock.run_for(30000)
    expect(page.locator('[data-group="overdue"] .task-title')).to_have_text('Наступит')
    expect(page.locator('#task-title')).to_be_focused()
    expect(page.locator('#task-title')).to_have_value('Мой черновик')
    expect(page.locator('#date-error')).to_be_visible()
    assert page.locator('#task-title').evaluate('(e)=>[e.selectionStart,e.selectionEnd]') == [2,7]
    assert raw(page)==before and writes(page)==0

@case('AT-23: moved card keeps its DOM node, focused action and scroll position')
def minute_card(page):
    seed(page,[task(f'Ранее {n:02d}','2026-10-19') for n in range(8)]+
        [task('Перемещается','2026-10-20','12:00')]+
        [task(f'Позже {n:02d}','2026-10-20','13:00') for n in range(20)])
    target=card(page,'Перемещается').get_by_role('button',name='Изменить задачу: Перемещается',exact=True)
    target.scroll_into_view_if_needed()
    target.focus()
    page.evaluate("window.beforeNode=document.activeElement; window.beforeScroll=scrollY")
    before=raw(page)
    page.clock.run_for(30000)
    expect(target).to_be_focused()
    assert page.evaluate('document.activeElement===window.beforeNode')
    assert abs(page.evaluate('scrollY-window.beforeScroll')) <= 1
    assert card(page,'Перемещается').evaluate("e=>e.closest('[data-group]').dataset.group") == 'overdue'
    assert raw(page)==before and writes(page)==0

@case('AT-23/25: midnight, leap day and year boundary recalculate headings without writes')
def midnight(page):
    for date, instant, following in [
        ('2026-12-31',datetime(2026,12,31,20,59,30,tzinfo=timezone.utc),'2027-01-01'),
        ('2024-02-28',datetime(2024,2,28,20,59,30,tzinfo=timezone.utc),'2024-02-29')]:
        page.clock.set_system_time(instant)
        seed(page,[task('Сегодня без времени',date),task('Следующий день',following)])
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        before=raw(page)
        page.clock.run_for(30000)
        expect(card(page,'Сегодня без времени').locator('.overdue-label')).to_be_visible()
        expect(page.locator(f'[data-group="{following}"] h2')).to_contain_text('Сегодня ·')
        assert raw(page)==before and writes(page)==0

@case('AT-24: hidden-page catchup and repeated focus do not duplicate timers or write')
def visibility(page):
    seed(page,[task('Срок','2026-10-20','12:00')])
    page.evaluate("""()=>{window.testHidden=true;Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.testHidden});
      document.dispatchEvent(new Event('visibilitychange'));}""")
    before=raw(page)
    page.clock.run_for(120000)
    assert card(page,'Срок').evaluate("e=>e.closest('[data-group]').dataset.group") == '2026-10-20'
    page.evaluate("window.testHidden=false;document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator('[data-group="overdue"] .task-title')).to_have_text('Срок')
    page.evaluate("window.__reads=0; for(let i=0;i<5;i++)window.dispatchEvent(new Event('focus'))")
    assert page.evaluate('window.__reads')==5
    page.clock.run_for(30000)
    assert page.evaluate('window.__reads')==6
    assert raw(page)==before and writes(page)==0

@case('AT-25: timezone change regroups local dates without shifting saved deadlines')
def timezone_change(page):
    page.clock.set_system_time(datetime(2026,10,20,17,0,tzinfo=timezone.utc))
    seed(page,[task('Без сдвига','2026-10-21','10:00')])
    expect(page.locator('[data-group="2026-10-21"] h2')).to_contain_text('Завтра ·')
    before=raw(page)
    session=page.context.new_cdp_session(page)
    session.send('Emulation.setTimezoneOverride',{'timezoneId':'Pacific/Kiritimati'})
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.locator('[data-group="2026-10-21"] h2')).to_contain_text('Сегодня ·')
    expect(card(page,'Без сдвига').locator('.task-meta')).to_have_text('Ср, 21.10.2026 · 10:00')
    assert raw(page)==before and writes(page)==0

@case('AT-38: long-list edit/save/cancel/no-op returns focus and restores draft')
def long_edit(page):
    seed(page,[task(f'Задача {n:02d}','2026-10-21') for n in range(30)]+[task('Выполненная',completed=True)])
    page.locator('#task-title').fill('Черновик ввода')
    page.locator('#task-date').fill('2026-10-23')
    target=card(page,'Задача 29').get_by_role('button',name='Изменить задачу: Задача 29',exact=True)
    for mode in ['cancel','noop','save']:
        target.click()
        expect(page.locator('#task-title')).to_be_focused()
        assert page.locator('.composer').evaluate("e=>getComputedStyle(e).position") not in ['sticky','fixed']
        if mode=='cancel':
            page.locator('#task-title').fill('Не сохранять')
            page.locator('#cancel-edit').click()
        else:
            if mode=='save': page.locator('#task-date').fill('2026-10-22')
            page.locator('#save-task').click()
        expect(target).to_be_focused()
        expect(page.locator('#task-title')).to_have_value('Черновик ввода')
        expect(page.locator('#task-date')).to_have_value('2026-10-23')
        rect=target.bounding_box()
        assert rect['y']>=-1 and rect['y']+rect['height']<=page.evaluate('innerHeight')+1, (mode,rect,page.evaluate('scrollY'))
    page.locator('#completed-heading').click()
    card(page,'Выполненная').get_by_role('button',name='Изменить задачу: Выполненная',exact=True).click()
    page.locator('#save-task').click()
    assert page.locator('#completed').evaluate('e=>e.open')
    expect(card(page,'Выполненная').get_by_role('button',name='Изменить задачу: Выполненная',exact=True)).to_be_focused()

@case('AT-32: keyboard completion/delete follows next/previous/fallback focus rules')
def neighbor_focus(page):
    seed(page,[task('А'),task('Б'),task('В')])
    card(page,'Б').get_by_role('checkbox').focus()
    page.keyboard.press('Space')
    expect(card(page,'В').get_by_role('checkbox')).to_be_focused()
    page.once('dialog',lambda d:d.accept())
    card(page,'В').get_by_role('button',name='Удалить задачу: В',exact=True).focus()
    page.keyboard.press('Enter')
    expect(card(page,'А').get_by_role('checkbox')).to_be_focused()
    page.keyboard.press('Space')
    expect(page.locator('#completed-heading')).to_be_focused()
    page.keyboard.press('Enter')
    card(page,'Б').get_by_role('checkbox').focus()
    page.keyboard.press('Space')
    expect(card(page,'Б').get_by_role('checkbox')).to_be_focused()
    assert not card(page,'Б').evaluate("e=>!!e.closest('#completed')")
    seed(page,[task('Последняя')])
    page.once('dialog',lambda d:d.accept())
    card(page,'Последняя').get_by_role('button',name='Удалить задачу: Последняя',exact=True).click()
    expect(page.locator('#task-title')).to_be_focused()

@case('AT-32: keyboard Tab reaches controls and hidden completed items are excluded')
def keyboard_navigation(page):
    seed(page,[task('Скрытая выполненная',completed=True)])
    page.locator('#task-title').focus()
    page.keyboard.insert_text('Клавиатура')
    page.keyboard.press('Enter')
    target=card(page,'Клавиатура').get_by_role('button',name='Изменить задачу: Клавиатура',exact=True)
    for _ in range(25):
        if target.evaluate('e=>e===document.activeElement'): break
        page.keyboard.press('Tab')
    expect(target).to_be_focused()
    page.keyboard.press('Enter')
    expect(page.locator('#task-title')).to_be_focused()
    page.keyboard.press('Control+A')
    page.keyboard.insert_text('Через клавиатуру')
    page.locator('#save-task').focus()
    page.keyboard.press('Enter')
    target=card(page,'Через клавиатуру').get_by_role('button',name='Изменить задачу: Через клавиатуру',exact=True)
    expect(target).to_be_focused()
    for _ in range(20):
        page.keyboard.press('Tab')
        assert not page.evaluate("!!document.activeElement.closest('#completed-list') && !document.getElementById('completed').open")

@case('AT-33/43: widths, hit areas, long title and source fallback do not overflow')
def layout(page):
    seed(page,[task('Д'*200,'2026-10-22','10:00'),task('Готово',completed=True)])
    for width in [320,390,768,1440]:
        page.set_viewport_size(dict(width=width,height=900))
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        for node in page.locator('button:visible, input:not([type=checkbox]):visible, .task-check:visible, summary:visible').all():
            box=node.bounding_box()
            assert box['width']>=44 and box['height']>=44, box
        assert page.locator('#task-title').evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')>=16
    page.set_viewport_size(dict(width=390,height=844))
    page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
    corrupt(page,'x'*20000)
    page.evaluate("Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true})")
    page.locator('#copy-raw').click()
    expect(page.locator('#raw-data')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')

def rgb(value): return [float(n)/255 for n in re.findall(r'[\d.]+',value)[:3]]
def luminance(value):
    components=[n/12.92 if n<=0.04045 else ((n+0.055)/1.055)**2.4 for n in rgb(value)]
    return sum(a*b for a,b in zip(components,[0.2126,0.7152,0.0722]))
def contrast(a,b):
    values=sorted([luminance(a),luminance(b)])
    return (values[1]+0.05)/(values[0]+0.05)

@case('AT-43: actual text, placeholder, button hover, borders and focus contrast')
def contrasts(page):
    add(page,'Просроченная','2026-10-19')
    selectors=['h1','.greeting','.storage-note','.task-title','.task-meta','.overdue-label',
        '.primary','.secondary','.optional','label[for=task-title]','#status']
    js="""e=>{const s=getComputedStyle(e);let p=e,bg;
      while(p){bg=getComputedStyle(p).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)'&&bg!=='transparent')break;p=p.parentElement;}
      return {fg:s.color,bg:bg||'rgb(17,22,25)',border:s.borderTopColor,outline:s.outlineColor};}"""
    for selector in selectors:
        for node in page.locator(selector).all():
            if node.is_visible():
                colors=node.evaluate(js)
                assert contrast(colors['fg'],colors['bg'])>=4.5,(selector,colors)
    for selector in ['.primary','.secondary']:
        node=page.locator(selector+':visible').first
        node.hover()
        colors=node.evaluate(js)
        assert contrast(colors['fg'],colors['bg'])>=4.5
    for node in page.locator('#task-title,#task-date,.secondary,.task-check input').all():
        if node.is_visible():
            colors=node.evaluate(js)
            assert contrast(colors['border'],colors['bg'])>=3,(colors,node)
    placeholder=page.locator('#task-title').evaluate("e=>({fg:getComputedStyle(e,'::placeholder').color,bg:getComputedStyle(e).backgroundColor})")
    assert contrast(placeholder['fg'],placeholder['bg'])>=4.5
    page.locator('#task-title').focus()
    page.keyboard.press('Tab')
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle")!='none'
    colors=page.locator('#task-date').evaluate(js)
    assert contrast(colors['outline'],colors['bg'])>=3

@case('AT-33: 200 percent CSS zoom keeps layout and actions usable')
def zoom(page):
    title=('Длинная задача '*10).strip()
    seed(page,[task(title,'2026-10-22','10:00')])
    page.set_viewport_size(dict(width=1280,height=900))
    page.evaluate("document.documentElement.style.zoom='2'")
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    card(page,title).get_by_role('button',name=re.compile('Изменить')).click()
    expect(page.locator('#task-title')).to_be_focused()
    page.locator('#cancel-edit').click()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(OUT/'zoom-200.png'),full_page=True)

if __name__=='__main__':
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=CHROMIUM,headless=True,args=['--no-sandbox'])
        print(f'Chromium {browser.version}, {platform.system()}',flush=True)
        selected=[(name,fn) for name,fn in CASES if os.environ.get('BRO_TEST_CASE','') in name]
        assert selected, 'No browser scenarios selected'
        for name,fn in selected:
            options=dict(locale='ru-RU',viewport=dict(width=1280,height=900))
            if 'timezone change' not in name:
                options['timezone_id']='Europe/Moscow'
            context=browser.new_context(**options)
            page=context.new_page()
            page.set_default_timeout(6000)
            errors=[]
            external=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:external.append(r.url) if not r.url.startswith(BASE) else None)
            page.add_init_script("""window.__writes=0;window.__reads=0;
              const get=Storage.prototype.getItem,set=Storage.prototype.setItem;
              Storage.prototype.getItem=function(k){if(k==='whats-up-bro.storage')window.__reads++;return get.call(this,k)};
              Storage.prototype.setItem=function(k,v){set.call(this,k,v);if(k==='whats-up-bro.storage')window.__writes++};""")
            page.clock.install(time=datetime(2026,10,20,8,59,0,tzinfo=timezone.utc))
            page.clock.pause_at(datetime(2026,10,20,8,59,30,tzinfo=timezone.utc))
            page.goto(BASE)
            try:
                fn(page)
                assert not errors,errors
                assert not external,external
                print('PASS '+name,flush=True)
            except Exception:
                page.screenshot(path=str(OUT/'failure.png'),full_page=True)
                (OUT/'failure.html').write_text(page.content())
                print('FAIL '+name,flush=True)
                raise
            finally: context.close()
        browser.close()
        print(f'Passed {len(selected)} scenarios; no JavaScript errors or external requests.',flush=True)
