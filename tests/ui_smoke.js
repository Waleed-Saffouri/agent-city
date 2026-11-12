// UI smoke test: opens every building and dock panel and fails on any page error.
// Run with the server up:  node tests/ui_smoke.js
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({viewport:{width:1280,height:760}});
  const errs=[]; p.on('pageerror', e=>errs.push(e.message));
  await p.goto('http://localhost:8000'); await p.waitForTimeout(1500);
  const out = {};
  for (const id of ["manager","scout","sentinel","writer","planner","school","inbox","trader"]) {
    const c = await p.evaluate(id => { const s = shapes.find(x=>x.id===id); const xs=s.pts.map(q=>q[0]), ys=s.pts.map(q=>q[1]); return [(Math.min(...xs)+Math.max(...xs))/2,(Math.max(...ys)*0.6+Math.min(...ys)*0.4)]; }, id);
    await p.evaluate(() => close()); await p.waitForTimeout(350);
    const c2 = await p.evaluate(id => { const s = shapes.find(x=>x.id===id); const xs=s.pts.map(q=>q[0]), ys=s.pts.map(q=>q[1]); return [(Math.min(...xs)+Math.max(...xs))/2,(Math.max(...ys)*0.6+Math.min(...ys)*0.4)]; }, id);
    await p.mouse.click(c2[0], c2[1]); await p.waitForTimeout(400);
    out[id] = await p.textContent('#pTitle');
  }
  for (const d of ["manager","planner","scout","writer","sentinel","log"]) { await p.evaluate(() => close()); await p.waitForTimeout(300); await p.click(`[data-open=${d}]`); await p.waitForTimeout(300); out['dock:'+d] = await p.textContent('#pTitle'); }
  await p.screenshot({path:'/tmp/claude-0/log.png'});
  console.log(JSON.stringify(out)); console.log('errors:', JSON.stringify(errs)); await b.close(); if (errs.length || Object.values(out).some(v => !v)) process.exit(1);
})();
