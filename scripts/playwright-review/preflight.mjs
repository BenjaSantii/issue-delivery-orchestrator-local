#!/usr/bin/env node
// Preflight for issue-delivery-playwright-review: Playwright resolves from the target
// worktree, headless Chromium launches, and every Local Runtime URL answers.
// Prints one JSON object; exits 1 when the reviewer must return BLOCKED.

import { parseArgs } from 'node:util';
import { loadPlaywright, phoneProfile } from './mobile.mjs';

const { values } = parseArgs({
  options: {
    worktree: { type: 'string' },
    url: { type: 'string', multiple: true, default: [] },
    phone: { type: 'string', default: 'android-small' },
  },
});

const report = { status: 'PASS', checks: [] };
const fail = (name, detail, hint) => {
  report.status = 'BLOCKED';
  report.checks.push({ name, ok: false, detail, ...(hint ? { hint } : {}) });
};
const pass = (name, detail) => report.checks.push({ name, ok: true, detail });

if (!values.worktree) {
  fail('arguments', '--worktree is required');
  finish();
}

let playwright;
try {
  playwright = loadPlaywright(values.worktree);
  pass('playwright', `${playwright.name}@${playwright.version}`);
} catch (error) {
  fail('playwright', error.message);
  finish();
}

let browser;
try {
  browser = await playwright.chromium.launch({ headless: true });
  pass('chromium', browser.version());
} catch (error) {
  fail(
    'chromium',
    error.message.split('\n')[0],
    'In Conductor Cloud run: corepack pnpm exec playwright install chromium',
  );
  finish();
}

try {
  const phone = phoneProfile(values.phone);
  const context = await browser.newContext({
    viewport: { width: phone.width, height: phone.height },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: phone.deviceScaleFactor,
    userAgent: phone.userAgent,
  });
  pass('phone', `${phone.name} ${phone.width}x${phone.height}`);
  for (const url of values.url) {
    const page = await context.newPage();
    try {
      const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      const status = response?.status() ?? 0;
      if (status >= 200 && status < 400) pass(`url ${url}`, `HTTP ${status}`);
      else fail(`url ${url}`, `HTTP ${status}`, 'Start the app through pnpm local-runtime first');
    } catch (error) {
      fail(`url ${url}`, error.message.split('\n')[0], 'Start the app through pnpm local-runtime first');
    } finally {
      await page.close();
    }
  }
  await context.close();
} catch (error) {
  fail('phone', error.message);
}

await browser.close();
finish();

function finish() {
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  process.exit(report.status === 'PASS' ? 0 : 1);
}
