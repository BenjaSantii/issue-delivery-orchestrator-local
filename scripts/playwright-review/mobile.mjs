// Helpers for issue-delivery-playwright-review and the mobile-web-reviewer agent.
// They drive the real UI like a user would (touch, typing, scrolling) and only add
// read-only instrumentation (frame and long-task sampling). They never mutate DOM,
// storage, or application state.

import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import path from 'node:path';

const ANDROID_UA =
  'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/131.0.0.0 Mobile Safari/537.36';
const IOS_UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 ' +
  '(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';

// Sizes in CSS px (dp). keyboardHeight approximates the on-screen keyboard plus its
// suggestion bar in portrait.
export const PHONES = {
  'android-small': {
    platform: 'android',
    width: 360,
    height: 800,
    deviceScaleFactor: 3,
    keyboardHeight: 336,
    userAgent: ANDROID_UA,
  },
  iphone: {
    platform: 'ios',
    width: 390,
    height: 844,
    deviceScaleFactor: 3,
    keyboardHeight: 336,
    userAgent: IOS_UA,
  },
};

const FRAME_BUDGET_MS = 1000 / 60;

export function loadPlaywright(worktree) {
  const require = createRequire(path.join(path.resolve(worktree), 'package.json'));
  for (const name of ['playwright', '@playwright/test']) {
    try {
      const module = require(name);
      if (module.chromium) {
        const version = require(`${name}/package.json`).version;
        return { chromium: module.chromium, devices: module.devices, name, version };
      }
    } catch (error) {
      if (error.code !== 'MODULE_NOT_FOUND') throw error;
    }
  }
  throw new Error(
    `Playwright is not installed in ${worktree}. Run the repository install before reviewing.`,
  );
}

export function phoneProfile(name) {
  const phone = PHONES[name];
  if (!phone) {
    throw new Error(`Unknown phone ${name}. Use one of: ${Object.keys(PHONES).join(', ')}`);
  }
  return { name, ...phone };
}

export async function openPhone({ worktree, phone = 'android-small', url, storageState, artifactsDir }) {
  const profile = phoneProfile(phone);
  const { chromium } = loadPlaywright(worktree);
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: profile.width, height: profile.height },
    screen: { width: profile.width, height: profile.height },
    deviceScaleFactor: profile.deviceScaleFactor,
    isMobile: true,
    hasTouch: true,
    userAgent: profile.userAgent,
    storageState: storageState && existsSync(storageState) ? storageState : undefined,
    recordVideo: artifactsDir
      ? { dir: artifactsDir, size: { width: profile.width, height: profile.height } }
      : undefined,
  });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => consoleErrors.push(String(error)));
  if (url) await page.goto(url, { waitUntil: 'networkidle' });
  return { browser, context, page, phone: profile, consoleErrors };
}

// Simulates the on-screen keyboard. On Android Chrome the layout viewport shrinks
// (resizes-content), so the app must reflow. On iOS Safari only the visual viewport
// shrinks; the keyboard covers the bottom of the layout, so the check is occlusion.
export async function showKeyboard(page, phone, { height = phone.keyboardHeight } = {}) {
  const keyboardTop = phone.height - height;
  if (phone.platform === 'android') {
    await page.setViewportSize({ width: phone.width, height: keyboardTop });
  }
  await settle(page);
  return { platform: phone.platform, keyboardTop, height };
}

export async function hideKeyboard(page, phone) {
  if (phone.platform === 'android') {
    await page.setViewportSize({ width: phone.width, height: phone.height });
  }
  await settle(page);
}

// Reports whether the focused field is usable with the keyboard open and not covered
// by another element. On Android the field must sit above the keyboard. On iOS Safari
// the visual viewport pans over the layout, so a field behind the keyboard is still
// reachable in web, but the same layout would be covered in a native app without
// keyboard avoidance: that case is flagged as `nativeRisk`.
export async function inspectFocused(page, { keyboardTop, platform }) {
  return page.evaluate(({ keyboardTop, platform }) => {
    const element = document.activeElement;
    if (!element || element === document.body) return { focused: false };
    const rect = element.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = Math.min(rect.top + rect.height / 2, window.innerHeight - 1);
    const hit = document.elementFromPoint(centerX, centerY);
    const coveredByOther = Boolean(hit && hit !== element && !element.contains(hit) && !hit.contains(element));
    const aboveKeyboard = rect.bottom <= keyboardTop && rect.top >= 0;
    const insideLayout = rect.top >= 0 && rect.bottom <= window.innerHeight;
    const reachable = platform === 'ios' ? insideLayout : aboveKeyboard;
    return {
      focused: true,
      tag: element.tagName.toLowerCase(),
      label:
        element.getAttribute('aria-label') ||
        element.getAttribute('placeholder') ||
        element.getAttribute('name') ||
        element.id ||
        null,
      rect: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
      keyboardTop,
      aboveKeyboard,
      coveredByOther,
      visible: reachable && !coveredByOther,
      nativeRisk: platform === 'ios' && !aboveKeyboard,
    };
  }, { keyboardTop, platform });
}

// Checks a target (for example the submit button) while the keyboard is open:
// visible above the keyboard, reachable with finger swipes, reachable on iOS only by
// panning the visual viewport (`pan`, with `nativeRisk`), or unreachable. It scrolls
// only with touch gestures: programmatic scrolling would also move containers that a
// user cannot scroll.
export async function inspectReachable(page, locator, keyboard, { maxSwipes = 4 } = {}) {
  const layoutHeight = await page.evaluate(() => window.innerHeight);
  const classify = (box, moved) => {
    if (!box || box.y < 0) return null;
    const bottom = box.y + box.height;
    if (bottom <= keyboard.keyboardTop) {
      return { reachable: true, how: moved ? 'scroll' : 'visible', nativeRisk: false, box };
    }
    if (keyboard.platform === 'ios' && bottom <= layoutHeight) {
      return { reachable: true, how: 'pan', nativeRisk: true, box };
    }
    return null;
  };
  const before = classify(await locator.boundingBox(), false);
  if (before) return before;
  const width = page.viewportSize().width;
  const visibleBottom = Math.min(keyboard.keyboardTop, layoutHeight);
  for (let swipe = 0; swipe < maxSwipes; swipe += 1) {
    const box = await locator.boundingBox();
    // Finger moves up to bring content below into view, down for content above.
    const direction = box && box.y < 0 ? 1 : -1;
    const distance = Math.round(visibleBottom * 0.5) * direction;
    const startY = Math.round(visibleBottom * (direction < 0 ? 0.75 : 0.25));
    await touchSwipe(page, {
      from: { x: Math.round(width / 2), y: startY },
      to: { x: Math.round(width / 2), y: startY + distance },
    });
    const found = classify(await locator.boundingBox(), true);
    if (found) return found;
  }
  return { reachable: false, how: 'hidden', nativeRisk: false, box: await locator.boundingBox() };
}

// Performs a real touch drag through CDP with many intermediate points and measures
// smoothness: frame intervals, long tasks, and how closely `track` follows the finger.
export async function touchDrag(
  page,
  { from, to, steps = 40, durationMs = 700, holdMs = 0, track, expectFollow = Boolean(track) },
) {
  const cdp = await page.context().newCDPSession(page);
  await startSampling(page);
  const trackBox = track ? await track.boundingBox() : null;
  const grabOffset = trackBox
    ? { x: from.x - (trackBox.x + trackBox.width / 2), y: from.y - (trackBox.y + trackBox.height / 2) }
    : null;
  const lags = [];

  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: from.x, y: from.y, id: 1 }],
  });
  if (holdMs) await page.waitForTimeout(holdMs);
  for (let index = 1; index <= steps; index += 1) {
    const ratio = index / steps;
    const point = { x: from.x + (to.x - from.x) * ratio, y: from.y + (to.y - from.y) * ratio };
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: point.x, y: point.y, id: 1 }],
    });
    await page.waitForTimeout(durationMs / steps);
    if (track && expectFollow) {
      const box = await track.boundingBox();
      if (box) {
        const center = { x: box.x + box.width / 2 + grabOffset.x, y: box.y + box.height / 2 + grabOffset.y };
        lags.push(Math.hypot(center.x - point.x, center.y - point.y));
      }
    }
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await settle(page);
  const samples = await stopSampling(page);
  await cdp.detach();
  const finalBox = track ? await track.boundingBox() : null;
  return summarizeGesture({ samples, lags, steps, durationMs, trackBox, finalBox });
}

// Finger swipe that scrolls like a real touch fling (Chromium's synthetic touch scroll
// gesture). Reports scroll movement and smoothness; `scroller` is the element expected
// to scroll (defaults to the page). Moving the finger up scrolls the content down.
export async function touchSwipe(page, { from, to, speed = 1200, scroller }) {
  const read = () =>
    scroller
      ? scroller.evaluate((element) => ({ top: element.scrollTop, left: element.scrollLeft }))
      : page.evaluate(() => ({ top: window.scrollY, left: window.scrollX }));
  const before = await read();
  const cdp = await page.context().newCDPSession(page);
  await startSampling(page);
  const startedAt = Date.now();
  await cdp.send('Input.synthesizeScrollGesture', {
    x: from.x,
    y: from.y,
    xDistance: to.x - from.x,
    yDistance: to.y - from.y,
    speed,
    gestureSourceType: 'touch',
    preventFling: false,
  });
  await page.waitForTimeout(400);
  const durationMs = Date.now() - startedAt;
  const samples = await stopSampling(page);
  await cdp.detach();
  const after = await read();
  return {
    ...summarizeGesture({ samples, lags: [], steps: 0, durationMs }),
    scrolled: { top: after.top - before.top, left: after.left - before.left },
  };
}

export async function settle(page) {
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
}

async function startSampling(page) {
  await page.evaluate(() => {
    const probe = { frames: [], longTasks: [], running: true };
    const tick = (time) => {
      if (!probe.running) return;
      probe.frames.push(time);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    try {
      probe.observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) probe.longTasks.push(entry.duration);
      });
      probe.observer.observe({ type: 'longtask', buffered: false });
    } catch {
      probe.longTaskUnsupported = true;
    }
    window.__issueDeliveryGestureProbe = probe;
  });
}

async function stopSampling(page) {
  return page.evaluate(() => {
    const probe = window.__issueDeliveryGestureProbe;
    probe.running = false;
    probe.observer?.disconnect();
    delete window.__issueDeliveryGestureProbe;
    return {
      frames: probe.frames,
      longTasks: probe.longTasks,
      longTaskUnsupported: Boolean(probe.longTaskUnsupported),
    };
  });
}

function percentile(values, ratio) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor(ratio * sorted.length))];
}

function round(value) {
  return value === null || value === undefined ? null : Math.round(value * 10) / 10;
}

export function summarizeGesture({ samples, lags, steps, durationMs, trackBox, finalBox }) {
  const intervals = samples.frames.slice(1).map((time, index) => time - samples.frames[index]);
  const expectedFrames = Math.floor(durationMs / FRAME_BUDGET_MS);
  const droppedFrames = intervals.reduce(
    (total, interval) => total + Math.max(0, Math.round(interval / FRAME_BUDGET_MS) - 1),
    0,
  );
  const frames = {
    sampled: samples.frames.length,
    expected: expectedFrames,
    p50Ms: round(percentile(intervals, 0.5)),
    p95Ms: round(percentile(intervals, 0.95)),
    maxMs: round(intervals.length ? Math.max(...intervals) : null),
    dropped: droppedFrames,
  };
  const longTasks = {
    count: samples.longTasks.length,
    maxMs: round(samples.longTasks.length ? Math.max(...samples.longTasks) : 0),
    unsupported: samples.longTaskUnsupported,
  };
  const tracking = lags.length
    ? {
        medianLagPx: round(percentile(lags, 0.5)),
        maxLagPx: round(Math.max(...lags)),
        moved: Boolean(
          trackBox &&
            finalBox &&
            Math.hypot(finalBox.x - trackBox.x, finalBox.y - trackBox.y) > 4,
        ),
      }
    : null;

  const problems = [];
  // Fewer than half the expected frames means the headless renderer throttled
  // requestAnimationFrame; frame metrics cannot judge smoothness in that case.
  const framesTrustworthy = frames.sampled >= expectedFrames / 2;
  if (framesTrustworthy && frames.p95Ms > FRAME_BUDGET_MS * 2.1) {
    problems.push(`p95 frame interval ${frames.p95Ms}ms exceeds two frames`);
  }
  if (longTasks.maxMs >= 100) problems.push(`long task of ${longTasks.maxMs}ms during the gesture`);
  if (tracking && tracking.medianLagPx > 24) {
    problems.push(`element trails the finger by ${tracking.medianLagPx}px (median)`);
  }
  if (tracking && !tracking.moved) problems.push('dragged element did not move');

  const verdict = problems.length ? 'janky' : framesTrustworthy ? 'smooth' : 'inconclusive';
  return { verdict, problems, frames, longTasks, tracking, steps, durationMs };
}
