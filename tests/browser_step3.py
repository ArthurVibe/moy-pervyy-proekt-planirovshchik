"""Final smoke checks; developer tool only, never shipped with prototype/."""
import os
import platform
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('BRO_TEST_BASE_URL', 'http://127.0.0.1:8765/').rstrip('/') + '/'
KEY = 'whats-up-bro.storage'
ROOT = Path(__file__).resolve().parents[1]


def saved(page):
    return page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', KEY)


def card(page, title):
    return page.locator('.task-card').filter(has=page.get_by_text(title, exact=True))


def monitor(page, issues):
    page.on('pageerror', lambda error: issues.append(str(error)))
    page.on('console', lambda msg: issues.append(msg.text) if msg.type == 'error' else None)
    page.on('request', lambda request: issues.append('Unexpected request: ' + request.url)
            if request.url not in (BASE, BASE + 'style.css', BASE + 'script.js') else None)
    page.on('requestfailed', lambda request: issues.append('Failed request: ' + request.url))


def smoke(engine, launch_options, profile):
    issues = []
    options = dict(headless=True, locale='ru-RU', timezone_id='Europe/Moscow', **launch_options)
    context = engine.launch_persistent_context(profile, **options)
    try:
        page = context.pages[0]
        monitor(page, issues)
        page.goto(BASE)
        expect(page.locator('#empty-state')).to_be_visible()
        assert page.evaluate('(k) => localStorage.getItem(k)', KEY) is None
        page.locator('#task-title').fill('Итоговая проверка')
        page.locator('#task-date').fill('2026-10-22')
        page.locator('#task-time').fill('10:30')
        page.get_by_role('button', name='Добавить', exact=True).click()
        original = saved(page)['tasks'][0]
        card(page, original['title']).get_by_role('button', name=re.compile('Изменить')).click()
        page.locator('#task-title').fill('Исправленная задача')
        page.get_by_role('button', name='Сохранить', exact=True).click()
        updated = saved(page)['tasks'][0]
        assert updated['id'] == original['id']
        assert updated['createdAt'] == original['createdAt']
        assert updated['dueTime'] == '10:30'
        card(page, updated['title']).get_by_role('checkbox').check()
        expect(page.locator('#completed-heading')).to_have_text('Выполненные · 1')
        assert saved(page)['tasks'][0]['completed'] is True
        page.reload()
        assert page.locator('#completed').get_attribute('open') is None
        page.locator('#completed-heading').click()
        card(page, updated['title']).get_by_role('checkbox').uncheck()
        page.once('dialog', lambda dialog: dialog.dismiss())
        card(page, updated['title']).get_by_role('button', name=re.compile('Удалить')).click()
        assert len(saved(page)['tasks']) == 1
        page.once('dialog', lambda dialog: dialog.accept())
        card(page, updated['title']).get_by_role('button', name=re.compile('Удалить')).click()
        page.reload()
        assert saved(page)['tasks'] == []
        print('PASS core actions and reload', flush=True)

        # Offline operation after assets arrive; no claim of offline site reopening.
        context.set_offline(True)
        page.locator('#task-title').fill('Работает после загрузки без сети')
        page.locator('#task-title').press('Enter')
        expect(page.locator('.task-title')).to_have_text('Работает после загрузки без сети')
        snapshot = saved(page)
        assert len(snapshot['tasks']) == 1
        context.set_offline(False)
        assert not issues, issues
        print('PASS offline actions after assets loaded; no console errors or external requests', flush=True)
        version = context.browser.version
    finally:
        context.close()

    context = engine.launch_persistent_context(profile, **options)
    try:
        page = context.pages[0]
        monitor(page, issues)
        page.goto(BASE)
        assert saved(page) == snapshot
        expect(page.locator('.task-title')).to_have_text(snapshot['tasks'][0]['title'])
        expect(page.locator('#task-title')).to_have_value('')
        expect(page.locator('#task-date')).to_have_value('')
        print('PASS full browser shutdown/relaunch with disk-backed storage', flush=True)
        # Isolated desktop analogue of AT-42, not an iPhone/Safari result.
        page.evaluate('(key) => localStorage.removeItem(key)', KEY)
        page.reload()
        expect(page.locator('#empty-state')).to_be_visible()
        assert page.locator('.task-card').count() == 0
        assert page.evaluate('(k) => localStorage.getItem(k)', KEY) is None
        assert not issues, issues
        print('PASS cleared app data produces first launch without restoring records', flush=True)
    finally:
        context.close()
    return version


if __name__ == '__main__':
    assert sorted(p.name for p in (ROOT / 'prototype').iterdir()) == ['index.html', 'script.js', 'style.css']
    print('PASS application contains exactly three files', flush=True)
    passed, missing = [], []
    with sync_playwright() as playwright:
        for name in ('chromium', 'firefox', 'webkit'):
            engine = getattr(playwright, name)
            options = {}
            executable = engine.executable_path
            if name == 'chromium':
                executable = os.environ.get('BRO_TEST_CHROMIUM', '/usr/bin/chromium')
                options = dict(executable_path=executable, args=['--no-sandbox'])
            if not Path(executable).is_file():
                missing.append(name)
                print(f'NOT RUN {name}: executable not installed', flush=True)
                continue
            with TemporaryDirectory(prefix=f'bro-final-{name}-') as profile:
                version = smoke(engine, options, profile)
            passed.append(name)
            print(f'PASS {name} {version}, {platform.system()}: 4 scenarios', flush=True)
    assert passed, 'No browser was tested'
    print(f'Passed: {len(passed) * 4} scenarios; unavailable engines: {", ".join(missing) or "none"}.', flush=True)
