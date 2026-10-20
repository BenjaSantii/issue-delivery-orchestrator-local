// Exercises the mobile review helpers against fixture pages in real headless Chromium.
// Requires Playwright in PLAYWRIGHT_WORKTREE (a checkout whose node_modules has it).

import assert from 'node:assert/strict';
import { after, before, describe, test } from 'node:test';
import {
  hideKeyboard,
  inspectFocused,
  inspectReachable,
  openPhone,
  showKeyboard,
  touchDrag,
  touchSwipe,
} from '../../scripts/playwright-review/mobile.mjs';

const worktree = process.env.PLAYWRIGHT_WORKTREE;

const DRAG_PAGE = (busyMs) => `data:text/html,${encodeURIComponent(`<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;height:100vh;touch-action:none}#card{position:absolute;left:40px;top:120px;width:80px;height:80px;background:#4a7}</style>
<div id="card"></div>
<script>
  const card = document.getElementById('card');
  let start = null, origin = { x: 40, y: 120 };
  card.addEventListener('pointerdown', (event) => { start = { x: event.clientX, y: event.clientY }; card.setPointerCapture(event.pointerId); });
  card.addEventListener('pointermove', (event) => {
    if (!start) return;
    const stop = performance.now() + ${busyMs};
    while (performance.now() < stop) {}
    card.style.left = origin.x + event.clientX - start.x + 'px';
    card.style.top = origin.y + event.clientY - start.y + 'px';
  });
  card.addEventListener('pointerup', () => { origin = { x: parseFloat(card.style.left), y: parseFloat(card.style.top) }; start = null; });
</script>`)}`;

const FORM_PAGE = (stickyFooter) => `data:text/html,${encodeURIComponent(`<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{margin:0;font:16px sans-serif}
  main{padding:16px;${stickyFooter ? 'height:100vh;box-sizing:border-box;overflow:auto;padding-bottom:80px' : ''}}
  .spacer{height:560px}
  input{display:block;width:100%;box-sizing:border-box;font-size:16px;padding:12px}
  footer{${stickyFooter ? 'position:fixed;left:0;right:0;bottom:0' : ''};padding:12px;background:#eee}
</style>
<main><div class="spacer"></div><input id="note" placeholder="Nota"></main>
<footer><button id="save">Guardar</button></footer>`)}`;

const PINNED_PAGE = `data:text/html,${encodeURIComponent(`<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;height:100%;overflow:hidden}input{position:absolute;top:80px;left:16px;font-size:16px}#save{position:absolute;top:700px;left:16px}</style>
<input id="note" placeholder="Nota"><button id="save">Guardar</button>`)}`;

const SCROLL_PAGE = `data:text/html,${encodeURIComponent(`<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0}#list{height:100vh;overflow:auto}#list div{height:80px;border-bottom:1px solid #ccc}</style>
<div id="list">${'<div></div>'.repeat(40)}</div>`)}`;

describe('mobile review helpers', { skip: !worktree && 'PLAYWRIGHT_WORKTREE is not set' }, () => {
  let session;

  before(async () => {
    session = await openPhone({ worktree, phone: 'android-small' });
  });

  after(async () => {
    await session?.browser.close();
  });

  test('opens a touch-enabled phone viewport', async () => {
    await session.page.goto(SCROLL_PAGE);
    const metrics = await session.page.evaluate(() => ({
      width: window.innerWidth,
      touch: navigator.maxTouchPoints > 0,
    }));
    assert.equal(metrics.width, 360);
    assert.equal(metrics.touch, true);
  });

  test('a responsive drag follows the finger smoothly', async () => {
    await session.page.goto(DRAG_PAGE(0));
    const result = await touchDrag(session.page, {
      from: { x: 80, y: 160 },
      to: { x: 280, y: 560 },
      track: session.page.locator('#card'),
    });
    assert.equal(result.tracking.moved, true);
    assert.ok(result.tracking.medianLagPx <= 24, JSON.stringify(result));
    assert.notEqual(result.verdict, 'janky', JSON.stringify(result));
  });

  test('a drag blocked by slow handlers is reported as janky', async () => {
    await session.page.goto(DRAG_PAGE(140));
    const result = await touchDrag(session.page, {
      from: { x: 80, y: 160 },
      to: { x: 280, y: 560 },
      track: session.page.locator('#card'),
    });
    assert.equal(result.verdict, 'janky', JSON.stringify(result));
  });

  test('a fixed element that ignores touches is reported as not moving', async () => {
    await session.page.goto(FORM_PAGE(false));
    const result = await touchDrag(session.page, {
      from: { x: 40, y: 40 },
      to: { x: 200, y: 200 },
      steps: 10,
      durationMs: 200,
      track: session.page.locator('#save'),
    });
    assert.equal(result.tracking.moved, false);
    assert.equal(result.verdict, 'janky');
  });

  test('android keyboard: a field that reflows above the keyboard is visible', async () => {
    await session.page.goto(FORM_PAGE(false));
    await session.page.locator('#note').tap();
    const keyboard = await showKeyboard(session.page, session.phone);
    await session.page.locator('#note').scrollIntoViewIfNeeded();
    const focused = await inspectFocused(session.page, keyboard);
    assert.equal(focused.focused, true);
    assert.equal(focused.visible, true, JSON.stringify(focused));
    await hideKeyboard(session.page, session.phone);
  });

  test('ios keyboard: a field behind the keyboard is web-reachable but a native risk', async () => {
    const iphone = await openPhone({ worktree, phone: 'iphone', url: FORM_PAGE(true) });
    try {
      await iphone.page.locator('#note').tap();
      const keyboard = await showKeyboard(iphone.page, iphone.phone);
      const focused = await inspectFocused(iphone.page, keyboard);
      assert.equal(focused.aboveKeyboard, false, JSON.stringify(focused));
      assert.equal(focused.visible, true, JSON.stringify(focused));
      assert.equal(focused.nativeRisk, true, JSON.stringify(focused));
      const save = await inspectReachable(iphone.page, iphone.page.locator('#save'), keyboard);
      assert.deepEqual([save.reachable, save.how, save.nativeRisk], [true, 'pan', true]);
    } finally {
      await iphone.browser.close();
    }
  });

  test('android keyboard: a button pinned below the keyboard without scroll is hidden', async () => {
    await session.page.goto(PINNED_PAGE);
    await session.page.locator('#note').tap();
    const keyboard = await showKeyboard(session.page, session.phone);
    const save = await inspectReachable(session.page, session.page.locator('#save'), keyboard);
    assert.equal(save.reachable, false, JSON.stringify(save));
    await hideKeyboard(session.page, session.phone);
  });

  test('swipe scrolls a list container', async () => {
    await session.page.goto(SCROLL_PAGE);
    const result = await touchSwipe(session.page, {
      from: { x: 180, y: 600 },
      to: { x: 180, y: 200 },
      scroller: session.page.locator('#list'),
    });
    assert.ok(result.scrolled.top > 100, JSON.stringify(result.scrolled));
  });
});
