/* inspector.js — Inspector tab logic */

let inspectorLoginSteps = [];   // [{url, fields:[{name,value}], submit_selector, wait_for_url}]
let lastInspectResult  = null;

// ── Login step builder (for inspection) ────────────────────

function addLoginStep() {
  const id = Date.now();
  inspectorLoginSteps.push({ _id: id, url: '', fields: [], submit_selector: 'button[type="submit"]', wait_for_url: '' });
  renderLoginSteps();
}

function removeLoginStep(id) {
  inspectorLoginSteps = inspectorLoginSteps.filter(s => s._id !== id);
  renderLoginSteps();
}

function addLoginField(stepId) {
  const step = inspectorLoginSteps.find(s => s._id === stepId);
  if (step) step.fields.push({ name: '', value: '' });
  renderLoginSteps();
}

function renderLoginSteps() {
  const container = document.getElementById('login-steps-list');
  if (!inspectorLoginSteps.length) {
    container.innerHTML = '<p class="hint" style="color:var(--text3);padding:.5rem 0">No login steps added.</p>';
    return;
  }

  container.innerHTML = inspectorLoginSteps.map((step, si) => `
    <div class="login-step-card">
      <div class="row">
        <span class="badge badge-blue">Step ${si + 1}</span>
        <span style="font-weight:600;flex:1">Login Step</span>
        <button class="btn btn-sm btn-danger" onclick="removeLoginStep(${step._id})">✕ Remove</button>
      </div>
      <div class="form-grid" style="margin-top:.75rem">
        <div class="form-group">
          <label>URL to navigate to</label>
          <input class="input" type="url" placeholder="https://example.com/login/"
            value="${esc(step.url)}"
            onchange="updateLoginStep(${step._id},'url',this.value)">
        </div>
        <div class="form-group">
          <label>Submit selector</label>
          <input class="input" placeholder='button[type="submit"]'
            value="${esc(step.submit_selector)}"
            onchange="updateLoginStep(${step._id},'submit_selector',this.value)">
        </div>
        <div class="form-group" style="grid-column:1/-1">
          <label>Wait for URL to contain</label>
          <input class="input" placeholder="/dashboard/"
            value="${esc(step.wait_for_url)}"
            onchange="updateLoginStep(${step._id},'wait_for_url',this.value)">
        </div>
      </div>
      <div class="step-fields">
        <div class="card-title" style="font-size:.8rem;margin-bottom:.5rem">Fields to fill (credentials)</div>
        ${step.fields.map((f, fi) => `
          <div class="login-field-row">
            <input class="input" placeholder='Field name (e.g. "identifier")'
              value="${esc(f.name)}"
              onchange="updateLoginField(${step._id},${fi},'name',this.value)">
            <input class="input" type="text" placeholder="Value"
              value="${esc(f.value)}"
              onchange="updateLoginField(${step._id},${fi},'value',this.value)">
            <button class="btn btn-sm btn-danger" onclick="removeLoginField(${step._id},${fi})">✕</button>
          </div>
        `).join('')}
        <button class="btn btn-sm btn-ghost" onclick="addLoginField(${step._id})" style="margin-top:.25rem">+ Add Field</button>
      </div>
    </div>
  `).join('');
}

function updateLoginStep(id, key, val) {
  const s = inspectorLoginSteps.find(s => s._id === id);
  if (s) s[key] = val;
}

function updateLoginField(stepId, fi, key, val) {
  const s = inspectorLoginSteps.find(s => s._id === stepId);
  if (s && s.fields[fi]) s.fields[fi][key] = val;
}

function removeLoginField(stepId, fi) {
  const s = inspectorLoginSteps.find(s => s._id === stepId);
  if (s) { s.fields.splice(fi, 1); renderLoginSteps(); }
}

// ── Run inspection ──────────────────────────────────────────

async function runInspect() {
  const url = document.getElementById('inspect-url').value.trim();
  if (!url) { alert('Please enter a URL to inspect.'); return; }

  document.getElementById('inspect-results').classList.add('hidden');
  document.getElementById('inspect-spinner').classList.remove('hidden');

  // Build login steps payload
  const loginSteps = inspectorLoginSteps.map(s => ({
    url: s.url,
    fields: s.fields.map(f => ({
      selector: `[name="${f.name}"]`,
      field_type: f.name.toLowerCase().includes('pass') ? 'password' : 'text',
      source: 'literal',
      literal_value: f.value,
    })),
    submit_selector: s.submit_selector,
    wait_for_url: s.wait_for_url,
  }));

  try {
    const result = await API.inspect(url, loginSteps);
    lastInspectResult = result;
    renderInspectResults(result, url);
  } catch (e) {
    alert('Inspector error: ' + e.message);
  } finally {
    document.getElementById('inspect-spinner').classList.add('hidden');
  }
}

// ── Render results ──────────────────────────────────────────

function renderInspectResults(result, url) {
  document.getElementById('inspect-results').classList.remove('hidden');
  document.getElementById('inspect-url-badge').textContent = result.final_url || url;

  const rows = [];

  // Inputs
  for (const f of (result.inputs || [])) {
    if (f.type === 'hidden') continue;
    const typeClass = `t-${f.type}`;
    const selectorCss = f.name ? `input[name="${f.name}"]` : (f.id ? `#${f.id}` : '?');
    
    // UPDATED: Show radio/checkbox values in the placeholder column
    let displayHint = esc(f.placeholder || '');
    if (f.type === 'radio' || f.type === 'checkbox') {
       displayHint = f.value ? `<span style="color:var(--text3)">val:</span> <b style="color:var(--text);font-family:var(--mono)">${esc(f.value)}</b>` : displayHint;
    }

    rows.push(`
      <tr>
        <td><span class="type-tag ${typeClass}">${esc(f.type)}</span></td>
        <td><code>${esc(f.name || '')}</code></td>
        <td><code>${esc(f.id || '')}</code></td>
        <td>${displayHint}</td>
        <td><code>${esc(selectorCss)}</code></td>
        <td></td>
      </tr>
    `);
  }

  // Selects
  for (const s of (result.selects || [])) {
    const selectorCss = s.name ? `select[name="${s.name}"]` : (s.id ? `#${s.id}` : '?');
    const optLabels = (s.options || []).slice(1, 6).map(o => `<div><span style="color:var(--text)">${esc(o.text)}</span> <span style="color:var(--text3); font-size:.75rem">val: <b>${esc(o.value)}</b></span></div>`).join('');
    rows.push(`
      <tr>
        <td><span class="type-tag t-select">select</span></td>
        <td><code>${esc(s.name || '')}</code></td>
        <td><code>${esc(s.id || '')}</code></td>
        <td><div class="options-list">${optLabels}${s.options.length > 6 ? `<div>+${s.options.length - 6} more</div>` : ''}</div></td>
        <td><code>${esc(selectorCss)}</code></td>
        <td></td>
      </tr>
    `);
  }

  // Textareas
  for (const t of (result.textareas || [])) {
    const sel = t.name ? `textarea[name="${t.name}"]` : (t.id ? `#${t.id}` : '?');
    rows.push(`
      <tr>
        <td><span class="type-tag">textarea</span></td>
        <td><code>${esc(t.name || '')}</code></td>
        <td><code>${esc(t.id || '')}</code></td>
        <td>${esc(t.placeholder || '')}</td>
        <td><code>${esc(sel)}</code></td>
        <td></td>
      </tr>
    `);
  }

  // Buttons
  for (const b of (result.buttons || [])) {
    const sel = b.id ? `#${b.id}` : (b.type === 'submit' ? 'button[type="submit"]' : `button:has-text("${b.text}")`);
    rows.push(`
      <tr>
        <td><span class="type-tag t-hidden">button</span></td>
        <td>${esc(b.text ? b.text.slice(0,30) : '')}</td>
        <td><code>${esc(b.id || '')}</code></td>
        <td>type=${esc(b.type || '')}</td>
        <td><code>${esc(sel)}</code></td>
        <td></td>
      </tr>
    `);
  }

  document.getElementById('fields-table-wrap').innerHTML = `
    <div class="table-wrap">
      <table class="fields-table">
        <thead>
          <tr>
            <th>Type</th>
            <th>name</th>
            <th>id</th>
            <th>Placeholder / Options</th>
            <th>Suggested Selector</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows.join('')}</tbody>
      </table>
    </div>
    <div style="margin-top:1rem">
      <button class="btn btn-primary btn-sm" onclick="useInspectInFlow()">
        → Use These Fields in Flow Builder
      </button>
    </div>
  `;
}

// ── Transfer to flow ────────────────────────────────────────

function useInspectInFlow() {
  if (!lastInspectResult) return;

  const url = document.getElementById('inspect-url').value.trim();

  // Auto-populate a new flow step with all fields
  const mappings = [];

  for (const f of (lastInspectResult.inputs || [])) {
    if (f.type === 'hidden') continue;
    const selector = f.name ? `input[name="${f.name}"]` : (f.id ? `#${f.id}` : '');
    if (!selector) continue;

    let ft = f.type;
    if (!['text','password','email','tel','number','radio','checkbox'].includes(ft)) ft = 'text';

    mappings.push({
      _id: Date.now() + Math.random(),
      selector,
      field_type: ft,
      radio_name: ft === 'radio' ? (f.name || '') : '',
      source: 'csv_column',
      csv_column: '',
      literal_value: '',
      label: f.placeholder || f.name || selector,
      extracted_value: f.value || '', // UPDATED: Save for display
      value_map: [],
    });
  }

  for (const s of (lastInspectResult.selects || [])) {
    const selector = s.name ? `select[name="${s.name}"]` : (s.id ? `#${s.id}` : '');
    if (!selector) continue;
    mappings.push({
      _id: Date.now() + Math.random(),
      selector,
      field_type: 'select',
      source: 'csv_column',
      csv_column: '',
      literal_value: '',
      label: s.name || selector,
      options: s.options, // UPDATED: Save for display
      value_map: [],
    });
  }

  for (const t of (lastInspectResult.textareas || [])) {
    const selector = t.name ? `textarea[name="${t.name}"]` : (t.id ? `#${t.id}` : '');
    if (!selector) continue;
    mappings.push({
      _id: Date.now() + Math.random(),
      selector, field_type: 'textarea',
      source: 'csv_column', csv_column: '', literal_value: '',
      label: t.placeholder || t.name || selector,
      value_map: [],
    });
  }

  // Create step
  const stepId = 'step_' + Date.now();
  const step = {
    _id: stepId,
    step_id: stepId,
    label: 'Step from Inspector',
    url,
    field_mappings: mappings,
    submit_selector: '',
    wait_for_url: '',
    skip_if_no_data: false,
  };

  flowSteps.push(step);
  renderFlowSteps();
  switchTab('flow');
}

function esc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}