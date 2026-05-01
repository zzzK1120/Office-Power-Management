/**
 * 办公室智能用电管理系统 - 前端公共脚本
 */

function $id(id) { return document.getElementById(id); }

async function apiFetch(url, options) {
    const resp = await fetch(url, Object.assign({
        headers: { 'Content-Type': 'application/json' }
    }, options || {}));
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || (data && data.ok === false)) {
        const msg = (data && data.error) ? data.error : ('请求失败：' + resp.status);
        throw new Error(msg);
    }
    return data;
}

function escapeHtml(str) {
    return String(str || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

/** CSV 单元格转义 */
function escapeCsvCell(val) {
    var s = String(val == null ? '' : val);
    if (/[",\n\r]/.test(s)) {
        return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
}

// 标签页切换（用电统计 日/周/月）
document.querySelectorAll('[data-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () {
        var tab = this.getAttribute('data-tab');
        document.querySelectorAll('[data-tab]').forEach(function (b) { b.classList.remove('active'); });
        document.querySelectorAll('[data-panel]').forEach(function (p) { p.hidden = p.getAttribute('data-panel') !== tab; });
        this.classList.add('active');
    });
});

// 确认对话框
window.confirmAction = function (msg, callback) {
    if (typeof callback === 'function' && confirm(msg || '确定要执行此操作吗？')) {
        callback();
    }
};

// ========== 首页（办公室选择与管理） ==========
async function initHomePage() {
    const bodyEl = $id('officeBody');
    const formEl = $id('officeForm');
    const nameEl = $id('officeName');
    const locEl = $id('officeLocation');
    const descEl = $id('officeDesc');
    const submitEl = $id('officeSubmit');
    const cancelEl = $id('officeCancelEdit');
    const hintEl = $id('officeSelectedHint');

    let selectedOfficeId = null;
    let editingOfficeId = null;
    let offices = [];

    function resetForm() {
        editingOfficeId = null;
        if (nameEl) nameEl.value = '';
        if (locEl) locEl.value = '';
        if (descEl) descEl.value = '';
        if (submitEl) submitEl.textContent = '添加办公室';
        if (cancelEl) cancelEl.hidden = true;
    }

    function render() {
        if (!bodyEl) return;
        bodyEl.innerHTML = offices.map(function (o) {
            const selectedMark = Number(o.id) === Number(selectedOfficeId) ? '✅' : '';
            const onCnt = Number(o.on_devices || 0);
            const totalCnt = Number(o.total_devices || 0);
            return `<tr data-office-id="${o.id}">
<td>${selectedMark}</td>
<td>${escapeHtml(o.name)}</td>
<td>${escapeHtml(o.location || '')}</td>
<td>${escapeHtml(o.description || '')}</td>
<td>${onCnt} / ${totalCnt}</td>
<td>
  <button class="btn btn-primary btn-sm" data-action="select">选择</button>
  <button class="btn btn-secondary btn-sm" data-action="edit">编辑</button>
  <button class="btn btn-danger btn-sm" data-action="delete">删除</button>
</td>
</tr>`;
        }).join('') || '<tr><td colspan="6">暂无办公室，请先添加</td></tr>';
        const current = offices.find(function (x) { return Number(x.id) === Number(selectedOfficeId); });
        if (hintEl) {
            hintEl.textContent = current
                ? ('当前已选择：' + (current.display_name || current.name))
                : '当前未选择办公室';
        }
    }

    async function loadOffices() {
        const res = await apiFetch('/api/offices');
        offices = res.offices || [];
        selectedOfficeId = res.selected_office_id;
        render();
    }

    if (formEl) {
        formEl.addEventListener('submit', async function (e) {
            e.preventDefault();
            const payload = {
                name: (nameEl && nameEl.value || '').trim(),
                location: (locEl && locEl.value || '').trim(),
                description: (descEl && descEl.value || '').trim(),
            };
            if (!payload.name) {
                alert('办公室名称不能为空');
                return;
            }
            if (editingOfficeId) {
                await apiFetch('/api/offices/' + editingOfficeId, {
                    method: 'PUT',
                    body: JSON.stringify(payload),
                });
            } else {
                await apiFetch('/api/offices', {
                    method: 'POST',
                    body: JSON.stringify(payload),
                });
            }
            resetForm();
            await loadOffices();
        });
    }

    if (cancelEl) {
        cancelEl.addEventListener('click', function () {
            resetForm();
        });
    }

    if (bodyEl) {
        bodyEl.addEventListener('click', async function (e) {
            const btn = e.target.closest('button[data-action]');
            const tr = e.target.closest('tr[data-office-id]');
            if (!btn || !tr) return;
            const officeId = Number(tr.getAttribute('data-office-id'));
            const action = btn.getAttribute('data-action');
            const row = offices.find(function (x) { return Number(x.id) === officeId; });
            if (!row) return;
            if (action === 'select') {
                await apiFetch('/api/select-office', {
                    method: 'POST',
                    body: JSON.stringify({ office_id: officeId }),
                });
                selectedOfficeId = officeId;
                render();
            } else if (action === 'edit') {
                editingOfficeId = officeId;
                if (nameEl) nameEl.value = row.name || '';
                if (locEl) locEl.value = row.location || '';
                if (descEl) descEl.value = row.description || '';
                if (submitEl) submitEl.textContent = '保存修改';
                if (cancelEl) cancelEl.hidden = false;
            } else if (action === 'delete') {
                if (!confirm('确定删除该办公室？')) return;
                await apiFetch('/api/offices/' + officeId, { method: 'DELETE' });
                await loadOffices();
                resetForm();
            }
        });
    }

    await loadOffices();
}

// ========== 远程控制页逻辑 ==========
async function initControlPage() {
    const groupListEl = $id('groupList');
    const deviceListEl = $id('deviceList');
    const groupEmptyEl = $id('groupEmpty');
    const deviceEmptyEl = $id('deviceEmpty');

    let groups = [];
    let devices = [];

    async function loadAll() {
        const [gRes, dRes] = await Promise.all([
            apiFetch('/control/api/groups'),
            apiFetch('/control/api/devices'),
        ]);
        groups = gRes.groups || [];
        devices = dRes.devices || [];
        render();
    }

    function render() {
        // groups
        if (!groups.length) {
            groupListEl.innerHTML = '';
            groupEmptyEl.hidden = false;
        } else {
            groupEmptyEl.hidden = true;
            groupListEl.innerHTML = groups.map(g => {
                return `
<div class="group-card" data-group-id="${g.id}">
  <div>
    <div class="group-name">${escapeHtml(g.name)}</div>
    <div class="group-count">${g.on_count || 0} / ${g.device_count} 开启</div>
  </div>
  <div style="display:flex; gap:10px; align-items:center;">
    <div class="switch ${g.is_on ? 'on' : ''}" data-action="group-toggle" title="点击切换分组开关"></div>
    <button class="btn btn-danger btn-sm" data-action="group-delete">删除</button>
  </div>
</div>`;
            }).join('');
        }

        // devices
        if (!devices.length) {
            deviceListEl.innerHTML = '';
            deviceEmptyEl.hidden = false;
        } else {
            deviceEmptyEl.hidden = true;
            const groupOptions = ['<option value="">选择分组加入…</option>']
                .concat(groups.map(g => `<option value="${g.id}">${escapeHtml(g.name)}</option>`))
                .join('');

            deviceListEl.innerHTML = devices.map(d => {
                const gs = (d.groups || []);
                const groupText = gs.length ? ('分组：' + gs.map(x => escapeHtml(x.name)).join('，')) : '未分组';
                const groupChips = gs.map(x => (
                    `<button class="btn btn-secondary btn-sm" data-action="remove-member" data-group-id="${x.id}" title="移出分组">- ${escapeHtml(x.name)}</button>`
                )).join(' ');

                return `
<div class="device-control-item" data-device-id="${d.id}">
  <div class="info">
    <div class="name">${escapeHtml(d.name)} <span style="color:var(--text-secondary); font-size:12px;">${escapeHtml(d.device_type || '其他')} · ${escapeHtml(d.location || '')}</span></div>
    <div class="group-tag">${groupText}</div>
    <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:8px; align-items:center;">
      <select data-action="add-to-group" style="padding:6px 10px; border-radius:8px; border:1px solid var(--card-border); font-size:13px;">
        ${groupOptions}
      </select>
      ${groupChips}
      <button class="btn btn-danger btn-sm" data-action="device-delete">删除设备</button>
    </div>
  </div>
  <div class="switch ${d.is_on ? 'on' : ''}" data-action="device-toggle" title="点击切换"></div>
</div>`;
            }).join('');
        }
    }

    // 顶部按钮：新增
    const btnAddGroup = $id('btnAddGroup');
    const btnAddDevice = $id('btnAddDevice');

    if (btnAddGroup) {
        btnAddGroup.addEventListener('click', async function () {
            const name = prompt('请输入分组名称（必填）');
            if (!name) return;
            const desc = prompt('请输入分组描述（可选）') || '';
            try {
                await apiFetch('/control/api/groups', { method: 'POST', body: JSON.stringify({ name, description: desc }) });
                await loadAll();
            } catch (e) {
                alert(e.message);
            }
        });
    }

    const deviceAddModal = $id('deviceAddModal');
    const deviceAddName = $id('deviceAddName');
    const deviceAddType = $id('deviceAddType');
    const deviceAddLocation = $id('deviceAddLocation');

    function openDeviceAddModal() {
        if (!deviceAddModal) return;
        if (deviceAddName) deviceAddName.value = '';
        if (deviceAddType) deviceAddType.value = '其他';
        if (deviceAddLocation) deviceAddLocation.value = '';
        deviceAddModal.hidden = false;
        if (deviceAddName) deviceAddName.focus();
    }

    function closeDeviceAddModal() {
        if (deviceAddModal) deviceAddModal.hidden = true;
    }

    if (btnAddDevice && deviceAddModal) {
        btnAddDevice.addEventListener('click', openDeviceAddModal);
        $id('deviceAddModalClose') && $id('deviceAddModalClose').addEventListener('click', closeDeviceAddModal);
        $id('deviceAddCancel') && $id('deviceAddCancel').addEventListener('click', closeDeviceAddModal);
        deviceAddModal.addEventListener('click', function (e) {
            if (e.target === deviceAddModal) closeDeviceAddModal();
        });
        $id('deviceAddSubmit') && $id('deviceAddSubmit').addEventListener('click', async function () {
            const name = (deviceAddName && deviceAddName.value || '').trim();
            if (!name) {
                alert('请填写设备名称');
                return;
            }
            const deviceType = (deviceAddType && deviceAddType.value) || '其他';
            const location = (deviceAddLocation && deviceAddLocation.value || '').trim();
            try {
                await apiFetch('/control/api/devices', {
                    method: 'POST',
                    body: JSON.stringify({ name: name, device_type: deviceType, location: location }),
                });
                closeDeviceAddModal();
                await loadAll();
            } catch (e) {
                alert(e.message);
            }
        });
    }

    // 事件委托：分组操作
    groupListEl.addEventListener('click', async function (e) {
        const card = e.target.closest('[data-group-id]');
        if (!card) return;
        const groupId = card.getAttribute('data-group-id');
        const btn = e.target.closest('button');
        const sw = e.target.closest('.switch');
        const action = (btn && btn.getAttribute('data-action')) || (sw && sw.getAttribute('data-action'));

        try {
            if (action === 'group-toggle') {
                const nowOn = sw.classList.contains('on');
                await apiFetch(`/control/api/groups/${groupId}/set_state`, { method: 'POST', body: JSON.stringify({ is_on: !nowOn }) });
            } else if (action === 'group-delete') {
                if (!confirm('确定删除该分组？（不会删除设备，只会删除分组和关联关系）')) return;
                await apiFetch(`/control/api/groups/${groupId}`, { method: 'DELETE' });
            } else {
                return;
            }
            await loadAll();
        } catch (err) {
            alert(err.message);
        }
    });

    // 事件委托：设备操作（开关、删除、加入/移出分组）
    deviceListEl.addEventListener('click', async function (e) {
        const el = e.target;
        const item = el.closest('[data-device-id]');
        if (!item) return;
        const deviceId = item.getAttribute('data-device-id');

        const btn = el.closest('button');
        const sw = el.closest('.switch');
        const action = (btn && btn.getAttribute('data-action')) || (sw && sw.getAttribute('data-action'));

        try {
            if (action === 'device-toggle') {
                // 读当前 DOM 状态，取反后提交
                const nowOn = sw.classList.contains('on');
                await apiFetch(`/control/api/devices/${deviceId}/set_state`, { method: 'POST', body: JSON.stringify({ is_on: !nowOn }) });
            } else if (action === 'device-delete') {
                if (!confirm('确定删除该设备？（会同时删除其分组关联和采集数据等外键级联数据）')) return;
                await apiFetch(`/control/api/devices/${deviceId}`, { method: 'DELETE' });
            } else if (action === 'remove-member') {
                const groupId = btn.getAttribute('data-group-id');
                await apiFetch(`/control/api/groups/${groupId}/members/${deviceId}`, { method: 'DELETE' });
            } else {
                return;
            }
            await loadAll();
        } catch (err) {
            alert(err.message);
        }
    });

    deviceListEl.addEventListener('change', async function (e) {
        const sel = e.target.closest('select[data-action="add-to-group"]');
        if (!sel) return;
        const item = sel.closest('[data-device-id]');
        if (!item) return;
        const deviceId = item.getAttribute('data-device-id');
        const groupId = sel.value;
        sel.value = '';
        if (!groupId) return;

        try {
            await apiFetch(`/control/api/groups/${groupId}/members`, { method: 'POST', body: JSON.stringify({ device_id: Number(deviceId) }) });
            await loadAll();
        } catch (err) {
            alert(err.message);
        }
    });

    await loadAll();
}

if (window.__PAGE__ === 'control') {
    initControlPage().catch(function (e) {
        console.error(e);
        alert('控制页初始化失败：' + e.message);
    });
}

// ========== 定时策略页逻辑 ==========
async function initSchedulePage() {
    const listEl = $id('scheduleList');
    const emptyEl = $id('scheduleEmpty');
    const formEl = $id('scheduleForm');
    const nameEl = $id('schName');
    const timeEl = $id('schTime');
    const actionEl = $id('schAction');
    const repeatEl = $id('schRepeat');
    const runDateEl = $id('schRunDate');
    const targetSelect = $id('schTarget');
    const groupOptEl = $id('schGroupOptions');
    const deviceOptEl = $id('schDeviceOptions');
    const btnNew = $id('btnNewSchedule');

    let schedules = [];

    function todayForInput() {
        const d = new Date();
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    function syncRunDateVisibility() {
        const wrap = $id('schRunDateWrap');
        const isOnce = repeatEl && repeatEl.value === 'ONCE';
        if (wrap) wrap.hidden = !isOnce;
        if (runDateEl && !runDateEl.value) runDateEl.value = todayForInput();
    }

    async function loadOptions() {
        const res = await apiFetch('/schedule/api/options');
        const groups = res.groups || [];
        const devices = res.devices || [];
        groupOptEl.innerHTML = groups.map(g => `<option value="GROUP:${g.id}">${escapeHtml(g.name)}</option>`).join('');
        deviceOptEl.innerHTML = devices.map(d => {
            const loc = d.location ? `（${escapeHtml(d.location)}）` : '';
            return `<option value="DEVICE:${d.id}">${escapeHtml(d.name)}${loc}</option>`;
        }).join('');
    }

    async function loadSchedules() {
        const res = await apiFetch('/schedule/api/schedules');
        schedules = res.schedules || [];
        render();
    }

    function render() {
        if (!schedules.length) {
            listEl.innerHTML = '';
            emptyEl.hidden = false;
            return;
        }
        emptyEl.hidden = true;
        listEl.innerHTML = schedules.map(s => {
            const actionText = s.action === 'ON' ? '开启' : '关闭';
            const enabledBadge = s.enabled
                ? '<span class="enabled-badge">已启用</span>'
                : '<span style="font-size:12px; padding:2px 8px; border-radius:999px; background:rgba(100,116,139,0.2); color:var(--text-secondary);">已禁用</span>';
            const repeatText = s.repeat_label || '';
            const runDateText = s.run_date ? ` · ${escapeHtml(s.run_date)}` : '';
            return `
<div class="schedule-item" data-id="${s.id}">
  <div class="schedule-info">
    <span class="time">${escapeHtml(s.time_of_day || '')}</span>
    <span class="action">${actionText}</span>
    <span class="target">${escapeHtml(s.target_name || '')}</span>
    ${enabledBadge}
    ${repeatText ? `<span style="font-size:12px; margin-left:8px; color:var(--text-secondary);">${escapeHtml(repeatText)}${runDateText}</span>` : ''}
  </div>
  <div style="display:flex; gap:8px;">
    <button class="btn btn-secondary btn-sm" data-action="toggle">${s.enabled ? '禁用' : '启用'}</button>
    <button class="btn btn-danger btn-sm" data-action="delete">删除</button>
  </div>
</div>`;
        }).join('');
    }

    if (btnNew) {
        btnNew.addEventListener('click', function () {
            nameEl.focus();
        });
    }

    if (repeatEl) {
        repeatEl.addEventListener('change', syncRunDateVisibility);
    }

    if (formEl) {
        formEl.addEventListener('submit', async function (e) {
            e.preventDefault();
            const name = nameEl.value.trim();
            const time = timeEl.value;
            const action = actionEl.value;
            const repeat_type = repeatEl.value;
            const run_date = runDateEl ? runDateEl.value : '';
            const targetValue = targetSelect.value;

            if (!name) {
                alert('请填写策略名称');
                return;
            }
            if (!time) {
                alert('请选择执行时间');
                return;
            }
            if (repeat_type === 'ONCE' && !run_date) {
                alert('请选择执行日期');
                return;
            }
            if (!targetValue) {
                alert('请选择作用对象（设备或分组）');
                return;
            }

            const [target_type, target_id_str] = targetValue.split(':');
            try {
                await apiFetch('/schedule/api/schedules', {
                    method: 'POST',
                    body: JSON.stringify({
                        name,
                        time,
                        action,
                        repeat_type,
                        run_date,
                        target_type,
                        target_id: Number(target_id_str),
                    }),
                });
                nameEl.value = '';
                if (runDateEl) runDateEl.value = todayForInput();
                await loadSchedules();
            } catch (err) {
                alert(err.message);
            }
        });
    }

    listEl.addEventListener('click', async function (e) {
        const btn = e.target.closest('button');
        if (!btn) return;
        const item = btn.closest('[data-id]');
        if (!item) return;
        const id = item.getAttribute('data-id');
        const action = btn.getAttribute('data-action');
        try {
            if (action === 'toggle') {
                await apiFetch(`/schedule/api/schedules/${id}/toggle`, { method: 'POST' });
            } else if (action === 'delete') {
                if (!confirm('确定删除该定时策略？')) return;
                await apiFetch(`/schedule/api/schedules/${id}`, { method: 'DELETE' });
            } else {
                return;
            }
            await loadSchedules();
        } catch (err) {
            alert(err.message);
        }
    });

    if (runDateEl && !runDateEl.value) runDateEl.value = todayForInput();
    syncRunDateVisibility();
    await loadOptions();
    await loadSchedules();
}

if (window.__PAGE__ === 'schedule') {
    initSchedulePage().catch(function (e) {
        console.error(e);
        alert('定时策略页初始化失败：' + e.message);
    });
}

// ========== 监控页逻辑 ==========
async function initMonitorPage() {
    const gridEl = $id('monitorGrid');
    const summaryEl = $id('monitorSummary');
    const btnRefresh = $id('btnMonitorRefresh');
    const stateLogModal = $id('stateLogModal');
    const stateLogDate = $id('stateLogDate');
    const stateLogQuery = $id('stateLogQuery');
    const stateLogBody = $id('stateLogBody');
    const stateLogHint = $id('stateLogDeviceHint');

    let currentLogDeviceId = null;
    let currentLogDeviceName = '';

    function _todayForInput() {
        var d = new Date();
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    function openStateLogModal(deviceId, deviceName) {
        if (!stateLogModal) return;
        currentLogDeviceId = Number(deviceId);
        currentLogDeviceName = deviceName || '';
        if (stateLogDate) stateLogDate.value = _todayForInput();
        if (stateLogHint) stateLogHint.innerHTML = '<span style="color:var(--text-secondary);font-size:13px;">设备：</span><b>' + escapeHtml(currentLogDeviceName) + '</b>';
        if (stateLogBody) stateLogBody.innerHTML = '<tr><td colspan="3">请选择日期后点击查询</td></tr>';
        stateLogModal.hidden = false;
    }

    function closeStateLogModal() {
        if (stateLogModal) stateLogModal.hidden = true;
    }

    async function loadStateLogs() {
        if (!currentLogDeviceId) return;
        const day = (stateLogDate && stateLogDate.value) || '';
        if (!day) {
            alert('请选择日期');
            return;
        }
        const res = await apiFetch('/monitor/api/devices/' + currentLogDeviceId + '/state-logs?date=' + encodeURIComponent(day));
        const logs = res.logs || [];
        const rowsHtml = logs.map(function (r) {
            var ts = (r.ts || '').replace('T', ' ');
            var hhmm = '--:--';
            if (ts.length >= 16) hhmm = ts.slice(11, 16);
            var stateText = r.is_on ? '开启' : '关闭';
            var src = r.source || '';
            var srcLabel = ({ manual: '手动', schedule: '定时', group: '分组', smart_close: '智能关闭' }[src] || src || '--');
            return '<tr><td>' + escapeHtml(hhmm) + '</td><td>' + escapeHtml(stateText) + '</td><td>' + escapeHtml(srcLabel) + '</td></tr>';
        }).join('');
        if (stateLogBody) {
            stateLogBody.innerHTML = rowsHtml || '<tr><td colspan="3">当天暂无开关记录</td></tr>';
        }
    }

    async function load() {
        const res = await apiFetch('/monitor/api/overview');
        const s = res.summary || {};
        const devices = res.devices || [];

        if (summaryEl) {
            summaryEl.innerHTML = `
<div class="summary-card"><div class="label">当前总功率</div><div class="value">${((s.total_power_w || 0) / 1000).toFixed(2)} kW</div></div>
<div class="summary-card"><div class="label">今日用电量</div><div class="value">${(s.today_kwh || 0).toFixed(3)} kWh</div></div>
<div class="summary-card"><div class="label">在线设备</div><div class="value">${s.online_count || 0} / ${s.total_devices || 0}</div></div>
<div class="summary-card"><div class="label">异常设备</div><div class="value" style="color:var(--danger);">${s.abnormal_count || 0}</div></div>`;
        }

        if (gridEl) {
            gridEl.innerHTML = devices.map(d => {
                const status = d.is_alarm ? 'alert' : (d.is_on ? 'on' : 'off');
                const statusText = d.is_alarm ? '异常' : (d.is_on ? '运行中' : '关闭');
                return `<div class="monitor-card">
  <div class="device-name" style="display:flex; gap:10px; align-items:center; justify-content:space-between;">
    <div>
      ${escapeHtml(d.name)} <span class="status-badge ${status}">${statusText}</span>
    </div>
    <button class="btn btn-secondary btn-sm" data-action="state-logs" data-id="${d.id}" data-name="${escapeHtml(d.name)}">记录查询</button>
  </div>
  <div class="device-location">${escapeHtml(d.device_type || '其他')} · ${escapeHtml(d.location || '')}</div>
  <div class="metrics">
    <div class="metric-item"><label>电压 (V)</label><span class="value">${d.voltage == null ? '--' : Number(d.voltage).toFixed(1)}</span></div>
    <div class="metric-item"><label>功率 (W)</label><span class="value">${d.power == null ? '--' : Number(d.power).toFixed(1)}</span></div>
    <div class="metric-item"><label>今日用电 (kWh)</label><span class="value">${Number(d.today_kwh || 0).toFixed(3)}</span></div>
    <div class="metric-item"><label>设备类型</label><span class="value">${escapeHtml(d.device_type || '其他')}</span></div>
  </div>
</div>`;
            }).join('');
        }
    }

    if (btnRefresh) btnRefresh.addEventListener('click', load);
    if (gridEl) {
        gridEl.addEventListener('click', function (e) {
            const btn = e.target.closest('button[data-action="state-logs"]');
            if (!btn) return;
            const id = btn.getAttribute('data-id');
            const name = btn.getAttribute('data-name') || '';
            openStateLogModal(id, name);
        });
    }
    if (stateLogModal) {
        $id('stateLogModalClose') && $id('stateLogModalClose').addEventListener('click', closeStateLogModal);
        $id('stateLogClose') && $id('stateLogClose').addEventListener('click', closeStateLogModal);
        stateLogModal.addEventListener('click', function (e) {
            if (e.target === stateLogModal) closeStateLogModal();
        });
    }
    if (stateLogQuery) {
        stateLogQuery.addEventListener('click', function () {
            loadStateLogs().catch(function (err) {
                alert('查询失败：' + err.message);
            });
        });
    }
    await load();
    setInterval(load, 1000);
}

if (window.__PAGE__ === 'monitor') {
    initMonitorPage().catch(function (e) {
        console.error(e);
        alert('监控页初始化失败：' + e.message);
    });
}

// ========== 统计页逻辑 ==========
var _echartsStatsDevice = null;
var _echartsStatsOfficeCompare = null;

function resizeEchartsStats() {
    try {
        if (_echartsStatsDevice) _echartsStatsDevice.resize();
    } catch (e) { /* ignore */ }
    try {
        if (_echartsStatsOfficeCompare) _echartsStatsOfficeCompare.resize();
    } catch (e) { /* ignore */ }
}

function renderStatsDeviceStackChart(payload) {
    var el = $id('statsDeviceChart');
    if (!el) return;
    if (typeof echarts === 'undefined') {
        el.innerHTML = '<p style="color:var(--text-secondary);padding:24px;text-align:center;">图表库未加载。</p>';
        return;
    }
    if (_echartsStatsDevice) {
        try { _echartsStatsDevice.dispose(); } catch (e) { /* ignore */ }
        _echartsStatsDevice = null;
    }
    var rawDates = payload.dates || [];
    var dates = rawDates.map(function (d) {
        var s = String(d).replace(/T.*/, '');
        return s.length >= 10 ? s.slice(5) : s;
    });
    var rawSeries = payload.series || [];
    _echartsStatsDevice = echarts.init(el);
    if (!rawSeries.length) {
        _echartsStatsDevice.setOption({
            title: {
                text: '暂无设备或所选区间内无分设备用电数据',
                left: 'center',
                top: 'center',
                textStyle: { color: '#64748b', fontSize: 14, fontWeight: 'normal' },
            },
        });
        return;
    }
    var series = rawSeries.map(function (s) {
        return {
            name: s.name,
            type: 'line',
            stack: 'total',
            smooth: true,
            symbol: 'circle',
            symbolSize: 6,
            emphasis: { focus: 'series' },
            lineStyle: { width: 2 },
            areaStyle: { opacity: 0.12 },
            data: (s.data || []).map(function (v) { return Number(v || 0).toFixed(3); }),
        };
    });
    _echartsStatsDevice.setOption({
        tooltip: { trigger: 'axis' },
        legend: { top: 0 },
        grid: { left: 48, right: 24, top: 48, bottom: 36 },
        xAxis: {
            type: 'category',
            data: dates,
            boundaryGap: false,
            axisLabel: { color: '#64748b' },
            axisLine: { lineStyle: { color: '#cbd5e1' } },
        },
        yAxis: {
            type: 'value',
            name: 'kWh',
            axisLabel: { color: '#64748b' },
            splitLine: { lineStyle: { color: '#e2e8f0', type: 'dashed' } },
        },
        series: series,
        color: ['#2563eb', '#10b981', '#f59e0b', '#7c3aed', '#ef4444', '#0ea5e9', '#14b8a6', '#f97316'],
    });
}

if (window.__PAGE__ === 'home') {
    initHomePage().catch(function (e) {
        console.error(e);
        alert('首页初始化失败：' + e.message);
    });
}

function renderStatsOfficeCompareChart(payload) {
    var el = $id('statsOfficeCompareChart');
    if (!el) return;
    if (typeof echarts === 'undefined') {
        el.innerHTML = '<p style="color:var(--text-secondary);padding:24px;text-align:center;">图表库未加载。</p>';
        return;
    }
    if (_echartsStatsOfficeCompare) {
        try { _echartsStatsOfficeCompare.dispose(); } catch (e) { /* ignore */ }
        _echartsStatsOfficeCompare = null;
    }
    var rows = payload.rows || [];
    _echartsStatsOfficeCompare = echarts.init(el);
    if (!rows.length) {
        _echartsStatsOfficeCompare.setOption({
            title: {
                text: '暂无办公室对比数据',
                left: 'center',
                top: 'center',
                textStyle: { color: '#64748b', fontSize: 14, fontWeight: 'normal' },
            },
        });
        return;
    }
    _echartsStatsOfficeCompare.setOption({
        tooltip: { trigger: 'axis' },
        grid: { left: 48, right: 24, top: 24, bottom: 72 },
        xAxis: {
            type: 'category',
            data: rows.map(function (r) { return r.office_name || '未命名办公室'; }),
            axisLabel: { color: '#64748b', interval: 0, rotate: 20 },
            axisLine: { lineStyle: { color: '#cbd5e1' } },
        },
        yAxis: {
            type: 'value',
            name: 'kWh',
            axisLabel: { color: '#64748b' },
            splitLine: { lineStyle: { color: '#e2e8f0', type: 'dashed' } },
        },
        series: [{
            type: 'bar',
            data: rows.map(function (r) { return Number(r.energy_kwh || 0).toFixed(3); }),
            itemStyle: { color: '#2563eb', borderRadius: [6, 6, 0, 0] },
            barMaxWidth: 48,
        }],
    });
}

async function initStatisticsPage() {
    var officeFilter = $id('statsOfficeFilter');
    var dailyBody = $id('statsDailyBody');
    var weekBody = $id('statsWeekBody');
    var monthBody = $id('statsMonthBody');
    var summaryEl = $id('statsSummary');
    var deviceStart = $id('statsDeviceStart');
    var deviceEnd = $id('statsDeviceEnd');
    var deviceQuery = $id('statsDeviceQuery');
    var deviceBody = $id('statsDeviceBody');
    var deviceFootnote = $id('statsDeviceFootnote');
    var compareStart = $id('statsCompareStart');
    var compareEnd = $id('statsCompareEnd');
    var compareQuery = $id('statsCompareQuery');
    var compareBody = $id('statsOfficeCompareBody');
    var compareFootnote = $id('statsOfficeCompareFootnote');
    var exportBtn = $id('btnStatsExport');

    function todayForInput() {
        var d = new Date();
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    function shiftDays(days) {
        var d = new Date();
        d.setDate(d.getDate() + days);
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    async function loadOfficeOptions() {
        if (!officeFilter) return;
        var res = await apiFetch('/api/offices');
        var offices = res.offices || [];
        officeFilter.innerHTML = '<option value="ALL">全局（跨办公室）</option>' + offices.map(function (o) {
            var label = o.location && o.name ? (o.location + ' · ' + o.name) : (o.name || o.location || ('办公室 ' + o.id));
            return '<option value="' + o.id + '">' + escapeHtml(label) + '</option>';
        }).join('');
    }

    function renderDaily(rows) {
        if (!dailyBody) return;
        dailyBody.innerHTML = (rows || []).map(function (r) {
            return '<tr><td>' + escapeHtml(r.date || '') + '</td><td>' + Number(r.energy_kwh || 0).toFixed(3) + '</td><td>' + Number((r.peak_power_w || 0) / 1000).toFixed(3) + '</td><td>' + Number(r.cost_estimated || 0).toFixed(2) + '</td></tr>';
        }).join('') || '<tr><td colspan="4">暂无数据</td></tr>';
    }

    function renderWeekly(rows) {
        if (!weekBody) return;
        weekBody.innerHTML = (rows || []).map(function (r) {
            return '<tr><td>' + escapeHtml(r.label || '') + '</td><td>' + Number(r.energy_kwh || 0).toFixed(3) + '</td><td>' + Number(r.peak_power_kw || 0).toFixed(3) + '</td><td>' + Number(r.daily_avg_kwh || 0).toFixed(3) + '</td><td>' + Number(r.cost_estimated || 0).toFixed(2) + '</td><td>' + Number(r.day_count || 0) + '</td></tr>';
        }).join('') || '<tr><td colspan="6">暂无数据</td></tr>';
    }

    function renderMonthly(rows) {
        if (!monthBody) return;
        monthBody.innerHTML = (rows || []).map(function (r) {
            return '<tr><td>' + escapeHtml(r.label || '') + '</td><td>' + Number(r.energy_kwh || 0).toFixed(3) + '</td><td>' + Number(r.peak_power_kw || 0).toFixed(3) + '</td><td>' + Number(r.daily_avg_kwh || 0).toFixed(3) + '</td><td>' + Number(r.cost_estimated || 0).toFixed(2) + '</td><td>' + Number(r.day_count || 0) + '</td></tr>';
        }).join('') || '<tr><td colspan="6">暂无数据</td></tr>';
    }

    function renderSummary(summary) {
        if (!summaryEl) return;
        summaryEl.innerHTML = '\n<div class="summary-card"><div class="label">本月累计 (kWh)</div><div class="value">' + Number(summary.month_total_kwh || 0).toFixed(3) + '</div></div>\n<div class="summary-card"><div class="label">本月日均 (kWh)</div><div class="value">' + Number(summary.daily_avg_kwh || 0).toFixed(3) + '</div></div>\n<div class="summary-card"><div class="label">数据来源</div><div class="value" style="font-size:16px;">模拟实时采样</div></div>';
    }

    async function loadOverview() {
        var officeId = officeFilter ? officeFilter.value : 'ALL';
        var query = '?office_id=' + encodeURIComponent(officeId || 'ALL');
        var dailyRes = await apiFetch('/statistics/api/daily' + query);
        var weeklyRes = await apiFetch('/statistics/api/weekly' + query);
        var monthlyRes = await apiFetch('/statistics/api/monthly' + query);
        renderDaily(dailyRes.rows || []);
        renderWeekly(weeklyRes.rows || []);
        renderMonthly(monthlyRes.rows || []);
        renderSummary(dailyRes.summary || {});
    }

    var lastDevicePayload = null;
    async function loadDeviceReport() {
        var officeId = officeFilter ? officeFilter.value : 'ALL';
        var res = await apiFetch('/statistics/api/by-device?office_id=' + encodeURIComponent(officeId || 'ALL') + '&start=' + encodeURIComponent(deviceStart.value) + '&end=' + encodeURIComponent(deviceEnd.value));
        var chartRes = await apiFetch('/statistics/api/daily-per-device?office_id=' + encodeURIComponent(officeId || 'ALL') + '&start=' + encodeURIComponent(deviceStart.value) + '&end=' + encodeURIComponent(deviceEnd.value));
        lastDevicePayload = res;
        var devices = res.devices || [];
        if (deviceBody) {
            deviceBody.innerHTML = devices.map(function (d) {
                return '<tr><td>' + escapeHtml(d.name || '') + '</td><td>' + escapeHtml(d.device_type || '') + '</td><td>' + escapeHtml(d.location || '') + '</td><td>' + Number(d.energy_kwh || 0).toFixed(3) + '</td><td>' + Number(d.peak_power_kw || 0).toFixed(3) + '</td><td>' + Number(d.cost_estimated || 0).toFixed(2) + '</td><td>' + Number(d.share_pct || 0).toFixed(1) + '%</td></tr>';
            }).join('') || '<tr><td colspan="7">暂无数据</td></tr>';
        }
        if (deviceFootnote) {
            deviceFootnote.textContent = '统计范围：' + (res.office_name || '全部办公室') + ' · ' + res.start + ' 至 ' + res.end + ' · 合计 ' + Number((res.summary || {}).total_kwh || 0).toFixed(3) + ' kWh';
        }
        renderStatsDeviceStackChart(chartRes || {});
    }

    async function loadCompareReport() {
        var res = await apiFetch('/statistics/api/compare-offices?start=' + encodeURIComponent(compareStart.value) + '&end=' + encodeURIComponent(compareEnd.value));
        var rows = res.rows || [];
        if (compareBody) {
            compareBody.innerHTML = rows.map(function (r) {
                return '<tr><td>' + escapeHtml(r.office_name || '') + '</td><td>' + Number(r.device_count || 0) + '</td><td>' + Number(r.energy_kwh || 0).toFixed(3) + '</td><td>' + Number(r.peak_power_kw || 0).toFixed(3) + '</td><td>' + Number(r.cost_estimated || 0).toFixed(2) + '</td><td>' + Number(r.share_pct || 0).toFixed(1) + '%</td></tr>';
            }).join('') || '<tr><td colspan="6">暂无数据</td></tr>';
        }
        if (compareFootnote) {
            compareFootnote.textContent = '统计范围：' + res.start + ' 至 ' + res.end + ' · 合计 ' + Number((res.summary || {}).total_kwh || 0).toFixed(3) + ' kWh';
        }
        renderStatsOfficeCompareChart(res || {});
    }

    function exportDeviceCsv() {
        if (!lastDevicePayload) {
            alert('请先查询设备报表');
            return;
        }
        var rows = [['设备名称', '类型', '位置', '用电量(kWh)', '峰值功率(kW)', '电费估算(元)', '用电占比(%)']];
        (lastDevicePayload.devices || []).forEach(function (d) {
            rows.push([
                d.name || '',
                d.device_type || '',
                d.location || '',
                Number(d.energy_kwh || 0).toFixed(3),
                Number(d.peak_power_kw || 0).toFixed(3),
                Number(d.cost_estimated || 0).toFixed(2),
                Number(d.share_pct || 0).toFixed(1),
            ]);
        });
        var csv = rows.map(function (row) { return row.map(escapeCsvCell).join(','); }).join('\r\n');
        var blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'device_energy_report.csv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    if (deviceStart && !deviceStart.value) deviceStart.value = shiftDays(-29);
    if (deviceEnd && !deviceEnd.value) deviceEnd.value = todayForInput();
    if (compareStart && !compareStart.value) compareStart.value = shiftDays(-29);
    if (compareEnd && !compareEnd.value) compareEnd.value = todayForInput();

    await loadOfficeOptions();
    await loadOverview();
    await loadDeviceReport();
    await loadCompareReport();

    if (officeFilter) {
        officeFilter.addEventListener('change', function () {
            loadOverview().catch(function (e) { alert('加载统计失败：' + e.message); });
            loadDeviceReport().catch(function (e) { alert('加载设备报表失败：' + e.message); });
        });
    }
    if (deviceQuery) {
        deviceQuery.addEventListener('click', function () {
            loadDeviceReport().catch(function (e) { alert('加载设备报表失败：' + e.message); });
        });
    }
    if (compareQuery) {
        compareQuery.addEventListener('click', function () {
            loadCompareReport().catch(function (e) { alert('加载办公室对比失败：' + e.message); });
        });
    }
    if (exportBtn) exportBtn.addEventListener('click', exportDeviceCsv);
    window.addEventListener('resize', resizeEchartsStats);
}

if (window.__PAGE__ === 'statistics') {
    initStatisticsPage().catch(function (e) {
        console.error(e);
        alert('统计页初始化失败：' + e.message);
    });
}

async function initAlarmPage() {
    var listEl = $id('alarmList');
    var bodyEl = $id('alarmRulesBody');
    var formEl = $id('alarmRuleForm');
    var scopeTypeEl = $id('ruleScopeType');
    var scopeDeviceEl = $id('ruleScopeDeviceSelect');
    var scopeDeviceWrap = $id('ruleScopeKeyGroup');
    var scopeTypeSelect = $id('ruleScopeTypeSelect');
    var scopeKeyLabel = $id('ruleScopeKeyLabel');
    var vMinEl = $id('ruleVMin');
    var vMaxEl = $id('ruleVMax');
    var pMaxEl = $id('rulePMax');
    var cMaxEl = $id('ruleCMax');

    function syncScopeInputs() {
        var scope = scopeTypeEl ? scopeTypeEl.value : 'ALL';
        if (!scopeDeviceEl || !scopeTypeSelect || !scopeKeyLabel || !scopeDeviceWrap) return;
        scopeDeviceEl.style.display = scope === 'DEVICE' ? '' : 'none';
        scopeTypeSelect.style.display = scope === 'TYPE' ? '' : 'none';
        scopeDeviceWrap.style.display = scope === 'ALL' ? 'none' : '';
        scopeKeyLabel.textContent = scope === 'TYPE' ? '设备类型' : '作用对象';
    }

    async function loadDevices() {
        if (!scopeDeviceEl) return;
        var res = await apiFetch('/alarm/api/devices');
        var devices = res.devices || [];
        scopeDeviceEl.innerHTML = '<option value="">请选择设备</option>' + devices.map(function (d) {
            return '<option value="' + d.id + '">' + escapeHtml(d.name) + '（' + escapeHtml(d.device_type || '其他') + '）</option>';
        }).join('');
    }

    async function loadRecords() {
        var res = await apiFetch('/alarm/api/records');
        var rows = res.records || [];
        if (!listEl) return;
        listEl.innerHTML = rows.map(function (r) {
            var actions = r.status === 'RESOLVED'
                ? '<button class="btn btn-danger btn-sm" data-action="delete" data-id="' + r.id + '">删除</button>'
                : '<button class="btn btn-primary btn-sm" data-action="resolve" data-id="' + r.id + '">标记已处理</button>';
            return '<div class="schedule-item"><div class="schedule-info"><span class="time">' + escapeHtml((r.ts || '').replace('T', ' ').slice(0, 16)) + '</span><span class="target">' + escapeHtml(r.place || '') + '</span><span class="action">' + escapeHtml(r.alarm_type || '') + '</span><span style="font-size:12px;color:var(--text-secondary);">' + escapeHtml(r.message || '') + '</span></div><div style="display:flex; gap:8px;">' + actions + '</div></div>';
        }).join('') || '<div class="empty-state"><div class="empty-icon">🔔</div><p>暂无报警记录</p></div>';
    }

    async function loadRules() {
        var res = await apiFetch('/alarm/api/rules');
        var rows = res.rules || [];
        if (!bodyEl) return;
        bodyEl.innerHTML = rows.map(function (r) {
            var scopeText = r.scope_type === 'ALL' ? '全局' : (r.scope_type === 'TYPE' ? ('类型：' + (r.scope_key || '')) : ('设备 ID：' + (r.scope_key || '')));
            var voltageText = [r.voltage_min != null ? r.voltage_min : '--', r.voltage_max != null ? r.voltage_max : '--'].join(' ~ ');
            return '<tr><td>' + escapeHtml(scopeText) + '</td><td>' + escapeHtml(voltageText) + '</td><td>' + (r.power_max == null ? '--' : Number(r.power_max).toFixed(1)) + '</td><td>' + (r.enabled ? '启用' : '禁用') + '</td><td><button class="btn btn-danger btn-sm" data-action="delete-rule" data-id="' + r.id + '">删除</button></td></tr>';
        }).join('') || '<tr><td colspan="5">暂无阈值规则</td></tr>';
    }

    if (scopeTypeEl) {
        scopeTypeEl.addEventListener('change', syncScopeInputs);
    }

    if (formEl) {
        formEl.addEventListener('submit', async function (e) {
            e.preventDefault();
            var scopeType = scopeTypeEl ? scopeTypeEl.value : 'ALL';
            var scopeKey = '';
            if (scopeType === 'DEVICE') scopeKey = scopeDeviceEl ? scopeDeviceEl.value : '';
            if (scopeType === 'TYPE') scopeKey = scopeTypeSelect ? scopeTypeSelect.value : '';
            await apiFetch('/alarm/api/rules', {
                method: 'POST',
                body: JSON.stringify({
                    scope_type: scopeType,
                    scope_key: scopeKey,
                    voltage_min: vMinEl ? vMinEl.value : '',
                    voltage_max: vMaxEl ? vMaxEl.value : '',
                    power_max: pMaxEl ? pMaxEl.value : '',
                    current_max: cMaxEl ? cMaxEl.value : '',
                }),
            });
            if (vMinEl) vMinEl.value = '';
            if (vMaxEl) vMaxEl.value = '';
            if (pMaxEl) pMaxEl.value = '';
            if (cMaxEl) cMaxEl.value = '';
            await loadRules();
        });
    }

    if (bodyEl) {
        bodyEl.addEventListener('click', async function (e) {
            var btn = e.target.closest('button[data-action="delete-rule"]');
            if (!btn) return;
            await apiFetch('/alarm/api/rules/' + btn.getAttribute('data-id'), { method: 'DELETE' });
            await loadRules();
        });
    }

    if (listEl) {
        listEl.addEventListener('click', async function (e) {
            var btn = e.target.closest('button[data-id]');
            if (!btn) return;
            var action = btn.getAttribute('data-action');
            var id = btn.getAttribute('data-id');
            if (action === 'resolve') {
                await apiFetch('/alarm/api/records/' + id + '/resolve', { method: 'POST' });
            } else if (action === 'delete') {
                await apiFetch('/alarm/api/records/' + id, { method: 'DELETE' });
            }
            await loadRecords();
        });
    }

    await loadDevices();
    syncScopeInputs();
    await loadRecords();
    await loadRules();
}

if (window.__PAGE__ === 'alarm') {
    initAlarmPage().catch(function (e) {
        console.error(e);
        alert('报警页初始化失败：' + e.message);
    });
}

function renderAgentResultPanel(payload) {
    var panel = $id('agentResultPanel');
    if (!panel) return;
    var result = payload.result || {};
    var html = '<div class="agent-result-panel">';
    html += '<div class="agent-result-summary">' + escapeHtml(payload.reply || '') + '</div>';

    if (payload.type === 'query_result') {
        if (payload.intent === 'check_unclosed_devices') {
            html += '<div class="agent-section-label">查询结果</div><div class="agent-result-list">' + (result.offices || []).slice(0, 8).map(function (o) {
                return '<div class="agent-result-item"><div class="agent-result-title">' + escapeHtml(o.display_name || o.name || '') + '</div><div class="agent-result-meta">未关闭设备 ' + Number(o.open_count || 0) + ' 台</div></div>';
            }).join('') + '</div>';
        } else if (payload.intent === 'compare_offices_energy') {
            html += '<div class="agent-section-label">办公室排行</div><div class="agent-result-list">' + (result.rows || []).slice(0, 5).map(function (o) {
                return '<div class="agent-result-item"><div class="agent-result-title">' + escapeHtml(o.display_name || o.office_name || '') + '</div><div class="agent-result-meta">用电 ' + Number(o.energy_kwh || 0).toFixed(3) + ' kWh · 占比 ' + Number(o.share_pct || 0).toFixed(1) + '%</div></div>';
            }).join('') + '</div>';
        } else if (payload.intent === 'office_energy_report') {
            html += '<div class="agent-section-label">办公室报表</div><div class="agent-result-list"><div class="agent-result-item"><div class="agent-result-title">' + escapeHtml(result.office_name || '') + '</div><div class="agent-result-meta">总用电 ' + Number((result.summary || {}).total_kwh || 0).toFixed(3) + ' kWh · 电费 ' + Number((result.summary || {}).total_cost || 0).toFixed(2) + ' 元</div></div></div>';
        } else if (payload.intent === 'device_energy_report') {
            html += '<div class="agent-section-label">设备报表</div><div class="agent-result-list">' + (result.devices || []).slice(0, 5).map(function (d) {
                return '<div class="agent-result-item"><div class="agent-result-title">' + escapeHtml(d.name || '') + '</div><div class="agent-result-meta">用电 ' + Number(d.energy_kwh || 0).toFixed(3) + ' kWh · ' + escapeHtml(d.office_name || '') + '</div></div>';
            }).join('') + '</div>';
        }
    }

    if (payload.type === 'action_result' && payload.intent === 'device_control') {
        html += '<div class="agent-section-label">控制结果</div><div class="agent-result-list"><div class="agent-result-item"><div class="agent-result-title">' + escapeHtml(result.scope === 'device_collection' ? (result.office_name || '批量控制') : (result.target_name || '设备控制')) + '</div><div class="agent-result-meta">' + escapeHtml([
            result.device_type_label || '',
            result.affected != null ? ('影响 ' + result.affected + ' 台') : '',
            result.changed != null ? ('变化 ' + result.changed + ' 台') : '',
        ].filter(Boolean).join(' · ')) + '</div></div></div>';
    }

    if (payload.type === 'action_result' && payload.intent === 'device_schedule') {
        html += '<div class="agent-section-label">定时策略</div><div class="agent-result-list"><div class="agent-result-item"><div class="agent-result-title">' + escapeHtml(result.name || '') + '</div><div class="agent-result-meta">' + escapeHtml([
            result.run_date || '',
            result.time_of_day || '',
            result.repeat_type === 'ONCE' ? '仅一次' : (result.repeat_type || ''),
            result.target_name || result.office_name || '',
        ].filter(Boolean).join(' · ')) + '</div></div></div>';
    }

    html += '</div>';
    panel.innerHTML = html;
}

function renderAgentActionPanel(payload) {
    var panel = $id('agentActionPanel');
    if (!panel) return;
    var pending = payload.pendingAction || {};
    var details = pending.details || [];
    panel.innerHTML = '<div class="agent-action-panel"><div class="agent-action-card"><div class="agent-action-kind">' + escapeHtml(pending.kind_label || '待确认操作') + '</div><div class="agent-action-summary">' + escapeHtml(pending.summary || '') + '</div><div class="agent-action-meta">' + details.map(function (item) {
        return '<div class="agent-action-detail"><span>' + escapeHtml(item.label || '') + '</span><strong>' + escapeHtml(item.value || '') + '</strong></div>';
    }).join('') + '</div><div class="agent-action-buttons"><button type="button" class="btn btn-primary" id="agentConfirmAction">确认执行</button><button type="button" class="btn btn-secondary" id="agentCancelAction">取消</button></div></div></div>';

    var confirmBtn = $id('agentConfirmAction');
    var cancelBtn = $id('agentCancelAction');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', async function () {
            try {
                var res = await apiFetch('/agent/api/actions/confirm', {
                    method: 'POST',
                    body: JSON.stringify({ pendingAction: pending }),
                });
                appendAgentMessage('assistant', res.reply || '已执行。');
                renderAgentResultPanel(res);
                clearAgentActionPanel();
            } catch (err) {
                alert(err.message);
            }
        });
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', function () {
            clearAgentActionPanel();
        });
    }
}

function clearAgentActionPanel() {
    var panel = $id('agentActionPanel');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-state agent-panel-empty"><div class="empty-icon">📝</div><p>识别到控制指令后，这里会展示待确认操作，确认后才会真正执行。</p></div>';
}

function appendAgentMessage(role, text) {
    var messages = $id('agentMessages');
    if (!messages) return;
    var bubble = document.createElement('div');
    bubble.className = 'agent-message ' + role;
    bubble.innerHTML = '<div class="agent-avatar">' + (role === 'user' ? '你' : 'AI') + '</div><div class="agent-bubble">' + escapeHtml(text || '') + '</div>';
    messages.appendChild(bubble);
    messages.scrollTop = messages.scrollHeight;
}

async function initAgentPage() {
    var form = $id('agentInputForm');
    var input = $id('agentInput');
    var exampleList = $id('agentExampleList');
    var clearBtn = $id('agentClearChat');

    function clearResultPanel() {
        var panel = $id('agentResultPanel');
        if (!panel) return;
        panel.innerHTML = '<div class="empty-state agent-panel-empty"><div class="empty-icon">🧠</div><p>发送一条指令后，这里会展示结构化查询结果。</p></div>';
    }

    if (form) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            var text = (input && input.value || '').trim();
            if (!text) return;
            appendAgentMessage('user', text);
            if (input) input.value = '';
            try {
                var res = await apiFetch('/agent/api/chat', {
                    method: 'POST',
                    body: JSON.stringify({ message: text }),
                });
                appendAgentMessage('assistant', res.reply || '已处理。');
                if (res.type === 'pending_action') {
                    renderAgentActionPanel(res);
                } else {
                    clearAgentActionPanel();
                }
                renderAgentResultPanel(res);
            } catch (err) {
                appendAgentMessage('assistant', '处理失败：' + err.message);
            }
        });
    }

    if (exampleList) {
        exampleList.addEventListener('click', function (e) {
            var btn = e.target.closest('.agent-example-chip');
            if (!btn || !input) return;
            input.value = btn.getAttribute('data-prompt') || '';
            input.focus();
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            var messages = $id('agentMessages');
            if (messages) {
                messages.innerHTML = '<div class="agent-message assistant"><div class="agent-avatar">AI</div><div class="agent-bubble">你好，我是智能助手。当前已升级为 LangChain 查询 Agent，并支持先确认、后执行的设备开关控制、办公室批量控制与定时策略创建。</div></div>';
            }
            clearResultPanel();
            clearAgentActionPanel();
        });
    }
}

if (window.__PAGE__ === 'agent') {
    initAgentPage().catch(function (e) {
        console.error(e);
        alert('AI 助手页初始化失败：' + e.message);
    });
}

async function initSimulationDebugPage() {
    var globalForm = $id('simGlobalForm');
    var deviceBody = $id('simDeviceBody');
    var fields = {
        SIM_SAMPLE_INTERVAL_SECONDS: $id('gInterval'),
        SIM_STANDBY_POWER_MAX: $id('gStandby'),
        SIM_VOLTAGE_MIN: $id('gVMin'),
        SIM_VOLTAGE_MAX: $id('gVMax'),
        SIM_POWER_MIN: $id('gPMin'),
        SIM_POWER_MAX: $id('gPMax'),
        SIM_ANOMALY_PROB_VOLTAGE: $id('gProbV'),
        SIM_ANOMALY_PROB_POWER: $id('gProbP'),
    };

    async function loadGlobal() {
        var res = await apiFetch('/monitor/api/simulation/config');
        var cfg = res.config || {};
        Object.keys(fields).forEach(function (key) {
            if (fields[key]) fields[key].value = cfg[key] == null ? '' : cfg[key];
        });
    }

    async function loadDevices() {
        var res = await apiFetch('/monitor/api/simulation/devices');
        var devices = res.devices || [];
        if (!deviceBody) return;
        deviceBody.innerHTML = devices.map(function (d) {
            var cfg = d.config || {};
            return '<tr data-device-id="' + d.id + '"><td>' + escapeHtml(d.name || '') + '</td><td><input data-field="voltage_min" type="number" step="0.1" value="' + (cfg.voltage_min == null ? '' : cfg.voltage_min) + '"> ~ <input data-field="voltage_max" type="number" step="0.1" value="' + (cfg.voltage_max == null ? '' : cfg.voltage_max) + '"></td><td><input data-field="power_min" type="number" step="0.1" value="' + (cfg.power_min == null ? '' : cfg.power_min) + '"> ~ <input data-field="power_max" type="number" step="0.1" value="' + (cfg.power_max == null ? '' : cfg.power_max) + '"></td><td><input data-field="anomaly_prob_voltage" type="number" min="0" max="1" step="0.01" value="' + (cfg.anomaly_prob_voltage == null ? '' : cfg.anomaly_prob_voltage) + '"> / <input data-field="anomaly_prob_power" type="number" min="0" max="1" step="0.01" value="' + (cfg.anomaly_prob_power == null ? '' : cfg.anomaly_prob_power) + '"></td><td><button class="btn btn-primary btn-sm" data-action="save">保存</button></td></tr>';
        }).join('') || '<tr><td colspan="5">当前办公室暂无设备</td></tr>';
    }

    if (globalForm) {
        globalForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var payload = {};
            Object.keys(fields).forEach(function (key) {
                if (fields[key]) payload[key] = fields[key].value;
            });
            await apiFetch('/monitor/api/simulation/config', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            alert('全局参数已保存');
        });
    }

    if (deviceBody) {
        deviceBody.addEventListener('click', async function (e) {
            var btn = e.target.closest('button[data-action="save"]');
            if (!btn) return;
            var tr = btn.closest('tr[data-device-id]');
            if (!tr) return;
            var payload = {};
            tr.querySelectorAll('input[data-field]').forEach(function (input) {
                payload[input.getAttribute('data-field')] = input.value;
            });
            await apiFetch('/monitor/api/simulation/devices/' + tr.getAttribute('data-device-id'), {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            alert('设备参数已保存');
        });
    }

    await loadGlobal();
    await loadDevices();
}

if (window.__PAGE__ === 'simulation_debug') {
    initSimulationDebugPage().catch(function (e) {
        console.error(e);
        alert('模拟调试页初始化失败：' + e.message);
    });
}
