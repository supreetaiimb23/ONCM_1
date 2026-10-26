/* Open Network for Carbon Markets (ONCM) Portal front end - vanilla JS SPA (Tailwind for styling). */
const S = { user: null, cache: {} };  // the session is an HttpOnly cookie: page scripts never see it
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (d) => d ? new Date(d).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '-';
const fmtD = (d) => d ? new Date(d).toLocaleDateString([], { dateStyle: 'medium' }) : '-';
const label = (s) => String(s ?? '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
const CATS = ['01 Onboarding documents', '02 Agreements', '03 Implementation', '04 Meetings and decisions'];
const STAGES = ['joined', 'documents_in_progress', 'agreement_signed', 'implementing', 'live'];
const BADGE = { draft: 'bg-neutral-100 text-neutral-600', submitted: 'bg-neutral-200 text-neutral-800', under_review: 'bg-amber-100 text-amber-800',
  changes_requested: 'bg-orange-100 text-orange-800', internally_approved: 'bg-emerald-100 text-emerald-800',
  shared_externally: 'bg-brand-100 text-brand-700', rejected: 'bg-red-100 text-red-800', superseded: 'bg-neutral-200 text-neutral-600',
  pending: 'bg-amber-100 text-amber-800', approved: 'bg-emerald-100 text-emerald-800', more_info: 'bg-orange-100 text-orange-800',
  sent: 'bg-emerald-100 text-emerald-800', logged: 'bg-neutral-200 text-neutral-700', failed: 'bg-red-100 text-red-800', queued: 'bg-neutral-200 text-neutral-800',
  open: 'bg-neutral-200 text-neutral-800', in_progress: 'bg-amber-100 text-amber-800', done: 'bg-emerald-100 text-emerald-800' };
const badge = (s) => `<span class="badge ${BADGE[s] || 'bg-neutral-200'}">${esc(label(s))}</span>`;

async function api(path, { method = 'GET', body, form } = {}) {
  const h = { 'X-ONCM': '1' };  // required by the server for cookie-authenticated changes (CSRF protection)
  let payload; if (form) payload = form; else if (body) { h['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const r = await fetch('/api' + path, { method, headers: h, body: payload });
  if (r.status === 401 && S.user) { S.user = null; S.meta = null; boot(); throw new Error('Session expired. Please sign in again.'); }
  if (!r.ok) { let m = r.statusText; try { const j = await r.json(); m = typeof j.detail === 'string' ? j.detail : (j.detail || []).map(x => { const custom = String(x.msg).startsWith('Value error, '); const fld = (x.loc || []).slice(-1)[0]; const msg = String(x.msg).replace(/^Value error, /, ''); return (!custom && typeof fld === 'string' && fld !== 'body') ? label(fld) + ': ' + msg : msg; }).join('; ') || m; } catch {} throw new Error(m); }
  const ct = r.headers.get('content-type') || ''; return ct.includes('json') ? r.json() : r;
}
function toast(msg, bad) {
  const el = document.createElement('div');
  el.className = `px-4 py-2 rounded-lg shadow text-sm text-white ${bad ? 'bg-red-700' : 'bg-neutral-800'}`; el.textContent = msg;
  $('#toast').append(el); setTimeout(() => el.remove(), 4500);
}
const run = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
function modal(title, html) {
  $('#dlg-body').innerHTML = `<div class="p-6"><div class="flex justify-between items-start mb-2"><h2 class="font-display text-xl font-bold">${esc(title)}</h2>
  <button class="btn btn-ghost" data-act="close" aria-label="Close">Close</button></div>${html}</div>`; $('#dlg').showModal();
}
const closeModal = () => $('#dlg').close();
async function download(path) { const r = await api(path); window.location.href = r.url; }
async function downloadFile(path, fallback) {
  const r = await api(path); const b = await r.blob(); const cd = r.headers.get('content-disposition') || '';
  const name = (cd.match(/filename="([^"]+)"/) || [])[1] || fallback; const a = document.createElement('a');
  a.href = URL.createObjectURL(b); a.download = name; a.click(); URL.revokeObjectURL(a.href);
}
const opts = (arr, sel) => arr.map(o => { const [v, t] = Array.isArray(o) ? o : [o, label(o)]; return `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''}>${esc(t)}</option>`; }).join('');
const card = (inner, cls = '') => `<section class="bg-white rounded-xl border border-neutral-200 p-5 ${cls}">${inner}</section>`;
const empty = (t) => `<p class="text-sm text-neutral-600 py-6 text-center">${esc(t)}</p>`;

/* ---------------- auth screens ---------------- */
async function logout() { try { await api('/auth/logout', { method: 'POST' }); } catch {} S.user = null; S.meta = null; location.hash = ''; boot(); }

function authShell(inner) {
  return `<main class="min-h-screen grid lg:grid-cols-2 bg-white">
    <div class="flex bg-white text-neutral-900 p-6 sm:p-10 lg:p-12 flex-col justify-between gap-10 lg:border-r border-neutral-200">
      <div>
        <img src="/logo.png" alt="IIM Bangalore and CDPG logo" style="width:240px;max-width:100%;height:auto;background:#FFFFFF" class="mb-10">
        <h1 class="font-display text-3xl sm:text-4xl font-bold leading-tight max-w-2xl">Open Network for Carbon Markets (ONCM) Portal</h1>
        <div class="h-1 w-16 bg-brand-600 mt-5 rounded"></div>
        <p class="mt-5 text-neutral-700 max-w-md">One place for every document, approval and onboarding step.</p>
        <p class="mt-4 text-neutral-500 max-w-md">Submit documents, follow your progress, and access approved resources without searching through emails.</p>
      </div>
      <div>
        ${csitmCredit()}
        <p class="text-sm text-neutral-500 mt-6">Internal drafts and review notes are never visible to participants.</p>
      </div>
    </div>
    <div class="flex items-center justify-center p-6 py-10 bg-[#F5F5F5]">
      <div class="w-full max-w-md">${inner}</div>
    </div>
  </main>`;
}
const csitmCredit = () => `<div class="flex items-center gap-4">
  <img src="/csitm.png" alt="CSITM logo" style="width:72px;height:auto;object-fit:contain">
  <div><p class="font-semibold text-sm text-neutral-800">Developed by CSITM</p>
  <p class="text-xs text-neutral-500 max-w-xs">Centre for Software and Information Technology Management</p></div></div>`;

function loginView() {
  $('#app').innerHTML = authShell(`${card(`<h2 class="font-display text-2xl font-bold mb-1">Sign in</h2>
  <form id="f-login"><label class="l" for="e">Email</label><input class="field" id="e" type="email" required autocomplete="username">
  <label class="l" for="p">Password</label><input class="field" id="p" type="password" required autocomplete="current-password">
  <button class="btn btn-primary w-full justify-center mt-5">Sign in</button></form><div id="sso"></div>
  <p class="text-sm mt-4">New ONCM Consortium Partner Firm? <a href="#/join" class="underline font-semibold text-brand-600">Submit an Onboarding Request</a></p>`)}
  <p class="text-center text-sm mt-4"><a href="#/public" class="underline">Browse public resources</a></p>`);
  api('/health').then(h => { if ((h.sso || []).includes('microsoft') && $('#sso')) $('#sso').innerHTML =
    `<div class="flex items-center gap-3 my-4 text-xs text-neutral-500"><span class="flex-1 border-t"></span>or<span class="flex-1 border-t"></span></div>
    <a class="btn btn-ghost w-full justify-center" href="/api/auth/sso/microsoft/start"><svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true"><path fill="#f25022" d="M1 1h9v9H1z"/><path fill="#00a4ef" d="M1 11h9v9H1z"/><path fill="#7fba00" d="M11 1h9v9h-9z"/><path fill="#ffb900" d="M11 11h9v9h-9z"/></svg>Sign in with Microsoft</a>`; }).catch(() => {});
  $('#f-login').onsubmit = run(async (e) => { e.preventDefault();
    const r = await api('/auth/login', { method: 'POST', body: { email: $('#e').value, password: $('#p').value } });
    r.mfa_required ? mfaLoginView(r.mfa_token) : setSession(); });
}
function mfaLoginView(token) {
  $('#app').innerHTML = authShell(card(`<h2 class="font-display text-2xl font-bold mb-1">Two-step verification</h2>
  <p class="text-sm text-neutral-600">Enter the 6-digit code from your authenticator app. Lost your phone? Enter one of your recovery codes instead.</p>
  <form id="f-mfa-login"><label class="l" for="mc">Code</label><input class="field font-mono tracking-widest" id="mc" required autocomplete="one-time-code" maxlength="20" autofocus>
  <button class="btn btn-primary w-full justify-center mt-5">Verify</button></form>
  <p class="text-sm mt-4"><a href="#" class="underline" id="mfa-back">Back to sign in</a></p>`));
  $('#mfa-back').onclick = (e) => { e.preventDefault(); loginView(); };
  $('#f-mfa-login').onsubmit = run(async (e) => { e.preventDefault();
    await api('/auth/mfa', { method: 'POST', body: { mfa_token: token, code: $('#mc').value.trim() } }); setSession(); });
}
/* Two-step verification setup: QR code -> confirm a code -> show recovery codes once. */
async function mfaSetup(target, done) {
  const r = await api('/auth/mfa/setup', { method: 'POST' });
  target.innerHTML = `<ol class="text-sm list-decimal pl-5 space-y-1"><li>Install an authenticator app (Microsoft Authenticator, Google Authenticator, 1Password, ...).</li>
    <li>Scan this QR code with the app, or enter the key by hand.</li><li>Type the 6-digit code the app shows.</li></ol>
    <div class="mt-3 w-52 h-52 bg-white border border-neutral-200 rounded-lg p-2" id="mfa-qr"></div>
    <p class="text-xs mt-2">Key: <code class="font-mono break-all">${esc(r.secret.replace(/(.{4})/g, '$1 ').trim())}</code></p>
    <form id="f-mfa-on" class="mt-3"><label class="l" for="mon">Code from the app</label><input class="field font-mono tracking-widest" id="mon" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required>
    <button class="btn btn-primary mt-3">Turn on two-step verification</button></form>`;
  $('#mfa-qr').innerHTML = r.qr_svg.replace(/<\?xml[^>]*>/, '').replace('<svg ', '<svg width="100%" height="100%" ');
  $('#f-mfa-on').onsubmit = run(async (e) => { e.preventDefault();
    const x = await api('/auth/mfa/enable', { method: 'POST', body: { code: $('#mon').value.trim() } }); showRecoveryCodes(target, x.recovery_codes, done); });
}
function showRecoveryCodes(target, codes, done) {
  target.innerHTML = `<p class="text-sm font-semibold">Two-step verification is on.</p><p class="text-sm mt-2">Save these recovery codes somewhere safe (a password manager, or printed). Each works once if you lose your phone. They are not shown again.</p>
    <pre class="mt-3 p-3 bg-neutral-100 rounded-lg font-mono text-sm leading-7">${codes.map(esc).join('\n')}</pre>
    <div class="flex gap-2 mt-3"><button class="btn btn-ghost" id="rc-dl">Download codes</button><button class="btn btn-primary" id="rc-ok">I have saved them</button></div>`;
  $('#rc-dl').onclick = () => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([`ONCM Workbench recovery codes for ${S.user ? S.user.email : ''}\n\n${codes.join('\n')}\n`], { type: 'text/plain' }));
    a.download = 'oncm-recovery-codes.txt'; a.click(); URL.revokeObjectURL(a.href); };
  $('#rc-ok').onclick = done;
}
function mfaRequiredView() {
  $('#app').innerHTML = authShell(card(`<h2 class="font-display text-2xl font-bold mb-1">Set up two-step verification</h2>
  <p class="text-sm text-neutral-600 mb-3">Admin accounts must use a second step when signing in. This takes about a minute.</p><div id="mfa-box"></div>
  <p class="text-sm mt-4"><a href="#" class="underline" id="mfa-out">Sign out</a></p>`));
  $('#mfa-out').onclick = (e) => { e.preventDefault(); logout(); };
  run(() => mfaSetup($('#mfa-box'), () => { S.user = null; boot(); }))();
}
function setSession() { S.meta = null; history.replaceState(null, '', '/'); boot(); }
/* ---------------- Request to join: Organizational Profile and Scale ---------------- */
const IN_STATES = ['Andaman and Nicobar Islands', 'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chandigarh', 'Chhattisgarh',
  'Dadra and Nagar Haveli and Daman and Diu', 'Delhi', 'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jammu and Kashmir', 'Jharkhand',
  'Karnataka', 'Kerala', 'Ladakh', 'Lakshadweep', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha',
  'Puducherry', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal'];
const COUNTRIES = ['India', 'Bangladesh', 'Bhutan', 'Nepal', 'Sri Lanka', 'Indonesia', 'Vietnam', 'Thailand', 'Philippines', 'Malaysia', 'Singapore',
  'Kenya', 'Ghana', 'Nigeria', 'Ethiopia', 'Tanzania', 'South Africa', 'United Kingdom', 'Germany', 'France', 'Netherlands', 'Switzerland',
  'United States', 'Canada', 'Brazil', 'Australia', 'United Arab Emirates'];
const REG_HINT = { 'Private Limited Company': 'CIN, for example U12345KA2020PTC123456', 'Public Limited Company': 'CIN, for example L12345KA2000PLC012345',
  'Section 8 Company': 'CIN of the Section 8 company', 'LLP': 'LLPIN, for example AAB-1234', 'Partnership': 'Partnership firm registration number',
  'Proprietorship': 'Udyam, GST or shop-establishment registration number', 'Trust': 'Trust registration or deed number', 'Society': 'Society registration number',
  'Government body': 'Department or notification reference number', 'Academic/ Research institution': 'Institution registration or recognition number',
  'NGO/ Non-Profit': 'Registration number (Trust, Society or Section 8) or equivalent', 'FPO/ Cooperative': 'CIN or cooperative society registration number' };
const JF_DRAFT = 'oncm_join_draft';
const JF_GROUPS = ['geographic_presence', 'beneficiary_segments', 'standards_worked_with', 'carbon_services'];
const JF_RADIOS = ['org_type', 'employee_range'];
const JF_REQUIRED = ['org_name', 'org_type', 'registration_number', 'date_of_incorporation', 'country', 'state_ut', 'city', 'year_established',
  'registered_address', 'employee_range', 'geographic_presence', 'beneficiary_segments', 'carbon_services', 'org_profile',
  'contact_name', 'contact_email', 'actor_type'];
const jfWords = (v) => (String(v).trim().match(/\S+/g) || []).length;
const jfChips = (name, type, items) => `<div class="jf-chips">${items.map(v => `<label class="jf-chip"><input type="${type}" name="${name}" value="${esc(v)}"><span>${esc(v)}</span></label>`).join('')}</div>`;
function jfQ(id, num, text, body, { group = false, optional = false, hint = '' } = {}) {
  const lab = `<span class="jf-n">${num || ''}</span><span>${esc(text)}${optional ? ' <em class="jf-opt">optional</em>' : ''}</span>`;
  const tail = `${hint ? `<p class="jf-h" id="h-${id}">${hint}</p>` : ''}<p class="jf-err" role="alert" hidden></p>`;
  return group ? `<fieldset class="jf-q" id="q-${id}"><legend class="jf-l">${lab}</legend><div class="jf-body">${body}${tail}</div></fieldset>`
    : `<div class="jf-q" id="q-${id}"><label class="jf-l" for="f-${id}">${lab}</label><div class="jf-body">${body}${tail}</div></div>`;
}
const jfIn = (id, extra = '') => `<input class="field" id="f-${id}" name="${id}" ${extra}>`;

async function joinView() {
  const m = await api('/meta'); const yr = new Date().getFullYear(); const today = new Date().toISOString().slice(0, 10);
  const sec = (id, title, sub, inner) => `<section class="jf-sec" id="s-${id}"><h2 class="jf-st">${esc(title)}</h2>${sub ? `<p class="jf-ss">${esc(sub)}</p>` : ''}${inner}</section>`;
  const html = `<div class="jf-page">
  <header class="jf-top"><div class="jf-top-in"><a href="#" aria-label="Back to sign in"><img src="/logo.png" alt="IIM Bangalore and CDPG logo" class="jf-logo"></a>
    <span class="jf-count" id="jf-count" aria-live="polite"></span><a href="#" class="jf-signin">Sign in</a></div>
    <div class="jf-bar" role="progressbar" aria-label="Scroll progress" aria-valuemin="0" aria-valuemax="100"><i id="jf-fill"></i></div>
    <nav class="jf-nav" id="jf-nav" aria-label="Form sections">${[['Registration', 's-reg'], ['Scale and reach', 's-scale'], ['Carbon market work', 's-carbon'], ['About you', 's-about'], ['Contact', 's-contact']]
      .map(([t, id]) => `<a href="#" data-jump="${id}">${t}</a>`).join('')}</nav></header>
  <main class="jf-main"><div class="jf-intro"><h1>Request to join ONCM</h1><p class="jf-sub">Organizational Profile and Scale</p>
    <p class="jf-note">ONCM verifies every request before opening a workspace. Your answers are saved on this device while you type, so you can come back to finish.</p></div>
  <form id="f-join" class="jf-sheet" novalidate autocomplete="on">
  ${sec('reg', 'Registration details', '', [
    jfQ('org_name', 1, 'Full legal name of the organization', jfIn('org_name', 'maxlength="255" autocomplete="organization"')),
    jfQ('org_type', 2, 'Organization type', jfChips('org_type', 'radio', m.org_types), { group: true }),
    jfQ('registration_number', 3, 'Company registration number / CIN / LLPIN / equivalent', jfIn('registration_number', 'maxlength="100" autocapitalize="characters"'), { hint: 'Choose the organization type above to see what to enter.' }),
    jfQ('date_of_incorporation', 4, 'Date of incorporation / registration', jfIn('date_of_incorporation', `type="date" min="1800-01-01" max="${today}"`)),
    jfQ('country', 5, 'Country of registration', `${jfIn('country', 'list="dl-country" value="India" autocomplete="country-name"')}<datalist id="dl-country">${COUNTRIES.map(c => `<option value="${c}">`).join('')}</datalist>`),
    jfQ('state_ut', 6, 'State / UT of registered office', `<select class="field" id="f-state_ut" name="state_ut"><option value="">Select state or UT</option>${opts(IN_STATES.map(x => [x, x]))}</select>
      <input class="field" id="f-state_ut_txt" name="state_ut_txt" maxlength="100" placeholder="State, province or region" hidden>`),
    jfQ('city', 7, 'City of registered office', jfIn('city', 'maxlength="100" autocomplete="address-level2"')),
    jfQ('year_established', 8, 'Year of establishment', jfIn('year_established', `type="number" inputmode="numeric" min="1800" max="${yr}" placeholder="${yr}"`)),
    jfQ('registered_address', 9, 'Full registered office address', `<textarea class="field" id="f-registered_address" name="registered_address" rows="3" maxlength="600" autocomplete="street-address"></textarea>`)].join(''))}
  ${sec('scale', 'Scale and reach', '', [
    jfQ('employee_range', 10, 'Number of full-time employees', jfChips('employee_range', 'radio', m.employee_ranges), { group: true }),
    jfQ('geographic_presence', 11, 'Geographic presence', jfChips('geographic_presence', 'checkbox', m.geographies), { group: true, hint: 'Select all that apply.' }),
    jfQ('beneficiary_segments', 12, 'Primary beneficiary or customer segments', jfChips('beneficiary_segments', 'checkbox', m.segments), { group: true, hint: 'Select all that apply.' })].join(''))}
  ${sec('carbon', 'Carbon market work', '', [
    jfQ('standards_worked_with', 13, 'Standards or registries your organization has worked with', jfChips('standards_worked_with', 'checkbox', m.standards), { group: true, optional: true }),
    jfQ('certifications', 14, 'Relevant certifications, accreditations, or memberships', `<textarea class="field" id="f-certifications" name="certifications" rows="3" maxlength="1500" placeholder="For example: ISO 14065 accreditation, IETA membership"></textarea>`, { optional: true }),
    jfQ('carbon_services', 15, 'Key carbon market services offered', jfChips('carbon_services', 'checkbox', m.services), { group: true, hint: 'Select all that apply.' })].join(''))}
  ${sec('about', 'About the organization', '', [
    jfQ('org_profile', 16, `Brief organizational profile (maximum ${m.max_profile_words} words)`, `<textarea class="field" id="f-org_profile" name="org_profile" rows="6"></textarea><p class="jf-wc" id="jf-wc" aria-live="polite">0 / ${m.max_profile_words} words</p>`),
    jfQ('website', 17, 'Official website', jfIn('website', 'type="url" inputmode="url" placeholder="https://" autocapitalize="none" maxlength="300"'), { optional: true })].join(''))}
  ${sec('contact', 'Contact and ONCM role', 'The person ONCM should write to. The workspace invitation goes to this email once the request is approved.', [
    jfQ('contact_name', '', 'Contact person name', jfIn('contact_name', 'maxlength="200" autocomplete="name"')),
    jfQ('contact_email', '', 'Contact email', jfIn('contact_email', 'type="email" inputmode="email" autocomplete="email" autocapitalize="none"')),
    jfQ('contact_phone', '', 'Contact phone', jfIn('contact_phone', 'type="tel" inputmode="tel" autocomplete="tel" maxlength="40"'), { optional: true }),
    jfQ('contact_designation', '', 'Designation', jfIn('contact_designation', 'maxlength="120" autocomplete="organization-title"'), { optional: true }),
    jfQ('actor_type', '', 'Role you are applying for in ONCM', `<select class="field" id="f-actor_type" name="actor_type"><option value="">Select a role</option>${opts(m.actor_types.map(x => [x, x]))}</select>`),
    jfQ('intended_role', '', 'Anything else about the role you intend to play', jfIn('intended_role', 'maxlength="255"'), { optional: true })].join(''))}
  <div class="jf-submit"><button class="btn btn-primary" id="jf-send" type="submit">Send request</button><p class="jf-small">By sending, you confirm the details are accurate to the best of your knowledge.</p></div>
  </form></main></div>`;
  $('#app').innerHTML = html;

  const f = $('#f-join'); const MAXW = m.max_profile_words;
  const val = (n) => (f.elements[n]?.value || '').trim();
  const checked = (n) => [...f.querySelectorAll(`input[name="${n}"]:checked`)].map(x => x.value);
  const isIndia = () => val('country').toLowerCase() === 'india';
  const get = (n) => JF_GROUPS.includes(n) ? checked(n) : JF_RADIOS.includes(n) ? (checked(n)[0] || '') : n === 'state_ut' ? (isIndia() ? val('state_ut') : val('state_ut_txt')) : val(n);
  const filled = (n) => { const v = get(n); return Array.isArray(v) ? v.length > 0 : String(v).length > 0; };
  const CHECK = {
    org_name: v => v.length >= 2 || 'Enter the full legal name of the organization.',
    org_type: v => !!v || 'Choose the organization type.',
    registration_number: v => v.length >= 2 || 'Enter the registration number.',
    date_of_incorporation: v => (!!v && v <= today && v >= '1800-01-01') || 'Enter a valid date in the past.',
    country: v => v.length >= 2 || 'Enter the country of registration.',
    state_ut: v => v.length >= 2 || 'Choose or enter the state / UT.',
    city: v => v.length >= 2 || 'Enter the city.',
    year_established: v => (/^\d{4}$/.test(v) && +v >= 1800 && +v <= yr) || `Enter a 4-digit year between 1800 and ${yr}.`,
    registered_address: v => v.length >= 10 || 'Enter the full registered office address.',
    employee_range: v => !!v || 'Choose a range.',
    geographic_presence: v => v.length > 0 || 'Choose at least one.',
    beneficiary_segments: v => v.length > 0 || 'Choose at least one.',
    carbon_services: v => v.length > 0 || 'Choose at least one service.',
    org_profile: v => v.length < 10 ? 'Write a short profile of the organization.' : jfWords(v) > MAXW ? `Please shorten this to ${MAXW} words or fewer (currently ${jfWords(v)}).` : true,
    website: v => !v || /^(https?:\/\/)?[^\s/$.?#][^\s]*\.[^\s.]{2,}(\/\S*)?$/i.test(v) || 'Enter a valid web address, for example https://example.org.',
    contact_name: v => v.length >= 2 || 'Enter the contact person\u2019s name.',
    contact_email: v => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v) || 'Enter a valid email address.',
    actor_type: v => !!v || 'Choose the role you are applying for.'
  };
  const setErr = (n, msg) => { const q = $('#q-' + n); if (!q) return; const e = q.querySelector('.jf-err'); e.textContent = msg || ''; e.hidden = !msg; q.classList.toggle('bad', !!msg); };
  const validate = (n) => { const fn = CHECK[n]; if (!fn) return true; const r = fn(get(n)); setErr(n, r === true ? '' : r); return r === true; };

  /* progress: scroll bar, section highlight and answered count */
  const links = [...document.querySelectorAll('#jf-nav a')]; const secs = [...document.querySelectorAll('.jf-sec')]; let cur = -1;
  const countEl = $('#jf-count');
  const refreshCount = () => { const n = JF_REQUIRED.filter(filled).length; countEl.textContent = `${n} of ${JF_REQUIRED.length} required answered`; };
  const onScroll = () => {
    if (!$('#jf-fill')) { window.removeEventListener('scroll', onScroll); window.removeEventListener('resize', onScroll); return; }
    const h = document.documentElement; const max = h.scrollHeight - h.clientHeight;
    $('#jf-fill').style.width = (max > 0 ? Math.min(100, (h.scrollTop / max) * 100) : 0) + '%';
    let c = 0; secs.forEach((s, i) => { if (s.getBoundingClientRect().top < 150) c = i; });
    if (max > 0 && h.scrollTop >= max - 4) c = secs.length - 1;
    if (c !== cur) { cur = c; links.forEach((a, i) => { a.classList.toggle('on', i === c); if (i === c) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current'); });
      const nav = $('#jf-nav'); nav.scrollTo({ left: Math.max(0, links[c].offsetLeft - 16), behavior: 'smooth' }); }
  };
  window.addEventListener('scroll', onScroll, { passive: true }); window.addEventListener('resize', onScroll);
  $('#jf-nav').addEventListener('click', (e) => { const a = e.target.closest('[data-jump]'); if (!a) return; e.preventDefault(); document.getElementById(a.dataset.jump).scrollIntoView({ behavior: 'smooth', block: 'start' }); });

  /* dependent fields */
  const syncState = () => { const ind = isIndia(); $('#f-state_ut').hidden = !ind; $('#f-state_ut_txt').hidden = ind; };
  const syncRegHint = () => { const t = get('org_type'); const h = $('#h-registration_number'); h.textContent = t ? (REG_HINT[t] || 'Registration number or equivalent') : 'Choose the organization type above to see what to enter.'; };
  const syncWords = () => { const w = jfWords(val('org_profile')); const el = $('#jf-wc'); el.textContent = `${w} / ${MAXW} words`; el.classList.toggle('over', w > MAXW); };

  /* draft saved on this device */
  const saveDraft = () => { try { const d = { v: {}, g: {} }; [...f.elements].forEach(el => { if (el.name && !['radio', 'checkbox', 'submit'].includes(el.type)) d.v[el.name] = el.value; });
    [...JF_GROUPS, ...JF_RADIOS].forEach(n => d.g[n] = checked(n)); localStorage.setItem(JF_DRAFT, JSON.stringify(d)); } catch {} };
  const loadDraft = () => { try { const d = JSON.parse(localStorage.getItem(JF_DRAFT) || 'null'); if (!d) return;
    Object.entries(d.v || {}).forEach(([k, v]) => { if (f.elements[k] && !(k === 'country' && !v)) f.elements[k].value = v; });
    Object.entries(d.g || {}).forEach(([k, arr]) => f.querySelectorAll(`input[name="${k}"]`).forEach(i => { i.checked = arr.includes(i.value); })); } catch {} };
  loadDraft(); syncState(); syncRegHint(); syncWords(); refreshCount(); onScroll();

  f.addEventListener('input', (e) => { const n = e.target.name; if (n === 'country') syncState(); if (n === 'org_profile') syncWords();
    if (n === 'state_ut_txt') setErr('state_ut', ''); else if ($('#q-' + n)?.classList.contains('bad')) validate(n);
    refreshCount(); saveDraft(); });
  f.addEventListener('change', (e) => { const n = e.target.name; if (n === 'org_type') syncRegHint(); if (n === 'country') { syncState(); validate('state_ut'); }
    if (CHECK[n] && (JF_GROUPS.includes(n) || JF_RADIOS.includes(n))) validate(n); refreshCount(); saveDraft(); });
  f.addEventListener('focusout', (e) => { const n = e.target.name === 'state_ut_txt' ? 'state_ut' : e.target.name; if (CHECK[n] && (filled(n) || $('#q-' + n)?.classList.contains('bad'))) validate(n); });

  f.onsubmit = run(async (e) => {
    e.preventDefault();
    const order = [...f.querySelectorAll('.jf-q')].map(q => q.id.slice(2)); let first = null;
    order.forEach(n => { if (CHECK[n] && !validate(n) && !first) first = n; });
    if (first) { const q = $('#q-' + first); q.scrollIntoView({ behavior: 'smooth', block: 'center' }); (q.querySelector('input:not([hidden]),select:not([hidden]),textarea'))?.focus({ preventScroll: true });
      toast('Please fix the highlighted questions.', true); return; }
    const body = { org_name: val('org_name'), org_type: get('org_type'), registration_number: val('registration_number'), date_of_incorporation: val('date_of_incorporation'),
      country: val('country'), state_ut: get('state_ut'), city: val('city'), year_established: parseInt(val('year_established'), 10), registered_address: val('registered_address'),
      employee_range: get('employee_range'), geographic_presence: get('geographic_presence'), beneficiary_segments: get('beneficiary_segments'),
      standards_worked_with: get('standards_worked_with'), certifications: val('certifications'), carbon_services: get('carbon_services'),
      org_profile: val('org_profile'), website: val('website'), contact_name: val('contact_name'), contact_email: val('contact_email'),
      contact_phone: val('contact_phone'), contact_designation: val('contact_designation'), actor_type: get('actor_type'), intended_role: val('intended_role') };
    const btn = $('#jf-send'); btn.disabled = true; btn.textContent = 'Sending\u2026'; let r;
    try { r = await api('/join-requests', { method: 'POST', body }); } finally { btn.disabled = false; btn.textContent = 'Send request'; }
    try { localStorage.removeItem(JF_DRAFT); } catch {}
    window.scrollTo(0, 0);
    $('#app').innerHTML = `<div class="jf-page"><header class="jf-top"><div class="jf-top-in"><img src="/logo.png" alt="IIM Bangalore and CDPG logo" class="jf-logo"></div></header>
      <main class="jf-main"><div class="jf-sheet jf-done"><h1>Request sent</h1><p class="mt-3">${esc(r.message)}</p>
      <p class="mt-2 text-sm text-neutral-600">Your request number is <b>${esc(r.id)}</b>. A confirmation has been emailed to ${esc(body.contact_email)}.</p>
      <p class="mt-6"><a class="btn btn-primary" href="#">Back to sign in</a></p></div></main></div>`;
  });
}
function inviteView(token) {
  $('#app').innerHTML = authShell(card(`<h2 class="font-display text-2xl font-bold mb-1">Set your password</h2>
  <form id="f-inv"><label class="l">New password (10+ characters)</label><input class="field" id="np" type="password" minlength="10" required autocomplete="new-password">
  <button class="btn btn-primary w-full justify-center mt-5">Activate account</button></form>`));
  $('#f-inv').onsubmit = run(async (e) => { e.preventDefault(); setSession(await api('/auth/accept-invite', { method: 'POST', body: { token, password: $('#np').value } })); });
}
async function publicView() {
  const r = await api('/public/resources'); S.cache.pubreq = r.request_only;
  $('#app').innerHTML = authShell(card(`<h2 class="font-display text-2xl font-bold mb-3">Public resources</h2>
  ${r.public.length ? r.public.map(x => `<div class="flex justify-between items-center py-2 border-b border-neutral-200"><span>${esc(x.title)}<span class="block text-xs font-mono">${esc(x.code)}</span></span>
  <button class="btn btn-ghost" data-act="pubdl" data-id="${x.id}">Download</button></div>`).join('') : empty('No public documents yet.')}
  <h2 class="font-display text-xl font-bold mt-6 mb-2">Available on request</h2>
  ${r.request_only.length ? r.request_only.map(x => `<div class="flex justify-between items-center py-2 border-b border-neutral-200"><span>${esc(x.title)}<span class="block text-xs font-mono">${esc(x.code)}</span></span>
  <button class="btn btn-ghost" data-act="pub-request" data-id="${x.id}">Request access</button></div>`).join('') : empty('Nothing is available on request right now.')}
  <p class="text-sm mt-4"><a href="#" class="underline">Back to sign in</a></p>`));
}

/* ---------------- app shell ---------------- */
const NAV = {
  participant: [['dash', 'My progress'], ['mydocs', 'My documents'], ['resources', 'Resources'], ['support', 'Support'], ['account', 'Account']],
  admin: [['overview', 'Overview'], ['joins', 'Onboarding Requests', 'm'], ['orgs', 'ONCM Consortium Partner Firms'],
    ['docs', 'Consortium Partner Documentation Status Dashboard'], ['workstreams', 'Workstream Status'],
    ['tasks', 'Workbench Task'], ['requests', 'Access requests', 'r'], ['changelog', 'Documentation Decision Status Log'],
    ['mail', 'Email monitor', 'x'], ['alerts', 'Admin alerts', 'm'], ['reports', 'Consortium Activity Tracking Report', 'e'],
    ['admins', 'Admin accounts', 'm'], ['audit', 'Audit log', 'm'], ['account', 'My account']],
};
const isAdmin = () => S.user.role !== 'participant';
const VIEWS = {};
function shell(active, inner) {
  const u = S.user, adm = isAdmin();
  const items = (adm ? NAV.admin : NAV.participant).filter(n => !n[2] || (n[2] === 'm' && u.role === 'master_admin') || (n[2] === 'x' && (u.role === 'master_admin' || u.can_export)) || (n[2] === 'e' && (u.role === 'master_admin' || u.can_export)) || (n[2] === 'r' && (u.role === 'master_admin' || u.can_share)));
  const space = adm ? 'ONCM Workbench' : (u.organization ? u.organization.name : '');
  $('#app').innerHTML = `<div class="min-h-screen flex flex-col bg-white">
  <header class="bg-white border-b-4 border-brand-600">
    <div class="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 lg:px-8 py-3">
      <img src="/logo.png" alt="IIM Bangalore and CDPG logo" style="height:52px;width:auto;background:#FFFFFF">
      <div class="sm:border-l sm:border-neutral-300 sm:pl-5 min-w-0 flex-1">
        <div class="font-display text-base sm:text-lg lg:text-xl font-bold leading-tight">Open Network for Carbon Markets (ONCM) Portal</div>
        ${space ? `<div class="text-sm font-semibold text-brand-600 mt-0.5">${esc(space)}</div>` : ''}
      </div>
      <div class="flex items-center gap-3 ml-auto">
        <div class="text-xs text-neutral-600 text-right hidden md:block">${esc(u.name)}<br>${esc(u.email)}</div>
        <button class="btn btn-ghost" data-act="logout">Sign out</button>
      </div>
    </div>
  </header>
  <div class="flex-1 lg:flex">
    <aside class="lg:w-72 shrink-0 bg-white border-b lg:border-b-0 lg:border-r border-neutral-200 p-3 lg:p-4 overflow-x-auto">
      <nav class="flex lg:flex-col gap-1" aria-label="Main">${items.map(([k, t]) => `<a href="#/${k}" ${k === active ? 'aria-current="page"' : ''} class="px-3 py-2 rounded-lg text-sm border-l-4 lg:whitespace-normal whitespace-nowrap ${k === active ? 'bg-brand-50 text-brand-700 font-semibold border-brand-600' : 'border-transparent text-neutral-700 hover:bg-neutral-100'}">${t}</a>`).join('')}</nav>
    </aside>
    <main class="flex-1 p-4 lg:p-8 max-w-6xl min-w-0">${inner}</main>
  </div>
  <footer class="border-t border-neutral-200 bg-white px-4 lg:px-8 py-4">${csitmCredit()}</footer></div>`;
}
const head = (t, sub = '', right = '') => `<div class="flex flex-wrap justify-between items-end gap-3 mb-5"><div><h1 class="font-display text-2xl lg:text-3xl font-bold">${esc(t)}</h1>${sub ? `<p class="text-sm text-neutral-600 mt-1">${esc(sub)}</p>` : ''}</div><div class="flex gap-2">${right}</div></div>`;

/* ---------------- participant views ---------------- */
VIEWS.dash = async () => {
  const d = await api('/me/dashboard'), o = d.organization, idx = d.stages.indexOf(o.stage);
  const done = d.checklist.filter(c => c.done).length;
  shell('dash', `${head(o.name, `${o.actor_type} · ONCM contact: ${d.oncm_contact.name} (${d.oncm_contact.email})`)}
  ${card(`<div class="rail">${d.stages.map((s, i) => `<div class="node ${i < idx ? 'done' : i === idx ? 'now' : ''}">${label(s)}</div>`).join('')}</div>
    <p class="text-sm mt-4">${o.waiting_on === 'participant' ? '<b>Next step is yours.</b> Upload or correct the items below.' : '<b>ONCM is reviewing.</b> You will get an email when something changes.'}</p>`)}
  <div class="grid md:grid-cols-2 gap-4 mt-4">
    ${card(`<h2 class="font-display font-bold text-lg">Onboarding checklist <span class="text-sm font-sans font-normal">(${done}/${d.checklist.length})</span></h2>
      <ul class="mt-3 space-y-2 text-sm">${d.checklist.map(c => `<li class="flex gap-2"><span aria-hidden="true">${c.done ? '✔' : '○'}</span><span class="${c.done ? 'line-through text-neutral-600' : ''}">${esc(c.title)}</span></li>`).join('')}</ul>
      <p class="text-xs text-neutral-600 mt-3">ONCM marks items complete after reviewing your documents.</p>`)}
    ${card(`<h2 class="font-display font-bold text-lg">Needs your action</h2>
      ${d.docs_needing_action.length ? d.docs_needing_action.map(x => `<div class="mt-3 text-sm"><b>${esc(x.title)}</b> ${badge(x.status)}<p class="mt-1 bg-brand-50 border border-brand-100 rounded p-2">${esc(x.feedback || 'See your documents page.')}</p></div>`).join('') : empty('Nothing waiting on you.')}
      <h2 class="font-display font-bold text-lg mt-5">With ONCM</h2>
      ${d.docs_with_oncm.length ? d.docs_with_oncm.map(x => `<div class="mt-2 text-sm">${esc(x.title)} ${badge(x.status)}</div>`).join('') : empty('No documents in review.')}
      <h2 class="font-display font-bold text-lg mt-5">Deadlines</h2>
      ${d.deadlines.length ? d.deadlines.map(x => `<div class="mt-2 text-sm">${esc(x.title)} · ${fmtD(x.due_date)}</div>`).join('') : empty('No deadlines set.')}`)}
  </div>`);
};
VIEWS.mydocs = async () => {
  const docs = await api('/me/documents');
  shell('mydocs', `${head('My documents', 'Only your ONCM Consortium Partner Firm can see these.', `<button class="btn btn-primary" data-act="p-upload">Upload document</button>`)}
  ${card(docs.length ? `<table class="t"><thead><tr><th>Document</th><th>Category</th><th>Version</th><th>Status</th><th></th></tr></thead><tbody>
  ${docs.map(d => `<tr><td><b>${esc(d.title)}</b><div class="text-xs font-mono">${esc(d.code)}</div>${d.feedback ? `<div class="mt-1 bg-brand-50 border border-brand-100 rounded p-2 text-xs">${esc(d.feedback)}</div>` : ''}</td><td>${esc(d.category)}</td><td>v${d.current_version}</td><td>${badge(d.status)}</td>
  <td class="text-right whitespace-nowrap"><button class="btn btn-ghost" data-act="p-dl" data-id="${d.id}">Download</button>
  ${['changes_requested', 'rejected', 'submitted'].includes(d.status) ? `<button class="btn btn-ghost" data-act="p-replace" data-id="${d.id}">Replace</button>` : ''}
  ${d.versions.length > 1 ? `<button class="btn btn-ghost" data-act="p-versions" data-id="${d.id}">History</button>` : ''}</td></tr>`).join('')}</tbody></table>` : empty('No documents yet. Upload your first requested document.'))}`);
  S.cache.mydocs = docs;
};
VIEWS.resources = async () => {
  const [rs, cat] = await Promise.all([api('/me/resources'), api('/me/catalogue')]);
  shell('resources', `${head('Resources', 'Approved material shared with you by ONCM.')}
  ${card(rs.length ? `<table class="t"><tbody>${rs.map(r => `<tr><td><b>${esc(r.title)}</b><div class="text-xs font-mono">${esc(r.code)}</div><div class="text-xs">${esc(r.repo_category_name || '')}</div></td>
  <td>${esc(r.audience_label)}</td><td class="text-right"><button class="btn btn-ghost" data-act="r-dl" data-id="${r.id}">Download</button></td></tr>`).join('')}</tbody></table>` : empty('Nothing has been shared with you yet.'))}
  <h2 class="font-display text-xl font-bold mt-6 mb-2">Available on request</h2>
  ${card(cat.filter(c => c.request_status !== 'approved').length ? `<table class="t"><tbody>${cat.filter(c => c.request_status !== 'approved').map(c => `<tr><td><b>${esc(c.title)}</b><div class="text-xs font-mono">${esc(c.code)}</div></td><td>${esc(c.repo_category_name || '')}</td>
  <td class="text-right">${c.request_status === 'pending' ? badge('pending') : `<button class="btn btn-ghost" data-act="r-request" data-id="${c.id}">Request access</button>`}${c.request_status === 'rejected' ? ' ' + badge('rejected') : ''}</td></tr>`).join('')}</tbody></table>` : empty('No request-only documents right now.'))}`);
};
VIEWS.support = async () => shell('support', `${head('Support', 'Messages go to the shared ONCM support mailbox.')}
  ${card(`<form id="f-q" class="max-w-xl"><label class="l">Subject</label><input class="field" name="subject" required minlength="3">
  <label class="l">Message</label><textarea class="field" name="message" rows="6" required minlength="5"></textarea>
  <button class="btn btn-primary mt-4">Send message</button></form>`)}`);
VIEWS.account = async () => {
  const u = S.user = await api('/auth/me');  // fresh two-step verification status
  const mfaCard = card(`<h2 class="font-display font-bold text-lg">Two-step verification</h2>
  ${u.mfa_enabled ? `<p class="text-sm mt-2"><span class="badge bg-emerald-100 text-emerald-800">On</span> ${esc(u.mfa_recovery_codes_left)} recovery code(s) left.</p>
    <div class="flex flex-wrap gap-2 mt-3"><button class="btn btn-ghost" data-act="mfa-codes">New recovery codes</button>${isAdmin() ? '' : '<button class="btn btn-danger" data-act="mfa-off">Turn off</button>'}</div>`
  : `<p class="text-sm mt-2"><span class="badge bg-neutral-200 text-neutral-700">Off</span> Asks for a code from your phone when you sign in, so a stolen password alone is not enough.</p>
    <button class="btn btn-primary mt-3" data-act="mfa-on">Set up</button>`}<div id="mfa-box" class="mt-4"></div>`);
  shell('account', `${head(isAdmin() ? 'My account' : 'Account')}<div class="grid md:grid-cols-2 gap-4">
  ${isAdmin() ? card(`<h2 class="font-display font-bold text-lg">${esc(u.name)}</h2><p class="mt-2 text-sm">${esc(u.email)} · ${esc(label(u.role))}</p>`) : card(`<h2 class="font-display font-bold text-lg">ONCM Consortium Partner Firm</h2><p class="mt-2 text-sm">${esc(u.organization?.name)} · ${esc(u.organization?.actor_type)}<br>${esc(u.name)} · ${esc(u.email)}</p>
  <h3 class="font-semibold mt-5 text-sm">Request a contact change</h3><form id="f-cc"><label class="l">New contact name</label><input class="field" name="contact_name" required>
  <label class="l">New contact email</label><input class="field" name="contact_email" type="email" required><label class="l">Reason</label><input class="field" name="reason" required>
  <button class="btn btn-ghost mt-3">Send for verification</button></form>`)}
  ${card(`<h2 class="font-display font-bold text-lg">Change password</h2><form id="f-pw"><label class="l">Current password</label><input class="field" name="current_password" type="password" required>
  <label class="l">New password (10+ characters)</label><input class="field" name="new_password" type="password" minlength="10" required><button class="btn btn-primary mt-4">Update password</button></form>
  <p class="text-xs text-neutral-600 mt-2">Changing it signs you out everywhere else.</p>`)}
  ${mfaCard}</div>`);
};

/* ---------------- admin views ---------------- */
VIEWS.overview = async () => {
  const o = await api('/admin/overview'); const tot = Math.max(o.organizations, 1);
  const stat = (n, t) => card(`<div class="font-display text-4xl font-bold text-brand-600">${n ?? '-'}</div><div class="text-sm mt-1">${t}</div>`);
  const r = o.repository;
  shell('overview', `${head('Overview', S.user.role === 'master_admin' ? 'All ONCM Consortium Partner Firms' : 'Your assigned ONCM Consortium Partner Firms')}
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">${stat(o.organizations, 'ONCM Consortium Partner Firms')}${stat(o.live, 'Live')}${stat(o.awaiting_review, 'Documents awaiting review')}${stat(o.overdue_tasks, 'Overdue tasks')}
  ${o.pending_join_requests !== null ? stat(o.pending_join_requests, 'Pending Onboarding Requests') : ''}${o.pending_access_requests !== null ? stat(o.pending_access_requests, 'Pending access requests') : ''}${stat(o.changes_requested, 'Waiting on participants')}${o.failed_emails !== null ? stat(o.failed_emails, 'Failed emails') : ''}</div>
  ${card(`<h2 class="font-display font-bold text-lg mb-3">Repository by stage</h2><div class="grid grid-cols-5 gap-2 text-center">${[['draft', 'Draft'], ['internal_review', 'In review'], ['approved', 'Approved'], ['published', 'Published'], ['archive', 'Archive']].map(([k, t]) => `<a href="#/docs" data-act="goto-stage" data-s="${k}" class="rounded-lg bg-mist py-3"><div class="font-display text-2xl font-bold">${r[k]}</div><div class="text-xs">${t}</div></a>`).join('')}</div>`, 'mt-4')}
  ${card(`<h2 class="font-display font-bold text-lg mb-3">Where ONCM Consortium Partner Firms are in onboarding</h2>
  ${STAGES.map(s => `<div class="flex items-center gap-3 mb-2 text-sm"><span class="w-44">${label(s)}</span><div class="flex-1 bg-mist rounded h-3"><div class="bg-brand-600 h-3 rounded" style="width:${(o.by_stage[s] / tot) * 100}%"></div></div><span class="w-6 text-right">${o.by_stage[s]}</span></div>`).join('')}`, 'mt-4')}`);
};
VIEWS.joins = async () => {
  const js = await api('/master/join-requests'); const admins = await api('/admin/admins'); S.cache.admins = admins; S.cache.joins = js;
  shell('joins', `${head('Onboarding Requests', 'Verify eligibility, duplicates and contact details before approving.')}
  ${card(js.length ? `<table class="t"><thead><tr><th>ONCM Consortium Partner Firm</th><th>Type</th><th>Contact</th><th>Status</th><th></th></tr></thead><tbody>${js.map(j => `<tr>
  <td><b>${esc(j.org_name)}</b>${j.duplicate_flag ? ' <span class="badge bg-orange-100 text-orange-800">Possible duplicate</span>' : ''}<div class="text-xs">${esc([j.org_type, j.city, j.country].filter(Boolean).join(' \u00b7 ') || (j.evidence || '').slice(0, 120))}</div></td>
  <td>${esc(j.actor_type)}</td><td>${esc(j.contact_name)}<div class="text-xs">${esc(j.contact_email)}</div></td><td>${badge(j.status)}</td>
  <td class="text-right whitespace-nowrap"><button class="btn btn-ghost" data-act="j-view" data-id="${j.id}">View</button> ${['pending', 'more_info'].includes(j.status) ? `<button class="btn btn-primary" data-act="j-decide" data-id="${j.id}">Decide</button>` : ''}</td></tr>`).join('')}</tbody></table>` : empty('No Onboarding Requests yet.'))}`);
};
VIEWS.orgs = async () => {
  const q = S.cache.orgq || { q: '', stage: '', actor_type: '' };
  const os = await api('/admin/organizations?' + new URLSearchParams(q));
  shell('orgs', `${head('ONCM Consortium Partner Firms')}
  <form id="f-orgq" class="flex flex-wrap gap-2 mb-3"><input class="field max-w-xs" name="q" placeholder="Search name" value="${esc(q.q)}">
  <select class="field max-w-[12rem]" name="stage"><option value="">All stages</option>${opts(STAGES, q.stage)}</select>
  <select class="field max-w-[12rem]" name="actor_type"><option value="">All actor types</option>${opts(['Project Developer', 'VVB', 'Obligated Entity', 'ACVA', 'TSP'], q.actor_type)}</select><button class="btn btn-ghost">Filter</button></form>
  ${card(os.length ? `<table class="t"><thead><tr><th>ONCM Consortium Partner Firm</th><th>Stage</th><th>Waiting on</th><th>Checklist</th><th>In review</th><th>ONCM contact</th><th></th></tr></thead><tbody>
  ${os.map(o => `<tr><td><b>${esc(o.name)}</b>${o.status !== 'active' ? ' ' + badge('failed').replace('Failed', 'Suspended') : ''}<div class="text-xs">${esc(o.actor_type)}</div></td><td>${label(o.stage)}</td><td>${label(o.waiting_on)}</td>
  <td>${o.checklist_done}/${o.checklist_total}</td><td>${o.pending_docs}</td><td>${esc(o.assigned_admin_name || 'Unassigned')}</td>
  <td class="text-right"><button class="btn btn-ghost" data-act="o-open" data-id="${o.id}">Open</button></td></tr>`).join('')}</tbody></table>` : empty('No ONCM Consortium Partner Firms match.'))}`);
};
VIEWS.docs = async () => {
  const f = S.cache.docq || { stage: '', status: '', q: '', mine: '', repo_category: '', workstream_id: '' };
  const p = Object.fromEntries(Object.entries(f).filter(([k, v]) => v && k !== 'stage'));
  const [all, orgs, ws] = await Promise.all([api('/admin/documents?' + new URLSearchParams(p)), api('/admin/organizations'), api('/admin/workstreams')]);
  S.cache.orglist = orgs; S.cache.ws = ws;
  const ds = f.stage ? all.filter(d => d.stage === f.stage) : all;
  const cnt = (k) => k ? all.filter(d => d.stage === k).length : all.length;
  shell('docs', `${head('Consortium Partner Documentation Status Dashboard', 'Every document with its code, stage and access level.', `<button class="btn btn-primary" data-act="a-upload">New document</button>`)}
  <div class="flex flex-wrap gap-1 mb-3">${STAGE_TABS.map(([k, t]) => `<button class="btn ${f.stage === k ? 'btn-primary' : 'btn-ghost'}" data-act="stage-tab" data-s="${k}">${t} (${cnt(k)})</button>`).join('')}</div>
  <form id="f-docq" class="flex flex-wrap gap-2 mb-3 items-center"><input class="field max-w-[14rem]" name="q" placeholder="Search title" value="${esc(f.q)}">
  <select class="field max-w-[13rem]" name="repo_category"><option value="">All categories</option>${opts(Object.entries(S.meta.repo_categories).map(([k, v]) => [k, `${k} · ${v}`]), f.repo_category)}</select>
  <select class="field max-w-[13rem]" name="workstream_id"><option value="">All workstreams</option>${opts(ws.map(w => [w.id, w.name]), f.workstream_id)}</select>
  <label class="flex items-center gap-2 text-sm"><input type="checkbox" name="mine" value="1" ${f.mine ? 'checked' : ''}>Assigned to me</label><button class="btn btn-ghost">Filter</button></form>
  ${card(ds.length ? `<table class="t"><thead><tr><th>Document</th><th>Access</th><th>Stage</th><th>Maker → Checker → Approver</th><th>Workstream</th><th>Updated</th><th></th></tr></thead><tbody>
  ${ds.map(d => `<tr><td><b>${esc(d.title)}</b><div class="text-xs font-mono">${esc(d.code)}</div><div class="text-xs">${esc(d.organization_name)}</div></td><td>${esc(lvlShort(d.access_level))}</td><td>${badge(d.status)}</td>
  <td class="text-xs">${esc(d.maker_name || '-')} → ${esc(d.reviewer_name || '?')} → ${esc(d.approver_name || '?')}</td><td>${esc(d.workstream_name || '-')}</td><td>${fmt(d.updated_at)}</td><td class="text-right"><button class="btn btn-ghost" data-act="d-open" data-id="${d.id}">Open</button></td></tr>`).join('')}</tbody></table>` : empty('No documents match. Start with New document.'))}`);
};
VIEWS.tasks = async () => {
  const [ts, admins, orgs, ws] = await Promise.all([api('/admin/tasks'), api('/admin/admins'), api('/admin/organizations'), api('/admin/workstreams')]); S.cache.admins = admins; S.cache.orglist = orgs; S.cache.ws = ws;
  shell('tasks', `${head('Workbench Task', 'One shared view for the core team. Review tasks are created automatically when a document goes into review.', `<button class="btn btn-primary" data-act="t-new">New Workbench Task</button>`)}
  ${card(ts.length ? `<table class="t"><thead><tr><th>Workbench Task</th><th>Workstream</th><th>ONCM Consortium Partner Firm</th><th>Owner</th><th>Due</th><th>Priority</th><th>Status</th></tr></thead><tbody>
  ${ts.map(t => `<tr><td><b>${esc(t.title)}</b><div class="text-xs font-mono">${esc(t.document_code || '')}</div><div class="text-xs">${esc(t.description || '')}</div></td><td>${esc(t.workstream_name || '-')}</td><td>${esc(t.organization_name || '-')}</td><td>${esc(t.owner_name || '-')}</td><td>${fmtD(t.due_date)}</td><td>${label(t.priority)}</td>
  <td><select class="field" data-act="t-status" data-id="${t.id}">${opts(['open', 'in_progress', 'done'], t.status)}</select></td></tr>`).join('')}</tbody></table>` : empty('No tasks yet.'))}`);
};
VIEWS.mail = async () => {
  const st = S.cache.mailq || ''; const ns = await api('/admin/notifications' + (st ? '?status=' + st : ''));
  shell('mail', `${head('Email monitor', 'Delivery state of every automated email.')}
  <div class="mb-3"><select class="field max-w-[12rem]" id="mailq"><option value="">All</option>${opts(['queued', 'sent', 'logged', 'failed'], st)}</select></div>
  ${card(ns.length ? `<table class="t"><thead><tr><th>Event</th><th>Recipient</th><th>Status</th><th>Tries</th><th>Last error</th><th>When</th><th></th></tr></thead><tbody>
  ${ns.map(n => `<tr><td>${esc(n.event)}<div class="text-xs">${esc(n.subject)}</div></td><td>${esc(n.recipient)}</td><td>${badge(n.status)}</td><td>${n.attempts}</td><td class="text-xs">${esc(n.last_error || '')}</td><td>${fmt(n.created_at)}</td>
  <td>${S.user.role === 'master_admin' && n.status !== 'sent' ? `<button class="btn btn-ghost" data-act="n-retry" data-id="${n.id}">Retry</button>` : ''}</td></tr>`).join('')}</tbody></table>` : empty('No emails recorded.'))}`);
};
VIEWS.alerts = async () => {
  const q = S.cache.alertq || '';
  const [st, rs, lg] = await Promise.all([api('/master/alert-settings'), api('/master/alert-recipients'), api('/master/alert-log?limit=100' + (q ? '&status=' + q : ''))]);
  S.cache.alertSettings = st; S.cache.alertRecipients = rs;
  const cats = (r) => r.categories ? r.categories.map(c => st.categories[c] || c).join(', ') : 'All updates';
  shell('alerts', `${head('Admin alerts', 'A separate sender mailbox emails these people about new ONCM Consortium Partner Firms, onboarding request decisions, onboarding progress, documents and requests.', `<button class="btn btn-primary" data-act="al-new">Add recipient</button>`)}
  ${card(`<div class="flex flex-wrap items-center gap-3 text-sm"><div><span class="font-semibold">Sender:</span> ${esc(st.sender || 'not set')}</div>
    ${st.smtp_configured ? '<span class="badge bg-emerald-100 text-emerald-800">Ready to send</span>' : '<span class="badge bg-amber-100 text-amber-800">Not configured: alerts are recorded but not sent</span>'}
    <div class="text-neutral-600">${st.recipients} recipient(s)${st.failed ? ` · <span class="text-red-700 font-semibold">${st.failed} failed</span>` : ''}</div></div>
    ${st.smtp_configured ? '' : '<p class="text-xs mt-2">Set <code>ALERT_SMTP_HOST</code>, <code>ALERT_SMTP_USER</code> and <code>ALERT_SMTP_PASSWORD</code> (a Gmail App Password) in <code>.env</code> and restart.</p>'}`)}
  ${card(rs.length ? `<table class="t"><thead><tr><th>Recipient</th><th>Receives</th><th>Status</th><th></th></tr></thead><tbody>
  ${rs.map(r => `<tr><td><b>${esc(r.name || '')}</b><div class="text-xs">${esc(r.email)}</div></td><td class="text-xs max-w-xs">${esc(cats(r))}</td>
  <td>${r.is_active ? '<span class="badge bg-emerald-100 text-emerald-800">Active</span>' : '<span class="badge bg-neutral-200 text-neutral-700">Paused</span>'}</td>
  <td class="text-right whitespace-nowrap"><button class="btn btn-ghost" data-act="al-test" data-id="${r.id}">Send test</button> <button class="btn btn-ghost" data-act="al-edit" data-id="${r.id}">Edit</button> <button class="btn btn-danger" data-act="al-del" data-id="${r.id}">Remove</button></td></tr>`).join('')}</tbody></table>` : empty('No recipients yet. Add the first one.'), 'mt-4')}
  ${card(`<div class="flex justify-between items-center mb-3"><h2 class="font-display font-bold text-lg">Recent alerts</h2>
    <select class="field max-w-[12rem]" id="alertq"><option value="">All</option>${opts(['queued', 'sent', 'logged', 'failed'], q)}</select></div>
  ${lg.length ? `<table class="t"><thead><tr><th>Alert</th><th>To</th><th>Status</th><th>Tries</th><th>Last error</th><th>When</th><th></th></tr></thead><tbody>
  ${lg.map(a => `<tr><td>${esc(a.subject)}<div class="text-xs">${esc(label(a.category))}</div></td><td>${esc(a.recipient_email)}</td><td>${badge(a.status)}</td><td>${a.attempts}</td><td class="text-xs">${esc(a.last_error || '')}</td><td>${fmt(a.created_at)}</td>
  <td>${a.status !== 'sent' ? `<button class="btn btn-ghost" data-act="al-retry" data-id="${a.id}">Retry</button>` : ''}</td></tr>`).join('')}</tbody></table>` : empty('No alerts yet.')}`, 'mt-4')}`);
};
VIEWS.reports = async () => {
  const orgs = await api('/admin/organizations');
  shell('reports', `${head('Consortium Activity Tracking Report', 'Downloads a fresh snapshot of current data. Each export is logged.')}
  ${card(`<form id="f-rep" class="grid sm:grid-cols-2 gap-x-4 max-w-2xl">
  <div><label class="l">ONCM Consortium Partner Firm</label><select class="field" name="organization_id"><option value="">All authorised</option>${opts(orgs.map(o => [o.id, o.name]))}</select></div>
  <div><label class="l">Actor type</label><select class="field" name="actor_type"><option value="">All</option>${opts(['Project Developer', 'VVB', 'Obligated Entity', 'ACVA', 'TSP'])}</select></div>
  <div><label class="l">Stage</label><select class="field" name="stage"><option value="">All</option>${opts(STAGES)}</select></div>
  <div><label class="l">Document status</label><select class="field" name="doc_status"><option value="">All</option>${opts(['submitted', 'under_review', 'changes_requested', 'internally_approved', 'shared_externally', 'rejected'])}</select></div>
  <div><label class="l">Documents from</label><input class="field" type="date" name="date_from"></div><div><label class="l">Documents to</label><input class="field" type="date" name="date_to"></div>
  <div class="sm:col-span-2 mt-4"><button class="btn btn-primary">Download Consortium Activity Tracking Report</button></div></form>
  <p class="text-sm mt-4">Sheets: Participant Tracker · Document Register · Approval History · Pending Tasks · Automation Report · Audit Report (Master Admin).</p>`)}`);
};
VIEWS.admins = async () => {
  const as = await api('/master/admins');
  shell('admins', `${head('Admin accounts', 'Every privilege change needs a reason and is audited.', `<button class="btn btn-primary" data-act="ad-new">New admin</button>`)}
  ${card(`<table class="t"><thead><tr><th>Name</th><th>Role</th><th>Repository access</th><th>Can share</th><th>Can export</th><th>Active</th><th>2-step</th><th></th></tr></thead><tbody>${as.map(a => `<tr><td><b>${esc(a.name)}</b><div class="text-xs">${esc(a.email)}</div></td><td>${label(a.role)}</td>
  <td class="text-xs">${a.role === 'master_admin' ? 'All' : esc((a.repo_access || ['CM', 'POL', 'RES', 'PPD', 'CDP']).join(', '))}</td>
  <td>${a.can_share ? 'Yes' : 'No'}</td><td>${a.can_export ? 'Yes' : 'No'}</td><td>${a.is_active ? 'Yes' : 'No'}</td><td>${a.mfa_enabled ? 'On' : 'Off'}</td><td class="text-right whitespace-nowrap">${a.mfa_enabled && a.id !== S.user.id ? `<button class="btn btn-ghost" data-act="ad-mfa" data-id="${a.id}">Reset 2-step</button>` : ''}<button class="btn btn-ghost" data-act="ad-edit" data-id="${a.id}">Edit</button></td></tr>`).join('')}</tbody></table>`)}`);
  S.cache.adminlist = as;
};
VIEWS.audit = async () => {
  const a = S.cache.auditq || ''; const ls = await api('/master/audit-logs?limit=300' + (a ? '&action=' + encodeURIComponent(a) : ''));
  shell('audit', `${head('Audit log', 'Who did what, and when.')}<form id="f-audit" class="mb-3 flex gap-2"><input class="field max-w-xs" name="a" placeholder="Filter by action, e.g. document.approve" value="${esc(a)}"><button class="btn btn-ghost">Filter</button></form>
  ${card(`<table class="t"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead><tbody>${ls.map(l => `<tr><td>${fmt(l.created_at)}</td><td>${esc(l.actor_email || 'system')}</td><td>${esc(l.action)}</td><td>${esc(l.resource)} ${esc(l.resource_id)}</td><td class="text-xs break-all">${esc(JSON.stringify(l.detail || {}))}</td></tr>`).join('')}</tbody></table>`)}`);
};

VIEWS.workstreams = async () => {
  const [ws, admins] = await Promise.all([api('/admin/workstreams'), api('/admin/admins')]); S.cache.ws = ws; S.cache.admins = admins;
  shell('workstreams', `${head('Workstream Status', 'Who owns each stream of work and where its documents stand.', `<button class="btn btn-primary" data-act="ws-new">New workstream</button>`)}
  ${card(ws.length ? `<table class="t"><thead><tr><th>Workstream</th><th>Owner</th><th>Status</th><th>Draft</th><th>In review</th><th>Approved</th><th>Published</th><th>Open tasks</th><th></th></tr></thead><tbody>
  ${ws.map(w => `<tr><td><b>${esc(w.name)}</b><div class="text-xs">${esc(w.description || '')}</div></td><td>${esc(w.owner_name || '-')}</td><td>${badge(w.status === 'active' ? 'open' : w.status === 'done' ? 'done' : 'in_progress').replace(/Open|Done|In progress/, label(w.status))}</td>
  <td>${w.docs.draft}</td><td>${w.docs.internal_review}</td><td>${w.docs.approved}</td><td>${w.docs.published}</td><td>${w.open_tasks}${w.overdue_tasks ? ` <span class="badge bg-red-100 text-red-800">${w.overdue_tasks} overdue</span>` : ''}</td>
  <td class="text-right whitespace-nowrap"><button class="btn btn-ghost" data-act="ws-docs" data-id="${w.id}">Documents</button><button class="btn btn-ghost" data-act="ws-edit" data-id="${w.id}">Edit</button></td></tr>`).join('')}</tbody></table>` : empty('No workstreams yet. Create one per stream of work, for example actor specs or TSP interface requirements.'))}`);
};
VIEWS.changelog = async () => {
  const cl = await api('/admin/changelog');
  shell('changelog', `${head('Documentation Decision Status Log', 'Decisions on which version or document is the one to use.', `<button class="btn btn-primary" data-act="cl-new">Record decision</button>`)}
  ${card(cl.length ? cl.map(c => `<div class="py-3 border-b border-neutral-200 text-sm"><div class="flex justify-between"><b>${esc(c.title)}</b><span class="text-xs">${fmt(c.created_at)} · ${esc(c.recorded_by_name || '')}</span></div>
  ${c.decision ? `<p class="mt-1">${esc(c.decision)}</p>` : ''}<p class="text-xs font-mono mt-1">${esc(c.document_code || '')}${c.supersedes_code ? ` replaces ${esc(c.supersedes_code)}` : ''}</p></div>`).join('') : empty('Nothing recorded yet.'))}`);
};
VIEWS.requests = async () => {
  const rs = await api('/admin/access-requests'); S.cache.reqs = rs;
  shell('requests', `${head('Access requests', 'Request-only documents: from signed-in participants, the public form, or email to the shared mailbox.', `<button class="btn btn-ghost" data-act="rq-log">Log an emailed request</button>`)}
  ${card(rs.length ? `<table class="t"><thead><tr><th>Document</th><th>Requester</th><th>Purpose</th><th>Source</th><th>Status</th><th></th></tr></thead><tbody>${rs.map(r => `<tr>
  <td><b>${esc(r.document_title)}</b><div class="text-xs font-mono">${esc(r.document_code)}</div></td><td>${esc(r.requester_name)}<div class="text-xs">${esc(r.requester_email)} ${esc(r.requester_org || '')}</div></td>
  <td class="text-xs max-w-xs">${esc(r.purpose || '')}</td><td>${label(r.source)}</td><td>${badge(r.status)}${r.expires_at ? `<div class="text-xs">until ${fmtD(r.expires_at)}</div>` : ''}</td>
  <td class="text-right">${r.status === 'pending' ? `<button class="btn btn-primary" data-act="rq-decide" data-id="${r.id}">Decide</button>` : ''}</td></tr>`).join('')}</tbody></table>` : empty('No access requests.'))}`);
};

/* ---------------- actions ---------------- */
const STAGE_TABS = [['', 'All'], ['draft', 'Draft'], ['internal_review', 'In review'], ['approved', 'Approved'], ['published', 'Published'], ['archive', 'Archive']];
const lvlShort = (l) => (S.meta?.access_levels?.[l] || label(l)).split(' - ')[0];
const catSel = (name, sel, extra = '') => `<select class="field" name="${name}" ${extra}>${opts(Object.entries(S.meta.repo_categories).filter(([k]) => S.meta.my_repo_codes.includes(k)).map(([k, v]) => [k, `${k} · ${v}`]), sel)}</select>`;
const lvlOpts = (code, sel) => opts((S.meta.allowed_levels[code] || []).map(l => [l, S.meta.access_levels[l]]), sel);
const checks = (name, items, sel = []) => items.map(([v, t]) => `<label class="flex gap-2 text-sm py-0.5"><input type="checkbox" name="${name}" value="${esc(v)}" ${sel.includes(v) ? 'checked' : ''}> ${esc(t)}</label>`).join('');
const actorChecks = (name, sel = []) => checks(name, S.meta.actor_types.map(a => [a, a]), sel);
const getAll = (f, name) => [...f.querySelectorAll(`[name="${name}"]:checked`)].map(x => x.value);
const catOpts = (extra = []) => opts([...CATS, ...extra]);
const A = {
  close: closeModal, logout,
  'mfa-on': run(async () => mfaSetup($('#mfa-box'), () => VIEWS.account())),
  'mfa-codes': () => { $('#mfa-box').innerHTML = `<form id="f-mfa-codes"><label class="l">Current code from your app (old recovery codes stop working)</label><input class="field font-mono" name="code" inputmode="numeric" maxlength="6" required><button class="btn btn-primary mt-3">Create new codes</button></form>`; },
  'mfa-off': () => { $('#mfa-box').innerHTML = `<form id="f-mfa-off"><label class="l">Password</label><input class="field" name="password" type="password" required><label class="l">Code from your app, or a recovery code</label><input class="field font-mono" name="code" maxlength="20" required><button class="btn btn-danger mt-3">Turn off two-step verification</button></form>`; },
  'ad-mfa': run(async (d) => { const reason = prompt('Reset two-step verification for this person (lost phone and recovery codes)? Confirm who they are first, e.g. by phone. Reason:');
    if (!reason) return; await api('/master/users/' + d.id + '/reset-mfa', { method: 'POST', body: { reason } }); toast('Two-step verification reset. They set it up again at next sign-in.'); VIEWS.admins(); }),
  pubdl: run(async (d) => download('/public/resources/' + d.id + '/download')),
  'p-dl': run(async (d) => download('/me/documents/' + d.id + '/download')),
  'r-dl': run(async (d) => download('/me/resources/' + d.id + '/download')),
  'p-upload': () => modal('Upload document', `<form id="f-pup"><label class="l">Title</label><input class="field" name="title" required>
    <label class="l">Folder</label><select class="field" name="category">${catOpts()}</select><label class="l">Document type</label><input class="field" name="doc_type" placeholder="e.g. Signed agreement">
    <label class="l">File (PDF, Word, Excel, image, CSV; max 25 MB)</label><input class="field" type="file" name="file" required><button class="btn btn-primary mt-5">Submit for review</button></form>`),
  'p-replace': (d) => modal('Upload corrected version', `<form id="f-prep" data-id="${d.id}"><input class="field" type="file" name="file" required><button class="btn btn-primary mt-5">Submit new version</button></form>`),
  'p-versions': (d) => { const x = S.cache.mydocs.find(m => m.id == d.id); modal('Version history', `<ul class="text-sm space-y-2 mt-3">${x.versions.map(v => `<li class="flex justify-between"><span>v${v.version} · ${esc(v.filename)} · ${fmt(v.created_at)}</span><button class="btn btn-ghost" data-act="p-dlv" data-id="${x.id}" data-v="${v.version}">Download</button></li>`).join('')}</ul>`); },
  'p-dlv': run(async (d) => download(`/me/documents/${d.id}/download?version=${d.v}`)),
  'j-view': (d) => {
    const j = S.cache.joins.find(x => x.id == d.id); const L = (a) => (a && a.length) ? a.join(', ') : '-';
    const rows = [['Full legal name', j.org_name], ['Organization type', j.org_type], ['Registration number', j.registration_number], ['Date of incorporation', fmtD(j.date_of_incorporation)],
      ['Country of registration', j.country], ['State / UT', j.state_ut], ['City', j.city], ['Year of establishment', j.year_established], ['Registered office address', j.registered_address],
      ['Full-time employees', j.employee_range], ['Geographic presence', L(j.geographic_presence)], ['Beneficiary / customer segments', L(j.beneficiary_segments)],
      ['Standards or registries', L(j.standards_worked_with)], ['Certifications, accreditations, memberships', j.certifications], ['Carbon market services', L(j.carbon_services)],
      ['Organizational profile', j.org_profile || j.evidence], ['Website', j.website], ['Contact', `${j.contact_name} <${j.contact_email}>`], ['Phone', j.contact_phone],
      ['Designation', j.contact_designation], ['Applying as', j.actor_type], ['Intended role', j.intended_role], ['Submitted', fmt(j.created_at)]];
    modal(j.org_name, `${j.duplicate_flag ? '<p class="badge bg-orange-100 text-orange-800 mb-2">Possible duplicate</p>' : ''}<dl class="text-sm">${rows.filter(([, v]) => v && v !== '-').map(([k, v]) => `<div class="py-2 border-b border-neutral-200 sm:grid sm:grid-cols-3 sm:gap-3"><dt class="font-semibold text-neutral-700">${esc(k)}</dt><dd class="sm:col-span-2 break-words whitespace-pre-line">${esc(v)}</dd></div>`).join('')}</dl>`);
  },
  'j-decide': async (d) => {
    const j = S.cache.joins.find(x => x.id == d.id);
    modal('Decide: ' + j.org_name, `<p class="text-sm">${esc(j.actor_type)} · ${esc(j.contact_email)}</p><form id="f-jd" data-id="${j.id}">
    <label class="l">Decision</label><select class="field" name="decision"><option value="approve">Approve and create workspace</option><option value="more_info">Ask for more information</option><option value="reject">Reject</option></select>
    <label class="l">Assign ONCM contact</label><select class="field" name="assigned_admin_id"><option value="">Unassigned</option>${opts(S.cache.admins.map(a => [a.id, a.name]))}</select>
    <label class="l">Message to applicant (required unless approving)</label><textarea class="field" name="reason" rows="3"></textarea><button class="btn btn-primary mt-5">Record decision</button></form>`);
  },
  'o-open': run(async (d) => {
    const o = await api('/admin/organizations/' + d.id); const admins = await api('/admin/admins'); S.cache.admins = admins; S.cache.org = o;
    const master = S.user.role === 'master_admin';
    modal(o.name, `<p class="text-sm">${esc(o.actor_type)} · ${esc(o.contact_name)} · ${esc(o.contact_email)}</p>
    <form id="f-op" data-id="${o.id}" class="grid sm:grid-cols-3 gap-3 mt-2"><div><label class="l">Stage</label><select class="field" name="stage">${opts(STAGES, o.stage)}</select></div>
    <div><label class="l">Waiting on</label><select class="field" name="waiting_on">${opts(['participant', 'oncm'], o.waiting_on)}</select></div>
    ${master ? `<div><label class="l">ONCM contact</label><select class="field" name="assigned_admin_id"><option value="">-</option>${opts(admins.map(a => [a.id, a.name]), o.assigned_admin_id)}</select></div>` : ''}
    <div class="sm:col-span-3"><label class="l">Reason (needed for overrides / suspensions)</label><input class="field" name="reason"></div>
    <div class="sm:col-span-3 flex gap-2 flex-wrap"><button class="btn btn-primary">Save</button>${master ? `<button type="button" class="btn btn-ghost" data-act="o-force" data-id="${o.id}">Force Live (override)</button><button type="button" class="btn btn-danger" data-act="o-suspend" data-id="${o.id}" data-s="${o.status === 'active' ? 'suspended' : 'active'}">${o.status === 'active' ? 'Suspend access' : 'Restore access'}</button>` : ''}
    <button type="button" class="btn btn-ghost" data-act="o-remind" data-id="${o.id}">Email missing-document reminder</button></div></form>
    <h3 class="font-semibold mt-5">Checklist</h3><ul class="text-sm mt-2 space-y-1">${o.checklist.map(c => `<li><label class="flex gap-2"><input type="checkbox" data-act="c-toggle" data-id="${c.id}" ${c.done ? 'checked' : ''}> ${esc(c.title)}</label></li>`).join('')}</ul>
    <h3 class="font-semibold mt-5">Documents</h3>${o.documents.length ? o.documents.map(x => `<div class="text-sm flex justify-between py-1"><span>${esc(x.title)} ${badge(x.status)}</span><button class="btn btn-ghost" data-act="d-open" data-id="${x.id}">Review</button></div>`).join('') : empty('No documents yet.')}`);
  }),
  'o-force': run(async (d) => { const r = $('#f-op [name=reason]').value; await api('/admin/organizations/' + d.id, { method: 'PATCH', body: { stage: 'live', override: true, reason: r } }); toast('Moved to Live'); closeModal(); go(); }),
  'o-suspend': run(async (d) => { const r = $('#f-op [name=reason]').value; await api('/admin/organizations/' + d.id, { method: 'PATCH', body: { status: d.s, reason: r } }); toast('Updated'); closeModal(); go(); }),
  'o-remind': run(async (d) => { const r = await api(`/admin/organizations/${d.id}/missing-docs-reminder`, { method: 'POST' }); toast(`Reminder queued (${r.sent_for} items)`); }),
  'c-toggle': run(async (d, el) => { await api(`/admin/checklist/${d.id}/toggle?done=${el.checked}`, { method: 'POST' }); toast('Checklist updated'); }),
  'a-upload': () => modal('New repository document', `<p class="text-sm">Starts as an internal draft and receives a code such as ONCM-POL-2026-V1-0003. Nothing is visible outside the ONCM team until it is reviewed, approved and published.</p>
    <form id="f-aup"><label class="l">Title</label><input class="field" name="title" required>
    <div class="grid sm:grid-cols-2 gap-3"><div><label class="l">Category</label>${catSel('repo_category', '', 'data-act="lvl-refresh"')}</div>
    <div><label class="l">Proposed access level</label><select class="field" name="access_level">${lvlOpts(S.meta.my_repo_codes[0], S.meta.default_levels[S.meta.my_repo_codes[0]] || 'internal')}</select></div></div>
    <label class="l">Intended user group (for role-specific access)</label><div class="grid grid-cols-2 sm:grid-cols-3">${actorChecks('actor')}</div>
    <div class="grid sm:grid-cols-2 gap-3"><div><label class="l">Workstream</label><select class="field" name="workstream_id"><option value="">None</option>${opts((S.cache.ws || []).map(w => [w.id, w.name]))}</select></div>
    <div><label class="l">ONCM Consortium Partner Firm (only if specific to one)</label><select class="field" name="organization_id"><option value="">None</option>${opts((S.cache.orglist || []).map(o => [o.id, o.name]))}</select></div></div>
    <label class="l">Document type</label><input class="field" name="doc_type" placeholder="e.g. Interface specification"><label class="l">File</label><input class="field" type="file" name="file" required><button class="btn btn-primary mt-5">Save as draft</button></form>`),
  'lvl-refresh': (d, el) => { const f = el.form; f.access_level.innerHTML = lvlOpts(el.value, S.meta.default_levels[el.value] || 'internal'); },
  'stage-tab': (d) => { S.cache.docq = { ...(S.cache.docq || {}), stage: d.s }; go(); },
  'goto-stage': (d) => { S.cache.docq = { ...(S.cache.docq || {}), stage: d.s }; },
  'd-open': run(async (d) => {
    const [x, admins, orgs, parts] = await Promise.all([api('/admin/documents/' + d.id), api('/admin/admins'), S.cache.orglist ? S.cache.orglist : api('/admin/organizations'), api('/admin/participants')]);
    S.cache.admins = admins; S.cache.orglist = orgs; S.cache.doc = x; S.cache.parts = parts;
    const ws = S.cache.ws || await api('/admin/workstreams'); S.cache.ws = ws;
    const master = S.user.role === 'master_admin', canShare = master || S.user.can_share, me = S.user.id;
    const editable = ['draft', 'submitted', 'under_review', 'changes_requested'].includes(x.status);
    const pipe = ['draft', 'internal_review', 'approved', 'published', 'archive'];
    const pi = pipe.indexOf(x.stage);
    const live = x.shares.find(s => s.status === 'active');
    modal(x.title, `<p class="font-mono text-sm">${esc(x.code)}</p>
    <div class="flex flex-wrap gap-1 mt-3">${pipe.map((k, i) => `<span class="badge ${i === pi ? 'bg-brand-600 text-white' : i < pi ? 'bg-brand-100 text-brand-700' : 'bg-mist text-neutral-600'}">${label(k)}</span>`).join('')}${x.stage === 'closed' ? badge('rejected') : ''} ${badge(x.status)}</div>
    <p class="text-sm mt-2">${esc(x.repo_category_name)} · ${esc(x.organization_name)} · Access: <b>${esc(S.meta.access_levels[x.access_level])}</b>${x.target_actor_types.length ? ` (${esc(x.target_actor_types.join(', '))})` : ''}
    · ${x.classification_confirmed ? 'classification confirmed' : 'classification not yet confirmed'}${x.superseded_by_id ? ' · superseded by document ' + x.superseded_by_id : ''}</p>
    <h3 class="font-semibold mt-4">Maker-checker</h3>
    <form id="f-as" data-id="${x.id}" class="grid sm:grid-cols-4 gap-3 items-end"><div><label class="l">Maker</label><div class="text-sm py-2">${esc(x.maker_name || '-')}</div></div>
    <div><label class="l">Checker (reviewer)</label><select class="field" name="reviewer_id"><option value="">-</option>${opts(admins.filter(a => a.id !== x.maker_id).map(a => [a.id, a.name]), x.assigned_reviewer_id)}</select></div>
    <div><label class="l">Approver</label><select class="field" name="approver_id" ${master ? '' : 'disabled'}><option value="">-</option>${opts(admins.filter(a => a.id !== x.maker_id).map(a => [a.id, a.name]), x.approver_id)}</select></div>
    <div><label class="l">Review due</label><input class="field" type="date" name="due_date" value="${x.due_date ? x.due_date.slice(0, 10) : ''}"></div>
    <button class="btn btn-ghost sm:col-span-4 justify-self-start">Save people and due date</button></form>
    <h3 class="font-semibold mt-4">Versions</h3>${x.versions.map(v => `<div class="text-sm flex justify-between py-1"><span>v${v.version} · ${esc(v.filename)} · ${fmt(v.created_at)}</span><button class="btn btn-ghost" data-act="d-dl" data-id="${x.id}" data-v="${v.version}">Download</button></div>`).join('')}
    ${x.origin === 'admin' && ['draft', 'changes_requested'].includes(x.status) ? `<form id="f-nv" data-id="${x.id}" class="mt-2 flex gap-2"><input class="field" type="file" name="file" required><button class="btn btn-ghost">Upload new version</button></form>` : ''}
    ${editable ? `<h3 class="font-semibold mt-4">Classification</h3><form id="f-dp" data-id="${x.id}" class="grid sm:grid-cols-2 gap-3"><div><label class="l">Access level</label><select class="field" name="access_level">${lvlOpts(x.repo_category, x.access_level)}</select></div>
      <div><label class="l">Workstream</label><select class="field" name="workstream_id"><option value="">None</option>${opts(ws.map(w => [w.id, w.name]), x.workstream_id)}</select></div>
      <div class="sm:col-span-2"><label class="l">Intended user group (role-specific)</label><div class="grid grid-cols-3">${actorChecks('actor', x.target_actor_types)}</div></div><button class="btn btn-ghost justify-self-start">Save classification</button></form>` : ''}
    <h3 class="font-semibold mt-4">Review</h3><form id="f-rv" data-id="${x.id}"><div class="grid sm:grid-cols-2 gap-3"><div><label class="l">Decision</label><select class="field" name="decision">
    <option value="start_review">${x.origin === 'admin' ? 'Submit for internal review' : 'Start review'}</option><option value="recommend">Recommend approval</option><option value="approve">Approve (final)</option><option value="changes">Request changes</option><option value="reject">Reject</option></select></div></div>
    <label class="flex gap-2 text-sm mt-3"><input type="checkbox" name="confirm"> I confirm the access level <b>${esc(lvlShort(x.access_level))}</b> and intended user group (required to approve)</label>
    <label class="l">Internal note (never shown to participants)</label><textarea class="field" name="comment" rows="2"></textarea>
    <label class="l">Feedback for participant (shown to them)</label><textarea class="field" name="feedback" rows="2"></textarea>
    <div class="flex gap-2 mt-3 flex-wrap"><button class="btn btn-primary">Record decision</button>
    ${!['archived', 'superseded'].includes(x.status) ? `<button type="button" class="btn btn-ghost" data-act="d-archive" data-id="${x.id}">Archive</button>` : ''}
    ${x.status === 'archived' && master ? `<button type="button" class="btn btn-ghost" data-act="d-restore" data-id="${x.id}">Restore to draft</button>` : ''}
    ${master ? `<button type="button" class="btn btn-danger" data-act="d-override" data-id="${x.id}">Override status</button>` : ''}
    <button type="button" class="btn btn-ghost" data-act="cl-new" data-doc="${x.id}">Record decision / supersede</button></div></form>
    ${['internally_approved', 'shared_externally'].includes(x.status) && canShare && x.access_level !== 'internal' ? `<h3 class="font-semibold mt-5">Repository configuration and publication</h3>
      <p class="text-sm">Publishes the approved classification: <b>${esc(S.meta.access_levels[x.access_level])}</b>. It cannot be broader than what was approved.</p>
      <form id="f-pub" data-id="${x.id}" data-level="${x.access_level}">
      ${x.access_level === 'role_specific' ? `<label class="l">Audience</label><div class="grid grid-cols-3">${actorChecks('actor', x.target_actor_types).replace(/name="actor"/g, 'name="actor"')}</div>` : ''}
      ${x.access_level === 'restricted' ? `<label class="l">Authorised ONCM Consortium Partner Firms</label><div class="max-h-32 overflow-auto border border-neutral-300 rounded p-2">${checks('org', orgs.map(o => [o.id, o.name]), x.organization_id ? [String(x.organization_id)] : [])}</div>
        <label class="l">Authorised individuals</label><div class="max-h-32 overflow-auto border border-neutral-300 rounded p-2">${checks('usr', parts.map(p => [p.id, `${p.name} (${p.organization})`]))}</div>` : ''}
      <label class="l">Access expires (optional)</label><input class="field max-w-xs" type="date" name="expires_at"><button class="btn btn-primary mt-3">${live ? 'Republish current version' : 'Publish version v' + x.current_version}</button></form>` : ''}
    ${x.shares.length ? `<h3 class="font-semibold mt-5">Publication</h3>${x.shares.map(s => `<div class="text-sm py-1 border-t border-neutral-200"><div class="flex justify-between"><span>${esc(lvlShort(s.audience))} · v${s.version} · ${esc(label(s.status))}${s.actor_types ? ' · ' + esc(s.actor_types.join(', ')) : ''}</span>${s.status === 'active' && canShare ? `<button class="btn btn-danger" data-act="s-revoke" data-id="${s.id}">Unpublish</button>` : ''}</div>
      ${s.grants.map(g => `<div class="text-xs ml-3 flex justify-between"><span>${esc(g.organization || g.user)}</span>${s.status === 'active' && canShare ? `<button class="btn btn-ghost" data-act="g-remove" data-id="${g.id}">Remove</button>` : ''}</div>`).join('')}</div>`).join('')}` : ''}
    ${x.change_log.length ? `<h3 class="font-semibold mt-5">Documentation Decision Status Log</h3>${x.change_log.map(c => `<div class="text-sm border-t border-neutral-200 py-1"><b>${esc(c.title)}</b> · ${fmt(c.created_at)}${c.decision ? `<div>${esc(c.decision)}</div>` : ''}</div>`).join('')}` : ''}
    <h3 class="font-semibold mt-5">Review history</h3>${x.reviews.length ? x.reviews.map(r => `<div class="text-sm border-t border-neutral-200 py-2"><b>${label(r.decision)}</b> by ${esc(r.reviewer_name)} · v${r.version} · ${fmt(r.created_at)}${r.internal_comment ? `<div>Internal: ${esc(r.internal_comment)}</div>` : ''}${r.feedback ? `<div>To participant: ${esc(r.feedback)}</div>` : ''}</div>`).join('') : empty('No reviews yet.')}`);
  }),
  'd-archive': run(async (d) => { const reason = prompt('Reason for archiving (required):'); if (!reason) return; await api(`/admin/documents/${d.id}/archive`, { method: 'POST', body: { reason } }); toast('Archived'); closeModal(); go(); }),
  'd-restore': run(async (d) => { const reason = prompt('Reason for restoring (required):'); if (!reason) return; await api(`/admin/documents/${d.id}/restore`, { method: 'POST', body: { reason } }); toast('Restored to draft'); closeModal(); go(); }),
  'g-remove': run(async (d) => { await api('/admin/grants/' + d.id, { method: 'DELETE' }); toast('Access removed'); closeModal(); go(); }),
  'd-dl': run(async (d) => download(`/admin/documents/${d.id}/download?version=${d.v}`)),
  'd-override': run(async (d) => { const status = prompt('Force status to (e.g. internally_approved):'); if (!status) return; const reason = prompt('Reason (required, audited):'); if (!reason) return;
    await api(`/master/documents/${d.id}/override`, { method: 'POST', body: { status, reason } }); toast('Status overridden'); closeModal(); go(); }),
  's-revoke': run(async (d) => { await api('/admin/shares/' + d.id, { method: 'DELETE' }); toast('Share revoked'); closeModal(); go(); }),
  't-new': () => modal('New Workbench Task', `<form id="f-tn"><label class="l">Title</label><input class="field" name="title" required><label class="l">Description</label><textarea class="field" name="description" rows="2"></textarea>
    <div class="grid sm:grid-cols-2 gap-3"><div><label class="l">Workstream</label><select class="field" name="workstream_id"><option value="">None</option>${opts(S.cache.ws.map(w => [w.id, w.name]))}</select></div>
    <div><label class="l">ONCM Consortium Partner Firm</label><select class="field" name="organization_id"><option value="">None</option>${opts(S.cache.orglist.map(o => [o.id, o.name]))}</select></div>
    <div><label class="l">Owner</label><select class="field" name="owner_id">${opts(S.cache.admins.map(a => [a.id, a.name]), S.user.id)}</select></div>
    <div><label class="l">Due date</label><input class="field" type="date" name="due_date"></div><div><label class="l">Priority</label><select class="field" name="priority">${opts(['low', 'medium', 'high'], 'medium')}</select></div></div><button class="btn btn-primary mt-5">Create task</button></form>`),
  't-status': run(async (d, el) => { await api('/admin/tasks/' + d.id, { method: 'PATCH', body: { status: el.value } }); toast('Task updated'); }),
  'n-retry': run(async (d) => { await api(`/admin/notifications/${d.id}/retry`, { method: 'POST' }); toast('Retry queued'); setTimeout(go, 1500); }),
  'al-new': () => modal('Add alert recipient', `<form id="f-aln"><label class="l">Email address</label><input class="field" type="email" name="email" required>
    <label class="l">Name (optional)</label><input class="field" name="name">
    <label class="l">Which updates should they receive?</label><div class="grid sm:grid-cols-2">${checks('cat', Object.entries(S.cache.alertSettings.categories), Object.keys(S.cache.alertSettings.categories))}</div>
    <button class="btn btn-primary mt-5">Add recipient</button></form>`),
  'al-edit': (d) => { const r = S.cache.alertRecipients.find(x => x.id == d.id); const all = Object.keys(S.cache.alertSettings.categories);
    modal('Edit ' + r.email, `<form id="f-ale" data-id="${r.id}"><label class="l">Name</label><input class="field" name="name" value="${esc(r.name || '')}">
    <label class="flex gap-2 mt-3 text-sm"><input type="checkbox" name="is_active" ${r.is_active ? 'checked' : ''}> Active (untick to pause alerts)</label>
    <label class="l">Updates they receive</label><div class="grid sm:grid-cols-2">${checks('cat', Object.entries(S.cache.alertSettings.categories), r.categories || all)}</div>
    <button class="btn btn-primary mt-5">Save changes</button></form>`); },
  'al-test': run(async (d) => { const r = await api(`/master/alert-recipients/${d.id}/test`, { method: 'POST' }); toast(r.ok ? 'Test email sent. Ask them to check their inbox (and spam).' : r.detail, !r.ok); go(); }),
  'al-del': run(async (d) => { const r = S.cache.alertRecipients.find(x => x.id == d.id); if (!confirm(`Stop sending alerts to ${r.email}?`)) return;
    await api('/master/alert-recipients/' + d.id, { method: 'DELETE' }); toast('Recipient removed'); go(); }),
  'al-retry': run(async (d) => { await api(`/master/alert-log/${d.id}/retry`, { method: 'POST' }); toast('Retry queued'); setTimeout(go, 1500); }),
  'ad-new': () => modal('New admin', `<form id="f-an"><label class="l">Name</label><input class="field" name="name" required><label class="l">Email</label><input class="field" type="email" name="email" required>
    <label class="l">Temporary password (10+ characters)</label><input class="field" type="password" name="password" minlength="10" required><label class="l">Role</label><select class="field" name="role"><option value="secondary_admin">Secondary Admin</option><option value="master_admin">Master Admin</option></select>
    <label class="l">Repository categories this admin may open</label><div class="grid sm:grid-cols-2">${checks('repo', Object.entries(S.meta.repo_categories).map(([k, v]) => [k, `${k} · ${v}`]), ['CM', 'POL', 'RES', 'PPD', 'CDP'])}</div>
    <label class="flex gap-2 mt-3 text-sm"><input type="checkbox" name="can_share"> May configure access, publish and approve access requests</label><label class="flex gap-2 mt-1 text-sm"><input type="checkbox" name="can_export"> May export Consortium Activity Tracking Reports</label>
    <label class="l">Reason (required for Master Admin)</label><input class="field" name="reason"><button class="btn btn-primary mt-5">Create admin</button></form>`),
  'ad-edit': (d) => { const a = S.cache.adminlist.find(x => x.id == d.id); modal('Edit ' + a.name, `<form id="f-ae" data-id="${a.id}">
    <label class="flex gap-2 text-sm"><input type="checkbox" name="is_active" ${a.is_active ? 'checked' : ''}> Active</label><label class="flex gap-2 mt-2 text-sm"><input type="checkbox" name="can_share" ${a.can_share ? 'checked' : ''}> May configure access, publish and approve requests</label>
    <label class="flex gap-2 mt-2 text-sm"><input type="checkbox" name="can_export" ${a.can_export ? 'checked' : ''}> May export reports</label>
    <label class="l">Repository categories</label><div class="grid sm:grid-cols-2">${checks('repo', Object.entries(S.meta.repo_categories).map(([k, v]) => [k, `${k} · ${v}`]), a.repo_access || ['CM', 'POL', 'RES', 'PPD', 'CDP'])}</div>
    <label class="l">Reason (required)</label><input class="field" name="reason" required minlength="3"><button class="btn btn-primary mt-5">Save changes</button></form>`); },
  'ws-new': () => modal('New workstream', `<form id="f-ws"><label class="l">Name</label><input class="field" name="name" required><label class="l">Description</label><textarea class="field" name="description" rows="2"></textarea>
    <label class="l">Owner</label><select class="field" name="owner_id">${opts(S.cache.admins.map(a => [a.id, a.name]), S.user.id)}</select><button class="btn btn-primary mt-5">Create workstream</button></form>`),
  'ws-edit': (d) => { const w = S.cache.ws.find(x => x.id == d.id); modal('Edit workstream', `<form id="f-ws" data-id="${w.id}"><label class="l">Name</label><input class="field" name="name" value="${esc(w.name)}" required>
    <label class="l">Description</label><textarea class="field" name="description" rows="2">${esc(w.description || '')}</textarea><label class="l">Owner</label><select class="field" name="owner_id">${opts(S.cache.admins.map(a => [a.id, a.name]), w.owner_id)}</select>
    <label class="l">Status</label><select class="field" name="status">${opts(['active', 'on_hold', 'done'], w.status)}</select><button class="btn btn-primary mt-5">Save</button></form>`); },
  'ws-docs': (d) => { S.cache.docq = { stage: '', status: '', q: '', mine: '', repo_category: '', workstream_id: d.id }; location.hash = '#/docs'; },
  'cl-new': run(async (d) => {
    const ds = await api('/admin/documents'); const opt = (sel) => '<option value="">None</option>' + opts(ds.map(x => [x.id, `${x.code} · ${x.title}`]), sel);
    modal('Record a decision', `<form id="f-cl"><label class="l">Decision title</label><input class="field" name="title" required placeholder="e.g. Use the newer interaction deck">
    <label class="l">What was decided and why</label><textarea class="field" name="decision" rows="3"></textarea>
    <label class="l">Document to use (the latest)</label><select class="field" name="document_id">${opt(d.doc)}</select>
    <label class="l">Document it replaces (will be marked superseded and unpublished)</label><select class="field" name="supersedes_document_id">${opt('')}</select>
    <label class="l">Workstream</label><select class="field" name="workstream_id"><option value="">None</option>${opts((S.cache.ws || []).map(w => [w.id, w.name]))}</select><button class="btn btn-primary mt-5">Record decision</button></form>`);
  }),
  'rq-decide': (d) => { const r = S.cache.reqs.find(x => x.id == d.id); modal('Access request', `<p class="text-sm"><b>${esc(r.requester_name)}</b> (${esc(r.requester_email)}) asks for <b>${esc(r.document_title)}</b><br>${esc(r.purpose || '')}</p>
    <form id="f-rqd" data-id="${r.id}"><label class="l">Decision</label><select class="field" name="decision"><option value="approve">Approve</option><option value="reject">Reject</option></select>
    <label class="l">Access valid for (days)</label><input class="field max-w-[8rem]" type="number" name="valid_days" value="14" min="1" max="90"><label class="l">Note to requester</label><textarea class="field" name="note" rows="2"></textarea><button class="btn btn-primary mt-5">Record decision</button></form>`); },
  'rq-log': () => modal('Log an emailed request', `<form id="f-rql"><label class="l">Document code (e.g. ONCM-RES-2026-V1-0012)</label><input class="field" name="document_code" required>
    <label class="l">Requester name</label><input class="field" name="requester_name" required><label class="l">Requester email</label><input class="field" type="email" name="requester_email" required>
    <label class="l">ONCM Consortium Partner Firm</label><input class="field" name="requester_org"><label class="l">Purpose</label><textarea class="field" name="purpose" rows="2"></textarea><button class="btn btn-primary mt-5">Log request</button></form>`),
  'pub-request': (d) => { const x = S.cache.pubreq.find(r => r.id == d.id); modal('Request access: ' + x.title, `<form id="f-pubreq" data-id="${x.id}"><label class="l">Your name</label><input class="field" name="requester_name" required>
    <label class="l">Email</label><input class="field" type="email" name="requester_email" required><label class="l">ONCM Consortium Partner Firm</label><input class="field" name="requester_org"><label class="l">Why do you need it?</label><textarea class="field" name="purpose" rows="3" required minlength="5"></textarea><button class="btn btn-primary mt-5">Send request</button></form>`); },
  'r-request': (d) => modal('Request access', `<form id="f-rreq" data-id="${d.id}"><label class="l">Why do you need this document?</label><textarea class="field" name="purpose" rows="3" required minlength="5"></textarea><button class="btn btn-primary mt-5">Send request</button></form>`),
};

/* ---------------- event wiring ---------------- */
document.addEventListener('click', (e) => { const el = e.target.closest('[data-act]'); if (!el || el.tagName === 'SELECT' || el.type === 'checkbox') return; const f = A[el.dataset.act]; if (f) f(el.dataset, el); });
document.addEventListener('change', (e) => { const el = e.target.closest('[data-act]'); if (el && (el.tagName === 'SELECT' || el.type === 'checkbox')) A[el.dataset.act]?.(el.dataset, el); });
const fd = (f) => Object.fromEntries([...new FormData(f)].map(([k, v]) => [k, v === '' ? null : v]));
const FORMS = {
  'f-mfa-codes': async (f) => { const r = await api('/auth/mfa/recovery-codes', { method: 'POST', body: fd(f) }); showRecoveryCodes($('#mfa-box'), r.recovery_codes, () => VIEWS.account()); },
  'f-mfa-off': async (f) => { await api('/auth/mfa/disable', { method: 'POST', body: fd(f) }); toast('Two-step verification is off'); VIEWS.account(); },
  'f-pup': async (f) => { await api('/me/documents', { method: 'POST', form: new FormData(f) }); toast('Submitted for review'); closeModal(); go(); },
  'f-prep': async (f) => { await api(`/me/documents/${f.dataset.id}/versions`, { method: 'POST', form: new FormData(f) }); toast('New version submitted'); closeModal(); go(); },
  'f-q': async (f) => { const r = await api('/me/questions', { method: 'POST', body: fd(f) }); toast(r.message); f.reset(); },
  'f-cc': async (f) => { const r = await api('/me/contact-change', { method: 'POST', body: fd(f) }); toast(r.message); f.reset(); },
  'f-pw': async (f) => { await api('/auth/change-password', { method: 'POST', body: fd(f) }); toast('Password updated'); f.reset(); },
  'f-jd': async (f) => { const b = fd(f); b.assigned_admin_id = b.assigned_admin_id ? +b.assigned_admin_id : null; b.reason = b.reason || '';
    const r = await api(`/master/join-requests/${f.dataset.id}/decision`, { method: 'POST', body: b });
    if (r.invite_link) { modal('Approved', `<p class="text-sm">Workspace created and email queued. If email is not configured, share this one-time link with the contact:</p><input class="field mt-3" readonly value="${esc(location.origin + r.invite_link.replace(/^https?:\/\/[^/]+/, ''))}" onclick="this.select()">`); go(); } else { toast('Decision recorded'); closeModal(); go(); } },
  'f-op': async (f) => { const b = fd(f); if (b.assigned_admin_id) b.assigned_admin_id = +b.assigned_admin_id; else delete b.assigned_admin_id;
    await api('/admin/organizations/' + f.dataset.id, { method: 'PATCH', body: b }); toast('ONCM Consortium Partner Firm updated'); closeModal(); go(); },
  'f-aup': async (f) => { const m = new FormData(f); m.delete('actor'); m.set('target_actor_types', getAll(f, 'actor').join(',')); for (const k of ['organization_id', 'workstream_id']) if (!m.get(k)) m.delete(k);
    await api('/admin/documents', { method: 'POST', form: m }); toast('Draft saved (internal)'); closeModal(); go(); },
  'f-nv': async (f) => { await api(`/admin/documents/${f.dataset.id}/versions`, { method: 'POST', form: new FormData(f) }); toast('New version uploaded'); closeModal(); go(); },
  'f-as': async (f) => { const b = fd(f); const body = { due_date: b.due_date ? b.due_date + 'T17:00:00Z' : null }; if (b.reviewer_id) body.reviewer_id = +b.reviewer_id; if (b.approver_id) body.approver_id = +b.approver_id;
    await api(`/admin/documents/${f.dataset.id}/assign`, { method: 'POST', body }); toast('Saved'); closeModal(); go(); },
  'f-dp': async (f) => { const b = fd(f); await api('/admin/documents/' + f.dataset.id, { method: 'PATCH', body: { access_level: b.access_level, target_actor_types: getAll(f, 'actor'), workstream_id: b.workstream_id ? +b.workstream_id : 0 } }); toast('Classification saved (needs confirmation at approval)'); closeModal(); go(); },
  'f-rv': async (f) => { const b = fd(f); await api(`/admin/documents/${f.dataset.id}/review`, { method: 'POST', body: { decision: b.decision, comment: b.comment || '', feedback: b.feedback || '', confirm_classification: !!b.confirm } }); toast('Decision recorded'); closeModal(); go(); },
  'f-pub': async (f) => { const b = fd(f); const body = { expires_at: b.expires_at ? b.expires_at + 'T23:59:59Z' : null, organization_ids: getAll(f, 'org').map(Number), user_ids: getAll(f, 'usr').map(Number) };
    if (f.dataset.level === 'role_specific') body.actor_types = getAll(f, 'actor');
    await api(`/admin/documents/${f.dataset.id}/publish`, { method: 'POST', body }); toast('Published. Recipients were emailed a link to log in.'); closeModal(); go(); },
  'f-tn': async (f) => { const b = fd(f); ['organization_id', 'owner_id', 'workstream_id'].forEach(k => b[k] = b[k] ? +b[k] : null); b.due_date = b.due_date ? b.due_date + 'T09:00:00Z' : null; await api('/admin/tasks', { method: 'POST', body: b }); toast('Task created'); closeModal(); go(); },
  'f-an': async (f) => { const b = fd(f); b.can_share = !!b.can_share; b.can_export = !!b.can_export; b.reason = b.reason || ''; b.repo_access = getAll(f, 'repo'); delete b.repo; await api('/master/admins', { method: 'POST', body: b }); toast('Admin created'); closeModal(); go(); },
  'f-ae': async (f) => { const b = fd(f); await api('/master/admins/' + f.dataset.id, { method: 'PATCH', body: { is_active: !!b.is_active, can_share: !!b.can_share, can_export: !!b.can_export, repo_access: getAll(f, 'repo'), reason: b.reason } }); toast('Admin updated'); closeModal(); go(); },
  'f-aln': async (f) => { const b = fd(f), cats = getAll(f, 'cat'), all = Object.keys(S.cache.alertSettings.categories);
    await api('/master/alert-recipients', { method: 'POST', body: { email: b.email, name: b.name || '', categories: cats.length === all.length ? null : cats } }); toast('Recipient added'); closeModal(); go(); },
  'f-ale': async (f) => { const b = fd(f), cats = getAll(f, 'cat'), all = Object.keys(S.cache.alertSettings.categories); const body = { name: b.name || '', is_active: !!b.is_active };
    if (cats.length === all.length) body.all_categories = true; else body.categories = cats;
    await api('/master/alert-recipients/' + f.dataset.id, { method: 'PATCH', body }); toast('Recipient updated'); closeModal(); go(); },
  'f-ws': async (f) => { const b = fd(f); b.owner_id = b.owner_id ? +b.owner_id : null; b.status = b.status || 'active'; b.description = b.description || '';
    await api(f.dataset.id ? '/admin/workstreams/' + f.dataset.id : '/admin/workstreams', { method: f.dataset.id ? 'PATCH' : 'POST', body: b }); toast('Saved'); closeModal(); go(); },
  'f-cl': async (f) => { const b = fd(f); ['document_id', 'supersedes_document_id', 'workstream_id'].forEach(k => b[k] = b[k] ? +b[k] : null); b.decision = b.decision || ''; await api('/admin/changelog', { method: 'POST', body: b }); toast('Decision recorded'); closeModal(); go(); },
  'f-rqd': async (f) => { const b = fd(f); const r = await api(`/admin/access-requests/${f.dataset.id}/decision`, { method: 'POST', body: { decision: b.decision, note: b.note || '', valid_days: +b.valid_days || 14 } });
    if (r.link) modal('Approved', `<p class="text-sm">The requester was emailed a secure link. If email is not configured, send them this link (expires with the approval):</p><input class="field mt-3" readonly value="${esc(r.link)}" onclick="this.select()">`); else { toast('Decision recorded'); closeModal(); } go(); },
  'f-rql': async (f) => { await api('/admin/access-requests', { method: 'POST', body: fd(f) }); toast('Request logged'); closeModal(); go(); },
  'f-pubreq': async (f) => { const b = fd(f); b.document_id = +f.dataset.id; const r = await api('/public/access-requests', { method: 'POST', body: b }); toast(r.message); closeModal(); },
  'f-rreq': async (f) => { const r = await api('/me/access-requests', { method: 'POST', body: { document_id: +f.dataset.id, purpose: fd(f).purpose } }); toast(r.message); closeModal(); go(); },
  'f-orgq': async (f) => { S.cache.orgq = Object.fromEntries(new FormData(f)); go(); },
  'f-docq': async (f) => { S.cache.docq = { ...(S.cache.docq || {}), q: '', mine: '', repo_category: '', workstream_id: '', ...Object.fromEntries(new FormData(f)) }; go(); },
  'f-audit': async (f) => { S.cache.auditq = new FormData(f).get('a'); go(); },
  'f-rep': async (f) => { const p = new URLSearchParams(); for (const [k, v] of new FormData(f)) if (v) p.set(k, k.startsWith('date_') ? v + (k === 'date_to' ? 'T23:59:59Z' : 'T00:00:00Z') : v); await downloadFile('/admin/reports/export?' + p, 'ONCM_Consortium_Activity_Tracking_Report.xlsx'); toast('Report downloaded'); },
};
document.addEventListener('submit', (e) => { const f = FORMS[e.target.id]; if (!f) return; e.preventDefault(); run(f)(e.target); });
document.addEventListener('change', (e) => { if (e.target.id === 'mailq') { S.cache.mailq = e.target.value; go(); } if (e.target.id === 'alertq') { S.cache.alertq = e.target.value; go(); } });

/* ---------------- routing ---------------- */
async function go() {
  if (!S.user) return;
  const def = isAdmin() ? 'overview' : 'dash';
  let name = (location.hash.replace('#/', '') || def); const allowed = (isAdmin() ? NAV.admin : NAV.participant).map(n => n[0]);
  if (!allowed.includes(name)) name = def;
  try { await VIEWS[name](); } catch (e) { toast(e.message, true); shell(name, empty('Could not load this page: ' + e.message)); }
}
window.addEventListener('hashchange', () => S.user ? go() : boot());
const SSO_ERRORS = {
  'no-account': 'No ONCM account uses this Microsoft email. Ask ONCM to add you, then try again.',
  'domain-not-allowed': 'This Microsoft account is not from an organisation allowed to sign in here.',
  'account-linked-elsewhere': 'This ONCM account is linked to a different Microsoft account. Contact ONCM.',
  'organization-suspended': "This organization's access is suspended. Contact ONCM.",
  cancelled: 'Microsoft sign-in was cancelled.', expired: 'The sign-in took too long. Please try again.',
};
async function boot() {
  const qs = new URLSearchParams(location.search);
  const inv = qs.get('invite');
  if (inv) return inviteView(inv);
  if (qs.get('sso_error')) { history.replaceState(null, '', '/'); loginView(); return toast(SSO_ERRORS[qs.get('sso_error')] || 'Microsoft sign-in did not work. Please try again.', true); }
  if (qs.get('sso') === 'mfa') { history.replaceState(null, '', '/'); return mfaLoginView(''); }
  try { S.user = await api('/auth/me'); } catch { S.user = null; }
  if (S.user) {
    if (S.user.mfa_setup_required) return mfaRequiredView();
    try { if (isAdmin()) S.meta = await api('/admin/meta'); return go(); } catch (e) { toast(e.message, true); }
  }
  const h = location.hash; if (h === '#/join') return joinView(); if (h === '#/public') return publicView(); loginView();
}
boot();