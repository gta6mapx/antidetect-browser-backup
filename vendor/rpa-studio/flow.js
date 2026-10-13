/* flow.js — Flow Builder tab */

let flowSteps = [];           
let editingRecipeId = null;   

// ── Step management ─────────────────────────────────────────

function addFlowStep() {
  const id = 'step_' + Date.now();
  flowSteps.push({
    _id: id,
    step_id: id,
    label: 'New Step',
    url: '',
    field_mappings: [],
    submit_selector: 'button[type="submit"]',
    wait_for_url: '',
    wait_for_selector: '',
    skip_if_no_data: false,
    opens_new_tab: false,
    _open: true,
    inspection_steps: [], 
    requires_captcha: false,
    captcha_image_selector: '',
    captcha_input_selector: '',
    error_selector: '',
    error_text_contains: '',
    on_error: 'fail',
    max_retries: 2,
    dismiss_dialogs: false,
    dialog_action: 'accept'
  });
  renderFlowSteps();
}

function removeFlowStep(id) {
  flowSteps = flowSteps.filter(s => s._id !== id);
  renderFlowSteps();
}

function toggleStep(id) {
  const s = flowSteps.find(s => s._id === id);
  if (s) { s._open = !s._open; renderFlowSteps(); }
}

function updateStep(id, key, val) {
  const s = flowSteps.find(s => s._id === id);
  if (s) s[key] = val;
  if (key === 'requires_captcha' || key === 'opens_new_tab' || key === 'on_error' || key === 'dismiss_dialogs' || key === 'error_selector') renderFlowSteps();
}

function moveStep(id, dir) {
  const idx = flowSteps.findIndex(s => s._id === id);
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= flowSteps.length) return;
  [flowSteps[idx], flowSteps[newIdx]] = [flowSteps[newIdx], flowSteps[idx]];
  renderFlowSteps();
}

// ── In-Flow Inspection Pre-Steps Management ─────────────────

function addInspectionStep(stepId) {
  const s = flowSteps.find(s => s._id === stepId);
  if (s) {
    if (!s.inspection_steps) s.inspection_steps = [];
    s.inspection_steps.push({
      _id: Date.now() + Math.random(),
      url: '',
      fields: [],
      submit_selector: '',
      wait_for_url: ''
    });
    renderFlowSteps();
  }
}

function removeInspectionStep(stepId, insStepId) {
  const s = flowSteps.find(s => s._id === stepId);
  if (s && s.inspection_steps) {
    s.inspection_steps = s.inspection_steps.filter(is => is._id !== insStepId);
    renderFlowSteps();
  }
}

function updateInspectionStep(stepId, insStepId, key, val) {
  const s = flowSteps.find(s => s._id === stepId);
  if (s && s.inspection_steps) {
    const is = s.inspection_steps.find(i => i._id === insStepId);
    if (is) is[key] = val;
  }
}

function addInspectionField(stepId, insStepId) {
  const s = flowSteps.find(s => s._id === stepId);
  if (s && s.inspection_steps) {
    const is = s.inspection_steps.find(i => i._id === insStepId);
    if (is) {
      if (!is.fields) is.fields = [];
      is.fields.push({ selector: '', literal_value: '' });
      renderFlowSteps();
    }
  }
}

function updateInspectionField(stepId, insStepId, fi, key, val) {
  const s = flowSteps.find(s => s._id === stepId);
  if (s && s.inspection_steps) {
    const is = s.inspection_steps.find(i => i._id === insStepId);
    if (is && is.fields && is.fields[fi]) {
      is.fields[fi][key] = val;
    }
  }
}

function removeInspectionField(stepId, insStepId, fi) {
    const s = flowSteps.find(s => s._id === stepId);
    if (s && s.inspection_steps) {
        const is = s.inspection_steps.find(i => i._id === insStepId);
        if (is && is.fields) {
            is.fields.splice(fi, 1);
            renderFlowSteps();
        }
    }
}

// ── In-Flow Inspection ──────────────────────────────────────

async function inspectStepInFlow(stepId) {
  const step = flowSteps.find(s => s._id === stepId);
  if (!step.url) { alert('Please enter a URL for this step first.'); return; }
  
  const btn = document.getElementById(`btn-ins-${stepId}`);
  const oldText = btn.innerHTML;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;display:inline-block;vertical-align:middle;"></span> Inspecting...';
  btn.disabled = true;

  try {
    const inspectionPayload = (step.inspection_steps || []).map(is => ({
        url: is.url,
        fields: (is.fields || []).map(f => ({
            selector: `[name="${f.selector}"]`, 
            literal_value: f.literal_value
        })),
        submit_selector: is.submit_selector,
        wait_for_url: is.wait_for_url
    }));

    const result = await API.inspect(step.url, inspectionPayload);
    const mappings = [];
    
    for (const f of (result.inputs || [])) {
      if (f.type === 'hidden') continue;
      const selector = f.name ? `input[name="${f.name}"]` : (f.id ? `#${f.id}` : '');
      if (!selector) continue;
      let ft = f.type;
      if (!['text','password','email','tel','number','radio','checkbox'].includes(ft)) ft = 'text';
      
      mappings.push({ _id: Date.now() + Math.random(), selector, field_type: ft, radio_name: ft === 'radio' ? (f.name || '') : '', source: 'csv_column', csv_column: '', literal_value: '', label: f.placeholder || f.name || selector, extracted_value: f.value || '', value_map: [] });
    }
    
    for (const s of (result.selects || [])) {
      const selector = s.name ? `select[name="${s.name}"]` : (s.id ? `#${s.id}` : '');
      if (!selector) continue;
      mappings.push({ _id: Date.now() + Math.random(), selector, field_type: 'select', source: 'csv_column', csv_column: '', literal_value: '', label: s.name || selector, options: s.options, value_map: [] });
    }
    
    for (const t of (result.textareas || [])) {
      const selector = t.name ? `textarea[name="${t.name}"]` : (t.id ? `#${t.id}` : '');
      if (!selector) continue;
      mappings.push({ _id: Date.now() + Math.random(), selector, field_type: 'textarea', source: 'csv_column', csv_column: '', literal_value: '', label: t.placeholder || t.name || selector, value_map: [] });
    }
    
    for (const b of (result.buttons || [])) {
      const selector = b.id ? `#${b.id}` : (b.type === 'submit' ? 'button[type="submit"]' : `button:has-text("${b.text}")`);
      mappings.push({
        _id: Date.now() + Math.random(),
        selector,
        field_type: 'click', 
        source: 'literal',
        csv_column: '',
        literal_value: '',
        label: b.text ? b.text.substring(0, 30) : selector,
        value_map: []
      });
    }

    step.field_mappings.push(...mappings);
    renderFlowSteps();
    
  } catch (e) {
    alert('Inspection failed: ' + e.message);
  } finally {
    if(btn) { btn.innerHTML = oldText; btn.disabled = false; }
  }
}

// ── Mapping management ──────────────────────────────────────

function addMapping(stepId) {
  const s = flowSteps.find(s => s._id === stepId);
  if (!s) return;
  s.field_mappings.push({
    _id: Date.now() + Math.random(),
    selector: '',
    field_type: 'text',
    radio_name: '',
    source: 'csv_column',
    csv_column: '',
    literal_value: '',
    label: '',
    value_map: [],
    file_accept: 'any',
    file_max_mb: 0,
    file_source: 'server_path',
  });
  renderFlowSteps();
}

function removeMapping(stepId, mid) {
  const s = flowSteps.find(s => s._id === stepId);
  if (s) { s.field_mappings = s.field_mappings.filter(m => m._id !== mid); renderFlowSteps(); }
}

function updateMapping(stepId, mid, key, val) {
  const s = flowSteps.find(s => s._id === stepId);
  if (!s) return;
  const m = s.field_mappings.find(m => m._id === mid);
  if (m) m[key] = val;
  if (key === 'source' || key === 'field_type') renderFlowSteps();
}

// ── Value Map management ────────────────────────────────────

function addValueMap(stepId, mappingId) {
  const s = flowSteps.find(s => s._id === stepId);
  const m = s.field_mappings.find(m => m._id === mappingId);
  if (m) {
    if (!m.value_map) m.value_map = [];
    m.value_map.push({ _id: Date.now() + Math.random(), from_val: '', to_val: '' });
    renderFlowSteps();
  }
}

function updateValueMap(stepId, mappingId, vmId, key, val) {
  const s = flowSteps.find(s => s._id === stepId);
  const m = s.field_mappings.find(m => m._id === mappingId);
  if (m && m.value_map) {
    const vm = m.value_map.find(v => v._id === vmId);
    if (vm) vm[key] = val;
  }
}

function removeValueMap(stepId, mappingId, vmId) {
  const s = flowSteps.find(s => s._id === stepId);
  const m = s.field_mappings.find(m => m._id === mappingId);
  if (m && m.value_map) {
    m.value_map = m.value_map.filter(v => v._id !== vmId);
    renderFlowSteps();
  }
}

// ── Recipe login steps (in flow builder) ───────────────────

let recipeLoginSteps = [];

function addRecipeLoginStep() {
  recipeLoginSteps.push({
    _id: Date.now(),
    url: '',
    fields: [],
    submit_selector: 'button[type="submit"]',
    wait_for_url: '',
  });
  renderRecipeLoginSteps();
}

function removeRecipeLoginStep(id) {
  recipeLoginSteps = recipeLoginSteps.filter(s => s._id !== id);
  renderRecipeLoginSteps();
}

function addRecipeLoginField(stepId) {
  const s = recipeLoginSteps.find(s => s._id === stepId);
  if (s) s.fields.push({ selector: '', field_type: 'text', source: 'literal', literal_value: '', label: '' });
  renderRecipeLoginSteps();
}

function updateRecipeLoginStep(id, key, val) {
  const s = recipeLoginSteps.find(s => s._id === id);
  if (s) s[key] = val;
}

function updateRecipeLoginField(stepId, fi, key, val) {
  const s = recipeLoginSteps.find(s => s._id === stepId);
  if (s && s.fields[fi]) s.fields[fi][key] = val;
}

function removeRecipeLoginField(stepId, fi) {
  const s = recipeLoginSteps.find(s => s._id === stepId);
  if (s) { s.fields.splice(fi, 1); renderRecipeLoginSteps(); }
}

function renderRecipeLoginSteps() {
  const container = document.getElementById('recipe-login-steps');
  if (!recipeLoginSteps.length) {
    container.innerHTML = '<p class="hint" style="padding:.5rem 0">No login steps — add one if the site requires authentication before running the flow.</p>';
    return;
  }
  container.innerHTML = recipeLoginSteps.map((step, si) => `
    <div class="login-step-card">
      <div class="row">
        <span class="badge badge-blue">Login ${si + 1}</span>
        <span style="font-weight:600;flex:1">${step.url || 'Login Step'}</span>
        <button class="btn btn-sm btn-danger" onclick="removeRecipeLoginStep(${step._id})">✕</button>
      </div>
      <div class="form-grid" style="margin-top:.75rem">
        <div class="form-group">
          <label>URL</label>
          <input class="input" type="url" value="${esc(step.url)}"
            onchange="updateRecipeLoginStep(${step._id},'url',this.value)" placeholder="https://…/login/">
        </div>
        <div class="form-group">
          <label>Submit selector</label>
          <input class="input" value="${esc(step.submit_selector)}"
            onchange="updateRecipeLoginStep(${step._id},'submit_selector',this.value)"
            placeholder='button[type="submit"]'>
        </div>
        <div class="form-group" style="grid-column:1/-1">
          <label>Wait for URL to contain</label>
          <input class="input" value="${esc(step.wait_for_url)}"
            onchange="updateRecipeLoginStep(${step._id},'wait_for_url',this.value)"
            placeholder="/dashboard/">
        </div>
      </div>
      <div class="step-fields" style="margin-top:.75rem">
        <div style="font-size:.8rem;font-weight:600;color:var(--text2);margin-bottom:.5rem">Credential Fields</div>
        ${step.fields.map((f, fi) => `
          <div class="login-field-row" style="grid-template-columns:1fr 80px 1fr 36px">
            <input class="input" placeholder='Selector e.g. [name="identifier"]'
              value="${esc(f.selector)}"
              onchange="updateRecipeLoginField(${step._id},${fi},'selector',this.value)">
            <select class="input" onchange="updateRecipeLoginField(${step._id},${fi},'field_type',this.value)">
              <option value="text" ${f.field_type==='text'?'selected':''}>text</option>
              <option value="password" ${f.field_type==='password'?'selected':''}>password</option>
            </select>
            <input class="input" placeholder="Value (literal)"
              value="${esc(f.literal_value)}"
              type="${f.field_type==='password'?'password':'text'}"
              onchange="updateRecipeLoginField(${step._id},${fi},'literal_value',this.value)">
            <button class="btn btn-sm btn-danger" onclick="removeRecipeLoginField(${step._id},${fi})">✕</button>
          </div>
        `).join('')}
        <button class="btn btn-sm btn-ghost" onclick="addRecipeLoginField(${step._id})" style="margin-top:.35rem">+ Add Field</button>
      </div>
    </div>
  `).join('');
}

// ── Render flow steps ───────────────────────────────────────

function renderFlowSteps() {
  const container = document.getElementById('flow-steps-list');
  if (!flowSteps.length) {
    container.innerHTML = '<div class="empty-state">No steps yet. Click "+ Add Step" to begin.</div>';
    return;
  }

  container.innerHTML = flowSteps.map((step, si) => `
    <div class="flow-step" id="fstep-${step._id}">
      <div class="flow-step-header" onclick="toggleStep('${step._id}')">
        <div class="step-num">${si + 1}</div>
        <div style="flex:1;min-width:0">
          <div class="step-label">${esc(step.label || 'Unnamed Step')}</div>
          <div class="step-url">${esc(step.url || '(no URL)')}</div>
        </div>
        <span class="badge">${step.field_mappings.length} fields</span>
        <div class="row gap-sm" onclick="event.stopPropagation()">
          <button class="btn btn-sm btn-ghost" onclick="moveStep('${step._id}',-1)" title="Move up" ${si===0?'disabled':''}>↑</button>
          <button class="btn btn-sm btn-ghost" onclick="moveStep('${step._id}',1)"  title="Move down" ${si===flowSteps.length-1?'disabled':''}>↓</button>
          <button class="btn btn-sm btn-danger" onclick="removeFlowStep('${step._id}')">✕</button>
        </div>
      </div>
      <div class="flow-step-body ${step._open ? 'open' : ''}">
        <div class="form-grid" style="margin-bottom:1rem">
          <div class="form-group">
            <label>Step Label</label>
            <input class="input" value="${esc(step.label)}"
              onchange="updateStep('${step._id}','label',this.value)">
          </div>
          <div class="form-group">
            <label>URL to navigate to</label>
            <div class="row gap-sm">
              <input class="input flex-1" type="url" value="${esc(step.url)}"
                placeholder="Leave blank to stay on current page"
                onchange="updateStep('${step._id}','url',this.value)">
              <button id="btn-ins-${step._id}" class="btn btn-primary btn-sm" onclick="inspectStepInFlow('${step._id}')">Auto-Extract Fields</button>
            </div>
          </div>
          
          <div style="grid-column:1/-1; background: var(--bg); padding: .75rem; border: 1px solid var(--border); border-radius: var(--radius-sm); margin-top: .5rem;">
              <div class="row" style="justify-content:space-between; margin-bottom:.5rem;">
                  <div style="font-size: .85rem; font-weight: 600; color: var(--accent);">Inspection Setup (Optional)</div>
              </div>
              <p class="hint" style="margin-bottom: .5rem;">Define steps needed *just* to reach this page for inspection (e.g., logging in or clicking a menu). If the URL is directly accessible, leave this blank and just click Auto-Extract.</p>
              
              <div id="ins-steps-${step._id}">
                  ${(step.inspection_steps || []).map((is, isi) => `
                      <div style="background: var(--bg2); padding: .5rem; border-radius: var(--radius-sm); margin-bottom: .5rem; border: 1px solid var(--border2);">
                           <div class="row" style="margin-bottom: .5rem;">
                                <span class="badge">Pre-Step ${isi + 1}</span>
                                <button class="btn btn-sm btn-danger" style="margin-left:auto;" onclick="removeInspectionStep('${step._id}', ${is._id})">✕</button>
                           </div>
                           <div class="form-grid">
                               <div class="form-group">
                                    <input class="input" placeholder="URL (e.g., /login)" value="${esc(is.url)}" onchange="updateInspectionStep('${step._id}', ${is._id}, 'url', this.value)">
                               </div>
                               <div class="form-group">
                                    <input class="input" placeholder="Submit Selector" value="${esc(is.submit_selector)}" onchange="updateInspectionStep('${step._id}', ${is._id}, 'submit_selector', this.value)">
                               </div>
                               <div class="form-group" style="grid-column:1/-1;">
                                    <input class="input" placeholder="Wait for URL containing..." value="${esc(is.wait_for_url)}" onchange="updateInspectionStep('${step._id}', ${is._id}, 'wait_for_url', this.value)">
                               </div>
                           </div>
                           <div style="margin-top:.5rem;">
                               ${(is.fields || []).map((f, fi) => `
                                    <div class="row gap-sm" style="margin-bottom: .3rem;">
                                        <input class="input flex-1" placeholder='Field name (e.g. "identifier")' value="${esc(f.selector)}" onchange="updateInspectionField('${step._id}', ${is._id}, ${fi}, 'selector', this.value)">
                                        <input class="input flex-1" placeholder="Value (Literal)" value="${esc(f.literal_value)}" onchange="updateInspectionField('${step._id}', ${is._id}, ${fi}, 'literal_value', this.value)">
                                        <button class="btn btn-sm btn-danger" onclick="removeInspectionField('${step._id}', ${is._id}, ${fi})">✕</button>
                                    </div>
                               `).join('')}
                               <button class="btn btn-sm btn-ghost" onclick="addInspectionField('${step._id}', ${is._id})">+ Add Action</button>
                           </div>
                      </div>
                  `).join('')}
              </div>
              <button class="btn btn-sm btn-ghost" onclick="addInspectionStep('${step._id}')">+ Add Pre-Navigation Step</button>
          </div>

          <div class="form-group" style="grid-column:1/-1; background: var(--bg2); padding: .75rem; border: 1px solid var(--border2); border-radius: var(--radius-sm); margin-top: .5rem;">
            <label class="toggle-label" style="font-weight: 600; color: var(--text);">
              <input type="checkbox" ${step.requires_captcha ? 'checked' : ''} onchange="updateStep('${step._id}','requires_captcha',this.checked)">
              Requires Human Handoff (e.g. CAPTCHA)
            </label>
            <div id="captcha-fields-${step._id}" class="${step.requires_captcha ? '' : 'hidden'}" style="margin-top: .75rem;">
              <div class="form-grid">
                <div class="form-group">
                  <label>Image/Canvas Selector</label>
                  <input class="input" placeholder="e.g. canvas.captcha-canvas" value="${esc(step.captcha_image_selector||'')}" onchange="updateStep('${step._id}','captcha_image_selector',this.value)">
                </div>
                <div class="form-group">
                  <label>Input Field Selector</label>
                  <input class="input" placeholder="e.g. input#captcha" value="${esc(step.captcha_input_selector||'')}" onchange="updateStep('${step._id}','captcha_input_selector',this.value)">
                </div>
              </div>
            </div>
          </div>

          <div style="grid-column:1/-1; background: var(--bg2); padding: .75rem; border: 1px solid var(--border2); border-radius: var(--radius-sm); margin-top: .5rem;">
            <div style="font-size: .85rem; font-weight: 600; color: var(--accent); margin-bottom: .5rem;">Error Handling</div>
            <div class="form-grid">
              <div class="form-group">
                <label>Error selector</label>
                <input class="input" placeholder="e.g. #send_err:not(:empty)" value="${esc(step.error_selector||'')}" onchange="updateStep('${step._id}','error_selector',this.value)">
              </div>
              <div class="form-group">
                <label>Error text contains <span class="badge">optional</span></label>
                <input class="input" placeholder="e.g. Invalid OTP" value="${esc(step.error_text_contains||'')}" onchange="updateStep('${step._id}','error_text_contains',this.value)">
              </div>
              <div class="form-group">
                <label>On error</label>
                <select class="input" onchange="updateStep('${step._id}','on_error',this.value)">
                  <option value="fail" ${(step.on_error||'fail')==='fail'?'selected':''}>fail — mark row failed</option>
                  <option value="retry" ${(step.on_error||'fail')==='retry'?'selected':''}>retry — re-ask human_input fields</option>
                  <option value="ask" ${(step.on_error||'fail')==='ask'?'selected':''}>ask — prompt operator: retry or fail</option>
                </select>
              </div>
              <div class="form-group" style="${(step.on_error||'fail')==='fail' ? 'display:none' : ''}">
                <label>Max retries</label>
                <input class="input" type="number" min="1" max="10" value="${step.max_retries != null ? step.max_retries : 2}" onchange="updateStep('${step._id}','max_retries',parseInt(this.value)||2)">
              </div>
              <div class="form-group" style="grid-column:1/-1;">
                <label class="toggle-label">
                  <input type="checkbox" ${step.dismiss_dialogs ? 'checked' : ''} onchange="updateStep('${step._id}','dismiss_dialogs',this.checked)">
                  Auto-dismiss native browser dialogs (alert / confirm)
                </label>
              </div>
              <div class="form-group" style="${step.dismiss_dialogs ? '' : 'display:none'}">
                <label>Dialog action</label>
                <select class="input" onchange="updateStep('${step._id}','dialog_action',this.value)">
                  <option value="accept" ${(step.dialog_action||'accept')==='accept'?'selected':''}>accept (OK / Yes)</option>
                  <option value="dismiss" ${(step.dialog_action||'accept')==='dismiss'?'selected':''}>dismiss (Cancel / No)</option>
                </select>
              </div>
            </div>
          </div>

          <div class="form-group">
            <label>Submit selector</label>
            <input class="input" value="${esc(step.submit_selector)}"
              placeholder='button[type="submit"]'
              onchange="updateStep('${step._id}','submit_selector',this.value)">
          </div>
          <div class="form-group">
            <label>Wait for URL to contain</label>
            <input class="input" value="${esc(step.wait_for_url)}"
              placeholder="/dashboard/"
              onchange="updateStep('${step._id}','wait_for_url',this.value)">
          </div>
          <div class="form-group">
            <label>Wait for selector (optional)</label>
            <input class="input" value="${esc(step.wait_for_selector||'')}"
              placeholder=".success-message"
              onchange="updateStep('${step._id}','wait_for_selector',this.value)">
          </div>
          <div class="form-group" style="justify-content:flex-end;padding-top:1.4rem">
            <label class="toggle-label">
              <input type="checkbox" ${step.skip_if_no_data?'checked':''}
                onchange="updateStep('${step._id}','skip_if_no_data',this.checked)">
              Skip if no CSV data
            </label>
          </div>
          <div class="form-group" style="justify-content:flex-end;padding-top:.5rem">
            <label class="toggle-label">
              <input type="checkbox" ${step.opens_new_tab?'checked':''}
                onchange="updateStep('${step._id}','opens_new_tab',this.checked)">
              Submit opens new tab
            </label>
          </div>
        </div>

        <div style="margin-bottom:.5rem;font-size:.82rem;font-weight:600;color:var(--text2)">Field Mappings</div>
        <div style="display:grid;grid-template-columns:180px 120px 1fr auto auto;gap:.35rem;margin-bottom:.35rem;padding:0 .1rem">
          <span style="font-size:.73rem;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.5px">Selector</span>
          <span style="font-size:.73rem;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.5px">Field Type</span>
          <span style="font-size:.73rem;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.5px">CSV Column / Literal Value</span>
          <span></span>
          <span></span>
        </div>

        ${step.field_mappings.map(m => renderMappingRow(step._id, m)).join('')}

        <button class="btn btn-sm btn-ghost" onclick="addMapping('${step._id}')" style="margin-top:.5rem">+ Add Field</button>
      </div>
    </div>
  `).join('');
}

function renderMappingRow(stepId, m) {
  const ftypes = ['text','password','email','tel','number','textarea','select','radio','checkbox','click','human_input','split_fill','file_upload'];
  
  let valueMapHtml = '';
  if (m.value_map && m.value_map.length > 0) {
    valueMapHtml = `<div style="grid-column:1/-1; padding: .5rem; background: var(--bg); border-radius: var(--radius-sm); margin-bottom:.5rem;">
      <div style="font-size:.75rem; color:var(--text3); margin-bottom:.3rem;">Value Mapping (If CSV matches 'From', replace with 'To')</div>
      ${m.value_map.map(vm => `
        <div class="row gap-sm" style="margin-bottom:.3rem">
          <input class="input" style="font-size:.8rem" placeholder="From (e.g. Male)" value="${esc(vm.from_val)}" onchange="updateValueMap('${stepId}',${m._id},${vm._id},'from_val',this.value)">
          <span style="color:var(--text3)">→</span>
          <input class="input" style="font-size:.8rem" placeholder="To (e.g. male)" value="${esc(vm.to_val)}" onchange="updateValueMap('${stepId}',${m._id},${vm._id},'to_val',this.value)">
          <button class="btn btn-sm btn-danger" onclick="removeValueMap('${stepId}',${m._id},${vm._id})">✕</button>
        </div>
      `).join('')}
    </div>`;
  }

  let hintHtml = '';
  if (m.field_type === 'select' && m.options && m.options.length > 0) {
     const optLabels = m.options.slice(0, 10).map(o => `<span style="margin-right:.8rem">${esc(o.text)} <b style="color:var(--text);font-family:var(--mono)">${esc(o.value)}</b></span>`).join('');
     hintHtml = `<div style="grid-column:1/-1; font-size:.75rem; color:var(--text3); margin-top:-.3rem; margin-bottom:.4rem; padding-left:.5rem; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="Options available">Options: ${optLabels}${m.options.length>10?'...':''}</div>`;
  } else if ((m.field_type === 'radio' || m.field_type === 'checkbox') && m.extracted_value) {
     hintHtml = `<div style="grid-column:1/-1; font-size:.75rem; color:var(--text3); margin-top:-.3rem; margin-bottom:.4rem; padding-left:.5rem;">Detected HTML value: <b style="color:var(--text);font-family:var(--mono)">${esc(m.extracted_value)}</b></div>`;
  }

  // human_input: show question field
  let humanInputHtml = '';
  if (m.field_type === 'human_input') {
    humanInputHtml = `
    <div style="grid-column:1/-1; padding:.5rem; background: var(--bg); border-radius: var(--radius-sm); margin-bottom:.5rem; border: 1px solid var(--accent);">
      <div style="font-size:.75rem; color:var(--accent); font-weight:600; margin-bottom:.3rem;">Question to ask operator</div>
      <input class="input" style="font-size:.85rem" placeholder="e.g. Enter the OTP sent to the user's mobile"
        value="${esc(m.human_input_question||'')}"
        onchange="updateMapping('${stepId}',${m._id},'human_input_question',this.value)">
      <div style="font-size:.75rem; color:var(--text3); margin-top:.3rem;">Answer will be filled into: <b>${esc(m.selector||'(selector above)')}</b></div>
    </div>`;
  }

  // split_fill: show per-box selector+length config
  let splitFillHtml = '';
  if (m.field_type === 'split_fill') {
    const boxes = m.split_boxes || [];
    const humanInputQuestionHtml = m.source === 'human_input' ? `
      <div style="margin-bottom:.5rem;">
        <div style="font-size:.75rem; color:var(--accent); font-weight:600; margin-bottom:.3rem;">Question to ask operator</div>
        <input class="input" style="font-size:.85rem" placeholder="e.g. Enter the OTP sent to the user's mobile"
          value="${esc(m.human_input_question||'')}"
          onchange="updateMapping('${stepId}',${m._id},'human_input_question',this.value)">
      </div>` : '';
    splitFillHtml = `
    <div style="grid-column:1/-1; padding:.5rem; background: var(--bg); border-radius: var(--radius-sm); margin-bottom:.5rem; border: 1px solid var(--border);">
      <div style="font-size:.75rem; color:var(--text3); font-weight:600; margin-bottom:.4rem;">Split Boxes — one row per input box (filled left to right)</div>
      ${humanInputQuestionHtml}
      ${boxes.map((box, bi) => `
        <div class="row gap-sm" style="margin-bottom:.3rem">
          <input class="input flex-1" placeholder="Selector e.g. #aadhaar_1" style="font-size:.8rem"
            value="${esc(box.selector||'')}"
            onchange="updateSplitBox('${stepId}',${m._id},${bi},'selector',this.value)">
          <input class="input" style="width:80px;font-size:.8rem" type="number" min="1" placeholder="Length"
            value="${box.length||1}"
            onchange="updateSplitBox('${stepId}',${m._id},${bi},'length',parseInt(this.value)||1)">
          <button class="btn btn-sm btn-danger" onclick="removeSplitBox('${stepId}',${m._id},${bi})">✕</button>
        </div>
      `).join('')}
      <button class="btn btn-sm btn-ghost" onclick="addSplitBox('${stepId}',${m._id})" style="margin-top:.2rem">+ Add Box</button>
    </div>`;
  }

  // file_upload: show accept type and size limit config
  let fileUploadHtml = '';
  if (m.field_type === 'file_upload') {
    const acceptVal   = m.file_accept  || 'any';
    const maxMbVal    = m.file_max_mb  != null ? m.file_max_mb : 0;
    const fileSource  = m.file_source  || 'server_path';
    fileUploadHtml = `
    <div style="grid-column:1/-1; padding:.5rem; background: var(--bg); border-radius: var(--radius-sm); margin-bottom:.5rem; border: 1px solid var(--border);">
      <div style="font-size:.75rem; color:var(--text3); font-weight:600; margin-bottom:.4rem;">File Upload Constraints</div>
      <div class="row gap-sm" style="flex-wrap:wrap; align-items:center; margin-bottom:.35rem;">
        <label style="font-size:.8rem; color:var(--text2); white-space:nowrap;">File source:</label>
        <select class="input" style="width:200px" onchange="updateMapping('${stepId}',${m._id},'file_source',this.value)">
          <option value="server_path"  ${fileSource==='server_path' ?'selected':''}>Server path (absolute)</option>
          <option value="local_disk"   ${fileSource==='local_disk'  ?'selected':''}>Local disk (via temp folder)</option>
          <option value="external_url" ${fileSource==='external_url'?'selected':''}>External URL (S3 / HTTP link)</option>
        </select>
      </div>
      ${fileSource === 'local_disk' ? `<div style="font-size:.75rem; color:var(--text3); margin-bottom:.35rem;">Copy your file to <code>./storage/temp_attachments/</code> on the host, then put the filename (e.g. <code>/app/storage/temp_attachments/photo.jpg</code>) in the CSV. The file is auto-deleted from temp after upload.</div>` : ''}
      ${fileSource === 'external_url' ? `<div style="font-size:.75rem; color:var(--text3); margin-bottom:.35rem;">CSV column must contain a full HTTP/HTTPS URL (e.g. an S3 presigned URL). The file is downloaded to temp storage, uploaded, then auto-deleted.</div>` : ''}
      ${fileSource === 'server_path' ? `<div style="font-size:.75rem; color:var(--text3); margin-bottom:.35rem;">CSV column must contain the <b>absolute server path</b> to the file (e.g. <code>/app/storage/uploads/photo.jpg</code>).</div>` : ''}
      <div class="row gap-sm" style="flex-wrap:wrap; align-items:center; margin-bottom:.35rem;">
        <label style="font-size:.8rem; color:var(--text2); white-space:nowrap;">Accept type:</label>
        <select class="input" style="width:160px" onchange="updateMapping('${stepId}',${m._id},'file_accept',this.value)">
          <option value="any"   ${acceptVal==='any'  ?'selected':''}>Any document</option>
          <option value="image" ${acceptVal==='image'?'selected':''}>Image only (image/*)</option>
          <option value="pdf"   ${acceptVal==='pdf'  ?'selected':''}>PDF / Document only</option>
        </select>
        <label style="font-size:.8rem; color:var(--text2); white-space:nowrap; margin-left:.5rem;">Max size (MB):</label>
        <input class="input" type="number" min="0" step="0.1" style="width:90px"
          placeholder="0 = no limit"
          value="${maxMbVal}"
          onchange="updateMapping('${stepId}',${m._id},'file_max_mb',parseFloat(this.value)||0)">
        <span class="hint" style="font-size:.75rem; color:var(--text3);">0 = no limit; set 2 to enforce &lt;2 MB</span>
      </div>
    </div>`;
  }

  return `
    <div class="mapping-row" id="mrow-${m._id}">
      <input class="input" placeholder='[name="field"] or #id'
        value="${esc(m.selector)}"
        title="${esc(m.label||m.selector)}"
        onchange="updateMapping('${stepId}',${m._id},'selector',this.value)">
      <select class="input" onchange="updateMapping('${stepId}',${m._id},'field_type',this.value)">
        ${ftypes.map(t => `<option value="${t}" ${m.field_type===t?'selected':''}>${t}</option>`).join('')}
      </select>
      ${renderMappingValue(stepId, m)}
      <button class="btn btn-sm btn-ghost" onclick="addValueMap('${stepId}',${m._id})" title="Add inline mapping">🔀</button>
      <button class="btn btn-sm btn-danger" onclick="removeMapping('${stepId}',${m._id})">✕</button>
    </div>
    ${hintHtml}
    ${m.field_type === 'radio' ? `
    <div style="grid-column:1/-1;padding-left:.5rem;margin-bottom:.3rem;margin-top:-.25rem">
      <div style="font-size:.75rem;color:var(--text3);margin-bottom:.2rem">Radio name attribute:</div>
      <input class="input" style="width:200px;font-size:.8rem" placeholder='e.g. gender'
        value="${esc(m.radio_name||'')}"
        onchange="updateMapping('${stepId}',${m._id},'radio_name',this.value)">
    </div>` : ''}
    ${humanInputHtml}
    ${splitFillHtml}
    ${fileUploadHtml}
    ${valueMapHtml}
  `;
}

function renderMappingValue(stepId, m) {
  // human_input: source is implicit, no CSV/literal selector needed here
  if (m.field_type === 'human_input') {
    return `<div style="font-size:.8rem;color:var(--accent);padding:.4rem .5rem;background:var(--bg);border-radius:var(--radius-sm);border:1px solid var(--accent);">Human Input</div>`;
  }

  // split_fill: value comes from CSV column or human input
  if (m.field_type === 'split_fill') {
    if (m.source === 'human_input') {
      return `<div style="font-size:.8rem;color:var(--accent);padding:.4rem .5rem;background:var(--bg);border-radius:var(--radius-sm);border:1px solid var(--accent);">Human Input</div>`;
    }
    return `
      <div class="row gap-sm" style="min-width:0">
        <select class="input" style="width:110px;flex-shrink:0" onchange="updateMapping('${stepId}',${m._id},'source',this.value)">
          <option value="csv_column" ${m.source !== 'human_input' ? 'selected' : ''}>CSV col</option>
          <option value="human_input" ${m.source === 'human_input' ? 'selected' : ''}>Human Input</option>
        </select>
        <input class="input flex-1" placeholder="CSV header name"
          value="${esc(m.csv_column)}"
          onchange="updateMapping('${stepId}',${m._id},'csv_column',this.value)">
      </div>
    `;
  }

  if (m.source === 'literal') {
    const isPass = m.field_type === 'password';
    return `
      <div class="row gap-sm" style="min-width:0">
        <select class="input" style="width:90px;flex-shrink:0" onchange="updateMapping('${stepId}',${m._id},'source',this.value)">
          <option value="csv_column">CSV col</option>
          <option value="literal" selected>Literal</option>
        </select>
        <input class="input flex-1" type="${isPass?'password':'text'}" placeholder="literal value"
          value="${esc(m.literal_value)}"
          onchange="updateMapping('${stepId}',${m._id},'literal_value',this.value)">
      </div>
    `;
  }
  // csv_column
  return `
    <div class="row gap-sm" style="min-width:0">
      <select class="input" style="width:90px;flex-shrink:0" onchange="updateMapping('${stepId}',${m._id},'source',this.value)">
        <option value="csv_column" selected>CSV col</option>
        <option value="literal">Literal</option>
      </select>
      <input class="input flex-1" placeholder="CSV header name"
        value="${esc(m.csv_column)}"
        onchange="updateMapping('${stepId}',${m._id},'csv_column',this.value)">
    </div>
  `;
}

// ── Split box management (for split_fill field type) ────────

function addSplitBox(stepId, mappingId) {
  const s = flowSteps.find(s => s._id === stepId);
  const m = s && s.field_mappings.find(m => m._id === mappingId);
  if (m) {
    if (!m.split_boxes) m.split_boxes = [];
    m.split_boxes.push({ selector: '', length: 4 });
    renderFlowSteps();
  }
}

function updateSplitBox(stepId, mappingId, bi, key, val) {
  const s = flowSteps.find(s => s._id === stepId);
  const m = s && s.field_mappings.find(m => m._id === mappingId);
  if (m && m.split_boxes && m.split_boxes[bi]) {
    m.split_boxes[bi][key] = val;
  }
}

function removeSplitBox(stepId, mappingId, bi) {
  const s = flowSteps.find(s => s._id === stepId);
  const m = s && s.field_mappings.find(m => m._id === mappingId);
  if (m && m.split_boxes) {
    m.split_boxes.splice(bi, 1);
    renderFlowSteps();
  }
}

// ── Save / Load / Clear ─────────────────────────────────────

async function saveRecipe() {
  const name = document.getElementById('recipe-name').value.trim();
  if (!name) { alert('Please enter a recipe name.'); return; }

  const recipe = buildRecipePayload();

  try {
    let result;
    if (editingRecipeId) {
      result = await API.updateRecipe(editingRecipeId, recipe);
    } else {
      result = await API.saveRecipe(recipe);
      editingRecipeId = result.recipe_id;
    }
    const st = document.getElementById('save-status');
    st.textContent = `Saved as "${result.name}" (${result.recipe_id})`;
    setTimeout(() => { st.textContent = ''; }, 4000);
    loadRecipesList();
    populateRunRecipeSelect();
  } catch (e) {
    alert('Save error: ' + e.message);
  }
}

function buildRecipePayload() {
  const cleanLoginSteps = recipeLoginSteps.map(s => ({
    url: s.url,
    fields: (s.fields || []).map(f => ({
      selector: f.selector,
      field_type: f.field_type || 'text',
      source: 'literal',
      literal_value: f.literal_value || '',
    })),
    submit_selector: s.submit_selector,
    wait_for_url: s.wait_for_url,
  }));

  const cleanFlow = flowSteps.map(s => ({
    step_id: s.step_id || s._id,
    label: s.label,
    url: s.url,
    inspection_steps: (s.inspection_steps || []).map(is => ({
       url: is.url,
       fields: (is.fields || []).map(f => ({ selector: f.selector, literal_value: f.literal_value })),
       submit_selector: is.submit_selector,
       wait_for_url: is.wait_for_url
    })),
    captcha_image_selector: s.requires_captcha ? s.captcha_image_selector : '',
    captcha_input_selector: s.requires_captcha ? s.captcha_input_selector : '',
    opens_new_tab: !!s.opens_new_tab,
    field_mappings: (s.field_mappings || []).map(m => ({
      selector: m.selector,
      field_type: m.field_type,
      radio_name: m.radio_name || '',
      source: m.source || 'csv_column',
      csv_column: m.csv_column || '',
      literal_value: m.literal_value || '',
      label: m.label || m.selector,
      options: m.options,
      extracted_value: m.extracted_value,
      human_input_question: m.human_input_question || '',
      split_boxes: m.split_boxes || [],
      file_accept: m.file_accept || 'any',
      file_max_mb: m.file_max_mb != null ? m.file_max_mb : 0,
      file_source: m.file_source || 'server_path',
      value_map: (m.value_map || []).map(v => ({ from_val: v.from_val, to_val: v.to_val }))
    })),
    submit_selector: s.submit_selector || '',
    wait_for_url: s.wait_for_url || '',
    wait_for_selector: s.wait_for_selector || '',
    skip_if_no_data: !!s.skip_if_no_data,
    error_selector: s.error_selector || '',
    error_text_contains: s.error_text_contains || '',
    on_error: s.on_error || 'fail',
    max_retries: s.max_retries != null ? parseInt(s.max_retries) || 2 : 2,
    dismiss_dialogs: !!s.dismiss_dialogs,
    dialog_action: s.dialog_action || 'accept',
  }));

  return {
    name: document.getElementById('recipe-name').value.trim(),
    description: document.getElementById('recipe-desc').value.trim(),
    base_url: document.getElementById('recipe-base-url').value.trim(),
    flow: cleanFlow,
    login_steps: cleanLoginSteps,
    delay: {
      between_records_ms: parseInt(document.getElementById('d-records').value) || 800,
      between_fields_ms:  parseInt(document.getElementById('d-fields').value)  || 100,
      between_steps_ms:   parseInt(document.getElementById('d-steps').value)   || 300,
      char_delay_ms:      parseInt(document.getElementById('d-char').value)    || 0,
      page_load_timeout_ms: parseInt(document.getElementById('d-page').value)  || 15000,
      action_timeout_ms:    parseInt(document.getElementById('d-action').value)|| 8000,
    },
  };
}

function loadRecipeIntoFlow(recipe) {
  editingRecipeId = recipe.recipe_id;
  document.getElementById('recipe-name').value    = recipe.name || '';
  document.getElementById('recipe-desc').value    = recipe.description || '';
  document.getElementById('recipe-base-url').value= recipe.base_url || '';

  const d = recipe.delay || {};
  document.getElementById('d-records').value = d.between_records_ms ?? 800;
  document.getElementById('d-fields').value  = d.between_fields_ms  ?? 100;
  document.getElementById('d-steps').value   = d.between_steps_ms   ?? 300;
  document.getElementById('d-char').value    = d.char_delay_ms      ?? 0;
  document.getElementById('d-page').value    = d.page_load_timeout_ms ?? 15000;
  document.getElementById('d-action').value  = d.action_timeout_ms  ?? 8000;

  recipeLoginSteps = (recipe.login_steps || []).map((s, i) => ({
    ...s, _id: Date.now() + i,
    fields: (s.fields || []).map(f => ({ ...f })),
  }));
  renderRecipeLoginSteps();

  flowSteps = (recipe.flow || []).map((s, si) => ({
    ...s,
    _id: s.step_id || ('step_' + Date.now() + si),
    _open: false,
    requires_captcha: !!(s.captcha_image_selector && s.captcha_input_selector),
    captcha_image_selector: s.captcha_image_selector || '',
    captcha_input_selector: s.captcha_input_selector || '',
    opens_new_tab: !!s.opens_new_tab,
    inspection_steps: (s.inspection_steps || []).map(is => ({
      ...is,
      _id: Date.now() + Math.random(),
      fields: (is.fields || []).map(f => ({...f}))
    })),
    error_selector: s.error_selector || '',
    error_text_contains: s.error_text_contains || '',
    on_error: s.on_error || 'fail',
    max_retries: s.max_retries != null ? s.max_retries : 2,
    dismiss_dialogs: !!s.dismiss_dialogs,
    dialog_action: s.dialog_action || 'accept',
    field_mappings: (s.field_mappings || []).map(m => ({
      ...m, 
      _id: Date.now() + Math.random(),
      options: m.options || [],
      extracted_value: m.extracted_value || '',
      human_input_question: m.human_input_question || '',
      split_boxes: m.split_boxes || [],
      file_accept: m.file_accept || 'any',
      file_max_mb: m.file_max_mb != null ? m.file_max_mb : 0,
      file_source: m.file_source || 'server_path',
      value_map: m.value_map || [] 
    })),
  }));
  renderFlowSteps();
  switchTab('flow');
}

function downloadCurrentRecipeJSON() {
  const recipe = buildRecipePayload();
  const blob = new Blob([JSON.stringify(recipe, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${recipe.name || 'recipe'}.json`;
  a.click();
}

function clearFlow() {
  if (!confirm('Clear all flow steps?')) return;
  flowSteps = [];
  recipeLoginSteps = [];
  editingRecipeId = null;
  document.getElementById('recipe-name').value = '';
  document.getElementById('recipe-desc').value = '';
  document.getElementById('recipe-base-url').value = '';
  renderFlowSteps();
  renderRecipeLoginSteps();
}

function importRecipeFromJSON(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const recipe = JSON.parse(e.target.result);
      loadRecipeIntoFlow(recipe);
      editingRecipeId = null; // imported JSON is treated as a new flow, not an overwrite
      const st = document.getElementById('save-status');
      if (st) st.textContent = 'JSON imported — click "Save Recipe" to add it as a new flow.';
    } catch (err) {
      alert('Invalid JSON file: ' + err.message);
    }
  };
  reader.readAsText(file);
  input.value = '';
}

function esc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}