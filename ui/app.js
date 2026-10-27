'use strict';

(() => {
  const byId = id => document.getElementById(id);
  const STORAGE_KEY = 'jijiao-workbench-v1';
  const NUMBER_PATTERN = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
  const ACTIVE_STATUSES = new Set(['queued', 'running']);
  const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
  const MAIN_GROUPS = [
    { title: '屋面基准与分椽', keys: ['D', 'e', 'p', 't', 'B', 'L_n', 'U_n'], open: true },
    { title: '戗木与接背控制', keys: ['a', 'k_c', 'old_tail_scale', 'old_ridge_h', 'young_root_scale', 'young_tip_scale', 'setback', 'back_join_fraction'], open: true },
    { title: '里口与飞椽', keys: ['w_fly', 'likou_width', 'likou_h_regular', 'likou_h_corner_ratio', 'likou_extension_pitches', 'fly_tip_t', 'fly_corner_root_ratio'], open: true },
    { title: '望板厚度', keys: ['board_lower_t', 'board_upper_t'], open: true }
  ];
  const ADVANCED_GROUPS = [
    { title: '木料截面与贴合', keys: ['purlin_radius_mm', 'qiangshan_width_mm', 'qiangshan_clearance_mm', 'fan_rafter_width_mm', 'fan_rafter_height_mm', 'rafter_seat_relief_mm', 'straight_rafter_width_mm', 'straight_rafter_height_mm', 'old_seat_relief_mm', 'board_surface_overlap_mm', 'likou_surface_overlap_mm', 'upper_board_surface_overlap_mm'] },
    { title: '采样、求交与飞椽过渡', keys: ['fan_station_count', 'flyer_width_fiber_stations', 'flyer_edge_inboard_bias_mm', 'flyer_tail_search_step_mm', 'flyer_front_edge_search_span_mm', 'flyer_front_edge_coarse_step_mm', 'flyer_tail_root_max_search_mm', 'flyer_edge_fit_clearance_mm', 'flyer_root_fit_clearance_mm', 'flyer_outer_tip_offset_mm', 'flyer_tip_transition_power', 'flyer_contact_tail_gap_mm', 'flyer_seat_delta_mm'] },
    { title: '组合木控制', keys: ['combination_back_u_ratio_D', 'combination_front_u_ratio_D', 'combination_sagitta_ratio_chord', 'combination_width_ratio_young_tip', 'combination_cross_width_stations', 'combination_u_stations', 'combination_end_trim_mm'] },
    { title: '几何采样与输出网格', keys: ['mesh_tessellation_mm', 'mesh_angular_tolerance_rad'] }
  ];
  const LABELS = {
    case_id: '案例标识',
    corner_angle_deg: '建筑平面内角 α',
    D: '界深 D', e: '正身檐出 e', p: '正身飞椽水平外挑 p', t: '正身提栈系数 t', B: '分椽中心档控制值 B',
    L_n: '嫩戗控制弦长 Lₙ', U_n: '嫩戗端点高差 Uₙ', a: '老戗主体高度尺度 a', w_fly: '立脚飞椽宽度 w_fly',
    k_c: '放叉系数 k_c', old_tail_scale: '老戗梢部收分比例', old_ridge_h: '老戗车背加高', young_root_scale: '嫩戗根部主体比例',
    young_tip_scale: '嫩戗头部主体比例', setback: '嫩戗根部坐势退进', likou_width: '里口木平面宽度', likou_h_regular: '正身里口木高度',
    likou_h_corner_ratio: '角部里口高度倍率', likou_extension_pitches: '里口正身延长档数', board_lower_t: '下层木望板厚度',
    board_upper_t: '上层卷戗板厚度', fly_tip_t: '正身飞椽梢厚控制值', fly_corner_root_ratio: '近嫩戗飞椽根厚比例',
    back_join_fraction: '组合背弧接背点分位', purlin_radius_mm: '桁圆截面半径', qiangshan_width_mm: '戗山木宽度',
    qiangshan_clearance_mm: '戗山木定位留量', fan_rafter_width_mm: '摔网椽宽度', fan_rafter_height_mm: '摔网椽高度',
    rafter_seat_relief_mm: '扇椽承口让位深度', straight_rafter_width_mm: '正身檐椽宽度', straight_rafter_height_mm: '正身檐椽高度',
    old_seat_relief_mm: '老戗承口让位深度', board_surface_overlap_mm: '下层板实体贴合量', likou_surface_overlap_mm: '里口实体贴合量',
    fan_station_count: '摔网板面采样站点数', flyer_width_fiber_stations: '立脚飞椽宽向采样数', flyer_edge_inboard_bias_mm: '飞椽边缘向内偏置',
    flyer_tail_search_step_mm: '飞椽尾端搜索步长', flyer_front_edge_search_span_mm: '飞椽前缘搜索范围',
    flyer_front_edge_coarse_step_mm: '飞椽前缘粗搜索步长', flyer_tail_root_max_search_mm: '飞椽尾端最大搜索距离',
    flyer_edge_fit_clearance_mm: '飞椽边缘求交净空', flyer_root_fit_clearance_mm: '飞椽根部求交净空',
    flyer_outer_tip_offset_mm: '正身飞椽外梢高程偏移', flyer_tip_transition_power: '飞椽梢部过渡幂指数',
    flyer_contact_tail_gap_mm: '飞椽尾端触底间隙', flyer_seat_delta_mm: '立脚飞椽承口高度偏移',
    upper_board_surface_overlap_mm: '上层板底竖向间隙', combination_back_u_ratio_D: '组合木后端弦向比例',
    combination_front_u_ratio_D: '组合木前端弦向比例', combination_sagitta_ratio_chord: '组合木弓高与弦长比例',
    combination_width_ratio_young_tip: '组合木宽度与嫩戗头宽比例', combination_cross_width_stations: '组合木横向采样站点数',
    combination_u_stations: '组合木弦向采样站点数', combination_end_trim_mm: '组合木端部退刀量',
    mesh_tessellation_mm: '网格线性离散容差', mesh_angular_tolerance_rad: '网格角度容差'
  };
  const HELP_OVERRIDES = {
    k_c: '放叉量 c = k_c × p。它不是整根老戗外端距离，且与排料曲线 c 无关。',
    w_fly: '飞椽宽度；在排料中对应排料板厚。',
    old_tail_scale: '老戗梢部主体收分比例；主体高度和宽度按相同比例收分。',
    old_ridge_h: '老戗车背加高；与老戗主体高度分别计算。',
    young_root_scale: '嫩戗根部主体相对老戗头的比例，用于确定嫩戗根部截面。',
    young_tip_scale: '嫩戗头部相对根部主体的比例，用于确定嫩戗头部截面。',
    fly_corner_root_ratio: '控制近嫩戗飞椽根厚，相对于所采用嫩戗截面的尺度；不是扇椽根端宽度与飞椽宽度之比。',
    fly_tip_t: '正身飞椽梢厚控制值；量取时需与纵横方向的换算区分。',
    upper_board_surface_overlap_mm: '板底相对椽顶向上的竖向间隙，默认 0.2 mm；沿用历史字段名，不代表体积重叠。'
  };
  const state = {
    schema: null,
    defaults: null,
    examples: [],
    cases: [],
    draft: null,
    currentCase: null,
    activeTab: 'model',
    job: null,
    jobConfig: null,
    jobReused: false,
    jobFinalizingId: null,
    pollTimer: null,
    pollInFlight: false,
    validationInFlight: false,
    generating: false,
    touched: new Set(),
    validationAttempted: false,
    localErrors: [],
    serverValidation: null,
    serverValidationCurrent: false,
    initialised: false,
    toastTimer: null,
    searchTerm: ''
  };

  function asPlainObject(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function escapeLabel(value) {
    return String(value ?? '');
  }

  function formatNumber(value, digits = 2) {
    const number = typeof value === 'number' ? value : Number(value);
    if (!Number.isFinite(number)) return '—';
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, useGrouping: true }).format(number);
  }

  function formatDuration(seconds) {
    if (seconds == null || seconds === '' || !Number.isFinite(Number(seconds)) || Number(seconds) < 0) return '—';
    const total = Math.floor(Number(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    return hours > 0
      ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
      : `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }

  function setText(node, value) {
    if (node) node.textContent = value == null ? '' : String(value);
  }

  function showToast(message, isError = false, timeout = 4200) {
    const toast = byId('toast');
    if (!toast) return;
    clearTimeout(state.toastTimer);
    toast.textContent = String(message ?? '');
    toast.classList.toggle('toast-error', Boolean(isError));
    toast.hidden = false;
    state.toastTimer = setTimeout(() => { toast.hidden = true; }, timeout);
  }

  function setAppError(message, canRetry = false) {
    const box = byId('app-error');
    box.replaceChildren();
    const text = document.createElement('span');
    text.textContent = String(message ?? '');
    box.append(text);
    if (canRetry) {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'button button-secondary';
      retry.style.marginLeft = '10px';
      retry.textContent = '重新连接';
      retry.addEventListener('click', initialise);
      box.append(retry);
    }
    box.hidden = false;
  }

  function clearAppError() {
    const box = byId('app-error');
    box.hidden = true;
    box.replaceChildren();
  }

  function readableApiError(payload, fallback) {
    let message = payload && typeof payload.error === 'string' ? payload.error : fallback;
    if (payload && Array.isArray(payload.details) && payload.details.length) {
      const detailText = payload.details.map(item => {
        if (typeof item === 'string') return item;
        if (asPlainObject(item)) return Object.entries(item).map(([key, value]) => `${key}: ${String(value)}`).join('；');
        return String(item);
      }).join('；');
      message += `（${detailText}）`;
    }
    return message;
  }

  async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    let body = options.body;
    if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') {
      headers.set('Content-Type', 'application/json; charset=utf-8');
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, headers, body });
    let payload = null;
    const responseText = await response.text();
    if (responseText) {
      try { payload = JSON.parse(responseText); }
      catch { payload = { error: responseText.slice(0, 700) }; }
    }
    if (!response.ok) {
      const error = new Error(readableApiError(payload, `请求失败（HTTP ${response.status}）`));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    if (!payload || typeof payload !== 'object') throw new Error('本地服务没有返回有效的 JSON 数据。');
    return payload;
  }

  function schemaProperties(section) {
    const properties = state.schema && state.schema.properties && state.schema.properties[section];
    return properties && asPlainObject(properties.properties) ? properties.properties : {};
  }

  function makeRawDraft(source) {
    const input = asPlainObject(source) ? source : {};
    const draft = {
      case_id: input.case_id == null ? 'custom' : String(input.case_id),
      corner_angle_deg: input.corner_angle_deg == null ? '90' : String(input.corner_angle_deg),
      parameters: {},
      advanced: {}
    };
    for (const section of ['parameters', 'advanced']) {
      const sourceSection = asPlainObject(input[section]) ? input[section] : {};
      for (const [key, property] of Object.entries(schemaProperties(section))) {
        const value = Object.hasOwn(sourceSection, key) ? sourceSection[key] : property.default;
        draft[section][key] = value == null ? '' : String(value);
      }
    }
    return draft;
  }

  function readPersistentState() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      return asPlainObject(saved) ? saved : {};
    } catch {
      return {};
    }
  }

  function persistState() {
    if (!state.draft) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        draft: state.draft,
        displayedCaseId: state.currentCase ? state.currentCase.id : null
      }));
    } catch {
      // Private browsing or quota limits should not interrupt editing.
    }
  }

  function getUnitText(unit, key) {
    if (key === 'likou_extension_pitches') return '档';
    if (!unit) return '';
    const labels = {
      deg: '°',
      rad: 'rad',
      count: '个',
      '1 (slope ratio)': '比例',
      '1 (coefficient)': '系数',
      '1 (ratio)': '比例',
      '1 (fraction)': '比例',
      '1 (exponent)': '指数'
    };
    return labels[unit] || unit;
  }

  function rangeDescription(property, key) {
    const parts = [];
    if (Number.isFinite(property.exclusiveMinimum)) parts.push(`大于 ${property.exclusiveMinimum}`);
    else if (Number.isFinite(property.minimum)) parts.push(`不小于 ${property.minimum}`);
    if (Number.isFinite(property.exclusiveMaximum)) parts.push(`小于 ${property.exclusiveMaximum}`);
    else if (Number.isFinite(property.maximum)) parts.push(`不大于 ${property.maximum}`);
    if (property['x-constraints']) parts.push(String(property['x-constraints']));
    const crossConstraints = {
      board_surface_overlap_mm: '必须小于下层木望板厚度',
      upper_board_surface_overlap_mm: '必须小于上层卷戗板厚度',
      combination_back_u_ratio_D: '须为负值，并小于组合木前端比例',
      combination_front_u_ratio_D: '须为正值，并大于组合木后端比例'
    };
    if (crossConstraints[key]) parts.push(crossConstraints[key]);
    else if (property['x-cross-constraint']) parts.push(String(property['x-cross-constraint']));
    return parts.join('；');
  }

  function updateFieldError(path, message, shouldShow) {
    const control = document.querySelector(`[data-field-path="${CSS.escape(path)}"]`);
    if (!control) return;
    const input = control.querySelector('input');
    const errorNode = control.querySelector('.field-error');
    const visible = Boolean(shouldShow && message);
    control.classList.toggle('has-error', visible);
    if (input) input.setAttribute('aria-invalid', visible ? 'true' : 'false');
    if (errorNode) {
      errorNode.textContent = visible ? String(message) : '';
      errorNode.hidden = !visible;
    }
  }

  function renderField(section, key, property) {
    const path = section ? `${section}.${key}` : key;
    const fieldSection = section || 'top';
    const helpText = HELP_OVERRIDES[key] || property.description || '本字段参与当前模型计算。';
    const control = document.createElement('div');
    control.className = 'field-control';
    control.dataset.fieldPath = path;
    control.dataset.searchText = `${LABELS[key] || key} ${key} ${helpText}`.toLocaleLowerCase('zh-CN');

    const head = document.createElement('div');
    head.className = 'field-head';
    const label = document.createElement('label');
    label.className = 'field-title';
    label.htmlFor = `input-${fieldSection}-${key}`;
    label.textContent = LABELS[key] || key;
    const unit = document.createElement('span');
    unit.className = 'field-unit';
    unit.textContent = getUnitText(property['x-unit'], key);
    head.append(label, unit);

    const entry = document.createElement('div');
    entry.className = 'field-entry-row';
    const input = document.createElement('input');
    input.id = `input-${fieldSection}-${key}`;
    input.className = 'field-input';
    input.type = 'text';
    input.inputMode = 'decimal';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.value = (section ? state.draft[section][key] : state.draft[key]) ?? '';
    input.setAttribute('aria-describedby', `help-${fieldSection}-${key} error-${fieldSection}-${key}`);
    input.dataset.section = section || '';
    input.dataset.key = key;
    input.addEventListener('input', onNumericInput);
    const defaultValue = document.createElement('span');
    defaultValue.className = 'field-default';
    defaultValue.textContent = `默认 ${property.default}`;
    entry.append(input, defaultValue);

    const help = document.createElement('p');
    help.className = 'field-help';
    help.id = `help-${fieldSection}-${key}`;
    help.textContent = helpText;
    const constraint = rangeDescription(property, key);
    if (constraint) {
      const constraintLine = document.createElement('span');
      constraintLine.className = 'field-help';
      constraintLine.textContent = `约束：${constraint}`;
      constraintLine.style.display = 'block';
      help.append(constraintLine);
    }
    const error = document.createElement('p');
    error.className = 'field-error';
    error.id = `error-${fieldSection}-${key}`;
    error.hidden = true;
    control.append(head, entry, help, error);
    return control;
  }

  function renderFieldGroups(container, section, groups, properties, options = {}) {
    container.replaceChildren();
    const keys = Object.keys(properties);
    const assigned = new Set();
    const availableGroups = groups.map(group => ({
      title: group.title,
      open: Boolean(options.openAll || group.open),
      keys: group.keys.filter(key => Object.hasOwn(properties, key))
    }));
    for (const group of availableGroups) for (const key of group.keys) assigned.add(key);
    const remainder = keys.filter(key => !assigned.has(key));
    if (remainder.length) availableGroups.push({ title: '其他有效字段', keys: remainder, open: false });

    for (const group of availableGroups) {
      if (!group.keys.length) continue;
      const details = document.createElement('details');
      details.className = 'details-block parameter-group';
      details.open = Boolean(group.open || options.openAll);
      const summary = document.createElement('summary');
      const title = document.createElement('span');
      title.textContent = group.title;
      const count = document.createElement('span');
      count.className = 'summary-count';
      count.textContent = `${group.keys.length} 项`;
      summary.append(title, count);
      const fields = document.createElement('div');
      fields.className = 'group-fields';
      for (const key of group.keys) fields.append(renderField(section, key, properties[key]));
      details.append(summary, fields);
      container.append(details);
    }
  }

  function renderIdentityFields() {
    const container = byId('identity-fields');
    container.replaceChildren();
    const property = state.schema.properties.case_id || { default: 'custom', description: '案例标识。' };
    const control = document.createElement('div');
    control.className = 'field-control';
    control.dataset.fieldPath = 'case_id';
    const head = document.createElement('div');
    head.className = 'field-head';
    const label = document.createElement('label');
    label.className = 'field-title';
    label.htmlFor = 'input-case-id';
    label.textContent = LABELS.case_id;
    const unit = document.createElement('span');
    unit.className = 'field-unit';
    unit.textContent = '文字';
    head.append(label, unit);
    const input = document.createElement('input');
    input.id = 'input-case-id';
    input.className = 'field-input';
    input.type = 'text';
    input.autocomplete = 'off';
    input.maxLength = 120;
    input.value = state.draft.case_id;
    input.setAttribute('aria-describedby', 'help-case-id error-case-id');
    input.addEventListener('input', () => {
      state.draft.case_id = input.value;
      state.touched.add('case_id');
      state.serverValidation = null;
      state.serverValidationCurrent = false;
      state.validationAttempted = false;
      updateAllState();
      persistState();
    });
    const help = document.createElement('p');
    help.className = 'field-help';
    help.id = 'help-case-id';
    help.textContent = '保存到结果记录中的名称。相同几何参数可复用已有计算。';
    const error = document.createElement('p');
    error.className = 'field-error';
    error.id = 'error-case-id';
    error.hidden = true;
    control.append(head, input, help, error);
    container.append(control);
    container.append(renderField(null, 'corner_angle_deg', state.schema.properties.corner_angle_deg));
  }

  function renderParameterFields() {
    const parameterProperties = schemaProperties('parameters');
    const advancedProperties = schemaProperties('advanced');
    renderFieldGroups(byId('primary-fields'), 'parameters', MAIN_GROUPS, parameterProperties);
    renderFieldGroups(byId('advanced-fields'), 'advanced', ADVANCED_GROUPS, advancedProperties);
    renderIdentityFields();
    setText(byId('primary-count'), `${Object.keys(parameterProperties).length} 项`);
    setText(byId('advanced-count'), `${Object.keys(advancedProperties).length} 项`);
    setText(byId('advanced-summary-count'), `${Object.keys(advancedProperties).length} 项`);
    byId('parameter-search').disabled = false;
  }

  function numericValue(raw) {
    const trimmed = String(raw ?? '').trim();
    if (!trimmed || !NUMBER_PATTERN.test(trimmed)) return { valid: false, value: null };
    const value = Number(trimmed);
    return Number.isFinite(value) ? { valid: true, value } : { valid: false, value: null };
  }

  function localValidate() {
    const errors = [];
    const add = (path, message) => errors.push({ path, message });
    if (!String(state.draft.case_id ?? '').trim()) add('case_id', '请填写案例标识。');
    const angle = numericValue(state.draft.corner_angle_deg);
    if (!angle.valid || angle.value <= 0 || angle.value >= 180) add('corner_angle_deg', '建筑平面内角须为大于 0°、小于 180° 的有限数值。');

    const numericConfig = { case_id: String(state.draft.case_id ?? '').trim(), corner_angle_deg: angle.value, parameters: {}, advanced: {} };
    for (const section of ['parameters', 'advanced']) {
      for (const [key, property] of Object.entries(schemaProperties(section))) {
        const raw = state.draft[section][key];
        const path = `${section}.${key}`;
        if (String(raw ?? '').trim() === '') {
          add(path, '此项不能为空；空输入不会按 0 处理。');
          continue;
        }
        const parsed = numericValue(raw);
        if (!parsed.valid) {
          add(path, '请输入有效且有限的数字，例如 12.5。');
          continue;
        }
        const value = parsed.value;
        if (property.type === 'integer' && !Number.isInteger(value)) {
          add(path, '此项必须为整数。');
          continue;
        }
        if (property.type === 'integer' && String(property['x-constraints'] || '').includes('奇数') && value % 2 === 0) {
          add(path, '此项必须为奇数。');
        }
        if (Number.isFinite(property.minimum) && value < property.minimum) add(path, `数值不能小于 ${property.minimum}。`);
        if (Number.isFinite(property.exclusiveMinimum) && value <= property.exclusiveMinimum) add(path, `数值必须大于 ${property.exclusiveMinimum}。`);
        if (Number.isFinite(property.maximum) && value > property.maximum) add(path, `数值不能大于 ${property.maximum}。`);
        if (Number.isFinite(property.exclusiveMaximum) && value >= property.exclusiveMaximum) add(path, `数值必须小于 ${property.exclusiveMaximum}。`);
        numericConfig[section][key] = value;
      }
    }

    const p = numericConfig.parameters;
    if (Number.isFinite(p.U_n) && Number.isFinite(p.L_n) && p.U_n >= p.L_n) add('parameters.U_n', '嫩戗端点高差 Uₙ 必须小于控制弦长 Lₙ。');
    const a = numericConfig.advanced;
    if (Number.isFinite(a.board_surface_overlap_mm) && Number.isFinite(p.board_lower_t) && a.board_surface_overlap_mm >= p.board_lower_t) add('advanced.board_surface_overlap_mm', '下层板实体贴合量必须小于下层木望板厚度。');
    if (Number.isFinite(a.upper_board_surface_overlap_mm) && Number.isFinite(p.board_upper_t) && a.upper_board_surface_overlap_mm >= p.board_upper_t) add('advanced.upper_board_surface_overlap_mm', '上层板底竖向间隙必须小于上层卷戗板厚度。');
    if (Number.isFinite(a.combination_back_u_ratio_D) && a.combination_back_u_ratio_D >= 0) add('advanced.combination_back_u_ratio_D', '组合木后端比例必须小于 0。');
    if (Number.isFinite(a.combination_front_u_ratio_D) && a.combination_front_u_ratio_D <= 0) add('advanced.combination_front_u_ratio_D', '组合木前端比例必须大于 0。');

    return { errors, config: errors.length ? null : numericConfig };
  }

  function configForCase(caseValue) {
    if (!caseValue || !asPlainObject(caseValue.config)) return null;
    return caseValue.config;
  }

  function sameCalculation(left, right) {
    if (!left || !right) return false;
    if (Number(left.corner_angle_deg) !== Number(right.corner_angle_deg)) return false;
    for (const section of ['parameters', 'advanced']) {
      for (const key of Object.keys(schemaProperties(section))) {
        const leftValue = left[section] && left[section][key];
        const rightValue = right[section] && right[section][key];
        if (leftValue == null || rightValue == null || leftValue === '' || rightValue === '') return false;
        const l = typeof leftValue === 'number' ? leftValue : Number(leftValue);
        const r = typeof rightValue === 'number' ? rightValue : Number(rightValue);
        if (!Number.isFinite(l) || !Number.isFinite(r) || l !== r) return false;
      }
    }
    return true;
  }

  function sameDraftConfig(left, right) {
    return sameCalculation(left, right) && String(left.case_id || '') === String(right.case_id || '');
  }

  function isActiveJob() {
    return Boolean(state.job && ACTIVE_STATUSES.has(state.job.status));
  }

  function updateAllState() {
    if (!state.schema || !state.draft) {
      byId('draft-indicator').className = 'state-pill state-neutral';
      byId('draft-indicator').textContent = '连接中';
      byId('validate-button').disabled = true;
      byId('generate-button').disabled = true;
      byId('export-config-button').disabled = true;
      byId('import-button').disabled = true;
      byId('case-picker').disabled = true;
      byId('load-case-button').disabled = true;
      byId('view-case-button').disabled = true;
      byId('parameter-search').disabled = true;
      return;
    }
    const result = localValidate();
    state.localErrors = result.errors;
    const errorByPath = new Map(result.errors.map(item => [item.path, item.message]));
    const revealAll = state.validationAttempted;
    for (const [path, message] of errorByPath.entries()) {
      updateFieldError(path, message, revealAll || state.touched.has(path));
    }
    document.querySelectorAll('[data-field-path]').forEach(control => {
      const path = control.dataset.fieldPath;
      if (!errorByPath.has(path)) updateFieldError(path, '', false);
    });

    const indicator = byId('draft-indicator');
    indicator.className = 'state-pill';
    if (!state.initialised) {
      indicator.classList.add('state-neutral');
      indicator.textContent = '连接中';
    } else if (result.errors.length) {
      indicator.classList.add('state-error');
      indicator.textContent = `${result.errors.length} 处需修正`;
    } else {
      indicator.classList.add('state-good');
      indicator.textContent = '草稿可用';
    }

    const validationIndicator = byId('validation-indicator');
    validationIndicator.className = 'state-pill';
    if (result.errors.length) {
      validationIndicator.classList.add('state-error');
      validationIndicator.textContent = '需要修正';
      setText(byId('validation-summary'), result.errors[0].message);
    } else if (state.serverValidation && result.config && sameDraftConfig(state.serverValidation.config, result.config)) {
      validationIndicator.classList.add('state-good');
      validationIndicator.textContent = '快速校验通过';
      setText(byId('validation-summary'), '输入参数检查通过；模型形态需在生成后查看。');
    } else {
      validationIndicator.classList.add('state-neutral');
      validationIndicator.textContent = '未校验';
      setText(byId('validation-summary'), '检查输入值和已知参数范围；模型形态需在生成后查看。');
    }

    const valid = result.errors.length === 0;
    byId('validate-button').disabled = !state.initialised || !valid || state.validationInFlight || state.generating;
    byId('generate-button').disabled = !state.initialised || !valid || isActiveJob() || state.generating || state.validationInFlight;
    byId('export-config-button').disabled = !state.initialised || !valid;
    byId('import-button').disabled = !state.initialised || state.generating;
    byId('case-picker').disabled = !state.initialised || (!state.cases.length && !state.examples.length);
    byId('cancel-button').hidden = !isActiveJob();
    byId('cancel-button').disabled = state.job && state.job.status !== 'running' && state.job.status !== 'queued';
    byId('parameter-search').disabled = !state.initialised;
    renderResultState();
  }

  function renderResultState() {
    const pill = byId('result-state');
    pill.classList.remove('is-stale', 'is-empty');
    if (!state.currentCase) {
      pill.classList.add('is-empty');
      setResultStatePill('暂无结果');
      setText(byId('result-subtitle'), '请确认本地服务已登记成功案例。');
      return;
    }
    const resultConfig = configForCase(state.currentCase);
    const local = localValidate();
    const matches = local.config && sameCalculation(local.config, resultConfig);
    const nameChanged = local.config && resultConfig && String(local.config.case_id) !== String(resultConfig.case_id || '');
    if (isActiveJob()) {
      if (!matches) pill.classList.add('is-stale');
      setResultStatePill('已登记结果 · 后台仍在生成');
      const jobName = state.jobConfig && state.jobConfig.case_id;
      const taskLabel = jobName ? `后台任务“${String(jobName)}”` : '后台任务';
      const draftState = matches ? '草稿参数与当前显示结果一致。' : '草稿参数与当前显示结果不同。';
      setText(byId('result-subtitle'), `${draftState}${taskLabel}仍按已提交参数运行；当前显示结果不代表本次生成成功。`);
      return;
    }
    if (matches) {
      setResultStatePill('当前结果与参数一致');
      setText(byId('result-subtitle'), nameChanged
        ? `几何参数与当前结果一致；草稿案例名为“${local.config.case_id}”。`
        : '草稿参数与当前显示结果一致。');
    } else {
      pill.classList.add('is-stale');
      setResultStatePill('显示上次有效结果');
      setText(byId('result-subtitle'), '草稿已有改动；当前显示结果保持不变，完整生成成功后才会切换。');
    }
  }

  function setResultStatePill(label) {
    const pill = byId('result-state');
    const dot = document.createElement('span');
    dot.className = 'result-state-dot';
    const text = document.createElement('span');
    text.textContent = String(label);
    pill.replaceChildren(dot, text);
  }

  function onNumericInput(event) {
    const input = event.currentTarget;
    const section = input.dataset.section;
    const key = input.dataset.key;
    if (section) state.draft[section][key] = input.value;
    else state.draft[key] = input.value;
    state.touched.add(section ? `${section}.${key}` : key);
    state.serverValidation = null;
    state.serverValidationCurrent = false;
    state.validationAttempted = false;
    byId('validation-result').hidden = true;
    updateAllState();
    persistState();
  }

  function setFieldRaw(path, value) {
    if (path === 'corner_angle_deg') {
      state.draft.corner_angle_deg = String(value ?? '');
      const input = byId('input-top-corner_angle_deg');
      if (input) input.value = state.draft.corner_angle_deg;
      return;
    }
    if (path === 'case_id') {
      state.draft.case_id = String(value ?? '');
      const input = byId('input-case-id');
      if (input) input.value = state.draft.case_id;
      return;
    }
    const separator = path.indexOf('.');
    const section = path.slice(0, separator);
    const key = path.slice(separator + 1);
    state.draft[section][key] = String(value ?? '');
    const input = byId(`input-${section}-${key}`);
    if (input) input.value = state.draft[section][key];
  }

  function localValidationErrorMessage(errors) {
    const first = errors[0];
    const label = first.path === 'case_id' ? LABELS.case_id : LABELS[first.path.slice(first.path.indexOf('.') + 1)] || first.path;
    return `${errors.length} 处需要修正：${label}，${first.message}`;
  }

  function updateSearch() {
    const term = state.searchTerm.trim().toLocaleLowerCase('zh-CN');
    const advancedDetails = byId('advanced-details');
    const allGroups = document.querySelectorAll('.parameter-group');
    let visibleAdvanced = 0;
    for (const control of document.querySelectorAll('.field-control[data-search-text]')) {
      const visible = !term || control.dataset.searchText.includes(term);
      control.hidden = !visible;
      if (visible && control.dataset.fieldPath.startsWith('advanced.')) visibleAdvanced += 1;
    }
    if (term) {
      advancedDetails.open = visibleAdvanced > 0;
      for (const group of allGroups) {
        const anyVisible = Boolean(group.querySelector('.field-control:not([hidden])'));
        group.hidden = !anyVisible;
        if (anyVisible) group.open = true;
      }
      byId('advanced-count').textContent = `${visibleAdvanced} 项匹配`;
    } else {
      for (const group of allGroups) group.hidden = false;
      byId('advanced-count').textContent = `${Object.keys(schemaProperties('advanced')).length} 项`;
    }
  }

  function sortAndDeduplicateCases(cases) {
    const byCaseId = new Map();
    for (const caseValue of Array.isArray(cases) ? cases : []) {
      if (!caseValue || typeof caseValue.id !== 'string') continue;
      const previous = byCaseId.get(caseValue.id);
      if (!previous) byCaseId.set(caseValue.id, caseValue);
      else {
        const currentTime = Date.parse(caseValue.created_at || '') || 0;
        const previousTime = Date.parse(previous.created_at || '') || 0;
        if (currentTime >= previousTime) byCaseId.set(caseValue.id, caseValue);
      }
    }
    return Array.from(byCaseId.values()).sort((a, b) => {
      const timeA = Date.parse(a.created_at || '') || 0;
      const timeB = Date.parse(b.created_at || '') || 0;
      return timeB - timeA || String(a.title || a.id).localeCompare(String(b.title || b.id), 'zh-CN');
    });
  }

  function caseOptionLabel(caseValue) {
    const title = String(caseValue.title || caseValue.id);
    const parts = [];
    if (Number.isFinite(caseValue.config?.corner_angle_deg)) parts.push(`${formatNumber(caseValue.config.corner_angle_deg)}°`);
    if (caseValue.metrics && caseValue.metrics.parts_count != null && Number.isFinite(Number(caseValue.metrics.parts_count))) parts.push(`${formatNumber(caseValue.metrics.parts_count, 0)} 件`);
    if (caseValue.created_at) {
      const date = new Date(caseValue.created_at);
      if (!Number.isNaN(date.getTime())) parts.push(new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(date));
    }
    return parts.length ? `${title} · ${parts.join(' · ')}` : title;
  }

  function selectedCaseSource() {
    const value = byId('case-picker').value;
    if (value.startsWith('case:')) {
      const caseId = value.slice(5);
      const item = state.cases.find(candidate => candidate.id === caseId);
      return item ? { type: 'case', value: item } : null;
    }
    if (value.startsWith('example:')) {
      const exampleId = value.slice(8);
      const item = state.examples.find(candidate => candidate.id === exampleId);
      return item ? { type: 'example', value: item } : null;
    }
    return null;
  }

  function updateCaseSelectionUi() {
    const source = selectedCaseSource();
    const hasConfig = Boolean(source && asPlainObject(source.value.config));
    const hasResult = Boolean(source && source.type === 'case' && source.value.validated && source.value.urls && hasConfig);
    byId('load-case-button').disabled = !state.initialised || !hasConfig;
    byId('view-case-button').disabled = !state.initialised || !hasResult;
    if (!source) {
      setText(byId('case-note'), '“查看此结果”会切换右侧并将该结果参数回填到草稿；“载入参数到草稿”只回填草稿。两种操作都不会启动计算。');
    } else if (source.type === 'example') {
      setText(byId('case-note'), `已选内置示例“${String(source.value.title || source.value.id)}”。载入后会成为待生成草稿；该示例没有可查看的登记结果。`);
    } else {
      const activeNote = isActiveJob() ? ' 后台生成任务仍按提交时的参数继续运行。' : '';
      setText(byId('case-note'), `已选“${String(source.value.title || source.value.id)}”。查看会切换到这份结果并回填草稿；“载入参数到草稿”只回填草稿。${activeNote}两种操作均不会启动计算。`);
    }
  }

  function renderCasePicker(selectedId = null) {
    const picker = byId('case-picker');
    picker.replaceChildren();
    if (!state.cases.length && !state.examples.length) {
      const empty = document.createElement('option');
      empty.value = '';
      empty.textContent = '暂无已生成的结果';
      picker.append(empty);
      picker.disabled = true;
      setText(byId('case-count'), '0 项');
      updateCaseSelectionUi();
      return;
    }
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '选择成功结果或内置示例…';
    picker.append(placeholder);
    if (state.cases.length) {
      const caseGroup = document.createElement('optgroup');
      caseGroup.label = '成功案例与历史结果';
      for (const caseValue of state.cases) {
        const option = document.createElement('option');
        option.value = `case:${caseValue.id}`;
        option.textContent = caseOptionLabel(caseValue);
        caseGroup.append(option);
      }
      picker.append(caseGroup);
    }
    const caseIds = new Set(state.cases.map(item => item.id));
    const unrepresentedExamples = state.examples.filter(example => example && typeof example.id === 'string' && !caseIds.has(example.id));
    if (unrepresentedExamples.length) {
      const exampleGroup = document.createElement('optgroup');
      exampleGroup.label = '内置示例参数';
      for (const example of unrepresentedExamples) {
        const option = document.createElement('option');
        option.value = `example:${example.id}`;
        option.textContent = String(example.title || example.id);
        exampleGroup.append(option);
      }
      picker.append(exampleGroup);
    }
    picker.value = selectedId && state.cases.some(item => item.id === selectedId) ? `case:${selectedId}` : '';
    picker.disabled = false;
    setText(byId('case-count'), `${state.cases.length} 个结果`);
    updateCaseSelectionUi();
  }

  function safeResultUrl(url) {
    if (typeof url !== 'string' || !url.trim()) return null;
    try {
      const parsed = new URL(url, window.location.href);
      if (parsed.origin !== window.location.origin) return null;
      return parsed.href;
    } catch {
      return null;
    }
  }

  function addEmbedStyle(frame, kind) {
    const styleText = kind === 'model'
      ? 'html,body{width:100%;height:100%;min-height:0!important;margin:0!important;padding:0!important;overflow:hidden!important}body{padding:0!important}.masthead,footer{display:none!important}.workspace{grid-template-columns:minmax(154px,188px) minmax(0,1fr)!important;min-height:0!important;margin:0!important;border:0!important}.sidebar{max-height:100vh!important;min-height:0!important;overflow:auto!important;padding:13px 10px 13px 13px!important}.stage{height:100vh!important;min-height:520px!important}.stage-caption{bottom:10px!important}'
      : 'html,body{min-height:100%;height:auto!important}body{padding:18px!important;overflow:auto!important}header{display:none!important}.cards{grid-template-columns:minmax(0,1.05fr) minmax(0,1fr)!important}.card{min-width:0;overflow:auto}.data{max-width:100%;overflow:auto}svg{max-width:100%;height:auto}@media(max-width:900px){.cards{grid-template-columns:minmax(0,1fr)!important}}';
    try {
      const doc = frame.contentDocument;
      if (!doc || !doc.head || doc.getElementById('workbench-embed-style')) return;
      const style = doc.createElement('style');
      style.id = 'workbench-embed-style';
      style.textContent = styleText;
      doc.head.append(style);
      if (kind === 'model' && frame.contentWindow) frame.contentWindow.dispatchEvent(new frame.contentWindow.Event('resize'));
    } catch {
      // A broken or unavailable result page is handled by the iframe itself.
    }
  }

  function setFrameSource(frame, url, kind) {
    const safeUrl = safeResultUrl(url);
    if (!safeUrl) {
      frame.hidden = true;
      return false;
    }
    frame.hidden = false;
    if (frame.dataset.source !== safeUrl) {
      frame.addEventListener('load', () => addEmbedStyle(frame, kind), { once: true });
      frame.dataset.source = safeUrl;
      frame.src = safeUrl;
    } else {
      addEmbedStyle(frame, kind);
    }
    return true;
  }

  function renderMetric(label, value, suffix = '') {
    const card = document.createElement('div');
    card.className = 'metric-card';
    const caption = document.createElement('span');
    caption.textContent = label;
    const number = document.createElement('strong');
    number.textContent = value == null || value === '' || !Number.isFinite(Number(value)) ? '—' : `${formatNumber(value)}${suffix}`;
    card.append(caption, number);
    return card;
  }

  function renderResultMetrics(caseValue) {
    const container = byId('result-metrics');
    container.replaceChildren();
    if (!caseValue) {
      const card = document.createElement('div');
      card.className = 'metric-card metric-placeholder';
      const label = document.createElement('span');
      label.textContent = '结果统计';
      const value = document.createElement('strong');
      value.textContent = '暂无结果';
      card.append(label, value);
      container.append(card);
      byId('result-validation').hidden = true;
      return;
    }
    const metrics = asPlainObject(caseValue.metrics) ? caseValue.metrics : {};
    container.append(
      renderMetric('立脚飞椽 · 单侧', metrics.templates_per_side, ' 根'),
      renderMetric('三维构件', metrics.parts_count, ' 件'),
      renderMetric('排料板宽', metrics.board_width_mm, ' mm'),
      renderMetric('排料板高', metrics.board_height_mm, ' mm'),
      renderMetric('板厚', metrics.board_thickness_mm, ' mm'),
      renderMetric('面积利用率', metrics.utilization_percent, '%'),
      renderMetric('c/d 平移最大误差', metrics.translation_max_error_mm, ' mm')
    );
    byId('result-validation').hidden = !caseValue.validated;
  }

  function addDownloadLink(container, urls, key, label, filename = '') {
    const url = safeResultUrl(urls && urls[key]);
    if (!url) return;
    const link = document.createElement('a');
    link.className = 'download-link';
    link.href = url;
    link.textContent = label;
    link.setAttribute('download', filename || '');
    container.append(link);
  }

  function renderDownloads(caseValue) {
    const container = byId('result-downloads');
    container.replaceChildren();
    if (!caseValue || !asPlainObject(caseValue.urls)) return;
    const urls = caseValue.urls;
    addDownloadLink(container, urls, 'obj', '下载 OBJ', `${caseValue.id || 'model'}.obj`);
    addDownloadLink(container, urls, 'svg', '排料 SVG', `${caseValue.id || 'layout'}.svg`);
    addDownloadLink(container, urls, 'points', '尺寸表 CSV', `${caseValue.id || 'points'}.csv`);
    addDownloadLink(container, urls, 'config', '结果配置 JSON', `${caseValue.id || 'config'}.json`);
    addDownloadLink(container, urls, 'validation', '生成记录 JSON', `${caseValue.id || 'validation'}.json`);
    if (!container.childElementCount) {
      const unavailable = document.createElement('span');
      unavailable.className = 'field-note';
      unavailable.textContent = '该结果没有可用下载链接。';
      container.append(unavailable);
    }
  }

  function setActiveTab(tab) {
    state.activeTab = tab === 'layout' ? 'layout' : 'model';
    const isModel = state.activeTab === 'model';
    byId('model-tab').classList.toggle('active', isModel);
    byId('model-tab').setAttribute('aria-selected', isModel ? 'true' : 'false');
    byId('layout-tab').classList.toggle('active', !isModel);
    byId('layout-tab').setAttribute('aria-selected', !isModel ? 'true' : 'false');
    byId('model-view').hidden = !isModel || !state.currentCase;
    byId('layout-view').hidden = isModel || !state.currentCase;
  }

  function displayCase(caseValue, persist = true) {
    state.currentCase = caseValue || null;
    const displayedAngle = caseValue?.config?.corner_angle_deg;
    setText(byId('displayed-angle'), Number.isFinite(displayedAngle) ? `${formatNumber(displayedAngle)}°` : '—');
    byId('viewer-empty').hidden = Boolean(caseValue);
    if (!caseValue) {
      setText(byId('result-title'), '暂无已登记的有效结果');
      setText(byId('viewer-case-label'), '尚未载入');
      byId('model-view').hidden = true;
      byId('layout-view').hidden = true;
      renderResultMetrics(null);
      renderDownloads(null);
      renderResultState();
      if (persist) persistState();
      return;
    }
    const title = String(caseValue.title || caseValue.id || '已生成结果');
    setText(byId('result-title'), title);
    setText(byId('viewer-case-label'), title);
    const urls = asPlainObject(caseValue.urls) ? caseValue.urls : {};
    const modelOk = setFrameSource(byId('model-view'), urls.model, 'model');
    const layoutOk = setFrameSource(byId('layout-view'), urls.layout, 'layout');
    byId('viewer-empty').hidden = modelOk || layoutOk;
    setActiveTab(state.activeTab);
    renderResultMetrics(caseValue);
    renderDownloads(caseValue);
    renderResultState();
    if (persist) persistState();
  }

  function updateJobCard() {
    const card = byId('job-card');
    if (!state.job) {
      card.hidden = true;
      card.classList.remove('is-running', 'job-failed', 'job-cancelled', 'job-interrupted');
      return;
    }
    card.hidden = false;
    card.classList.remove('is-running', 'job-failed', 'job-cancelled', 'job-interrupted');
    const status = state.job.status;
    const active = ACTIVE_STATUSES.has(status);
    if (active) card.classList.add('is-running');
    if (status === 'failed') card.classList.add('job-failed');
    if (status === 'cancelled') card.classList.add('job-cancelled');
    if (status === 'interrupted') card.classList.add('job-interrupted');
    const statusLabels = {
      queued: '等待执行', running: '正在完整生成', succeeded: '生成成功', failed: '生成失败', cancelled: '任务已取消', interrupted: '服务重启后任务中断'
    };
    setText(byId('job-title'), statusLabels[status] || '任务状态');
    const elapsed = Number(state.job.elapsed_seconds);
    setText(byId('job-elapsed'), formatDuration(elapsed));
    const stage = String(state.job.stage || (status === 'queued' ? '等待运行资源' : status === 'running' ? '服务正在处理；等待下一阶段日志' : statusLabels[status] || ''));
    setText(byId('job-stage'), stage);
    let message = '';
    if (state.job.error) message = String(state.job.error);
    else if (state.jobReused && status === 'succeeded') message = '命中相同参数的已生成结果，已复用现有计算。';
    else if (active) message = '精确建模仍在后台运行；下方显示的是已登记结果，不代表本次任务已经成功。刷新页面可恢复任务观察。';
    else if (status === 'failed') message = '原有有效结果已保留。请查看日志或调整参数后再提交。';
    else if (status === 'cancelled') message = '原有有效结果已保留。草稿仍可继续编辑。';
    else if (status === 'interrupted') message = '服务重启后没有自动重启 CAD；原有有效结果已保留。';
    setText(byId('job-message'), message);
    const log = typeof state.job.log_tail === 'string' ? state.job.log_tail : '';
    byId('job-log-details').hidden = !log;
    byId('job-log').textContent = log;
  }

  function renderResultStateAndJob() {
    updateJobCard();
    renderResultState();
    byId('generate-button').disabled = !state.initialised || state.localErrors.length > 0 || isActiveJob() || state.generating || state.validationInFlight;
    byId('cancel-button').hidden = !isActiveJob();
    byId('cancel-button').disabled = !isActiveJob();
  }

  function renderCasePickerAfterCaseChange() {
    renderCasePicker(state.currentCase ? state.currentCase.id : null);
  }

  function addOrReplaceCase(caseValue) {
    if (!caseValue || typeof caseValue.id !== 'string') return;
    state.cases = sortAndDeduplicateCases([...state.cases, caseValue]);
    renderCasePicker(caseValue.id);
  }

  function validationDetailText(result) {
    const chunks = [];
    if (result && result.calculation_key) chunks.push(`计算身份：${String(result.calculation_key).slice(0, 22)}…`);
    return chunks.join('　');
  }

  function renderServerValidation(result) {
    const container = byId('validation-result');
    container.replaceChildren();
    container.classList.remove('validation-failed');
    if (!result || result.valid !== true) {
      container.classList.add('validation-failed');
      const message = document.createElement('span');
      message.textContent = '快速校验未通过。请查看返回的错误说明并修正参数。';
      container.append(message);
      if (result && Array.isArray(result.details) && result.details.length) {
        const details = document.createElement('p');
        details.textContent = result.details.map(String).join('；');
        container.append(details);
      }
      container.hidden = false;
      return;
    }
    const summary = document.createElement('div');
    summary.textContent = validationDetailText(result) || '参数与快速派生检查通过。';
    container.append(summary);
    if (result.derived && asPlainObject(result.derived)) {
      const details = document.createElement('details');
      const summaryNode = document.createElement('summary');
      summaryNode.textContent = '查看快速派生数据';
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(result.derived, null, 2);
      details.append(summaryNode, pre);
      container.append(details);
    }
    if (Array.isArray(result.warnings) && result.warnings.length) {
      const warnings = document.createElement('p');
      warnings.textContent = `提醒：${result.warnings.map(String).join('；')}`;
      container.append(warnings);
    }
    container.hidden = false;
  }

  async function requestServerValidation(config) {
    return apiRequest('/api/validate', { method: 'POST', body: { config } });
  }

  async function validateDraft() {
    state.validationAttempted = true;
    const local = localValidate();
    state.localErrors = local.errors;
    updateAllState();
    if (local.errors.length) {
      showToast(localValidationErrorMessage(local.errors), true);
      return null;
    }
    state.validationInFlight = true;
    state.serverValidation = null;
    state.serverValidationCurrent = false;
    byId('validation-result').hidden = true;
    updateAllState();
    const button = byId('validate-button');
    button.textContent = '正在校验…';
    byId('validation-indicator').textContent = '校验中';
    byId('validation-indicator').className = 'state-pill state-neutral';
    try {
      const result = await requestServerValidation(local.config);
      if (result.valid !== true) {
        state.serverValidation = null;
        renderServerValidation(result);
        showToast('快速参数校验未通过。', true);
        return null;
      }
      const currentConfig = localValidate().config;
      if (!sameDraftConfig(local.config, currentConfig)) {
        state.serverValidation = null;
        state.serverValidationCurrent = false;
        byId('validation-result').hidden = true;
        showToast('校验期间草稿已变化；请重新校验当前参数。', true, 6000);
        return null;
      }
      state.serverValidation = { config: local.config, result };
      state.serverValidationCurrent = true;
      renderServerValidation(result);
      showToast('快速参数校验通过；这不代表完整实体已经生成。');
      return { config: result.normalized_config || local.config, result };
    } catch (error) {
      state.serverValidation = null;
      state.serverValidationCurrent = false;
      renderServerValidation({ valid: false, details: [error.message] });
      showToast(error.message, true, 6500);
      return null;
    } finally {
      state.validationInFlight = false;
      button.textContent = '校验参数';
      updateAllState();
    }
  }

  async function startGeneration() {
    if (isActiveJob() || state.generating) return;
    state.validationAttempted = true;
    const local = localValidate();
    state.localErrors = local.errors;
    updateAllState();
    if (local.errors.length) {
      showToast(localValidationErrorMessage(local.errors), true, 6500);
      return;
    }

    state.generating = true;
    updateAllState();
    const button = byId('generate-button');
    button.textContent = '校验并提交…';
    try {
      const validation = await requestServerValidation(local.config);
      if (validation.valid !== true) {
        state.serverValidation = null;
        renderServerValidation(validation);
        showToast('参数校验未通过，任务没有启动。', true, 6500);
        return;
      }
      const config = validation.normalized_config || local.config;
      state.jobConfig = clone(config);
      const draftAtResponse = localValidate().config;
      state.serverValidation = sameDraftConfig(config, draftAtResponse) ? { config, result: validation } : null;
      state.serverValidationCurrent = Boolean(state.serverValidation);
      renderServerValidation(validation);
      if (!state.serverValidation) {
        const snapshotNote = document.createElement('p');
        snapshotNote.textContent = '提交任务所用的参数已通过快速校验；之后改动的草稿尚未校验。';
        byId('validation-result').append(snapshotNote);
      }
      button.textContent = '提交完整计算…';
      const response = await apiRequest('/api/jobs', { method: 'POST', body: { config } });
      state.job = response.job || null;
      state.jobReused = Boolean(response.reused);
      state.jobFinalizingId = null;
      if (!state.job || !state.job.id) throw new Error('服务没有返回可观察的任务编号。');
      updateJobCard();
      updateAllState();
      if (TERMINAL_STATUSES.has(state.job.status)) {
        await handleTerminalJob(state.job);
      } else {
        schedulePoll(0);
        showToast(response.reused ? '已复用相同参数的结果，正在载入。' : '完整计算已提交；当前有效结果仍在右侧显示。', false, 6000);
      }
    } catch (error) {
      if (error.status === 409) showToast(`当前已有生成任务：${error.message}`, true, 6500);
      else showToast(error.message, true, 6500);
    } finally {
      state.generating = false;
      button.textContent = '更新模型';
      updateAllState();
    }
  }

  function schedulePoll(delay = 2000) {
    clearTimeout(state.pollTimer);
    if (!isActiveJob()) return;
    state.pollTimer = setTimeout(pollJob, delay);
  }

  async function pollJob() {
    if (!state.job || !state.job.id || state.pollInFlight) return;
    state.pollInFlight = true;
    try {
      const response = await apiRequest(`/api/jobs/${encodeURIComponent(state.job.id)}`);
      if (response.job) {
        state.job = response.job;
        updateJobCard();
        updateAllState();
      }
      if (state.job && TERMINAL_STATUSES.has(state.job.status)) await handleTerminalJob(state.job);
      else schedulePoll(2000);
    } catch (error) {
      setText(byId('job-message'), `暂时无法读取任务状态：${error.message}。工作台会继续尝试恢复连接。`);
      schedulePoll(5000);
    } finally {
      state.pollInFlight = false;
    }
  }

  async function refreshCases() {
    try {
      const response = await apiRequest('/api/cases');
      if (Array.isArray(response.cases)) state.cases = sortAndDeduplicateCases(response.cases);
    } catch {
      // Bootstrap already contains the initial cases; keep the last known history.
    }
    renderCasePicker(state.currentCase ? state.currentCase.id : null);
  }

  async function handleTerminalJob(job) {
    if (!job || !TERMINAL_STATUSES.has(job.status)) return;
    if (job.status !== 'succeeded') {
      updateJobCard();
      updateAllState();
      const text = job.status === 'failed' ? `生成失败：${job.error || '请查看任务日志。'}`
        : job.status === 'cancelled' ? '任务已取消，原有有效结果保持显示。'
          : '服务重启后任务中断，原有有效结果保持显示。';
      showToast(text, job.status === 'failed' || job.status === 'interrupted', 7000);
      state.jobFinalizingId = job.id;
      return;
    }
    if (state.jobFinalizingId === job.id) return;
    state.jobFinalizingId = job.id;
    let result = job.result;
    if (!result) {
      await refreshCases();
      result = state.cases.find(item => item.id === job.case_id) || null;
    }
    updateJobCard();
    if (result && result.validated && result.urls) {
      addOrReplaceCase(result);
      displayCase(result);
      setActiveTab('model');
      const sameDraft = sameCalculation(configForCase(result), localValidate().config);
      showToast(state.jobReused
        ? `已载入已生成结果“${String(result.title || result.id)}”。`
        : `生成成功，已切换到“${String(result.title || result.id)}”。`, false, 6500);
      if (!sameDraft) setText(byId('result-subtitle'), '新结果已载入；生成期间修改的草稿仍保留，可继续调整。');
    } else {
      setText(byId('job-message'), '任务报告成功，但服务没有提供完整的已生成结果；原有显示结果已保留。');
      showToast('任务没有可载入的完整验证结果；原有结果已保留。', true, 7000);
    }
    updateAllState();
    persistState();
  }

  async function cancelJob() {
    if (!isActiveJob()) return;
    const button = byId('cancel-button');
    button.disabled = true;
    setText(byId('job-message'), '正在请求取消；当前显示结果保持不变。');
    try {
      const response = await apiRequest(`/api/jobs/${encodeURIComponent(state.job.id)}/cancel`, { method: 'POST', body: {} });
      if (response.job) state.job = response.job;
      updateJobCard();
      updateAllState();
      if (state.job && TERMINAL_STATUSES.has(state.job.status)) await handleTerminalJob(state.job);
      else schedulePoll(1000);
    } catch (error) {
      showToast(`取消请求未完成：${error.message}`, true, 6500);
      button.disabled = false;
    }
  }

  function mergeWithDefaults(config) {
    const input = asPlainObject(config) ? config : {};
    return {
      ...clone(state.defaults || {}),
      ...input,
      corner_angle_deg: input.corner_angle_deg == null ? 90 : input.corner_angle_deg,
      parameters: { ...(state.defaults && state.defaults.parameters || {}), ...(asPlainObject(input.parameters) ? input.parameters : {}) },
      advanced: { ...(state.defaults && state.defaults.advanced || {}), ...(asPlainObject(input.advanced) ? input.advanced : {}) }
    };
  }

  function replaceDraftFromConfig(config) {
    if (!asPlainObject(config)) return false;
    state.draft = makeRawDraft(mergeWithDefaults(config));
    state.touched.clear();
    state.serverValidation = null;
    state.serverValidationCurrent = false;
    state.validationAttempted = true;
    state.searchTerm = '';
    byId('parameter-search').value = '';
    byId('validation-result').hidden = true;
    renderParameterFields();
    updateSearch();
    updateAllState();
    persistState();
    return true;
  }

  function loadSelectedConfigToDraft() {
    const source = selectedCaseSource();
    if (!source || !asPlainObject(source.value.config)) return;
    replaceDraftFromConfig(source.value.config);
    const taskNote = isActiveJob() ? '后台任务仍按提交时的参数运行。' : '';
    showToast(`“${String(source.value.title || source.value.id)}”的参数已载入草稿；当前显示结果保持不变。${taskNote}请校验后更新模型。`, false, 6500);
  }

  function viewSelectedCaseResult() {
    const source = selectedCaseSource();
    if (!source || source.type !== 'case' || !asPlainObject(source.value.config)) return;
    if (!replaceDraftFromConfig(source.value.config)) return;
    state.activeTab = 'model';
    displayCase(source.value);
    setActiveTab('model');
    updateAllState();
    const taskNote = isActiveJob() ? '后台任务仍按已提交参数继续运行，尚未报告成功。' : '不会启动计算。';
    showToast(`正在查看“${String(source.value.title || source.value.id)}”；该结果的主参数、高级参数和案例名已回填草稿。${taskNote}`, false, 6500);
  }

  function detectUnknownKeys(input) {
    const unknown = [];
    if (!asPlainObject(input)) return ['配置顶层必须是 JSON 对象'];
    const topAllowed = new Set(['case_id', 'corner_angle_deg', 'parameters', 'advanced']);
    for (const key of Object.keys(input)) if (!topAllowed.has(key)) unknown.push(key);
    for (const section of ['parameters', 'advanced']) {
      if (input[section] == null) continue;
      if (!asPlainObject(input[section])) {
        unknown.push(`${section}（应为对象）`);
        continue;
      }
      const allowed = new Set(Object.keys(schemaProperties(section)));
      for (const key of Object.keys(input[section])) if (!allowed.has(key)) unknown.push(`${section}.${key}`);
    }
    return unknown;
  }

  async function importConfigFile(file) {
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (!asPlainObject(parsed)) throw new Error('配置内容必须是 JSON 对象。');
      const unknown = detectUnknownKeys(parsed);
      if (unknown.length) throw new Error(`包含当前版本不认识的字段：${unknown.join('、')}`);
      if (Object.hasOwn(parsed, 'corner_angle_deg') && (typeof parsed.corner_angle_deg !== 'number' || !Number.isFinite(parsed.corner_angle_deg) || parsed.corner_angle_deg <= 0 || parsed.corner_angle_deg >= 180)) throw new Error('建筑平面内角须为大于 0°、小于 180° 的有限 JSON 数字。该配置尚未导入。');
      if (Object.hasOwn(parsed, 'case_id') && typeof parsed.case_id !== 'string') throw new Error('case_id 必须是文字。该配置尚未导入。');
      for (const section of ['parameters', 'advanced']) {
        if (Object.hasOwn(parsed, section) && !asPlainObject(parsed[section])) throw new Error(`${section} 必须是 JSON 对象。该配置尚未导入。`);
        for (const [key, value] of Object.entries(parsed[section] || {})) {
          if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${section}.${key} 必须是有限的 JSON 数字。该配置尚未导入。`);
        }
      }
      state.draft = makeRawDraft(mergeWithDefaults(parsed));
      state.touched.clear();
      state.serverValidation = null;
      state.serverValidationCurrent = false;
      state.validationAttempted = true;
      state.searchTerm = '';
      byId('parameter-search').value = '';
      renderParameterFields();
      updateSearch();
      state.validationAttempted = true;
      updateAllState();
      persistState();
      showToast('配置已导入草稿。请校验参数后再更新模型。');
    } catch (error) {
      showToast(`导入失败：${error.message}`, true, 7000);
    } finally {
      byId('config-file').value = '';
    }
  }

  function downloadBlob(filename, text, mimeType) {
    const blob = new Blob([text], { type: mimeType });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1200);
  }

  function safeFilename(value) {
    const cleaned = String(value || 'custom').trim().replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/\s+/g, '_').slice(0, 80);
    return cleaned || 'custom';
  }

  function exportDraft() {
    const local = localValidate();
    if (local.errors.length) {
      state.validationAttempted = true;
      updateAllState();
      showToast(localValidationErrorMessage(local.errors), true, 6500);
      return;
    }
    const json = `${JSON.stringify(local.config, null, 2)}\n`;
    downloadBlob(`${safeFilename(local.config.case_id)}.json`, json, 'application/json;charset=utf-8');
    showToast('当前有效草稿配置已下载。');
  }

  function initialiseEvents() {
    byId('import-button').addEventListener('click', () => byId('config-file').click());
    byId('config-file').addEventListener('change', event => importConfigFile(event.currentTarget.files && event.currentTarget.files[0]));
    byId('export-config-button').addEventListener('click', exportDraft);
    byId('validate-button').addEventListener('click', validateDraft);
    byId('generate-button').addEventListener('click', startGeneration);
    byId('cancel-button').addEventListener('click', cancelJob);
    byId('load-case-button').addEventListener('click', loadSelectedConfigToDraft);
    byId('view-case-button').addEventListener('click', viewSelectedCaseResult);
    byId('model-tab').addEventListener('click', () => setActiveTab('model'));
    byId('layout-tab').addEventListener('click', () => setActiveTab('layout'));
    byId('case-picker').addEventListener('change', event => {
      updateCaseSelectionUi();
    });
    byId('parameter-search').addEventListener('input', event => {
      state.searchTerm = event.currentTarget.value;
      updateSearch();
    });
    document.addEventListener('keydown', event => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return;
      event.preventDefault();
      byId('parameter-search').focus();
    });
    window.addEventListener('beforeunload', persistState);
  }

  async function initialise() {
    clearAppError();
    byId('draft-indicator').textContent = '连接中';
    try {
      const bootstrap = await apiRequest('/api/bootstrap');
      if (!bootstrap.schema || !asPlainObject(bootstrap.schema.properties)) throw new Error('bootstrap 没有提供参数 schema。');
      state.schema = bootstrap.schema;
      state.defaults = asPlainObject(bootstrap.defaults) ? bootstrap.defaults : {};
      state.examples = Array.isArray(bootstrap.examples) ? bootstrap.examples : [];
      state.cases = sortAndDeduplicateCases(bootstrap.cases || []);
      const saved = readPersistentState();
      const base = makeRawDraft(state.defaults);
      if (asPlainObject(saved.draft)) {
        const savedDraft = makeRawDraft(saved.draft);
        state.draft = { ...base, ...savedDraft, parameters: { ...base.parameters, ...savedDraft.parameters }, advanced: { ...base.advanced, ...savedDraft.advanced } };
      } else {
        state.draft = base;
      }
      renderParameterFields();
      const savedId = typeof saved.displayedCaseId === 'string' ? saved.displayedCaseId : null;
      const initialCase = state.cases.find(item => item.id === savedId)
        || state.cases.find(item => item.id === 'default')
        || state.cases[0]
        || null;
      displayCase(initialCase, false);
      renderCasePicker(initialCase && initialCase.id);
      state.initialised = true;
      clearAppError();
      updateCaseSelectionUi();
      updateAllState();
      persistState();
      if (bootstrap.active_job && bootstrap.active_job.id) {
        state.job = bootstrap.active_job;
        state.jobConfig = state.job.result && state.job.result.config ? state.job.result.config : null;
        state.jobReused = false;
        updateJobCard();
        updateAllState();
        if (state.job.status === 'succeeded') await handleTerminalJob(state.job);
        else if (ACTIVE_STATUSES.has(state.job.status)) schedulePoll(0);
        else if (TERMINAL_STATUSES.has(state.job.status)) updateJobCard();
      }
    } catch (error) {
      state.initialised = false;
      updateAllState();
      setAppError(`无法连接本地工作台 API：${error.message}`, true);
    }
  }

  initialiseEvents();
  initialise();
})();
