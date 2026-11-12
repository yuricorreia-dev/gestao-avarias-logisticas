const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const money = value => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(value || 0));
const fmtDate = value => value ? new Date(`${value}T12:00:00`).toLocaleDateString('pt-BR') : '—';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const statusNames = { pending: 'Pendente', under_review: 'Em análise', approved: 'Aprovada' };
const decisionNames = { pending: 'Pendente', include: 'Incluído', exclude: 'Excluído' };
const roleNames = { admin: 'Administrador', analyst: 'Analista', approver: 'Aprovador' };
const functionNames = { owner: 'Proprietário', admin: 'Administrador', manager: 'Gestor', supervisor: 'Supervisor', analyst: 'Analista', operator: 'Operador', viewer: 'Visualizador', legacy_admin: 'Administrador', legacy_analyst: 'Analista', legacy_approver: 'Aprovador' };
const state = { user: null, settings: null, options: {}, occurrences: [], summary: null, audit: [], page: 'dashboard', quickClass: '', review: null, edit: null, wizardStep: 0, drill: null, toastTimer: null };
const today = new Date().toISOString().slice(0, 10);

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  if (options.raw && response.ok) return response;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Não foi possível concluir a operação');
  return data;
}
function showToast(message) {
  const toast = $('#toast'); toast.textContent = message; toast.classList.add('show');
  clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
}
function setView(authenticated) {
  $('#loginView').hidden = authenticated;
  $('#appView').hidden = !authenticated;
}
async function boot() {
  $('#todayLabel').textContent = new Date().toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: 'short' });
  try {
    const result = await api('/api/me'); state.user = result.user; setView(true); setupUser(); await refresh(); showPage('role-home');
  } catch { setView(false); }
}
function setupUser() {
  document.body.dataset.role = state.user.role;
  $('#userName').textContent = state.user.display_name;
  $('#userRole').textContent = functionNames[state.user.role_code] || roleNames[state.user.role] || state.user.role;
  $('#userInitial').textContent = state.user.display_name.trim().charAt(0).toUpperCase();
  $$('.admin-only').forEach(element => element.hidden = !hasPermission('manage_users'));
  $('#newOccurrenceBtn').hidden = !hasPermission('create_occurrences');
}
function hasPermission(permission) { return state.user?.permissions?.includes(permission) || false; }
function renderRoleHome() {
  const screens = {
    owner: ['Painel da corporação', 'ADMINISTRAÇÃO CORPORATIVA', 'Configure colaboradores, cargos, setores, metas e segurança.', [['users', 'Gestão de colaboradores'], ['settings', 'Metas e configurações'], ['sectors', 'Gestão por setores'], ['reports', 'Relatórios']]],
    admin: ['Painel administrativo', 'GESTÃO OPERACIONAL', 'Administre os recursos delegados e acompanhe a operação.', [['users', 'Colaboradores'], ['sectors', 'Setores'], ['analysis', 'Central de análise'], ['reports', 'Relatórios']]],
    manager: ['Painel gerencial', 'RESULTADOS E DECISÕES', 'Acompanhe metas, indicadores e pendências dos setores autorizados.', [['comparison', 'Comparativo de setores'], ['sectors', 'Painéis setoriais'], ['analysis', 'Pendências de análise'], ['reports', 'Relatórios']]],
    supervisor: ['Painel do supervisor', 'ACOMPANHAMENTO DE SETORES', 'Revise lançamentos e acompanhe os setores sob sua responsabilidade.', [['sectors', 'Meus setores'], ['records', 'Lançamentos'], ['analysis', 'Revisões pendentes']]],
    analyst: ['Central de trabalho do analista', 'ANÁLISE E RECLASSIFICAÇÃO', 'Investigue ocorrências, registre pareceres e encaminhe decisões.', [['analysis', 'Central de análise'], ['records', 'Lançamentos'], ['reports', 'Histórico e relatórios']]],
    operator: ['Painel do operador', 'REGISTRO OPERACIONAL', 'Registre ocorrências e acompanhe seus lançamentos autorizados.', [['records', 'Meus lançamentos'], ['records', 'Novo lançamento']]],
    viewer: ['Painel de consulta', 'VISUALIZAÇÃO AUTORIZADA', 'Consulte os indicadores liberados para o seu vínculo.', [['dashboard', 'Indicadores gerais'], ['sectors', 'Setores autorizados']]],
  };
  const screenRole = { legacy_admin: 'admin', legacy_analyst: 'analyst', legacy_approver: 'manager' }[state.user.role_code] || state.user.role_code;
  const home = screens[screenRole] || screens.viewer; const root = $('#roleHomeRoot');
  root.innerHTML = `<div class="role-hero"><p class="eyebrow">${home[1]}</p><h1>${home[0]}</h1><p>${home[2]}</p><div class="role-context"><span>${esc(state.user.corporation_name || 'Ambiente legado')}</span><span>${functionNames[state.user.role_code] || roleNames[state.user.role] || 'Usuário'}</span><span>${(state.user.departments || []).map(item => esc(item.name)).join(' · ') || 'Todos os setores autorizados'}</span></div></div><div class="role-action-grid">${home[3].map(([page, label]) => `<button class="role-action" data-role-action="${page}"><strong>${label}</strong><span>Acessar módulo <b>→</b></span></button>`).join('')}</div>`;
  $$('[data-role-action]', root).forEach(button => button.addEventListener('click', () => showPage(button.dataset.roleAction)));
}
$('#loginForm').addEventListener('submit', async event => {
  event.preventDefault(); $('#loginError').textContent = '';
  const form = new FormData(event.currentTarget);
  try {
    const result = await api('/api/login', { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) });
    state.user = result.user; setView(true); setupUser(); await refresh();
  } catch (error) { $('#loginError').textContent = error.message; }
});
$$('[data-auth-dialog]').forEach(button => button.addEventListener('click', () => {
  const dialog = $(`#${button.dataset.authDialog}`); if (button.dataset.authDialog === 'inviteDialog') $('#inviteAcceptForm').reset(); if (button.dataset.authDialog === 'resetDialog') $('#resetForm').reset(); dialog.showModal();
}));
$('#corporationForm').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; $('#corporationError').textContent = '';
  try { const result = await api('/api/corporations', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form))) }); $('#corporationDialog').close(); showToast(result.message || 'Cadastro iniciado'); if (result.verification_url) window.prompt('Ambiente local: abra este link para confirmar o e-mail', result.verification_url); }
  catch (error) { $('#corporationError').textContent = error.message; }
});
$('#inviteAcceptForm').elements.token.addEventListener('input', async event => {
  const token = event.target.value.trim(); if (token.length < 20) { $('#invitePreview').hidden = true; return; }
  try { const result = await api(`/api/invitations/lookup?token=${encodeURIComponent(token)}`); $('#invitePreview').hidden = false; $('#invitePreview').textContent = `${result.invitation.corporation_name} · ${result.invitation.role_name}`; }
  catch { $('#invitePreview').hidden = true; }
});
$('#inviteAcceptForm').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; $('#inviteError').textContent = '';
  try { const result = await api('/api/invitations/accept', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form))) }); $('#inviteDialog').close(); state.user = result.user; setView(true); setupUser(); await refresh(); showPage('role-home'); showToast('Conta ativada'); }
  catch (error) { $('#inviteError').textContent = error.message; }
});
$('#resetForm').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; $('#resetMessage').textContent = '';
  try { const data = Object.fromEntries(new FormData(form)); const endpoint = data.token && data.password ? '/api/password/reset' : '/api/password/forgot'; const result = await api(endpoint, { method: 'POST', body: JSON.stringify(data) }); $('#resetMessage').textContent = result.message; if (result.reset_url) { form.querySelector('.reset-token-field').hidden = false; form.querySelector('.reset-password-field').hidden = false; form.elements.token.value = new URL(result.reset_url).searchParams.get('token') || ''; } }
  catch (error) { $('#resetMessage').textContent = error.message; }
});
$('#userMenu').addEventListener('click', async () => {
  if (!confirm('Deseja sair do sistema?')) return;
  await api('/api/logout', { method: 'POST', body: '{}' }).catch(() => {});
  state.user = null; setView(false);
});

function paramsFromDashboard() {
  const params = new URLSearchParams();
  const mapping = { date_from: $('#filterFrom').value, date_to: $('#filterTo').value, sector: $('#filterSector').value,
    aisle: $('#filterAisle').value, classification: $('#filterCategory').value, reason: $('#filterReason').value, supplier: $('#filterSupplier').value };
  Object.entries(mapping).forEach(([key, value]) => { if (value) params.set(key, value); });
  return params;
}
async function refresh() {
  const query = paramsFromDashboard().toString();
  const [records, summary, settings, audit, options] = await Promise.all([
    api(`/api/occurrences${query ? `?${query}` : ''}`), api(`/api/summary${query ? `?${query}` : ''}`), api('/api/settings'), api('/api/audit'), api('/api/options')
  ]);
  state.occurrences = records.occurrences; state.summary = summary; state.settings = settings; state.audit = audit.entries; state.options = options;
  fillFilterOptions(); renderDashboard(); renderRecords(); renderAnalysis(); renderReports(); fillCategoryChoices();
  if (state.page === 'settings') renderSettings();
}
function setOptions(element, values, firstLabel) {
  const current = element.value;
  element.innerHTML = `<option value="">${esc(firstLabel)}</option>${values.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('')}`;
  if (values.includes(current)) element.value = current;
}
function fillFilterOptions() {
  setOptions($('#filterSector'), state.options.sector || [], 'Todos'); setOptions($('#filterAisle'), state.options.aisle || [], 'Todas');
  setOptions($('#filterReason'), state.options.reason || [], 'Todos'); setOptions($('#filterSupplier'), state.options.supplier || [], 'Todos');
  setOptions($('#filterCategory'), state.settings.categories, 'Todas'); setOptions($('#analysisCategory'), state.settings.categories, 'Todas');
}
function fillCategoryChoices() {
  const options = state.settings.categories.map(category => `<option value="${esc(category)}">${esc(category)}</option>`).join('');
  $('#createForm [name=classification]').innerHTML = options;
  $('#reviewForm [name=classification]').innerHTML = options;
  $('#splitForm [name=classification]').innerHTML = options;
}
function renderDashboard() {
  const data = state.summary; if (!data) return;
  $('#kpiGross').textContent = money(data.gross); $('#kpiRecovered').textContent = money(data.recovered); $('#kpiCollected').textContent = money(data.collected); $('#kpiNet').textContent = money(data.net); $('#kpiPending').textContent = data.pending;
  $('#monthLimitText').textContent = `${money(data.month_realized)} / ${money(data.monthly_limit)}`;
  $('#dayLimitText').textContent = `${money(data.today_realized)} / ${money(data.daily_limit)}`;
  $('#monthProgress').style.width = `${Math.min(data.month_percent, 100)}%`; $('#dayProgress').style.width = `${Math.min(data.today_percent, 100)}%`;
  $('#monthPercent').textContent = `${data.month_percent}% do limite mensal`; $('#dayPercent').textContent = `${data.today_percent}% do limite diário`;
  $('#discountValue').textContent = money(data.discount_included); $('#discountIncluded').textContent = money(data.discount_included);
  $('#discountExcluded').textContent = money(data.discount_excluded); $('#repackTotal').textContent = money(data.repack_recovery.value);
  $('#repackQty').textContent = new Intl.NumberFormat('pt-BR').format(data.repack_recovery.quantity);
  renderDaily(data.daily); renderBars('#sectorChart', data.sectors, 'sector'); renderBars('#reasonChart', data.reasons, 'reason');
  renderBars('#aisleChart', data.aisles, 'aisle'); renderSignal(data.signal, data.signal_values); renderProducts(data.top_products);
}
function renderDaily(days) {
  const recent = days.slice(-12); const max = Math.max(1, ...recent.map(day => day.gross));
  $('#dailyChart').innerHTML = recent.length ? `${recent.map(day => `<button class="day-column" data-drill-date="${esc(day.date)}" title="${fmtDate(day.date)} · ${money(day.gross)}"><span class="day-bars"><i class="day-bar" style="height:${Math.max(3, day.gross / max * 100)}%"></i><i class="day-bar recovered" style="height:${Math.max(2, day.recovered / max * 100)}%"></i></span><span class="day-label">${new Date(`${day.date}T12:00:00`).getDate()}</span></button>`).join('')}<span class="chart-legend"><span><i class="legend-dot" style="background:#5d7e96"></i>Bruta</span><span><i class="legend-dot" style="background:#77b99b"></i>Recuperada</span></span>` : '<div class="empty-state">Sem dados para o período</div>';
}
function renderBars(selector, values, type) {
  const root = $(selector), max = Math.max(1, ...values.map(item => item.value));
  if (!values.length) { root.innerHTML = '<div class="empty-state">Sem dados para o período</div>'; return; }
  root.innerHTML = values.slice(0, 6).map(item => `<button class="bar-item" data-drill-${type}="${esc(item.name)}" title="${esc(item.name)} · ${money(item.value)}"><span class="bar-name">${esc(item.name)}</span><span class="bar-track"><i class="bar-fill" style="display:block;width:${Math.max(2, item.value / max * 100)}%"></i></span><span class="bar-value">${money(item.value)}</span><span class="bar-pareto">${item.cumulative}%</span></button>`).join('');
}
function renderSignal(values, amounts) {
  const total = values['Sinalizado'] + values['Não sinalizado'];
  const portion = total ? values['Sinalizado'] / total * 100 : 0;
  $('#signalChart').innerHTML = `<div class="signal-ring" style="background:conic-gradient(#18866a ${portion}%,#dce4e8 0)"><strong>${Math.round(portion)}%</strong></div><div class="signal-legend"><button data-drill-signal="1"><i class="signal-dot" style="background:#18866a"></i><span>Sinalizado<small>${money(amounts['Sinalizado'])}</small></span><strong>${values['Sinalizado']}</strong></button><button data-drill-signal="0"><i class="signal-dot" style="background:#dce4e8"></i><span>Não sinalizado<small>${money(amounts['Não sinalizado'])}</small></span><strong>${values['Não sinalizado']}</strong></button></div>`;
}
function renderProducts(products) {
  $('#productChart').innerHTML = products.length ? products.slice(0, 10).map((product, index) => `<button class="product-item" data-drill-product="${esc(product.name)}"><span class="product-rank">${String(index + 1).padStart(2, '0')}</span><span class="product-name">${esc(product.name)}</span><span class="product-qty">${product.quantity} un.</span></button>`).join('') : '<div class="empty-state">Sem produtos no período</div>';
}
function allAllocations() { return state.occurrences.flatMap(occurrence => occurrence.allocations.map(allocation => ({ ...allocation, occurrence }))); }
function statusPill(status) { return `<span class="status-pill status-${status}">${statusNames[status] || status}</span>`; }
function renderRecords() {
  const query = ($('#recordSearch')?.value || '').trim().toLocaleLowerCase('pt-BR'); const status = $('#recordStatus')?.value || '';
  const occurrences = state.occurrences.filter(occurrence => `${occurrence.product_code} ${occurrence.product} ${occurrence.sector} ${occurrence.reason} ${occurrence.responsible}`.toLocaleLowerCase('pt-BR').includes(query) && (!status || occurrence.allocations.some(item => item.status === status)) && (!state.quickClass || occurrence.allocations.some(item => item.classification === state.quickClass)));
  $('#recordsTable').innerHTML = occurrences.map(occurrence => {
    const destinations = occurrence.allocations.map(item => `<button class="table-link" data-review="${occurrence.id}" data-allocation="${item.id}">${esc(item.classification)} · ${item.quantity}</button>`).join('<br>');
    const status = occurrence.allocations.some(item => item.status !== 'approved') ? (occurrence.allocations.some(item => item.status === 'under_review') ? 'under_review' : 'pending') : 'approved';
    return `<tr><td><strong>${esc(occurrence.product)}</strong><small>${esc(occurrence.product_code || `#${occurrence.id}`)} · ${fmtDate(occurrence.occurrence_date)} · ${occurrence.quantity} un.</small></td><td><strong>${esc(occurrence.sector)}</strong><small>${esc(occurrence.aisle || 'Rua não informada')}</small></td><td>${esc(occurrence.initial_classification)}</td><td>${destinations}</td><td>${occurrence.signaled ? 'Sinalizado' : 'Não sinalizado'}</td><td><strong>${money(occurrence.quantity * occurrence.unit_value)}</strong></td><td>${occurrence.record_status === 'cancelled' ? '<span class="status-pill status-pending">Cancelado</span>' : occurrence.record_status === 'draft' ? '<span class="status-pill">Rascunho</span>' : statusPill(status)}</td><td><button class="table-link" data-edit-occurrence="${occurrence.id}">Editar</button></td></tr>`;
  }).join('');
  $('#recordsEmpty').hidden = occurrences.length > 0;
}
function analysisMatches(entry) {
  const query = ($('#analysisSearch').value || '').trim().toLocaleLowerCase('pt-BR');
  return `${entry.occurrence.product} ${entry.occurrence.sector} ${entry.occurrence.reason} ${entry.occurrence.responsible}`.toLocaleLowerCase('pt-BR').includes(query)
    && (!$('#analysisStatus').value || entry.status === $('#analysisStatus').value)
    && (!$('#analysisCategory').value || entry.classification === $('#analysisCategory').value)
    && (!$('#analysisFrom').value || entry.occurrence.occurrence_date >= $('#analysisFrom').value)
    && (!$('#analysisTo').value || entry.occurrence.occurrence_date <= $('#analysisTo').value);
}
function renderAnalysis() {
  const entries = allAllocations(); const pending = entries.filter(entry => entry.status !== 'approved').length;
  $('#pendingNav').textContent = pending; $('#analysisPendingCount').textContent = pending;
  const shown = entries.filter(analysisMatches);
  $('#analysisList').innerHTML = shown.length ? shown.map(entry => `<article class="analysis-item"><div class="analysis-product"><strong>${esc(entry.occurrence.product)}</strong><small>#${entry.occurrence.id} · ${fmtDate(entry.occurrence.occurrence_date)} · ${entry.quantity} un.</small></div><div class="analysis-cell"><small>ORIGINAL · ${esc(entry.occurrence.initial_classification)}</small><span>${esc(entry.occurrence.sector)} · ${esc(entry.occurrence.reason)}</span></div><div class="analysis-cell"><small>CLASSIFICAÇÃO ATUAL</small><span>${esc(entry.classification)} · ${money(entry.quantity * entry.occurrence.unit_value)}</span></div><div class="analysis-cell"><small>RESPONSÁVEL · SINALIZAÇÃO</small><span>${esc(entry.occurrence.responsible)} · ${entry.occurrence.signaled ? 'Sinalizado' : 'Não sinalizado'}</span></div><div>${statusPill(entry.status)}</div><button class="analysis-action" data-review="${entry.occurrence.id}" data-allocation="${entry.id}">Analisar</button></article>`).join('') : '<div class="panel empty-state">Nenhuma ocorrência corresponde a estes filtros.</div>';
}
function renderReports() {
  const entries = allAllocations();
  $('#reportIncluded').textContent = money(state.summary.discount_included); $('#reportExcluded').textContent = money(state.summary.discount_excluded);
  $('#reportPending').textContent = state.summary.pending; $('#reportCount').textContent = entries.length;
  const query = ($('#reportSearch')?.value || '').trim().toLocaleLowerCase('pt-BR'), decision = $('#reportDecision')?.value || '';
  const visible = entries.filter(entry => `${entry.occurrence.product} ${entry.occurrence.sector} ${entry.occurrence.responsible}`.toLocaleLowerCase('pt-BR').includes(query) && (!decision || entry.decision === decision) && (!state.drill || state.drill(entry)));
  $('#reportTable').innerHTML = visible.map(entry => `<tr><td><strong>#${entry.occurrence.id}</strong><small>${fmtDate(entry.occurrence.occurrence_date)}</small></td><td><strong>${esc(entry.occurrence.product)}</strong><small>${entry.quantity} un. × ${money(entry.occurrence.unit_value)}</small></td><td>${esc(entry.occurrence.sector)}<small>${esc(entry.occurrence.aisle || 'Rua não informada')}</small></td><td>${esc(entry.classification)}<small>Inicial: ${esc(entry.occurrence.initial_classification)}</small></td><td>${esc(entry.occurrence.reason)}</td><td><span class="decision-pill decision-${entry.decision}">${decisionNames[entry.decision]}</span><small>${statusNames[entry.status]}</small></td><td>${entry.recovery_confirmed ? money(entry.recovery_value) : 'Não confirmada'}</td><td>${esc(entry.occurrence.responsible)}</td></tr>`).join('');
  $('#reportEmpty').hidden = visible.length > 0;
  const history = state.audit.slice(0, 40);
  $('#auditList').innerHTML = history.length ? history.map(item => `<div class="audit-entry"><time>${esc(item.created_at)}</time><div><strong>${esc(item.action)}${item.occurrence_number ? ` · #${item.occurrence_number} ${esc(item.product || '')}` : ''}</strong><p>${esc(formatDetails(item.details))}</p></div><span>${esc(item.user_name || 'Sistema')}</span></div>`).join('') : '<div class="empty-state">As alterações realizadas aparecerão aqui.</div>';
}
function formatDetails(value) {
  try { const data = JSON.parse(value); return Object.entries(data).map(([key, val]) => `${key.replaceAll('_', ' ')}: ${val && typeof val === 'object' && 'before' in val ? `${val.before} → ${val.after}` : typeof val === 'object' ? JSON.stringify(val) : val}`).join(' · '); }
  catch { return value || ''; }
}
function renderSettings() {
  const form = $('#settingsForm'); if (!state.settings) return;
  form.elements.categories.value = state.settings.categories.join('\n'); form.elements.monthly_limit.value = state.settings.monthly_limit;
  form.elements.daily_limit.value = state.settings.daily_limit; form.elements.discount_rule.value = state.settings.discount_rule;
  $$('[name=approval_roles]', form).forEach(input => input.checked = state.settings.approval_roles.includes(input.value));
  if ($('#settingsForm').elements.gross_categories) $('#settingsForm').elements.gross_categories.value = state.settings.gross_categories.join('\n');
  renderGrossCategories();
}
async function renderUsers() {
  try {
    const { users } = await api('/api/users');
    $('#usersTable').innerHTML = users.map(user => `<tr><td><strong>${esc(user.display_name)}</strong></td><td>${esc(user.username)}</td><td><select data-user-role="${user.id}"><option value="analyst" ${user.role === 'analyst' ? 'selected' : ''}>Analista</option><option value="approver" ${user.role === 'approver' ? 'selected' : ''}>Aprovador</option><option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Administrador</option></select></td><td><span class="status-pill ${user.active ? 'status-approved' : 'status-pending'}">${user.active ? 'Ativo' : 'Inativo'}</span></td><td><button class="table-link" data-user-active="${user.id}" data-active="${user.active ? '0' : '1'}">${user.active ? 'Desativar' : 'Ativar'}</button></td></tr>`).join('');
  } catch (error) { showToast(error.message); }
}
function renderGrossCategories() {
  const fieldset = $('#grossCategoriesFields'); if (!fieldset || !state.settings) return;
  const categories = $('#settingsForm').elements.categories.value.split('\n').map(item => item.trim()).filter(Boolean);
  fieldset.innerHTML = categories.map(category => `<label class="check-label"><input type="checkbox" name="gross_categories" value="${esc(category)}" ${state.settings.gross_categories.includes(category) ? 'checked' : ''}> ${esc(category)}</label>`).join('');
}
function openReview(occurrenceId, allocationId) {
  const occurrence = state.occurrences.find(item => item.id === Number(occurrenceId));
  const allocation = occurrence?.allocations.find(item => item.id === Number(allocationId)); if (!occurrence || !allocation) return;
  state.review = { occurrence, allocation }; const form = $('#reviewForm');
  $('#reviewId').textContent = `· #${occurrence.id}`; $('#reviewProduct').textContent = occurrence.product;
  $('#originalDetails').innerHTML = detailLines([
    ['Classificação inicial', occurrence.initial_classification], ['Quantidade', `${occurrence.quantity} un.`], ['Valor unitário', money(occurrence.unit_value)],
    ['Data', fmtDate(occurrence.occurrence_date)], ['Setor / rua', `${occurrence.sector} · ${occurrence.aisle || '—'}`], ['Motivo', occurrence.reason],
    ['Sinalização', occurrence.signaled ? 'Sinalizado' : 'Não sinalizado'], ['Fornecedor', occurrence.supplier || '—'], ['Responsável', occurrence.responsible]
  ]);
  $('#currentDetails').innerHTML = detailLines([['Classificação', allocation.classification], ['Quantidade alocada', `${allocation.quantity} un.`], ['Valor bruto', money(allocation.quantity * occurrence.unit_value)], ['Decisão', decisionNames[allocation.decision]], ['Situação', statusNames[allocation.status]], ['Recuperação', allocation.recovery_confirmed ? money(allocation.recovery_value) : 'Não confirmada'], ['Justificativa atual', allocation.rationale || '—'], ['Evidência', allocation.evidence || '—']]);
  form.elements.classification.value = allocation.classification; form.elements.decision.value = allocation.decision; form.elements.status.value = allocation.status;
  form.elements.recovery_value.value = allocation.recovery_value; form.elements.recovery_confirmed.checked = Boolean(allocation.recovery_confirmed);
  form.elements.collected_value.value = allocation.collected_value; form.elements.collected_confirmed.checked = Boolean(allocation.collected_confirmed);
  form.elements.rationale.value = allocation.rationale || ''; form.elements.evidence.value = allocation.evidence || '';
  $('#reviewHistory').innerHTML = occurrence.history.length ? occurrence.history.map(item => `<div class="audit-entry"><time>${esc(item.created_at)}</time><div><strong>${esc(item.action)}</strong><p>${esc(formatDetails(item.details))}</p></div><span>${esc(item.user_name || 'Sistema')}</span></div>`).join('') : '<p class="empty-state">Sem alterações anteriores.</p>';
  $('#splitBtn').disabled = allocation.status === 'approved' && !['admin', 'approver'].includes(state.user.role);
  $('#reviewDialog').showModal();
}
function openEditOccurrence(occurrenceId) {
  const occurrence = state.occurrences.find(item => item.id === Number(occurrenceId)); if (!occurrence || occurrence.record_status === 'cancelled') return;
  const approved = occurrence.allocations.some(item => item.status === 'approved');
  const approvalRoles = state.settings.approval_roles;
  if (approved && !approvalRoles.includes(state.user.role)) return showToast('Registro aprovado exige perfil autorizador');
  state.edit = occurrence; const form = $('#editForm'); form.reset();
  $('#editId').textContent = `#${occurrence.id}`; $('#editProductTitle').textContent = occurrence.product;
  for (const field of ['product_code', 'product', 'quantity', 'unit_value', 'sector', 'aisle', 'occurrence_date', 'occurred_time', 'reason', 'supplier', 'responsible', 'notes']) form.elements[field].value = occurrence[field] ?? '';
  form.elements.signaled.checked = Boolean(occurrence.signaled); form.elements.authorized_adjustment.checked = false;
  const options = state.settings.categories.map(category => `<option value="${esc(category)}">${esc(category)}</option>`).join('');
  form.elements.classification.innerHTML = options; form.elements.classification.value = occurrence.allocations[0]?.classification || occurrence.initial_classification;
  const proofField = form.querySelector('[name=evidence]');
  if (!proofField) form.querySelector('.edit-fields').insertAdjacentHTML('beforeend', `<label class="span-two">Evidência da reclassificação<input name="evidence" placeholder="Obrigatória ao alterar classificação"></label>`);
  $('#editHistory').innerHTML = occurrence.history.length ? occurrence.history.map(item => `<div class="audit-entry"><time>${esc(item.created_at)}</time><div><strong>${esc(item.action)}</strong><p>${esc(formatDetails(item.details))}</p></div><span>${esc(item.user_name || 'Sistema')}</span></div>`).join('') : '<p class="empty-state">Sem alterações anteriores.</p>';
  $('#editAttachments').innerHTML = occurrence.attachments.map(item => `<a href="/api/attachments/${item.id}" target="_blank" rel="noreferrer">${esc(item.filename)}</a>`).join(' · ') || '<small>Nenhuma fotografia anexada</small>';
  $('#cancelOccurrenceBtn').hidden = !['admin', 'approver'].includes(state.user.role);
  form.elements.authorized_adjustment.closest('label').hidden = !approved;
  updateEditTotal(); $('#editDialog').showModal();
}
function updateEditTotal() {
  const form = $('#editForm'); $('#editTotal').textContent = money(Number(form.elements.quantity.value || 0) * Number(form.elements.unit_value.value || 0));
}
$('#editForm').elements.quantity.addEventListener('input', updateEditTotal); $('#editForm').elements.unit_value.addEventListener('input', updateEditTotal);
$('#editForm').addEventListener('submit', async event => {
  event.preventDefault(); if (!state.edit) return;
  const form = event.currentTarget; const data = Object.fromEntries(new FormData(form)); const files = form.elements.photos.files;
  delete data.photos; data.signaled = form.elements.signaled.checked; data.authorized_adjustment = form.elements.authorized_adjustment.checked;
  data.allocation_id = state.edit.allocations[0]?.id;
  try {
    await api(`/api/occurrences/${state.edit.id}`, { method: 'PATCH', body: JSON.stringify(data) });
    $('#editDialog').close(); await refresh();
    try { await uploadPhotos(state.edit.id, files); showToast('Lançamento atualizado com histórico preservado'); }
    catch (photoError) { showToast(`Alterações salvas; foto não anexada: ${photoError.message}`); }
  } catch (error) { showToast(error.message); }
});
$('#cancelOccurrenceBtn').addEventListener('click', () => { if (!state.edit) return; $('#cancelForm').reset(); $('#editDialog').close(); $('#cancelDialog').showModal(); });
$('#cancelForm').addEventListener('submit', async event => {
  event.preventDefault(); if (!state.edit) return;
  const form = event.currentTarget;
  try { await api(`/api/occurrences/${state.edit.id}/cancel`, { method: 'POST', body: JSON.stringify({ reason: form.elements.reason.value, authorized_adjustment: form.elements.authorized_adjustment.checked }) });
    $('#cancelDialog').close(); await refresh(); showToast('Lançamento cancelado e mantido no histórico'); }
  catch (error) { showToast(error.message); }
});
function detailLines(items) { return `<div class="detail-lines">${items.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('')}</div>`; }
function showPage(name) {
  if ((name === 'settings' || name === 'users') && state.user.role !== 'admin') return;
  const originalName = name;
  if (name === 'repack' || name === 'supplier') { state.quickClass = name === 'repack' ? 'Reembalagem' : 'Fornecedor'; name = 'records'; renderRecords(); }
  else state.quickClass = '';
  const pageName = name === 'comparison' ? 'sectors' : name;
  state.page = pageName; $$('.page').forEach(page => page.classList.toggle('active', page.id === `page-${pageName}`));
  $$('.nav-link[data-page]').forEach(button => button.classList.toggle('active', button.dataset.page === originalName));
  const titles = { dashboard: 'Visão geral', records: 'Lançamentos', analysis: 'Central de análise', reports: 'Relatórios', settings: 'Metas e configurações', users: 'Usuários e permissões', sectors: 'Gestão por Setores', comparison: 'Comparativo de setores', repack: 'Reembalagens', supplier: 'Fornecedores', 'role-home': 'Meu painel' };
  $('#pageTitle').textContent = titles[originalName];
  if (pageName === 'sectors') window.renderSectorsModule?.(originalName === 'comparison' ? 'comparison' : 'sectors');
  if (pageName === 'settings') renderSettings(); if (pageName === 'records') renderRecords();
  if (pageName === 'users') renderUsers(); if (pageName === 'role-home') renderRoleHome();
}
function openDrill(predicate) { state.drill = predicate; $('#reportSearch').value = ''; $('#reportDecision').value = ''; showPage('reports'); renderReports(); }

$$('.nav-link[data-page]').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
window.openOccurrenceFromSector = async (occurrenceId, allocationId) => {
  try { const result = await api(`/api/occurrences?id=${occurrenceId}`); const occurrence = result.occurrences[0];
    if (!occurrence) return showToast('Lançamento não encontrado');
    state.occurrences = [occurrence, ...state.occurrences.filter(item => item.id !== occurrence.id)];
    showPage('analysis'); openReview(occurrenceId, allocationId);
  } catch (error) { showToast(error.message); }
};
$('#newUserBtn').addEventListener('click', () => { $('#userForm').reset(); $('#userDialog').showModal(); });
$('#userForm').addEventListener('submit', async event => {
  event.preventDefault(); const data = Object.fromEntries(new FormData(event.currentTarget));
  try { await api('/api/users', { method: 'POST', body: JSON.stringify(data) }); $('#userDialog').close(); await renderUsers(); showToast('Usuário criado'); }
  catch (error) { showToast(error.message); }
});
$('#usersTable').addEventListener('change', async event => {
  const control = event.target.closest('[data-user-role]'); if (!control) return;
  try { await api(`/api/users/${control.dataset.userRole}`, { method: 'PATCH', body: JSON.stringify({ role: control.value }) }); showToast('Perfil atualizado'); }
  catch (error) { showToast(error.message); await renderUsers(); }
});
$('#usersTable').addEventListener('click', async event => {
  const button = event.target.closest('[data-user-active]'); if (!button) return;
  try { await api(`/api/users/${button.dataset.userActive}`, { method: 'PATCH', body: JSON.stringify({ active: button.dataset.active === '1' }) }); await renderUsers(); showToast('Acesso atualizado'); }
  catch (error) { showToast(error.message); }
});
$('#newOccurrenceBtn').addEventListener('click', () => openCreate());
$$('[data-open-create]').forEach(button => button.addEventListener('click', () => openCreate()));
function openCreate() {
  const form = $('#createForm'); form.reset(); form.elements.occurrence_date.value = today;
  form.elements.occurred_time.value = new Date().toTimeString().slice(0, 5);
  form.elements.responsible.value = state.user.display_name; $('#createError').textContent = ''; setWizardStep(0); updateCreateTotal(); $('#createDialog').showModal();
}
$$('[data-close]').forEach(button => button.addEventListener('click', () => $(`#${button.dataset.close}`).close()));
$('#createForm').addEventListener('submit', async event => {
  event.preventDefault(); await submitOccurrence(false);
});
function setWizardStep(step) {
  state.wizardStep = Math.max(0, Math.min(3, step));
  $$('.wizard-step').forEach(section => section.classList.toggle('active', Number(section.dataset.wizardStep) === state.wizardStep));
  $$('.wizard-progress i').forEach((item, index) => item.classList.toggle('current', index <= state.wizardStep));
  $('#wizardStepLabel').textContent = `ETAPA ${state.wizardStep + 1} DE 4`;
  $('#wizardBack').hidden = state.wizardStep === 0; $('#wizardNext').hidden = state.wizardStep === 3; $('#createSubmit').hidden = state.wizardStep !== 3;
  if (state.wizardStep === 3) updateCreateSummary();
}
function updateCreateTotal() {
  const form = $('#createForm'); $('#createTotal').textContent = money(Number(form.elements.quantity.value || 0) * Number(form.elements.unit_value.value || 0));
}
function updateCreateSummary() {
  const form = $('#createForm'); const values = new FormData(form);
  $('#createReviewSummary').innerHTML = detailLines([['Produto', values.get('product') || '—'], ['Código', values.get('product_code') || '—'], ['Quantidade', values.get('quantity') || '—'], ['Valor total', $('#createTotal').textContent], ['Setor / rua', `${values.get('sector') || '—'} / ${values.get('aisle') || '—'}`], ['Classificação', values.get('classification') || '—']]);
}
$('#wizardNext').addEventListener('click', () => {
  const section = $(`[data-wizard-step="${state.wizardStep}"]`);
  const invalid = [...section.querySelectorAll('input[required],select[required]')].find(input => !input.reportValidity());
  if (!invalid) setWizardStep(state.wizardStep + 1);
});
$('#wizardBack').addEventListener('click', () => setWizardStep(state.wizardStep - 1));
$('#createForm').elements.quantity.addEventListener('input', updateCreateTotal);
$('#createForm').elements.unit_value.addEventListener('input', updateCreateTotal);
async function submitOccurrence(isDraft) {
  const form = $('#createForm'); const data = Object.fromEntries(new FormData(form));
  const photos = form.elements.photos.files;
  delete data.photos; data.signaled = form.elements.signaled.checked; data.record_status = isDraft ? 'draft' : 'active';
  try {
    const result = await api('/api/occurrences', { method: 'POST', body: JSON.stringify(data) });
    $('#createDialog').close(); await refresh(); showPage(isDraft ? 'records' : 'analysis');
    try { await uploadPhotos(result.id, photos); showToast(isDraft ? 'Rascunho salvo' : 'Ocorrência enviada para análise'); }
    catch (photoError) { showToast(`Lançamento salvo; foto não anexada: ${photoError.message}`); }
  }
  catch (error) { $('#createError').textContent = error.message; }
}
$('#saveDraftBtn').addEventListener('click', () => submitOccurrence(true));
async function uploadPhotos(occurrenceId, files) {
  for (const file of [...files]) {
    if (file.size > 5 * 1024 * 1024) throw new Error(`${file.name}: cada foto deve ter até 5 MB`);
    const bytes = new Uint8Array(await file.arrayBuffer()); let binary = ''; bytes.forEach(byte => { binary += String.fromCharCode(byte); });
    await api(`/api/occurrences/${occurrenceId}/attachments`, { method: 'POST', body: JSON.stringify({ filename: file.name, content_type: file.type, data: btoa(binary) }) });
  }
}
let productTimer;
$('#createForm').elements.product_code.addEventListener('input', () => {
  clearTimeout(productTimer); productTimer = setTimeout(async () => {
    const code = $('#createForm').elements.product_code.value.trim(); if (!code) { $('#productSuggestions').hidden = true; return; }
    try { const result = await api(`/api/products?search=${encodeURIComponent(code)}`); const exact = result.products.find(product => product.code === code);
      if (exact) { $('#createForm').elements.product.value = exact.description; $('#createForm').elements.unit_value.value = exact.unit_value; updateCreateTotal(); }
      $('#productSuggestions').innerHTML = result.products.map(product => `<button type="button" data-product-code="${esc(product.code)}" data-product-description="${esc(product.description)}" data-product-value="${product.unit_value}">${esc(product.code)} · ${esc(product.description)}</button>`).join('');
      $('#productSuggestions').hidden = result.products.length === 0;
    } catch { $('#productSuggestions').hidden = true; }
  }, 180);
});
$('#reviewForm').addEventListener('submit', async event => {
  event.preventDefault(); if (!state.review) return;
  const form = event.currentTarget; const data = Object.fromEntries(new FormData(form)); data.recovery_confirmed = form.elements.recovery_confirmed.checked; data.collected_confirmed = form.elements.collected_confirmed.checked;
  try { await api(`/api/allocations/${state.review.allocation.id}`, { method: 'PATCH', body: JSON.stringify(data) }); $('#reviewDialog').close(); await refresh(); showToast('Revisão registrada no histórico'); }
  catch (error) { showToast(error.message); }
});
$('#splitBtn').addEventListener('click', () => {
  const { occurrence, allocation } = state.review; const form = $('#splitForm'); form.reset(); form.elements.quantity.max = Math.max(.01, allocation.quantity - .01);
  $('#splitBalance').textContent = `Saldo deste destino: ${allocation.quantity} unidades · ${occurrence.product}`; $('#splitError').textContent = '';
  form.elements.classification.value = state.settings.categories[0]; $('#splitDialog').showModal();
});
$('#splitForm').addEventListener('submit', async event => {
  event.preventDefault(); const { occurrence, allocation } = state.review; const form = event.currentTarget;
  try { await api(`/api/occurrences/${occurrence.id}/split`, { method: 'POST', body: JSON.stringify({ allocation_id: allocation.id, quantity: form.elements.quantity.value, classification: form.elements.classification.value }) });
    $('#splitDialog').close(); $('#reviewDialog').close(); await refresh(); showToast('Quantidade dividida em um novo destino pendente'); }
  catch (error) { $('#splitError').textContent = error.message; }
});
$('#settingsForm').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget;
  const data = { categories: form.elements.categories.value.split('\n').map(item => item.trim()).filter(Boolean), monthly_limit: form.elements.monthly_limit.value,
    daily_limit: form.elements.daily_limit.value, discount_rule: form.elements.discount_rule.value,
    approval_roles: $$('[name=approval_roles]:checked', form).map(input => input.value),
    gross_categories: $$('[name=gross_categories]:checked', form).map(input => input.value) };
  try { await api('/api/settings', { method: 'PATCH', body: JSON.stringify(data) }); $('#settingsMessage').textContent = 'Parâmetros salvos.'; await refresh(); showToast('Parâmetros atualizados'); }
  catch (error) { $('#settingsMessage').textContent = error.message; }
});
$('#settingsForm').elements.categories.addEventListener('input', renderGrossCategories);

let filterTimer;
['filterFrom', 'filterTo', 'filterSector', 'filterAisle', 'filterCategory', 'filterReason', 'filterSupplier'].forEach(id => $(`#${id}`).addEventListener('change', () => { clearTimeout(filterTimer); filterTimer = setTimeout(refresh, 120); }));
$('#clearFilters').addEventListener('click', () => { ['filterFrom', 'filterTo', 'filterSector', 'filterAisle', 'filterCategory', 'filterReason', 'filterSupplier'].forEach(id => $(`#${id}`).value = ''); refresh(); });
$$('[data-period]').forEach(button => button.addEventListener('click', () => {
  $$('.view-switch button').forEach(item => item.classList.toggle('selected', item === button));
  const period = button.dataset.period; $('#filterTo').value = period === 'all' ? '' : today;
  $('#filterFrom').value = period === 'month' ? `${today.slice(0, 7)}-01` : period === 'day' ? today : ''; refresh();
}));
$('#recordSearch').addEventListener('input', renderRecords); $('#recordStatus').addEventListener('change', renderRecords);
['analysisSearch', 'analysisStatus', 'analysisCategory', 'analysisFrom', 'analysisTo'].forEach(id => $(`#${id}`).addEventListener('input', renderAnalysis));
$('#reportSearch').addEventListener('input', renderReports); $('#reportDecision').addEventListener('change', renderReports);
document.addEventListener('click', event => {
  const productSuggestion = event.target.closest('[data-product-code]');
  if (productSuggestion) {
    const form = $('#createForm'); form.elements.product_code.value = productSuggestion.dataset.productCode;
    form.elements.product.value = productSuggestion.dataset.productDescription; form.elements.unit_value.value = productSuggestion.dataset.productValue;
    $('#productSuggestions').hidden = true; updateCreateTotal();
  }
  const edit = event.target.closest('[data-edit-occurrence]'); if (edit) openEditOccurrence(edit.dataset.editOccurrence);
  const review = event.target.closest('[data-review]'); if (review) openReview(review.dataset.review, review.dataset.allocation);
  const sector = event.target.closest('[data-drill-sector]'); if (sector) openDrill(entry => entry.occurrence.sector === sector.dataset.drillSector);
  const reason = event.target.closest('[data-drill-reason]'); if (reason) openDrill(entry => entry.occurrence.reason === reason.dataset.drillReason);
  const aisle = event.target.closest('[data-drill-aisle]'); if (aisle) openDrill(entry => (entry.occurrence.aisle || 'Sem rua') === aisle.dataset.drillAisle);
  const product = event.target.closest('[data-drill-product]'); if (product) openDrill(entry => entry.occurrence.product === product.dataset.drillProduct);
  const signal = event.target.closest('[data-drill-signal]'); if (signal) openDrill(entry => Number(entry.occurrence.signaled) === Number(signal.dataset.drillSignal));
  const day = event.target.closest('[data-drill-date]'); if (day) openDrill(entry => entry.occurrence.occurrence_date === day.dataset.drillDate);
  const drill = event.target.closest('[data-drill]'); if (drill) {
    const key = drill.dataset.drill;
    if (key === 'pending') openDrill(entry => entry.status !== 'approved');
    else if (key === 'include' || key === 'exclude') openDrill(entry => entry.decision === key && entry.status === 'approved');
    else if (key === 'gross' || key === 'net' || key === 'daily') openDrill(() => true);
    else if (key === 'recovered') openDrill(entry => entry.recovery_confirmed);
    else if (key === 'collected') openDrill(entry => entry.collected_confirmed);
    else if (key === 'repack') openDrill(entry => entry.classification.toLocaleLowerCase('pt-BR') === 'reembalagem' && entry.recovery_confirmed);
    else if (key === 'sectors' || key === 'reasons' || key === 'aisles' || key === 'products' || key === 'signal') openDrill(() => true);
  }
  if (event.target.closest('[data-export]')) {
    const query = paramsFromDashboard().toString(); window.location.href = `/api/export.csv${query ? `?${query}` : ''}`;
  }
});

$('#filterFrom').value = `${today.slice(0, 7)}-01`; $('#filterTo').value = today;
boot();
