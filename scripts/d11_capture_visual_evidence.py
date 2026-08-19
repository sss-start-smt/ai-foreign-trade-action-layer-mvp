from pathlib import Path
import json, re, requests, hashlib
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
PROJECT_ROOT=ROOT.parents[2]
OUT=PROJECT_ROOT/'04_EVIDENCE'/'D11_V04_FINAL_VISUAL'
OUT.mkdir(parents=True,exist_ok=True)
base='http://127.0.0.1:8001'; headers={'X-Auth-Token':'tok-user-1'}

paths=[
'/health','/api/settings?user_id=USER-1','/api/reviews?status=PENDING','/api/operators',
'/api/action-workspace','/api/orders?current_user_id=USER-1','/api/reviews',
'/api/agent/overview?current_user_id=USER-1&current_role=operator',
'/api/action-workspace/AC-D11-UAT','/api/action-workspace/AC-D11-NORMAL',
'/api/orders/ORD-D11-UAT?current_user_id=USER-1','/api/orders/ORD-D11-NORMAL?current_user_id=USER-1',
]
api_data={}
for path in paths:
    r=requests.get(base+path,headers=headers,timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f'{path} -> {r.status_code} {r.text[:500]}')
    api_data[path]=r.json()

index=(ROOT/'static/index.html').read_text(encoding='utf-8')
css=(ROOT/'static/styles.css').read_text(encoding='utf-8')
js=(ROOT/'static/app.js').read_text(encoding='utf-8')
# Use the actual shell HTML and styles, but make the visual runner self-contained.
index=re.sub(r'<link rel="preconnect"[^>]*>\s*','',index)
index=re.sub(r'<link href="https://fonts.googleapis.com[^>]*>\s*','',index)
index=re.sub(r'<link rel="stylesheet" href="/static/styles.css[^>]*>','',index)
index=re.sub(r'<script src="/static/app.js[^>]*></script>','',index)
index=index.replace('</head>', f'<style>\n{css}\n</style></head>')

fetch_stub=f"""
window.__EVIDENCE_API__ = {json.dumps(api_data, ensure_ascii=False)};
window.fetch = async function(url, options={{}}) {{
  const key = String(url);
  if (Object.prototype.hasOwnProperty.call(window.__EVIDENCE_API__, key)) {{
    return new Response(JSON.stringify(window.__EVIDENCE_API__[key]), {{status:200, headers:{{'Content-Type':'application/json'}}}});
  }}
  return new Response(JSON.stringify({{detail:'visual evidence stub has no fixture for '+key}}), {{status:404, headers:{{'Content-Type':'application/json'}}}});
}};
"""

def assert_complete(page, expected_width):
    page.wait_for_function("() => !document.querySelector('.loading-state')", timeout=15000)
    page.wait_for_selector('.d11v4-grid, .d11v4-section, .d11v4-group', timeout=15000)
    metrics=page.evaluate("""() => ({
      width: window.innerWidth,
      height: window.innerHeight,
      sw: document.documentElement.scrollWidth,
      cw: document.documentElement.clientWidth,
      loading: !!document.querySelector('.loading-state'),
      role: document.body.dataset.role || ''
    })""")
    if metrics['width'] != expected_width: raise AssertionError(metrics)
    if metrics['loading']: raise AssertionError(metrics)
    if metrics['sw'] > metrics['cw'] + 1: raise AssertionError(f'horizontal overflow: {metrics}')
    if metrics['role'] != 'operator': raise AssertionError(f'role not resolved: {metrics}')
    return metrics

def shot(page, name, expected_width):
    metrics=assert_complete(page,expected_width)
    path=OUT/name
    page.screenshot(path=str(path),full_page=True)
    from PIL import Image
    with Image.open(path) as im:
        actual=im.size
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    return {'file':name,'viewport_width':expected_width,'screenshot_size':actual,'sha256':sha,'metrics':metrics}

results=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])

    def open_page(width, route_hash):
        page=browser.new_page(viewport={'width':width,'height':900})
        page.set_content(index,wait_until='domcontentloaded')
        page.evaluate(fetch_stub)
        page.evaluate("h => { location.hash = h; }", route_hash)
        page.add_script_tag(content=js)
        page.wait_for_function("() => !document.querySelector('.loading-state')", timeout=15000)
        return page

    # Today workbench at the three required desktop widths.
    for width in (1366,1440,1920):
        page=open_page(width,'today')
        page.wait_for_selector('.d11v4-grid',timeout=15000)
        results.append(shot(page,f'today_{width}.png',width))
        page.close()

    # Confirmation page, isolated from drawer animation/routing state.
    page=open_page(1366,'confirm')
    page.wait_for_selector('.d11v4-confirm-list',timeout=10000)
    page.wait_for_function("() => document.querySelectorAll('.d11v4-confirm-item').length >= 1", timeout=10000)
    results.append(shot(page,'confirm_1366.png',1366))
    page.close()

    # Orders grouped list.
    page=open_page(1366,'orders')
    page.wait_for_selector('.d11v4-group',timeout=10000)
    page.wait_for_function("() => document.querySelectorAll('.d11v4-order-row').length >= 2", timeout=10000)
    results.append(shot(page,'orders_1366.png',1366))
    page.close()

    # Current/actionable case drawer + completed-node expansion.
    page=open_page(1366,'today')
    page.wait_for_selector('.d11v4-grid',timeout=10000)
    page.locator('[data-case-detail="AC-D11-UAT"]').first.click()
    page.wait_for_selector('#drawer[aria-hidden="false"] .d11v4-flow-node.current.expanded',timeout=10000)
    # Wait for the 250ms drawer slide-in transition; otherwise a screenshot can
    # capture the panel mid-animation and falsely make it look too narrow.
    page.wait_for_timeout(350)
    results.append(shot(page,'drawer_current_1366.png',1366))
    toggle=page.locator('#drawer .d11v4-flow-node.completed .d11v4-flow-toggle').first
    if toggle.count():
        toggle.click()
        page.wait_for_selector('#drawer .d11v4-flow-node.completed.expanded',timeout=5000)
        page.wait_for_timeout(100)
        results.append(shot(page,'drawer_completed_expanded_1366.png',1366))
    page.close()

    # Normal order must use the same vertical timeline component.
    page=open_page(1366,'orders')
    page.wait_for_selector('.d11v4-order-row',timeout=10000)
    page.evaluate("openOrderOnlyFlowDrawer('ORD-D11-NORMAL')")
    page.wait_for_selector('#drawer[aria-hidden="false"] .d11v4-flow',timeout=10000)
    page.wait_for_timeout(350)
    results.append(shot(page,'drawer_normal_1366.png',1366))
    page.close()

    # Daily recap without any drawer overlay.
    page=open_page(1366,'recap')
    page.wait_for_selector('.d11v4-recap-grid',timeout=10000)
    results.append(shot(page,'recap_1366.png',1366))
    page.close()

    browser.close()

# Three desktop sizes must be distinct, and no screenshot may be mislabeled.
today=[x for x in results if x['file'].startswith('today_')]
if len({x['sha256'] for x in today}) != 3:
    raise AssertionError('desktop screenshots unexpectedly have identical hashes')
for x in today:
    if x['screenshot_size'][0] != x['viewport_width']:
        raise AssertionError(f'mislabeled screenshot: {x}')

(OUT/'visual_evidence_manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
