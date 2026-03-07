// ========== 安全工具函数 ==========
/**
 * HTML 转义函数 - 防止 XSS 攻击
 * 将特殊字符转换为 HTML 实体
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 统一错误处理函数
 * @param {Error} error - 错误对象
 * @param {string} userMessage - 用户友好的错误提示
 * @param {boolean} showAlert - 是否显示 alert（默认 true）
 */
function handleFetchError(error, userMessage, showAlert = true) {
    console.error('API Error:', error);
    const message = userMessage || '操作失败，请稍后重试';
    if (showAlert) {
        alert(message);
    }
    return message;
}

/**
 * 安全的 fetch 包装函数
 * 自动处理错误和 JSON 解析
 */
async function safeFetch(url, options = {}, errorMessage = '请求失败') {
    try {
        const response = await fetch(url, options);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return await response.json();
    } catch (error) {
        handleFetchError(error, errorMessage);
        throw error;
    }
}

/**
 * 显示加载状态
 */
function showLoading(message = '加载中...') {
    // 可以在这里添加全局 loading 组件
    console.log('Loading:', message);
}

/**
 * 隐藏加载状态
 */
function hideLoading() {
    console.log('Loading complete');
}

// ========== Global State ==========
var currentView = 'severity';
var currentSeverity = '';
var currentStatus = 'active';
var listData = null;
var batchQueue = [];
var batchIndex = 0;
var currentSurgeryId = null;
var surgeryStartTime = 0;
var selectedAnswer = '';
var selectedAnswers = [];        // 多选题答案数组
var currentQuestionType = 'A1';  // 当前题目类型
var selectedConfidence = 'unsure';
var treeCollapsed = {};          // 折叠状态缓存：{sectionId: true/false}
var timelineTreeData = null;     // 时间视图原始数据缓存（用于前端过滤）
var currentTimeFilter = 'all';   // 当前时间筛选模式
var landmineRecallText = '';     // 地雷盲测回忆文本
var landmineDetail = null;       // 地雷盲测题目详情缓存
var currentQuestionDetail = null; // 当前题目详情缓存（用于反思面板显示原题）
var importPreviewItems = [];     // 外部导入解析预览
var importMeta = null;           // 外部导入解析元数据

// ========== 融合升级全局状态 (Merge & Upgrade) ==========
var currentFusionSourceId = null;    // 当前融合源题目ID
var fusionCandidates = [];           // 融合候选题目列表
var selectedFusionPartners = [];     // 已选择的融合伙伴（最多4个）
var currentFusionId = null;          // 当前融合题ID
var fusionJudgePending = false;      // 是否有待评判的答案

document.addEventListener('DOMContentLoaded', function() { syncAndReload(); });

// ========== Sync ==========
async function syncAndReload() {
    var btn = document.getElementById('syncBtn');
    btn.textContent = '⏳ 同步中...';
    btn.disabled = true;
    try {
        await safeFetch('/api/wrong-answers/sync', { method: 'POST' }, '同步失败');
        await loadStats();
        await loadList();
    } catch(e) {
        console.error('sync error', e);
        handleFetchError(e, '同步数据失败，请检查网络连接');
    } finally {
        btn.textContent = '🔄 同步数据';
        btn.disabled = false;
    }
}

async function loadStats() {
    try {
        const data = await safeFetch('/api/wrong-answers/stats', {}, '加载统计数据失败');
        document.getElementById('statActive').textContent = data.total_active;
        document.getElementById('statCritical').textContent = data.severity_counts.critical || 0;
        document.getElementById('statArchived').textContent = data.total_archived;
        document.getElementById('statRetryRate').textContent = data.retry_correct_rate + '%';
    } catch(e) {
        console.error('stats error', e);
        // 显示默认值
        document.getElementById('statActive').textContent = '-';
        document.getElementById('statCritical').textContent = '-';
        document.getElementById('statArchived').textContent = '-';
        document.getElementById('statRetryRate').textContent = '-';
    }
}

// ========== External Import ==========
function openImportModal() {
    document.getElementById('importModal').classList.remove('hidden');
}

function closeImportModal() {
    document.getElementById('importModal').classList.add('hidden');
}

function clearImportInput() {
    document.getElementById('importRawText').value = '';
    document.getElementById('importFile').value = '';
    document.getElementById('importParseStatus').textContent = '';
    importPreviewItems = [];
    importMeta = null;
    renderImportPreview();
}

function setImportSelection(flag) {
    importPreviewItems.forEach(function(item) { item.__selected = !!flag; });
    renderImportPreview();
}

function updateImportField(index, field, value) {
    var item = importPreviewItems[index];
    if (!item) return;

    if (field === '__selected') {
        item.__selected = !!value;
    } else if (field === 'correct_answer') {
        var ans = (value || '').toUpperCase().trim();
        item.correct_answer = /^[A-E]$/.test(ans) ? ans : '';
    } else if (field === 'chapter_name') {
        item.chapter_name = value || '';
        item.chapter_id = null; // 用户手改章节名后，交给后端重新匹配
    } else if (field === 'question_text') {
        item.question_text = value || '';
    } else if (field === 'key_point') {
        item.key_point = value || '';
    }

    renderImportPreview();
}

async function parseExternalImport() {
    var parseBtn = document.getElementById('importParseBtn');
    var statusEl = document.getElementById('importParseStatus');
    var rawText = (document.getElementById('importRawText').value || '').trim();
    var fileInput = document.getElementById('importFile');
    var fileObj = fileInput.files && fileInput.files.length ? fileInput.files[0] : null;
    var maxItems = parseInt(document.getElementById('importMaxItems').value || '200', 10);

    if (!rawText && !fileObj) {
        alert('请粘贴文本或上传文件');
        return;
    }
    if (isNaN(maxItems) || maxItems < 1) maxItems = 200;

    parseBtn.disabled = true;
    parseBtn.className = 'bg-gray-300 text-white px-4 py-2 rounded text-sm cursor-not-allowed';
    parseBtn.textContent = '解析中...';
    statusEl.textContent = '正在调用 AI 解析，请稍候...';

    try {
        var fd = new FormData();
        if (rawText) fd.append('text', rawText);
        if (fileObj) fd.append('file', fileObj);
        fd.append('max_items', String(maxItems));

        var resp = await safeFetch('/api/wrong-answers/import/parse', {
            method: 'POST',
            body: fd,
            headers: { 'accept': 'application/json' }
        }, '解析失败');

        var data = resp;

        importMeta = data;
        importPreviewItems = (data.items || []).map(function(item) {
            return {
                question_no: item.question_no,
                question_text: item.question_text || '',
                options: item.options || {},
                correct_answer: item.correct_answer || '',
                chapter_name: item.chapter_name || '',
                chapter_id: item.chapter_id || null,
                chapter_label: item.chapter_label || '',
                book_name: item.book_name || '',
                key_point: item.key_point || '',
                question_type: item.question_type || 'A1',
                difficulty: item.difficulty || '基础',
                exists: !!item.exists,
                existing_wrong_id: item.existing_wrong_id || null,
                __selected: true
            };
        });

        statusEl.textContent = '解析完成，可微调后确认导入';
        renderImportPreview();
    } catch (e) {
        console.error('parseExternalImport error', e);
        statusEl.textContent = '解析失败：' + (e.message || '未知错误');
        document.getElementById('importPreviewContainer').innerHTML =
            '<div class="p-8 text-center text-red-500 text-sm">解析失败：' + escapeHtml(e.message || '未知错误') + '</div>';
        document.getElementById('importConfirmBtn').disabled = true;
        document.getElementById('importConfirmBtn').className = 'bg-gray-300 text-white px-4 py-2 rounded text-sm cursor-not-allowed';
    } finally {
        parseBtn.disabled = false;
        parseBtn.className = 'bg-indigo-500 text-white px-4 py-2 rounded hover:bg-indigo-600 text-sm';
        parseBtn.textContent = '开始解析';
    }
}

function renderImportPreview() {
    var summaryEl = document.getElementById('importPreviewSummary');
    var container = document.getElementById('importPreviewContainer');
    var confirmBtn = document.getElementById('importConfirmBtn');

    if (!importPreviewItems.length) {
        summaryEl.textContent = '暂无解析结果';
        container.innerHTML = '<div class="p-8 text-center text-gray-400 text-sm">点击“开始解析”获取预览</div>';
        confirmBtn.disabled = true;
        confirmBtn.className = 'bg-gray-300 text-white px-4 py-2 rounded text-sm cursor-not-allowed';
        return;
    }

    var selectedCount = importPreviewItems.filter(function(item) { return !!item.__selected; }).length;
    var dupCount = importPreviewItems.filter(function(item) { return !!item.exists; }).length;
    var newCount = importPreviewItems.length - dupCount;

    summaryEl.textContent = '共 ' + importPreviewItems.length + ' 题，新增候选 ' + newCount + '，重复 ' + dupCount + '，已选 ' + selectedCount;

    if (selectedCount > 0) {
        confirmBtn.disabled = false;
        confirmBtn.className = 'bg-indigo-500 text-white px-4 py-2 rounded text-sm hover:bg-indigo-600';
    } else {
        confirmBtn.disabled = true;
        confirmBtn.className = 'bg-gray-300 text-white px-4 py-2 rounded text-sm cursor-not-allowed';
    }

    var html = '<table class="min-w-full text-xs">';
    html += '<thead class="sticky top-0 bg-gray-50 border-b">';
    html += '<tr>';
    html += '<th class="px-2 py-2 text-left">导入</th>';
    html += '<th class="px-2 py-2 text-left">题号</th>';
    html += '<th class="px-2 py-2 text-left">题干（可编辑）</th>';
    html += '<th class="px-2 py-2 text-left">选项</th>';
    html += '<th class="px-2 py-2 text-left">答案</th>';
    html += '<th class="px-2 py-2 text-left">章节/知识点</th>';
    html += '<th class="px-2 py-2 text-left">状态</th>';
    html += '</tr></thead><tbody>';

    importPreviewItems.forEach(function(item, i) {
        var rowClass = item.exists ? 'bg-yellow-50' : '';
        var optionKeys = Object.keys(item.options || {}).sort();
        var optionsHtml = optionKeys.map(function(k) {
            return '<div><span class="font-semibold mr-1">' + escapeHtml(k) + '.</span>' + escapeHtml(item.options[k]) + '</div>';
        }).join('');
        if (!optionsHtml) optionsHtml = '<span class="text-gray-400">无</span>';

        var statusBadge = item.exists
            ? '<span class="px-2 py-0.5 rounded bg-yellow-100 text-yellow-700">重复</span>'
            : '<span class="px-2 py-0.5 rounded bg-green-100 text-green-700">新题</span>';

        var chapterHint = item.chapter_label ? escapeHtml(item.chapter_label) : (item.chapter_id ? escapeHtml(item.chapter_id) : '未自动匹配章节');

        html += '<tr class="border-b ' + rowClass + '">';
        html += '<td class="px-2 py-2 align-top"><input type="checkbox" ' + (item.__selected ? 'checked' : '') + ' onchange="updateImportField(' + i + ', \'__selected\', this.checked)"></td>';
        html += '<td class="px-2 py-2 align-top text-gray-500">' + escapeHtml(String(item.question_no || (i + 1))) + '</td>';
        html += '<td class="px-2 py-2 align-top min-w-[320px]"><textarea rows="4" class="w-full border rounded p-2 text-xs" oninput="updateImportField(' + i + ', \'question_text\', this.value)">' + escapeHtml(item.question_text || '') + '</textarea></td>';
        html += '<td class="px-2 py-2 align-top min-w-[220px]"><div class="space-y-1">' + optionsHtml + '</div></td>';
        html += '<td class="px-2 py-2 align-top">';
        html += '<select class="border rounded px-2 py-1 text-xs" onchange="updateImportField(' + i + ', \'correct_answer\', this.value)">';
        html += ['A','B','C','D','E'].map(function(opt) {
            return '<option value="' + opt + '"' + (item.correct_answer === opt ? ' selected' : '') + '>' + opt + '</option>';
        }).join('');
        html += '</select></td>';
        html += '<td class="px-2 py-2 align-top min-w-[230px]">';
        html += '<input value="' + escapeHtml(item.chapter_name || '') + '" placeholder="章节名（可编辑）" class="w-full border rounded px-2 py-1 text-xs mb-1" oninput="updateImportField(' + i + ', \'chapter_name\', this.value)">';
        html += '<input value="' + escapeHtml(item.key_point || '') + '" placeholder="知识点（可选）" class="w-full border rounded px-2 py-1 text-xs mb-1" oninput="updateImportField(' + i + ', \'key_point\', this.value)">';
        html += '<div class="text-[11px] text-gray-400">匹配: ' + chapterHint + '</div>';
        html += '</td>';
        html += '<td class="px-2 py-2 align-top">' + statusBadge + (item.existing_wrong_id ? '<div class="text-[11px] text-gray-400 mt-1">#' + item.existing_wrong_id + '</div>' : '') + '</td>';
        html += '</tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;
}

async function confirmExternalImport() {
    var selected = importPreviewItems.filter(function(item) { return !!item.__selected; });
    if (!selected.length) {
        alert('请至少选择 1 道题');
        return;
    }

    var severity = document.getElementById('importDefaultSeverity').value || 'normal';
    var confirmBtn = document.getElementById('importConfirmBtn');
    var statusEl = document.getElementById('importParseStatus');

    confirmBtn.disabled = true;
    confirmBtn.className = 'bg-gray-300 text-white px-4 py-2 rounded text-sm cursor-not-allowed';
    confirmBtn.textContent = '导入中...';
    statusEl.textContent = '正在写入错题本...';

    var payload = {
        default_severity: severity,
        items: selected.map(function(item) {
            return {
                question_text: item.question_text || '',
                options: item.options || {},
                correct_answer: item.correct_answer || '',
                chapter_name: item.chapter_name || '',
                chapter_id: item.chapter_id || null,
                book_name: item.book_name || '',
                key_point: item.key_point || '',
                explanation: item.explanation || '',
                question_type: item.question_type || 'A1',
                difficulty: item.difficulty || '基础'
            };
        })
    };

    try {
        var data = await safeFetch('/api/wrong-answers/import/confirm', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'accept': 'application/json'
            },
            body: JSON.stringify(payload)
        }, '导入失败');

        var resultMsg = '导入完成：新增' + (data.created || 0) + '，更新' + (data.updated || 0) + '，跳过' + (data.skipped || 0);
        if (data.errors && data.errors.length) {
            resultMsg += '（有' + data.errors.length + '条校验错误）';
        }
        statusEl.textContent = resultMsg;
        alert(resultMsg);

        closeImportModal();
        await loadStats();
        await loadList();
    } catch (e) {
        console.error('confirmExternalImport error', e);
        statusEl.textContent = '导入失败：' + (e.message || '未知错误');
        alert('导入失败：' + (e.message || '未知错误'));
    } finally {
        confirmBtn.disabled = false;
        confirmBtn.className = 'bg-indigo-500 text-white px-4 py-2 rounded text-sm hover:bg-indigo-600';
        confirmBtn.textContent = '确认导入';
    }
}

function switchView(view) {
    currentView = view;
    ['severity','chapter','timeline'].forEach(function(v) {
        var btn = document.getElementById('tab-' + v);
        if (v === view) btn.className = 'px-4 py-2 rounded text-sm font-medium bg-red-500 text-white';
        else btn.className = 'px-4 py-2 rounded text-sm font-medium text-gray-600 hover:bg-gray-100';
    });
    // 时间视图工具栏联
    document.getElementById('timelineToolbar').classList.toggle('hidden', view !== 'timeline');
    loadList();
}

function toggleSeverity(sev) {
    currentSeverity = sev;
    document.querySelectorAll('.sev-btn').forEach(function(btn) {
        if (btn.dataset.sev === sev) btn.className = 'sev-btn px-2 py-1 rounded text-xs bg-gray-200 text-gray-700 font-medium';
        else btn.className = 'sev-btn px-2 py-1 rounded text-xs text-gray-600 hover:bg-gray-100';
    });
    loadList();
}

function applyFilters() {
    currentStatus = document.getElementById('statusFilter').value;
    loadList();
}

async function loadList() {
    var container = document.getElementById('listContainer');
    container.innerHTML = '<div class="p-8 text-center text-gray-400">加载中...</div>';
    try {
        var url = '/api/wrong-answers/list?view=' + currentView + '&status=' + currentStatus;
        if (currentSeverity) url += '&severity=' + currentSeverity;
        listData = await safeFetch(url, {}, '加载错题列表失败');
        if (currentView === 'severity') renderSeverityView(listData);
        else if (currentView === 'chapter') renderChapterView(listData);
        else if (currentView === 'timeline') renderTimelineView(listData);
    } catch(e) {
        container.innerHTML = '<div class="p-8 text-center text-red-400">加载失败，请刷新重试</div>';
        console.error('loadList error:', e);
    }
}

// ========== Renderers ==========
function renderSeverityView(data) {
    var container = document.getElementById('listContainer');
    if (!data.items || data.items.length === 0) {
        container.innerHTML = '<div class="p-8 text-center text-gray-400">\u6682\u65e0\u9519\u9898\uff0c\u7ee7\u7eed\u4fdd\u6301 \ud83c\udf89</div>';
        checkFusionChallengeReady();
        return;
    }
    var html = '';
    data.items.forEach(function(item) { html += renderCard(item); });
    container.innerHTML = html;
}

// 检查是否显示融合挑战提示
async function checkFusionChallengeReady() {
    try {
        var queue = await safeFetch('/api/fusion/queue?limit=1', {}, '请求失败');

        if (queue.length > 0) {
            var shouldStart = confirm(
                '✅ 基础认知已激活，是否开启今日 🔥 融合挑战？\n\n' +
                '待复习的融合题: ' + queue.length + ' 道'
            );
            if (shouldStart) {
                openFusionAnswer(queue[0].id, queue[0].question_text, queue[0].fusion_level, []);
            }
        }
    } catch (e) {
        console.log('Fusion check failed:', e);
    }
}

function renderChapterView(data) {
    var container = document.getElementById('listContainer');
    if (!data.tree || Object.keys(data.tree).length === 0) {
        container.innerHTML = '<div class="p-8 text-center text-gray-400">\u6682\u65e0\u9519\u9898</div>';
        return;
    }
    var html = '';
    var bookIdx = 0;
    for (var book in data.tree) {
        var bookData = data.tree[book];
        var bs = bookData._stats || {};
        var bookId = 'ch-book-' + bookIdx;
        var bookOpen = !treeCollapsed[bookId];
        html += '<div class="border-b">';
        // 科目
        html += '<div class="px-6 py-3 bg-blue-50 cursor-pointer hover:bg-blue-100 flex items-center justify-between" onclick="toggleSection(\'' + bookId + '\')">';
        html += '<div class="flex items-center space-x-2">';
        html += '<span id="arrow-' + bookId + '" class="text-sm text-gray-400">' + (bookOpen ? '\u25bc' : '\u25b6') + '</span>';
        html += '<span class="font-bold text-blue-800">\ud83d\udcda ' + book + '</span>';
        html += '</div>';
        html += '<div class="flex items-center space-x-3 text-xs">';
        html += '<span class="text-gray-500">' + (bs.total || 0) + '\u9898</span>';
        if (bs.critical) html += '<span class="text-red-600 font-bold">\ud83d\udea8' + bs.critical + '</span>';
        html += '<span class="text-gray-400">\u7d2f\u8ba1\u9519' + (bs.error_sum || 0) + '\u6b21</span>';
        html += '</div></div>';
        // 章节列表
        html += '<div id="' + bookId + '" class="' + (bookOpen ? '' : 'hidden') + '">';
        var chIdx = 0;
        var chapters = bookData.chapters || {};
        for (var ch in chapters) {
            var chData = chapters[ch];
            var cs = chData._stats || {};
            var chId = bookId + '-ch-' + chIdx;
            var chOpen = !treeCollapsed[chId];
            html += '<div class="border-t">';
            // 章节
            html += '<div class="px-8 py-2 bg-gray-50 cursor-pointer hover:bg-gray-100 flex items-center justify-between" onclick="toggleSection(\'' + chId + '\')">';
            html += '<div class="flex items-center space-x-2">';
            html += '<span id="arrow-' + chId + '" class="text-xs text-gray-400">' + (chOpen ? '\u25bc' : '\u25b6') + '</span>';
            html += '<span class="text-sm font-medium text-gray-700">\ud83d\udcd6 ' + ch + '</span>';
            html += '</div>';
            html += '<div class="flex items-center space-x-3 text-xs">';
            html += '<span class="text-gray-500">' + (cs.total || 0) + '\u9898</span>';
            if (cs.critical) html += '<span class="text-red-600 font-bold">\ud83d\udea8' + cs.critical + '</span>';
            html += '<span class="text-gray-400">\u7d2f\u8ba1\u9519' + (cs.error_sum || 0) + '\u6b21</span>';
            html += '</div></div>';
            // 知识点 + 错题卡片
            html += '<div id="' + chId + '" class="' + (chOpen ? '' : 'hidden') + '">';
            var kps = chData.key_points || {};
            for (var kp in kps) {
                html += '<div class="px-10 py-1 text-xs text-gray-500 border-t border-dashed bg-white">\ud83d\udccc ' + kp + ' (' + kps[kp].length + ')</div>';
                kps[kp].forEach(function(item) { html += renderCard(item); });
            }
            html += '</div></div>';
            chIdx++;
        }
        html += '</div></div>';
        bookIdx++;
    }
    container.innerHTML = html;
}

function renderTimelineView(data) {
    var container = document.getElementById('listContainer');
    var tree = data.tree || {};
    var currentMonth = data.current_month || '';
    timelineTreeData = data; // 缓存用于前端过滤

    var months = Object.keys(tree);
    if (months.length === 0) {
        container.innerHTML = '<div class="p-8 text-center text-gray-400">\u6682\u65e0\u9519\u9898</div>';
        return;
    }

    // 应用时间筛
    var filteredMonths = months;
    if (currentTimeFilter === '7d') {
        var cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - 7);
        var cutoffStr = cutoff.toISOString().slice(0, 10);
        filteredMonths = months.filter(function(m) {
            var dates = Object.keys(tree[m].dates || {});
            return dates.some(function(d) { return d >= cutoffStr; });
        });
    } else if (currentTimeFilter === 'month') {
        filteredMonths = currentMonth ? months.filter(function(m) { return m === currentMonth; }) : months.slice(0, 1);
    } else if (currentTimeFilter === 'pick') {
        var picked = document.getElementById('monthPicker').value;
        if (picked) filteredMonths = months.filter(function(m) { return m === picked; });
    }

    if (filteredMonths.length === 0) {
        container.innerHTML = '<div class="p-8 text-center text-gray-400">\u8be5\u65f6\u95f4\u8303\u56f4\u5185\u65e0\u9519\u9898</div>';
        return;
    }

    var html = '';
    filteredMonths.forEach(function(monthKey, mi) {
        var monthData = tree[monthKey];
        var ms = monthData._stats || {};
        var monthId = 'tl-month-' + mi;
        // 默认展开当前月或筛选后只有一个月
        var defaultOpen = (monthKey === currentMonth) || filteredMonths.length === 1;
        var monthOpen = treeCollapsed[monthId] === undefined ? defaultOpen : !treeCollapsed[monthId];

        // 月份标题
        var parts = monthKey.split('-');
        var monthLabel = parts.length === 2 ? parts[0] + '\u5e74' + parseInt(parts[1]) + '\u6708' : monthKey;

        html += '<div class="border-b">';
        html += '<div class="px-6 py-3 bg-purple-50 cursor-pointer hover:bg-purple-100 flex items-center justify-between" onclick="toggleSection(\'' + monthId + '\')">';
        html += '<div class="flex items-center space-x-2">';
        html += '<span id="arrow-' + monthId + '" class="text-sm text-gray-400">' + (monthOpen ? '\u25bc' : '\u25b6') + '</span>';
        html += '<span class="font-bold text-purple-800">\ud83d\udcc5 ' + monthLabel + '</span>';
        html += '</div>';
        html += '<div class="flex items-center space-x-3 text-xs">';
        html += '<span class="text-gray-500">' + (ms.total || 0) + '\u9898</span>';
        if (ms.critical) html += '<span class="text-red-600 font-bold">\ud83d\udea8' + ms.critical + '</span>';
        html += '</div></div>';

        // 日期列表
        html += '<div id="' + monthId + '" class="' + (monthOpen ? '' : 'hidden') + '">';
        var dates = Object.keys(monthData.dates || {}).sort().reverse();

        // 7天模式下过滤日期
        if (currentTimeFilter === '7d') {
            dates = dates.filter(function(d) { return d >= cutoffStr; });
        }

        dates.forEach(function(dateKey, di) {
            var items = monthData.dates[dateKey] || [];
            var dateId = monthId + '-d-' + di;
            var dateOpen = !treeCollapsed[dateId];

            // 日期标签（周三）
            var dd = new Date(dateKey + 'T00:00:00');
            var weekdays = ['\u5468\u65e5','\u5468\u4e00','\u5468\u4e8c','\u5468\u4e09','\u5468\u56db','\u5468\u4e94','\u5468\u516d'];
            var dateLabel = (dd.getMonth() + 1) + '\u6708' + dd.getDate() + '\u65e5 ' + weekdays[dd.getDay()];

            html += '<div class="border-t">';
            html += '<div class="px-8 py-2 bg-gray-50 cursor-pointer hover:bg-gray-100 flex items-center justify-between" onclick="toggleSection(\'' + dateId + '\')">';
            html += '<div class="flex items-center space-x-2">';
            html += '<span id="arrow-' + dateId + '" class="text-xs text-gray-400">' + (dateOpen ? '\u25bc' : '\u25b6') + '</span>';
            html += '<span class="text-sm font-medium text-gray-700">' + dateLabel + '</span>';
            html += '</div>';
            html += '<span class="text-xs text-gray-400">' + items.length + '\u9898</span>';
            html += '</div>';

            html += '<div id="' + dateId + '" class="' + (dateOpen ? '' : 'hidden') + '">';
            items.forEach(function(item) { html += renderCard(item); });
            html += '</div></div>';
        });
        html += '</div></div>';
    });
    container.innerHTML = html;
}

function renderCard(item) {
    var badge = severityBadge(item.severity_tag);
    var retryInfo = '';
    if (item.retry_count > 0) {
        retryInfo = '<span class="text-xs ' + (item.last_retry_correct ? 'text-green-500' : 'text-red-500') + '">\u91cd\u505a' + item.retry_count + '\u6b21 ' + (item.last_retry_correct ? '\u2713' : '\u2717') + '</span>';
    }
    var statusBadge = '';
    if (item.mastery_status === 'archived') {
        statusBadge = '<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded text-xs ml-2">\u5df2\u5f52\u6863</span>';
    }
    return '<div class="px-6 py-4 hover:bg-gray-50 transition border-b last:border-b-0">' +
        '<div class="flex items-center justify-between mb-2">' +
            '<div class="flex items-center space-x-2 flex-1 min-w-0">' +
                badge + statusBadge +
                '<span class="text-xs text-gray-400">' + (item.question_type || 'A1') + ' | ' + (item.difficulty || '\u57fa\u7840') + '</span>' +
                '<span class="text-xs text-red-500 font-bold">\u9519' + item.error_count + '\u6b21</span>' +
                retryInfo +
            '</div>' +
            '<div class="flex items-center space-x-2 flex-shrink-0 ml-2">' +
                (item.mastery_status === 'archived' && !item.is_fusion ?
                    '<button onclick="event.stopPropagation(); checkFusionUnlock(' + item.id + ')" ' +
                    'class="bg-orange-50 text-orange-600 px-3 py-1 rounded text-sm hover:bg-orange-100" ' +
                    'title="将已掌握的概念与其他概念融合">🔍 融合</button>' : '') +
                '<button onclick="openSurgery(' + item.id + ')" class="bg-red-50 text-red-600 px-3 py-1 rounded text-sm hover:bg-red-100">🔬 重做</button>' +
            '</div>' +
        '</div>' +
        '<div class="text-sm text-gray-800 mb-1">' + item.question_preview + '</div>' +
        '<div class="flex items-center space-x-2 text-xs text-gray-400">' +
            (item.key_point ? '<span>📌 ' + item.key_point + '</span>' : '') +
            (item.last_wrong_at ? '<span>' + formatTimeAgo(item.last_wrong_at) + '</span>' : '') +
        '</div>' +
    '</div>';
}

// ========== Surgery Modal ==========
var variantData = null;  // 缓存变式题数

async function openSurgery(id) {
    currentSurgeryId = id;
    batchQueue = [id];
    batchIndex = 0;
    variantData = null;
    document.getElementById('surgeryModal').classList.remove('hidden');
    document.getElementById('surgeryProgress').textContent = '';

    // 先获取详情判断 severity
    try {
        var detail = await safeFetch('/api/wrong-answers/' + id, {}, '请求失败');
        if (detail.severity_tag === 'critical') {
            await renderVariantPhase1(id, detail);
        } else if (detail.severity_tag === 'landmine') {
            renderLandminePhase1(id, detail);
        } else {
            await renderPhase1(id);
        }
    } catch(e) {
        await renderPhase1(id);
    }
}

async function openBatchSurgery() {
    try {
        var data = await safeFetch('/api/wrong-answers/retry-batch/next?count=5', {}, '请求失败');
        if (!data.items || data.items.length === 0) {
            alert('\u6682\u65e0\u5f85\u91cd\u505a\u7684\u9519\u9898');
            return;
        }
        batchQueue = data.items.map(function(i) { return i.id; });
        batchIndex = 0;
        currentSurgeryId = batchQueue[0];
        document.getElementById('surgeryModal').classList.remove('hidden');
        updateBatchProgress();
        await renderPhase1(batchQueue[0]);
    } catch(e) { alert('\u52a0\u8f7d\u5931\u8d25'); }
}

function updateBatchProgress() {
    if (batchQueue.length > 1) {
        document.getElementById('surgeryProgress').textContent = '\u7b2c ' + (batchIndex + 1) + ' / ' + batchQueue.length + ' \u9898';
    }
}

async function renderPhase1(id) {
    var content = document.getElementById('surgeryContent');
    content.innerHTML = '<div class="text-center text-gray-400 py-8">\u52a0\u8f7d\u9898\u76ee...</div>';
    surgeryStartTime = Date.now();
    selectedAnswer = '';
    selectedAnswers = [];
    selectedConfidence = 'unsure';

    try {
        var data = await safeFetch('/api/wrong-answers/' + id, {}, '请求失败');
        currentSurgeryId = id;
        currentQuestionType = data.question_type || 'A1';
        currentQuestionDetail = data; // 缓存原题数据，用于反思面板显示

        var badge = severityBadge(data.severity_tag);
        var html = '<div class="mb-4">' + badge +
            '<span class="text-xs text-gray-400 ml-2">[' + (data.question_type || 'A1') + '] [' + (data.difficulty || '\u57fa\u7840') + '] \u9519' + data.error_count + '\u6b21</span>' +
            (data.key_point ? '<span class="text-xs text-blue-500 ml-2">\ud83d\udccc ' + data.key_point + '</span>' : '') +
            '</div>';

        html += '<div class="bg-gray-50 rounded-lg p-4 mb-4"><div class="text-base leading-relaxed">' + data.question_text + '</div></div>';

        // 多选题提示
        var isMultiple = currentQuestionType === 'X';
        if (isMultiple) {
            html += '<div class="bg-blue-50 border border-blue-200 rounded-lg p-2 mb-3 text-sm text-blue-700">' +
                '💡 这是多选题，可以选择多个选项（点击选项可切换选中状态）' +
                '</div>';
        }

        // Options (no correct answer shown)
        html += '<div class="space-y-2 mb-6">';
        if (data.options) {
            for (var opt in data.options) {
                html += '<label class="block p-3 border rounded-lg cursor-pointer hover:bg-blue-50 transition option-label" data-opt="' + opt + '" onclick="selectOption(this,\'' + opt + '\')">' +
                    '<span class="font-bold mr-2">' + opt + '.</span>' + data.options[opt] +
                '</label>';
            }
        }
        html += '</div>';

        // Confidence
        html += '<div class="mb-4"><div class="text-sm text-gray-500 mb-2">\u4f60\u7684\u628a\u63e1\uff1a</div>' +
            '<div class="flex space-x-2">' +
                '<button onclick="setConfidence(\'sure\')" id="conf-sure" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-green-50">Q 确定</button>' +
                '<button onclick="setConfidence(\'unsure\')" id="conf-unsure" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-yellow-50">W 模糊</button>' +
                '<button onclick="setConfidence(\'no\')" id="conf-no" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-red-50">E 不会</button>' +
            '</div></div>';

        // Submit
        html += '<button onclick="submitRetry()" id="submitRetryBtn" disabled class="w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed">\u8bf7\u5148\u9009\u62e9\u7b54\u6848</button>';

        content.innerHTML = html;
    } catch(e) {
        content.innerHTML = '<div class="text-center text-red-400 py-8">\u52a0\u8f7d\u5931\u8d25</div>';
    }
}

function selectOption(el, opt) {
    var isMultiple = currentQuestionType === 'X';

    if (isMultiple) {
        // 多选题逻辑：切换选中状态
        var idx = selectedAnswers.indexOf(opt);
        if (idx > -1) {
            // 已选中，取消选中
            selectedAnswers.splice(idx, 1);
            el.classList.remove('bg-blue-100', 'border-blue-500');
        } else {
            // 未选中，添加选中
            selectedAnswers.push(opt);
            el.classList.add('bg-blue-100', 'border-blue-500');
        }
        // 按字母顺序排序
        selectedAnswers.sort();
        selectedAnswer = selectedAnswers.join('');
    } else {
        // 单选题逻辑：如果已选中则取消，否则选中
        if (selectedAnswer === opt) {
            // 已选中该选项，取消选择
            selectedAnswer = '';
            selectedAnswers = [];
            el.classList.remove('bg-blue-100', 'border-blue-500');
            // 取消radio选中
            var radio = el.querySelector('input[type="radio"]');
            if (radio) radio.checked = false;
        } else {
            // 选中该选项
            selectedAnswer = opt;
            selectedAnswers = [opt];
            document.querySelectorAll('.option-label').forEach(function(label) {
                label.classList.remove('bg-blue-100', 'border-blue-500');
            });
            el.classList.add('bg-blue-100', 'border-blue-500');
        }
    }

    // 更新提交按钮状态
    var btn = document.getElementById('submitRetryBtn') || document.getElementById('variantNextBtn');
    if (btn) {
        if (selectedAnswer) {
            btn.disabled = false;
            btn.className = 'w-full bg-red-500 text-white py-3 rounded-lg font-bold text-lg hover:bg-red-600 cursor-pointer';
            btn.textContent = btn.id === 'variantNextBtn' ? '\u2192 \u9501\u5b9a\u7b54\u6848\uff0c\u8fdb\u5165\u81ea\u8bc1' : '\u63d0\u4ea4\u7b54\u6848';
        } else {
            btn.disabled = true;
            btn.className = 'w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed';
            btn.textContent = '\u8bf7\u5148\u9009\u62e9\u7b54\u6848';
        }
    }
}

function setConfidence(conf) {
    // 如果已选中相同等级，则取消选择
    if (selectedConfidence === conf) {
        selectedConfidence = '';
        document.querySelectorAll('.conf-btn').forEach(function(btn) {
            btn.classList.remove('bg-green-100', 'bg-yellow-100', 'bg-red-100', 'border-green-500', 'border-yellow-500', 'border-red-500');
        });
        return;
    }

    selectedConfidence = conf;
    document.querySelectorAll('.conf-btn').forEach(function(btn) {
        btn.classList.remove('bg-green-100', 'bg-yellow-100', 'bg-red-100', 'border-green-500', 'border-yellow-500', 'border-red-500');
    });
    var el = document.getElementById('conf-' + conf);
    var colors = { sure: ['bg-green-100', 'border-green-500'], unsure: ['bg-yellow-100', 'border-yellow-500'], no: ['bg-red-100', 'border-red-500'] };
    if (colors[conf]) colors[conf].forEach(function(c) { el.classList.add(c); });
}

// ========== Submit & Phase 2 ==========
async function submitRetry() {
    if (!selectedAnswer || !currentSurgeryId) return;
    var btn = document.getElementById('submitRetryBtn');
    btn.disabled = true;
    btn.textContent = '\u5224\u5b9a\u4e2d...';

    var elapsed = Math.round((Date.now() - surgeryStartTime) / 1000);
    try {
        var result = await safeFetch('/api/wrong-answers/' + currentSurgeryId + '/retry', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_answer: selectedAnswer,
                confidence: selectedConfidence,
                time_spent_seconds: elapsed
            })
        }, '请求失败');
        renderPhase2(result);
    } catch(e) {
        btn.textContent = '\u63d0\u4ea4\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5';
        btn.disabled = false;
    }
}

function renderPhase2(result) {
    var content = document.getElementById('surgeryContent');
    var isCorrect = result.is_correct;
    var confLabels = { sure: 'Q 确定', unsure: 'W 模糊', no: 'E 不会' };

    var html = '<div class="text-center mb-6">';
    if (isCorrect) {
        html += '<div class="text-6xl mb-2">\u2705</div><div class="text-2xl font-bold text-green-600">\u56de\u7b54\u6b63\u786e\uff01</div>';
    } else {
        html += '<div class="text-6xl mb-2">\u274c</div><div class="text-2xl font-bold text-red-600">\u56de\u7b54\u9519\u8bef</div>';
    }
    html += '</div>';

    // Comparison
    html += '<div class="grid grid-cols-2 gap-4 mb-4">' +
        '<div class="p-4 rounded-lg ' + (isCorrect ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200') + '">' +
            '<div class="text-xs text-gray-500 mb-1">\u8fd9\u6b21\u9009\u62e9</div>' +
            '<div class="text-xl font-bold ' + (isCorrect ? 'text-green-600' : 'text-red-600') + '">' + selectedAnswer + ' (' + (confLabels[selectedConfidence] || selectedConfidence) + ')</div>' +
        '</div>' +
        '<div class="p-4 rounded-lg bg-green-50 border border-green-200">' +
            '<div class="text-xs text-gray-500 mb-1">\u6b63\u786e\u7b54\u6848</div>' +
            '<div class="text-xl font-bold text-green-600">' + result.correct_answer + '</div>' +
        '</div>' +
    '</div>';

    // Previous attempts
    if (result.previous_attempts && result.previous_attempts.length > 1) {
        html += '<div class="bg-gray-50 rounded-lg p-3 mb-4"><div class="text-xs text-gray-500 mb-2">\u5386\u53f2\u91cd\u505a\u8bb0\u5f55</div>';
        result.previous_attempts.slice(0, 5).forEach(function(a, i) {
            var icon = a.is_correct ? '\u2705' : '\u274c';
            html += '<div class="text-sm">' + icon + ' \u7b2c' + (i + 1) + '\u6b21: \u9009' + a.user_answer + ' (' + (confLabels[a.confidence] || '') + ')</div>';
        });
        html += '</div>';
    }

    // Original question display for reflection
    if (currentQuestionDetail) {
        html += '<div class="mb-4 bg-indigo-50 border border-indigo-200 rounded-lg p-4">' +
            '<div class="text-sm text-indigo-700 font-bold mb-2">\ud83d\udccc \u539f\u9898\u56de\u987e</div>' +
            '<div class="text-sm text-gray-700 leading-relaxed mb-3">' + escapeHtml(currentQuestionDetail.question_text) + '</div>';

        var hasOption = false;
        var optionsHtml = '';
        if (currentQuestionDetail.options) {
            for (var opt in currentQuestionDetail.options) {
                if (!currentQuestionDetail.options[opt]) continue;
                hasOption = true;
                var isCorrect = opt === result.correct_answer;
                var isUserChoice = opt === selectedAnswer;
                var optClass = isCorrect ? 'text-green-700 font-bold' : (isUserChoice ? 'text-red-600' : 'text-gray-700');
                var optIcon = isCorrect ? ' \u2705' : (isUserChoice ? ' \u274c' : '');
                optionsHtml += '<div class="text-sm ' + optClass + '"><span class="font-bold mr-1">' + escapeHtml(opt) + '.</span>' +
                    escapeHtml(currentQuestionDetail.options[opt]) + optIcon + '</div>';
            }
        }
        if (hasOption) {
            html += '<div class="space-y-1 bg-white rounded p-2">' + optionsHtml + '</div>';
        }
        html += '</div>';
    }

    // Explanation (micro-friction for wrong/unsure/no)
    if (result.explanation) {
        html += buildExplanationSection(
            result.explanation,
            '\ud83d\udcd6 \u89e3\u6790',
            isCorrect,
            selectedConfidence,
            'retry'
        );
    }

    // Action buttons
    html += '<div class="flex space-x-3">';
    if (result.can_archive) {
        html += '<button onclick="archiveAndNext()" class="flex-1 bg-green-500 text-white py-3 rounded-lg font-bold hover:bg-green-600">\u2705 \u6807\u8bb0\u5df2\u638c\u63e1</button>';
    }
    if (batchQueue.length > 1 && batchIndex < batchQueue.length - 1) {
        html += '<button onclick="nextQuestion()" class="flex-1 bg-blue-500 text-white py-3 rounded-lg font-bold hover:bg-blue-600">\u2192 \u4e0b\u4e00\u9898</button>';
    } else {
        html += '<button onclick="closeSurgery()" class="flex-1 bg-gray-500 text-white py-3 rounded-lg font-bold hover:bg-gray-600">\u5173\u95ed</button>';
    }
    html += '</div>';

    content.innerHTML = html;
}

async function archiveAndNext() {
    if (!currentSurgeryId) return;
    await safeFetch('/api/wrong-answers/' + currentSurgeryId + '/archive', { method: 'POST' }, '操作失败');
    if (batchQueue.length > 1 && batchIndex < batchQueue.length - 1) {
        nextQuestion();
    } else {
        closeSurgery();
    }
}

function nextQuestion() {
    batchIndex++;
    if (batchIndex < batchQueue.length) {
        currentSurgeryId = batchQueue[batchIndex];
        updateBatchProgress();
        renderPhase1(batchQueue[batchIndex]);
    } else {
        closeSurgery();
    }
}

function closeSurgery() {
    document.getElementById('surgeryModal').classList.add('hidden');
    currentSurgeryId = null;
    selectedAnswer = '';
    selectedConfidence = 'unsure';
    variantData = null;
    landmineRecallText = '';
    landmineDetail = null;
    currentQuestionDetail = null; // 清理题目缓存
    batchQueue = [];
    batchIndex = 0;
    loadStats();
    loadList();
}

// ========== Variant Surgery (3-Phase) ==========

async function renderVariantPhase1(id, detail) {
    var content = document.getElementById('surgeryContent');
    content.innerHTML = '<div class="text-center text-gray-400 py-8">\ud83e\uddec \u6b63\u5728\u751f\u6210\u53d8\u5f0f\u9898...</div>';
    surgeryStartTime = Date.now();
    selectedAnswer = '';
    selectedAnswers = [];
    currentQuestionType = detail && detail.question_type ? detail.question_type : 'A1';
    selectedConfidence = 'unsure';
    document.getElementById('surgeryTitle').innerHTML = '\ud83e\uddec \u53d8\u5f0f\u624b\u672f\u53f0 <span class="text-sm font-normal text-red-400">(\u81f4\u547d\u76f2\u533a)</span>';

    try {
        variantData = await safeFetch('/api/wrong-answers/' + id + '/variant/generate', { method: 'POST' }, '变式生成失败');
        // Keep original question context for Phase 2 rationale
        variantData.original_question_text = detail && detail.question_text ? detail.question_text : '';
        variantData.original_options = detail && detail.options ? detail.options : {};

        var badge = severityBadge('critical');
        var html = '<div class="mb-3">' + badge +
            '<span class="bg-purple-100 text-purple-700 px-2 py-0.5 rounded text-xs font-bold ml-2">\ud83e\uddec ' + (variantData.transform_type || '\u53d8\u5f0f\u9898') + '</span>' +
            '</div>';

        html += '<div class="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-4 text-sm text-yellow-800">' +
            '\u26a0\ufe0f \u8fd9\u662f AI \u751f\u6210\u7684<b>\u53d8\u5f0f\u9898</b>\uff0c\u4e0d\u662f\u539f\u9898\uff01\u8003\u5bdf\u7684\u77e5\u8bc6\u70b9\u76f8\u540c\uff0c\u4f46\u573a\u666f/\u9009\u9879\u5df2\u6539\u53d8\u3002' +
            (variantData.core_knowledge ? '<br>\u6838\u5fc3\u8003\u70b9\uff1a<b>' + variantData.core_knowledge + '</b>' : '') +
            '</div>';

        html += '<div class="bg-gray-50 rounded-lg p-4 mb-4"><div class="text-base leading-relaxed">' + variantData.variant_question + '</div></div>';

        // 多选题提示
        var isMultiple = currentQuestionType === 'X';
        if (isMultiple) {
            html += '<div class="bg-blue-50 border border-blue-200 rounded-lg p-2 mb-3 text-sm text-blue-700">' +
                '💡 这是多选题，可以选择多个选项（点击选项可切换选中状态）' +
                '</div>';
        }

        // Options
        html += '<div class="space-y-2 mb-6">';
        if (variantData.variant_options) {
            for (var opt in variantData.variant_options) {
                html += '<label class="block p-3 border rounded-lg cursor-pointer hover:bg-blue-50 transition option-label" data-opt="' + opt + '" onclick="selectOption(this,\'' + opt + '\')">' +
                    '<span class="font-bold mr-2">' + opt + '.</span>' + variantData.variant_options[opt] +
                '</label>';
            }
        }
        html += '</div>';

        // Confidence
        html += '<div class="mb-4"><div class="text-sm text-gray-500 mb-2">\u4f60\u7684\u628a\u63e1\uff1a</div>' +
            '<div class="flex space-x-2">' +
                '<button onclick="setConfidence(\'sure\')" id="conf-sure" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-green-50">Q 确定</button>' +
                '<button onclick="setConfidence(\'unsure\')" id="conf-unsure" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-yellow-50">W 模糊</button>' +
                '<button onclick="setConfidence(\'no\')" id="conf-no" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-red-50">E 不会</button>' +
            '</div></div>';

        // Next button (goes to Phase 2, not submit)
        html += '<button onclick="renderVariantPhase2()" id="variantNextBtn" disabled class="w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed">\u8bf7\u5148\u9009\u62e9\u7b54\u6848</button>';

        content.innerHTML = html;
    } catch(e) {
        content.innerHTML = '<div class="text-center py-8 text-red-400">\u53d8\u5f0f\u751f\u6210\u5931\u8d25<br><button onclick="renderPhase1(' + id + ')" class="text-blue-500 underline mt-2">\u56de\u9000\u5230\u666e\u901a\u91cd\u505a</button></div>';
    }
}

function renderVariantPhase2() {
    if (!selectedAnswer) return;
    var content = document.getElementById('surgeryContent');
    var originalQuestionText = variantData && variantData.original_question_text ? variantData.original_question_text : '';
    var originalOptions = variantData && variantData.original_options ? variantData.original_options : {};

    var html = '<div class="mb-4">' +
        '<div class="bg-blue-50 border border-blue-200 rounded-lg p-4">' +
            '<div class="text-sm text-blue-700 font-bold mb-2">\ud83d\udd12 Phase 2: \u903b\u8f91\u81ea\u8bc1</div>' +
            '<div class="text-sm text-blue-600">\u4f60\u7684\u9009\u62e9\u5df2\u9501\u5b9a\uff0c\u73b0\u5728\u8bf7\u89e3\u91ca\u4f60\u7684\u63a8\u7406\u8fc7\u7a0b\u3002</div>' +
        '</div>' +
    '</div>';

    if (originalQuestionText) {
        html += '<div class="mb-4 bg-indigo-50 border border-indigo-200 rounded-lg p-4">' +
            '<div class="text-sm text-indigo-700 font-bold mb-2">\ud83d\udccc \u539f\u9898\u56de\u770b\uff08\u4ec5\u4f9b\u81ea\u8ff0\uff0c\u4e0d\u542b\u7b54\u6848\uff09</div>' +
            '<div class="text-sm text-gray-700 leading-relaxed mb-3">' + escapeHtml(originalQuestionText) + '</div>';

        var hasOption = false;
        var optionsHtml = '';
        for (var opt in originalOptions) {
            if (!originalOptions[opt]) continue;
            hasOption = true;
            optionsHtml += '<div class="text-sm text-gray-700"><span class="font-bold mr-1">' + escapeHtml(opt) + '.</span>' +
                escapeHtml(originalOptions[opt]) + '</div>';
        }
        if (hasOption) {
            html += '<div class="space-y-1">' + optionsHtml + '</div>';
        }
        html += '</div>';
    }

    // Show locked answer
    var confLabels = { sure: 'Q 确定', unsure: 'W 模糊', no: 'E 不会' };
    html += '<div class="grid grid-cols-2 gap-4 mb-4">' +
        '<div class="p-3 bg-gray-50 rounded-lg border">' +
            '<div class="text-xs text-gray-500 mb-1">\u5df2\u9501\u5b9a\u7b54\u6848</div>' +
            '<div class="text-2xl font-bold text-blue-600">' + selectedAnswer + '</div>' +
        '</div>' +
        '<div class="p-3 bg-gray-50 rounded-lg border">' +
            '<div class="text-xs text-gray-500 mb-1">\u81ea\u4fe1\u5ea6</div>' +
            '<div class="text-lg font-bold">' + (confLabels[selectedConfidence] || selectedConfidence) + '</div>' +
        '</div>' +
    '</div>';

    // Rationale textarea
    html += '<div class="mb-4">' +
        '<label class="block text-sm font-bold text-gray-700 mb-2">\u8bf7\u5199\u51fa\u4f60\u7684\u63a8\u7406\u8fc7\u7a0b <span class="text-red-500">*</span></label>' +
        '<div class="text-xs text-gray-400 mb-2">\u4e3a\u4ec0\u4e48\u9009\u8fd9\u4e2a\u7b54\u6848\uff1f\u6d89\u53ca\u54ea\u4e2a\u77e5\u8bc6\u70b9\uff1f\u6392\u9664\u5176\u4ed6\u9009\u9879\u7684\u7406\u7531\u662f\u4ec0\u4e48\uff1f</div>' +
        '<textarea id="rationaleInput" oninput="updateRationaleCount()" rows="6" class="w-full border rounded-lg p-3 text-sm focus:ring-2 focus:ring-blue-300 focus:border-blue-300" placeholder="\u4f8b\u5982\uff1a\u6211\u9009A\u662f\u56e0\u4e3a...\u8fd9\u4e2a\u77e5\u8bc6\u70b9\u7684\u6838\u5fc3\u662f...\u6392\u9664B\u662f\u56e0\u4e3a..."></textarea>' +
        '<div class="flex justify-between mt-1">' +
            '<span id="rationaleCount" class="text-xs text-gray-400">0/30 \u5b57\uff08\u6700\u5c1130\u5b57\uff09</span>' +
            '<span class="text-xs text-gray-400">\u5199\u5f97\u8d8a\u8be6\u7ec6\uff0cAI\u8bc4\u5206\u8d8a\u9ad8</span>' +
        '</div>' +
    '</div>';

    // Submit button
    html += '<button onclick="submitVariantJudge()" id="variantSubmitBtn" disabled class="w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed">\u81f3\u5c1130\u5b57\u624d\u80fd\u63d0\u4ea4</button>';

    content.innerHTML = html;
}

function updateRationaleCount() {
    var text = document.getElementById('rationaleInput').value;
    var len = text.length;
    var countEl = document.getElementById('rationaleCount');
    var btn = document.getElementById('variantSubmitBtn');
    countEl.textContent = len + '/30 \u5b57' + (len < 30 ? '\uff08\u6700\u5c1130\u5b57\uff09' : ' \u2713');
    countEl.className = len >= 30 ? 'text-xs text-green-500' : 'text-xs text-gray-400';
    if (len >= 30) {
        btn.disabled = false;
        btn.className = 'w-full bg-red-500 text-white py-3 rounded-lg font-bold text-lg hover:bg-red-600 cursor-pointer';
        btn.textContent = '\ud83d\udd2c \u63d0\u4ea4\u5ba1\u5224';
    } else {
        btn.disabled = true;
        btn.className = 'w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed';
        btn.textContent = '\u81f3\u5c1130\u5b57\u624d\u80fd\u63d0\u4ea4';
    }
}

async function submitVariantJudge() {
    var rationale = document.getElementById('rationaleInput').value;
    if (rationale.length < 30 || !currentSurgeryId) return;

    var btn = document.getElementById('variantSubmitBtn');
    btn.disabled = true;
    btn.textContent = '\ud83e\udde0 AI\u5ba1\u5224\u4e2d...';

    var elapsed = Math.round((Date.now() - surgeryStartTime) / 1000);
    try {
        var result = await safeFetch('/api/wrong-answers/' + currentSurgeryId + '/variant/judge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_answer: selectedAnswer,
                confidence: selectedConfidence,
                rationale_text: rationale,
                time_spent_seconds: elapsed
            })
        }, '请求失败');
        renderVariantPhase3(result);
    } catch(e) {
        btn.textContent = '\u63d0\u4ea4\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5';
        btn.disabled = false;
    }
}

function renderVariantPhase3(result) {
    var content = document.getElementById('surgeryContent');
    var verdict = result.verdict;
    var score = result.reasoning_score || 0;

    // Header based on verdict
    var html = '<div class="text-center mb-6">';
    if (verdict === 'logic_closed') {
        html += '<div class="text-6xl mb-2">\u2705</div>' +
            '<div class="text-2xl font-bold text-green-600">\u903b\u8f91\u95ed\u73af\uff01</div>' +
            '<div class="text-sm text-green-500">\u7b54\u6848\u6b63\u786e + \u63a8\u7406\u5b8c\u6574\uff0c\u771f\u6b63\u638c\u63e1\u4e86\u8fd9\u4e2a\u77e5\u8bc6\u70b9</div>';
    } else if (verdict === 'lucky_guess') {
        html += '<div class="text-6xl mb-2">\ud83c\udf40</div>' +
            '<div class="text-2xl font-bold text-yellow-600">\u8499\u5bf9\u4e86\uff01</div>' +
            '<div class="text-sm text-yellow-500">\u7b54\u6848\u6b63\u786e\u4f46\u63a8\u7406\u6709\u6f0f\u6d1e\uff0c\u5df2\u964d\u7ea7\u4e3a\u300c\u9690\u5f62\u5730\u96f7\u300d</div>';
    } else {
        html += '<div class="text-6xl mb-2">\u274c</div>' +
            '<div class="text-2xl font-bold text-red-600">\u53d8\u5f0f\u4e5f\u6ca1\u8fc7</div>' +
            '<div class="text-sm text-red-500">\u9519\u8bef\u6b21\u6570+1\uff0c\u8fd9\u4e2a\u77e5\u8bc6\u70b9\u9700\u8981\u91cd\u70b9\u653b\u514b</div>';
    }
    html += '</div>';

    // Answer comparison
    html += '<div class="grid grid-cols-2 gap-4 mb-4">' +
        '<div class="p-4 rounded-lg ' + (result.is_correct ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200') + '">' +
            '<div class="text-xs text-gray-500 mb-1">\u4f60\u7684\u7b54\u6848</div>' +
            '<div class="text-xl font-bold ' + (result.is_correct ? 'text-green-600' : 'text-red-600') + '">' + selectedAnswer + '</div>' +
        '</div>' +
        '<div class="p-4 rounded-lg bg-green-50 border border-green-200">' +
            '<div class="text-xs text-gray-500 mb-1">\u6b63\u786e\u7b54\u6848</div>' +
            '<div class="text-xl font-bold text-green-600">' + result.variant_answer + '</div>' +
        '</div>' +
    '</div>';

    // Reasoning score bar
    var scoreColor = score >= 70 ? 'bg-green-500' : (score >= 40 ? 'bg-yellow-500' : 'bg-red-500');
    var scoreTextColor = score >= 70 ? 'text-green-600' : (score >= 40 ? 'text-yellow-600' : 'text-red-600');
    html += '<div class="bg-gray-50 rounded-lg p-4 mb-4">' +
        '<div class="flex justify-between items-center mb-2">' +
            '<span class="text-sm font-bold text-gray-700">\ud83e\udde0 \u63a8\u7406\u8bc4\u5206</span>' +
            '<span class="text-2xl font-bold ' + scoreTextColor + '">' + score + '<span class="text-sm text-gray-400">/100</span></span>' +
        '</div>' +
        '<div class="w-full bg-gray-200 rounded-full h-3">' +
            '<div class="' + scoreColor + ' h-3 rounded-full transition-all" style="width:' + score + '%"></div>' +
        '</div>' +
    '</div>';

    // AI Diagnosis
    if (result.diagnosis) {
        html += '<div class="bg-blue-50 rounded-lg p-4 mb-4">' +
            '<div class="text-sm font-bold text-blue-700 mb-1">\ud83d\udd2c AI\u8bca\u65ad</div>' +
            '<div class="text-sm text-gray-700 leading-relaxed">' + result.diagnosis + '</div>' +
        '</div>';
    }

    // Weak links
    if (result.weak_links && result.weak_links.length > 0) {
        html += '<div class="mb-4"><div class="text-sm font-bold text-gray-700 mb-2">\u8584\u5f31\u73af\u8282</div><div class="flex flex-wrap gap-2">';
        result.weak_links.forEach(function(w) {
            html += '<span class="bg-red-100 text-red-700 px-3 py-1 rounded-full text-xs font-medium">' + w + '</span>';
        });
        html += '</div></div>';
    }

    // Explanation
    if (result.variant_explanation) {
        html += '<div class="bg-gray-50 rounded-lg p-4 mb-4">' +
            '<div class="text-sm font-bold text-gray-700 mb-1">\ud83d\udcd6 \u53d8\u5f0f\u9898\u89e3\u6790</div>' +
            '<div class="text-sm text-gray-600 leading-relaxed">' + result.variant_explanation + '</div>' +
        '</div>';
    }

    // Action buttons
    html += '<div class="flex space-x-3">';
    // Rescue report button (always show)
    html += '<button onclick="copyRescueReport()" class="flex-1 bg-purple-500 text-white py-3 rounded-lg font-bold hover:bg-purple-600">\ud83d\udccb \u590d\u5236\u6c42\u52a9\u62a5\u544a</button>';

    if (result.can_archive) {
        html += '<button onclick="archiveAndNext()" class="flex-1 bg-green-500 text-white py-3 rounded-lg font-bold hover:bg-green-600">\u2705 \u6807\u8bb0\u5df2\u638c\u63e1</button>';
    }
    if (batchQueue.length > 1 && batchIndex < batchQueue.length - 1) {
        html += '<button onclick="nextQuestion()" class="flex-1 bg-blue-500 text-white py-3 rounded-lg font-bold hover:bg-blue-600">\u2192 \u4e0b\u4e00\u9898</button>';
    } else {
        html += '<button onclick="closeSurgery()" class="flex-1 bg-gray-500 text-white py-3 rounded-lg font-bold hover:bg-gray-600">\u5173\u95ed</button>';
    }
    html += '</div>';

    content.innerHTML = html;
}

async function copyRescueReport() {
    if (!currentSurgeryId) return;
    try {
        var data = await safeFetch('/api/wrong-answers/' + currentSurgeryId + '/variant/rescue-report', { method: 'POST' }, '请求失败');
        await navigator.clipboard.writeText(data.content);
        alert('\u2713 \u6c42\u52a9\u62a5\u544a\u5df2\u590d\u5236\u5230\u526a\u8d34\u677f\uff0c\u53ef\u7c98\u8d34\u7ed9AI\u8f85\u5bfc\u8001\u5e08');
    } catch(e) {
        alert('\u590d\u5236\u5931\u8d25');
    }
}

// ========== Export ==========
async function exportMarkdown() {
    try {
        var data = await safeFetch('/api/wrong-answers/export?status=' + currentStatus, {}, '请求失败');
        navigator.clipboard.writeText(data.content).then(function() {
            alert('\u2713 \u5df2\u590d\u5236\u5230\u526a\u8d34\u677f\uff08' + data.total + '\u9053\u9519\u9898\uff09');
        });
    } catch(e) { alert('\u5bfc\u51fa\u5931\u8d25'); }
}

// ========== Landmine Blind Recall (2-Phase) ==========

function renderLandminePhase1(id, detail) {
    landmineDetail = detail;
    currentSurgeryId = id;
    currentQuestionType = detail.question_type || 'A1';
    surgeryStartTime = Date.now();
    selectedAnswer = '';
    selectedAnswers = [];
    selectedConfidence = 'unsure';
    landmineRecallText = '';

    document.getElementById('surgeryTitle').innerHTML = '\ud83d\udca3 \u5730\u96f7\u6392\u9664 <span class="text-sm font-normal text-yellow-500">(\u76f2\u6d4b\u56de\u5fc6)</span>';

    var content = document.getElementById('surgeryContent');
    var badge = severityBadge('landmine');

    var html = '<div class="mb-3">' + badge +
        '<span class="text-xs text-gray-400 ml-2">[' + (detail.question_type || 'A1') +
        '] [' + (detail.difficulty || '\u57fa\u7840') + '] \u9519' + detail.error_count + '\u6b21</span>' +
        '</div>';

    html += '<div class="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-4 text-sm text-yellow-800">' +
        '\u26a0\ufe0f \u4f60\u4e0a\u6b21<b>\u7b54\u5bf9\u4e86\u4f46\u4e0d\u786e\u5b9a</b>\uff0c\u53ef\u80fd\u662f\u8499\u5bf9\u7684\u3002<br>' +
        '\u73b0\u5728\u8bf7<b>\u4e0d\u770b\u9009\u9879</b>\uff0c\u51ed\u8bb0\u5fc6\u5199\u51fa\u7b54\u6848\u8981\u70b9\uff0c\u9a8c\u8bc1\u662f\u5426\u771f\u6b63\u7406\u89e3\u3002</div>';

    html += '<div class="bg-gray-50 rounded-lg p-4 mb-4"><div class="text-base leading-relaxed">' + detail.question_text + '</div></div>';

    if (detail.key_point) {
        html += '<div class="bg-blue-50 rounded-lg p-3 mb-4 text-sm">' +
            '\ud83d\udca1 \u63d0\u793a\u77e5\u8bc6\u70b9\uff1a<b>' + detail.key_point + '</b></div>';
    }

    html += '<div class="mb-4">' +
        '<label class="block text-sm font-bold text-gray-700 mb-2">' +
        '\u8bf7\u51ed\u8bb0\u5fc6\u5199\u51fa\u7b54\u6848\u8981\u70b9 <span class="text-red-500">*</span></label>' +
        '<div class="text-xs text-gray-400 mb-2">\u5173\u952e\u8bcd\u5373\u53ef\uff1a\u6b63\u786e\u7b54\u6848\u662f\u4ec0\u4e48\uff1f\u4e3a\u4ec0\u4e48\uff1f\u6d89\u53ca\u4ec0\u4e48\u673a\u5236\uff1f</div>' +
        '<textarea id="landmineRecallInput" oninput="updateLandmineRecallCount()" rows="4" ' +
        'class="w-full border rounded-lg p-3 text-sm focus:ring-2 focus:ring-yellow-300 focus:border-yellow-300" ' +
        'placeholder="\u4f8b\u5982\uff1a\u7b54\u6848\u5e94\u8be5\u662fB\uff0c\u56e0\u4e3a\u8be5\u75c5\u7684\u7279\u5f81\u6027\u8868\u73b0\u662f...\u673a\u5236\u662f..."></textarea>' +
        '<div class="flex justify-between mt-1">' +
        '<span id="landmineRecallCount" class="text-xs text-gray-400">0/10 \u5b57\uff08\u6700\u5c1110\u5b57\uff09</span>' +
        '<span class="text-xs text-gray-400">\u5199\u5b8c\u540e\u53ef\u67e5\u770b\u9009\u9879\u9a8c\u8bc1</span></div></div>';

    html += '<button onclick="renderLandminePhase2()" id="landmineRevealBtn" disabled ' +
        'class="w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed">' +
        '\u81f3\u5c1110\u5b57\u624d\u80fd\u67e5\u770b\u9009\u9879</button>';

    content.innerHTML = html;
}

function updateLandmineRecallCount() {
    var text = document.getElementById('landmineRecallInput').value;
    var len = text.length;
    var countEl = document.getElementById('landmineRecallCount');
    var btn = document.getElementById('landmineRevealBtn');

    countEl.textContent = len + '/10 \u5b57' + (len < 10 ? '\uff08\u6700\u5c1110\u5b57\uff09' : ' \u2713');
    countEl.className = len >= 10 ? 'text-xs text-green-500' : 'text-xs text-gray-400';

    if (len >= 10) {
        btn.disabled = false;
        btn.className = 'w-full bg-yellow-500 text-white py-3 rounded-lg font-bold text-lg hover:bg-yellow-600 cursor-pointer';
        btn.textContent = '\ud83d\udc41 \u663e\u793a\u9009\u9879\uff0c\u8fdb\u5165\u9a8c\u8bc1';
    } else {
        btn.disabled = true;
        btn.className = 'w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed';
        btn.textContent = '\u81f3\u5c1110\u5b57\u624d\u80fd\u67e5\u770b\u9009\u9879';
    }
}

function renderLandminePhase2() {
    landmineRecallText = document.getElementById('landmineRecallInput').value;
    var detail = landmineDetail;
    var content = document.getElementById('surgeryContent');
    selectedAnswer = '';
    selectedAnswers = [];
    selectedConfidence = 'unsure';

    document.getElementById('surgeryTitle').innerHTML = '\ud83d\udca3 \u5730\u96f7\u6392\u9664 <span class="text-sm font-normal text-yellow-500">(\u9a8c\u8bc1\u9009\u62e9)</span>';

    var html = '<div class="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-4">' +
        '<div class="text-xs text-yellow-600 font-bold mb-1">\ud83d\udcdd \u4f60\u7684\u76f2\u6d4b\u56de\u5fc6</div>' +
        '<div class="text-sm text-gray-700 italic">\u201c' + escapeHtml(landmineRecallText) + '\u201d</div></div>';

    html += '<div class="bg-gray-50 rounded-lg p-4 mb-4"><div class="text-base leading-relaxed">' + detail.question_text + '</div></div>';

    // 多选题提示
    var isMultiple = currentQuestionType === 'X';
    if (isMultiple) {
        html += '<div class="bg-blue-50 border border-blue-200 rounded-lg p-2 mb-3 text-sm text-blue-700">' +
            '💡 这是多选题，可以选择多个选项（点击选项可切换选中状态）' +
            '</div>';
    }

    html += '<div class="space-y-2 mb-6">';
    if (detail.options) {
        for (var opt in detail.options) {
            html += '<label class="block p-3 border rounded-lg cursor-pointer hover:bg-blue-50 transition option-label" ' +
                'data-opt="' + opt + '" onclick="selectOption(this,\'' + opt + '\')">' +
                '<span class="font-bold mr-2">' + opt + '.</span>' + detail.options[opt] + '</label>';
        }
    }
    html += '</div>';

    html += '<div class="mb-4"><div class="text-sm text-gray-500 mb-2">\u4f60\u7684\u628a\u63e1\uff1a</div>' +
        '<div class="flex space-x-2">' +
        '<button onclick="setConfidence(\'sure\')" id="conf-sure" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-green-50">Q 确定</button>' +
        '<button onclick="setConfidence(\'unsure\')" id="conf-unsure" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-yellow-50">W 模糊</button>' +
        '<button onclick="setConfidence(\'no\')" id="conf-no" class="conf-btn px-4 py-2 rounded border text-sm hover:bg-red-50">E 不会</button>' +
        '</div></div>';

    html += '<button onclick="submitLandmineRetry()" id="submitRetryBtn" disabled ' +
        'class="w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed">\u8bf7\u5148\u9009\u62e9\u7b54\u6848</button>';

    content.innerHTML = html;
}

async function submitLandmineRetry() {
    if (!selectedAnswer || !currentSurgeryId) return;
    var btn = document.getElementById('submitRetryBtn');
    btn.disabled = true;
    btn.textContent = '\u5224\u5b9a\u4e2d...';

    var elapsed = Math.round((Date.now() - surgeryStartTime) / 1000);
    try {
        var result = await safeFetch('/api/wrong-answers/' + currentSurgeryId + '/retry', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_answer: selectedAnswer,
                confidence: selectedConfidence,
                time_spent_seconds: elapsed,
                recall_text: landmineRecallText,
                is_landmine_recall: true
            })
        }, '请求失败');
        renderLandmineResult(result);
    } catch(e) {
        btn.textContent = '\u63d0\u4ea4\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5';
        btn.disabled = false;
    }
}

function renderLandmineResult(result) {
    var content = document.getElementById('surgeryContent');
    var isCorrect = result.is_correct;
    var needReflectionBeforeExplanation = shouldGateExplanation(isCorrect, selectedConfidence);
    var confLabels = { sure: 'Q 确定', unsure: 'W 模糊', no: 'E 不会' };

    var html = '<div class="text-center mb-6">';
    if (isCorrect && selectedConfidence === 'sure') {
        html += '<div class="text-6xl mb-2">\ud83d\udca3\u2705</div>' +
            '<div class="text-2xl font-bold text-green-600">\u5730\u96f7\u6392\u9664\uff01</div>' +
            '<div class="text-sm text-green-500">\u7b54\u5bf9 + \u786e\u5b9a = \u771f\u6b63\u7406\u89e3\uff0c\u53ef\u4ee5\u5f52\u6863</div>';
    } else if (isCorrect) {
        html += '<div class="text-6xl mb-2">\ud83d\udca3\u26a0\ufe0f</div>' +
            '<div class="text-2xl font-bold text-yellow-600">\u7b54\u5bf9\u4e86\uff0c\u4f46\u8fd8\u4e0d\u591f\u786e\u5b9a</div>' +
            '<div class="text-sm text-yellow-500">\u5730\u96f7\u6807\u7b7e\u4fdd\u7559\uff0c\u4e0b\u6b21\u7ee7\u7eed\u76f2\u6d4b</div>';
    } else {
        html += '<div class="text-6xl mb-2">\ud83d\udca5</div>' +
            '<div class="text-2xl font-bold text-red-600">\u5730\u96f7\u5f15\u7206\uff01</div>' +
            '<div class="text-sm text-red-500">\u679c\u7136\u6ca1\u6709\u771f\u6b63\u638c\u63e1\uff0c\u9700\u8981\u91cd\u70b9\u590d\u4e60</div>';
    }
    html += '</div>';

    html += '<div class="grid grid-cols-2 gap-4 mb-4">' +
        '<div class="p-4 rounded-lg ' + (isCorrect ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200') + '">' +
        '<div class="text-xs text-gray-500 mb-1">\u8fd9\u6b21\u9009\u62e9</div>' +
        '<div class="text-xl font-bold ' + (isCorrect ? 'text-green-600' : 'text-red-600') + '">' +
        selectedAnswer + ' (' + (confLabels[selectedConfidence] || '') + ')</div></div>' +
        '<div class="p-4 rounded-lg bg-green-50 border border-green-200">' +
        '<div class="text-xs text-gray-500 mb-1">\u6b63\u786e\u7b54\u6848</div>' +
        '<div class="text-xl font-bold text-green-600">' + result.correct_answer + '</div></div></div>';

    // Original question display
    if (landmineDetail) {
        html += '<div class="mb-4 bg-indigo-50 border border-indigo-200 rounded-lg p-4">' +
            '<div class="text-sm text-indigo-700 font-bold mb-2">\ud83d\udccc \u539f\u9898\u56de\u987e</div>' +
            '<div class="text-sm text-gray-700 leading-relaxed mb-3">' + escapeHtml(landmineDetail.question_text) + '</div>';

        var hasOption = false;
        var optionsHtml = '';
        if (landmineDetail.options) {
            for (var opt in landmineDetail.options) {
                if (!landmineDetail.options[opt]) continue;
                hasOption = true;
                var isCorrectOpt = opt === result.correct_answer;
                var isUserChoice = opt === selectedAnswer;
                var optClass = isCorrectOpt ? 'text-green-700 font-bold' : (isUserChoice ? 'text-red-600' : 'text-gray-700');
                var optIcon = isCorrectOpt ? ' \u2705' : (isUserChoice ? ' \u274c' : '');
                optionsHtml += '<div class="text-sm ' + optClass + '"><span class="font-bold mr-1">' + escapeHtml(opt) + '.</span>' +
                    escapeHtml(landmineDetail.options[opt]) + optIcon + '</div>';
            }
        }
        if (hasOption) {
            html += '<div class="space-y-1 bg-white rounded p-2">' + optionsHtml + '</div>';
        }
        html += '</div>';
    }

    html += '<div class="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-4">' +
        '<div class="text-sm font-bold text-yellow-700 mb-2">\ud83d\udcdd \u76f2\u6d4b\u56de\u5fc6 vs \u6b63\u786e\u89e3\u6790</div>' +
        '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">' +
        '<div class="bg-white rounded p-3 border">' +
        '<div class="text-xs text-gray-400 mb-1">\u4f60\u5199\u7684</div>' +
        '<div class="text-sm text-gray-700">' + escapeHtml(landmineRecallText) + '</div></div>' +
        '<div class="bg-white rounded p-3 border">' +
        '<div class="text-xs text-gray-400 mb-1">\u6b63\u786e\u89e3\u6790</div>' +
        '<div class="text-sm text-gray-700">' +
            ((needReflectionBeforeExplanation && result.explanation) ? '\u5b8c\u6210\u4e0b\u65b9\u53cd\u601d\u540e\u663e\u793a\u89e3\u6790' : (result.explanation || '\u65e0\u89e3\u6790')) +
        '</div></div>' +
        '</div>' +
        '<div class="text-xs text-gray-400 mt-2">\u5bf9\u6bd4\u56de\u5fc6\u548c\u89e3\u6790\uff0c\u627e\u51fa\u7406\u89e3\u504f\u5dee</div></div>';

    if (result.explanation) {
        html += buildExplanationSection(
            result.explanation,
            '\ud83d\udcd6 \u5b8c\u6574\u89e3\u6790',
            isCorrect,
            selectedConfidence,
            'landmine'
        );
    }

    html += '<div class="flex space-x-3">';
    if (result.can_archive) {
        html += '<button onclick="archiveAndNext()" class="flex-1 bg-green-500 text-white py-3 rounded-lg font-bold hover:bg-green-600">\ud83d\udca3\u2705 \u5730\u96f7\u5df2\u6392\u9664</button>';
    }
    if (batchQueue.length > 1 && batchIndex < batchQueue.length - 1) {
        html += '<button onclick="nextQuestion()" class="flex-1 bg-blue-500 text-white py-3 rounded-lg font-bold hover:bg-blue-600">\u2192 \u4e0b\u4e00\u9898</button>';
    } else {
        html += '<button onclick="closeSurgery()" class="flex-1 bg-gray-500 text-white py-3 rounded-lg font-bold hover:bg-gray-600">\u5173\u95ed</button>';
    }
    html += '</div>';

    content.innerHTML = html;
}

// ========== Utilities ==========
function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function shouldGateExplanation(isCorrect, confidence) {
    return !isCorrect || confidence !== 'sure';
}

function buildExplanationSection(explanation, title, isCorrect, confidence, prefix) {
    if (!explanation) return '';
    var needGate = shouldGateExplanation(isCorrect, confidence);
    var html = '';

    if (needGate) {
        html += '<div id="' + prefix + 'ReflectionGate" class="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-4">' +
            '<div class="text-sm font-bold text-amber-800 mb-2">\ud83e\udde0 \u5148\u505a\u4e00\u6b65\u53cd\u601d</div>' +
            '<div class="text-sm text-amber-700 mb-3">\u4f60\u4f3c\u4e4e\u5728\u8fd9\u91cc\u9047\u5230\u4e86\u56f0\u96be\u3002\u5728\u770b\u89e3\u6790\u524d\uff0c\u8bf7\u5148\u5199\u4e0b\uff08\u6216\u5728\u8111\u6d77\u4e2d\u60f3\u4e00\u4e0b\uff09\u4f60\u8ba4\u4e3a\u81ea\u5df1\u54ea\u91cc\u60f3\u9519\u4e86\u3002</div>' +
            '<textarea id="' + prefix + 'ReflectionInput" oninput="updateReflectionGate(\'' + prefix + '\')" rows="3" class="w-full border rounded-lg p-3 text-sm focus:ring-2 focus:ring-amber-300 focus:border-amber-300" placeholder="\u4f8b\u5982\uff1a\u6211\u6df7\u6dc6\u4e86... / \u6211\u5ffd\u7565\u4e86..."></textarea>' +
            '<div class="flex items-center justify-between mt-2 gap-2">' +
                '<span id="' + prefix + 'ReflectionCount" class="text-xs text-amber-600">0/10 \u5b57</span>' +
                '<label class="text-xs text-amber-700 flex items-center gap-1">' +
                    '<input id="' + prefix + 'ReflectionMental" type="checkbox" onchange="updateReflectionGate(\'' + prefix + '\')" class="rounded border-amber-300">' +
                    '<span>\u6211\u5df2\u5728\u8111\u6d77\u4e2d\u5b8c\u6210\u53cd\u601d</span>' +
                '</label>' +
            '</div>' +
            '<button id="' + prefix + 'RevealBtn" onclick="revealExplanationAfterReflection(\'' + prefix + '\')" disabled class="mt-3 w-full bg-gray-300 text-white py-2 rounded-lg font-bold cursor-not-allowed">\u5b8c\u6210\u53cd\u601d\u540e\u67e5\u770b\u89e3\u6790</button>' +
        '</div>';
    }

    html += '<div id="' + prefix + 'ExplanationPanel" class="bg-blue-50 rounded-lg p-4 mb-4' + (needGate ? ' hidden' : '') + '">' +
        '<div class="text-sm font-bold text-blue-700 mb-1">' + title + '</div>' +
        '<div class="text-sm text-gray-700 leading-relaxed">' + explanation + '</div></div>';

    return html;
}

function updateReflectionGate(prefix) {
    var input = document.getElementById(prefix + 'ReflectionInput');
    var check = document.getElementById(prefix + 'ReflectionMental');
    var count = document.getElementById(prefix + 'ReflectionCount');
    var btn = document.getElementById(prefix + 'RevealBtn');
    if (!btn) return;

    var textLen = input ? input.value.trim().length : 0;
    if (count) {
        count.textContent = textLen + '/10 \u5b57' + (textLen >= 10 ? ' \u2713' : '');
    }

    var ready = textLen >= 10 || (check && check.checked);
    btn.disabled = !ready;
    if (ready) {
        btn.className = 'mt-3 w-full bg-blue-500 text-white py-2 rounded-lg font-bold hover:bg-blue-600 cursor-pointer';
    } else {
        btn.className = 'mt-3 w-full bg-gray-300 text-white py-2 rounded-lg font-bold cursor-not-allowed';
    }
}

function revealExplanationAfterReflection(prefix) {
    var input = document.getElementById(prefix + 'ReflectionInput');
    var check = document.getElementById(prefix + 'ReflectionMental');
    var textLen = input ? input.value.trim().length : 0;
    var ready = textLen >= 10 || (check && check.checked);
    if (!ready) return;

    var gate = document.getElementById(prefix + 'ReflectionGate');
    var panel = document.getElementById(prefix + 'ExplanationPanel');
    if (gate) gate.classList.add('hidden');
    if (panel) panel.classList.remove('hidden');
}

function severityBadge(tag) {
    var map = {
        critical: '<span class="bg-red-100 text-red-700 px-2 py-0.5 rounded text-xs font-bold">\ud83d\udea8 \u81f4\u547d\u76f2\u533a</span>',
        stubborn: '<span class="bg-orange-100 text-orange-700 px-2 py-0.5 rounded text-xs font-bold">\ud83d\ude91 \u987d\u56fa\u75c5\u7076</span>',
        landmine: '<span class="bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded text-xs font-bold">\u26a0\ufe0f \u9690\u5f62\u5730\u96f7</span>',
        normal: '<span class="bg-gray-100 text-gray-600 px-2 py-0.5 rounded text-xs font-bold">\ud83d\udccb \u666e\u901a</span>'
    };
    return map[tag] || map.normal;
}

function formatTimeAgo(isoStr) {
    if (!isoStr) return '';
    var d = new Date(isoStr);
    var now = new Date();
    var diff = Math.floor((now - d) / 1000);
    if (diff < 60) return '\u521a\u521a';
    if (diff < 3600) return Math.floor(diff / 60) + '\u5206\u949f\u524d';
    if (diff < 86400) return Math.floor(diff / 3600) + '\u5c0f\u65f6\u524d';
    if (diff < 604800) return Math.floor(diff / 86400) + '\u5929\u524d';
    return d.toLocaleDateString('zh-CN');
}

// ========== Accordion Toggle ==========
function toggleSection(id) {
    var el = document.getElementById(id);
    var arrow = document.getElementById('arrow-' + id);
    if (!el) return;
    treeCollapsed[id] = !el.classList.contains('hidden');
    el.classList.toggle('hidden');
    if (arrow) arrow.textContent = el.classList.contains('hidden') ? '\u25b6' : '\u25bc';
}

// ========== Timeline Filter ==========
function timeFilter(mode) {
    currentTimeFilter = mode;
    // 高亮按钮
    document.querySelectorAll('.tf-btn').forEach(function(btn) {
        btn.className = 'tf-btn px-3 py-1 rounded text-xs border hover:bg-blue-50';
    });
    var activeMap = {'7d': 0, 'month': 1, 'all': 2};
    var btns = document.querySelectorAll('.tf-btn');
    if (activeMap[mode] !== undefined && btns[activeMap[mode]]) {
        btns[activeMap[mode]].className = 'tf-btn px-3 py-1 rounded text-xs border bg-gray-200 font-medium';
    }
    // 用缓存数据重新渲染（不重新请求后端）
    if (timelineTreeData) {
        renderTimelineView(timelineTreeData);
    }
}

// Click outside modal to close
document.getElementById('surgeryModal').addEventListener('click', function(e) {
    if (e.target === this) closeSurgery();
});
document.getElementById('challengeModal').addEventListener('click', function(e) {
    if (e.target === this) closeChallenge();
});
document.getElementById('importModal').addEventListener('click', function(e) {
    if (e.target === this) closeImportModal();
});

// ========== Challenge Mode (靶向闯关) ==========
var challengeQueue = [];       // 闯关队列
var challengeIdx = 0;          // 当前题目索引
var challengeVariantCache = {};// 预加载变式题缓存 {wrongId: variantData}
var challengeStartTime = 0;    // 当前题目开始时
var challengeAnswer = '';      // 当前选择的答
var challengeAnswers = [];     // 多选题答案数组
var challengeQuestionType = 'A1'; // 当前题目类型
var challengeConfidence = 'unsure';
var challengeIsVariant = false;// 当前题目是否变式
var challengeResults = [];     // 闯关结果记录

async function startChallenge() {
    var content = document.getElementById('challengeContent');
    document.getElementById('challengeModal').classList.remove('hidden');
    content.innerHTML = '<div class="text-center py-8 text-gray-400">⏳ 加载闯关队列...</div>';

    try {
        var data = await safeFetch('/api/challenge/queue?count=10', {}, '请求失败');
        if (!data.items || data.items.length === 0) {
            content.innerHTML = '<div class="text-center py-12">' +
                '<div class="text-5xl mb-4"></div>' +
                '<div class="text-xl font-bold text-green-600 mb-2">今日无待闯关错题</div>' +
                '<div class="text-gray-400 text-sm">所有错题都未到复习时间，或已全部掌握</div>' +
                '<button onclick="closeChallenge()" class="mt-6 bg-gray-500 text-white px-6 py-2 rounded-lg">关闭</button></div>';
            return;
        }
        challengeQueue = data.items;
        challengeIdx = 0;
        challengeResults = [];
        challengeVariantCache = {};

        // 预加载第1题变
        prefetchVariant(challengeQueue[0].id);
        // 预加载第2题变
        if (challengeQueue.length > 1) prefetchVariant(challengeQueue[1].id);

        renderChallengeQuestion();
    } catch(e) {
        content.innerHTML = '<div class="text-center py-8 text-red-400">加载失败: ' + e.message + '</div>';
    }
}

function prefetchVariant(wrongId) {
    if (challengeVariantCache[wrongId]) return; // 已缓
    challengeVariantCache[wrongId] = 'loading';
    fetch('/api/challenge/variant?wrong_answer_id=' + wrongId, { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.variant_question && !data.fallback) {
                challengeVariantCache[wrongId] = data;
            } else {
                challengeVariantCache[wrongId] = null; // 生成失败，用原题
            }
        })
        .catch(function() {
            challengeVariantCache[wrongId] = null;
        });
}

function renderChallengeQuestion() {
    var item = challengeQueue[challengeIdx];
    var content = document.getElementById('challengeContent');
    challengeStartTime = Date.now();
    challengeAnswer = '';
    challengeAnswers = [];
    challengeQuestionType = item.question_type || 'A1';
    challengeConfidence = 'unsure';
    challengeIsVariant = false;

    document.getElementById('challengeProgress').textContent =
        '第 ' + (challengeIdx + 1) + ' / ' + challengeQueue.length + ' 题';

    // 检查变式题缓存
    var variant = challengeVariantCache[item.id];
    var questionText, options;

    if (variant && variant !== 'loading' && variant.variant_question) {
        // 使用变式
        challengeIsVariant = true;
        questionText = variant.variant_question;
        options = variant.variant_options;
    } else {
        // 用原题
        challengeIsVariant = false;
        questionText = item.question_text;
        options = item.options;
    }

    var badge = severityBadge(item.severity_tag);
    var html = '<div class="mb-3">' + badge +
        '<span class="text-xs text-gray-400 ml-2">[' + (item.question_type || 'A1') + '] [' + (item.difficulty || '基础') + '] 错' + item.error_count + '次</span>' +
        (item.key_point ? '<span class="text-xs text-blue-500 ml-2"> ' + item.key_point + '</span>' : '');

    if (challengeIsVariant) {
        html += '<span class="bg-purple-100 text-purple-700 px-2 py-0.5 rounded text-xs font-bold ml-2"> 变式</span>';
    }

    // SM-2 状态
    if (item.sm2_repetitions > 0) {
        html += '<span class="text-xs text-gray-400 ml-2">连对' + item.sm2_repetitions + '次 | 间隔' + item.sm2_interval + '天</span>';
    }
    html += '</div>';

    html += '<div class="bg-gray-50 rounded-lg p-4 mb-4"><div class="text-base leading-relaxed">' + questionText + '</div></div>';

    // 多选题提示
    var isMultiple = challengeQuestionType === 'X';
    if (isMultiple) {
        html += '<div class="bg-blue-50 border border-blue-200 rounded-lg p-2 mb-3 text-sm text-blue-700">' +
            '💡 这是多选题，可以选择多个选项（点击选项可切换选中状态）' +
            '</div>';
    }

    // 选项
    html += '<div class="space-y-2 mb-6">';
    if (options) {
        for (var opt in options) {
            if (!options[opt]) continue;
            html += '<label class="block p-3 border rounded-lg cursor-pointer hover:bg-blue-50 transition ch-option-label" data-opt="' + opt + '" onclick="selectChallengeOption(this,\'' + opt + '\')">' +
                '<span class="font-bold mr-2">' + opt + '.</span>' + options[opt] + '</label>';
        }
    }
    html += '</div>';

    // 自信
    html += '<div class="mb-4"><div class="text-sm text-gray-500 mb-2">你的把握</div>' +
        '<div class="flex space-x-2">' +
        '<button onclick="setChallengeConfidence(\'sure\')" id="ch-conf-sure" class="ch-conf-btn px-4 py-2 rounded border text-sm hover:bg-green-50">Q 确定</button>' +
        '<button onclick="setChallengeConfidence(\'unsure\')" id="ch-conf-unsure" class="ch-conf-btn px-4 py-2 rounded border text-sm hover:bg-yellow-50">W 模糊</button>' +
        '<button onclick="setChallengeConfidence(\'no\')" id="ch-conf-no" class="ch-conf-btn px-4 py-2 rounded border text-sm hover:bg-red-50">E 不会</button>' +
        '</div></div>';

    // 提交
    html += '<button onclick="submitChallengeAnswer()" id="chSubmitBtn" disabled class="w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed">请先选择答案</button>';

    content.innerHTML = html;

    // 预加载下一题变
    if (challengeIdx + 2 < challengeQueue.length) {
        prefetchVariant(challengeQueue[challengeIdx + 2].id);
    }
}

function selectChallengeOption(el, opt) {
    var isMultiple = challengeQuestionType === 'X';

    if (isMultiple) {
        // 多选题逻辑：切换选中状态
        var idx = challengeAnswers.indexOf(opt);
        if (idx > -1) {
            // 已选中，取消选中
            challengeAnswers.splice(idx, 1);
            el.classList.remove('bg-blue-100', 'border-blue-500');
        } else {
            // 未选中，添加选中
            challengeAnswers.push(opt);
            el.classList.add('bg-blue-100', 'border-blue-500');
        }
        // 按字母顺序排序
        challengeAnswers.sort();
        challengeAnswer = challengeAnswers.join('');
    } else {
        // 单选题逻辑：如果已选中则取消，否则选中
        if (challengeAnswer === opt) {
            // 已选中该选项，取消选择
            challengeAnswer = '';
            challengeAnswers = [];
            document.querySelectorAll('.ch-option-label').forEach(function(label) {
                label.classList.remove('bg-blue-100', 'border-blue-500');
            });
            el.classList.remove('bg-blue-100', 'border-blue-500');
        } else {
            // 选中该选项
            challengeAnswer = opt;
            challengeAnswers = [opt];
            document.querySelectorAll('.ch-option-label').forEach(function(label) {
                label.classList.remove('bg-blue-100', 'border-blue-500');
            });
            el.classList.add('bg-blue-100', 'border-blue-500');
        }
    }

    // 更新提交按钮状态
    var btn = document.getElementById('chSubmitBtn');
    if (btn) {
        if (challengeAnswer) {
            btn.disabled = false;
            btn.className = 'w-full bg-orange-500 text-white py-3 rounded-lg font-bold text-lg hover:bg-orange-600 cursor-pointer';
            btn.textContent = '提交答案';
        } else {
            btn.disabled = true;
            btn.className = 'w-full bg-gray-300 text-white py-3 rounded-lg font-bold text-lg cursor-not-allowed';
            btn.textContent = '请先选择答案';
        }
    }
}

function setChallengeConfidence(conf) {
    // 如果已选中相同等级，则取消选择
    if (challengeConfidence === conf) {
        challengeConfidence = 'unsure';
        document.querySelectorAll('.ch-conf-btn').forEach(function(btn) {
            btn.classList.remove('bg-green-100', 'bg-yellow-100', 'bg-red-100', 'border-green-500', 'border-yellow-500', 'border-red-500');
        });
        return;
    }

    challengeConfidence = conf;
    document.querySelectorAll('.ch-conf-btn').forEach(function(btn) {
        btn.classList.remove('bg-green-100', 'bg-yellow-100', 'bg-red-100', 'border-green-500', 'border-yellow-500', 'border-red-500');
    });
    var el = document.getElementById('ch-conf-' + conf);
    var colors = { sure: ['bg-green-100', 'border-green-500'], unsure: ['bg-yellow-100', 'border-yellow-500'], no: ['bg-red-100', 'border-red-500'] };
    if (colors[conf]) colors[conf].forEach(function(c) { el.classList.add(c); });
}

async function submitChallengeAnswer() {
    if (!challengeAnswer) return;
    var item = challengeQueue[challengeIdx];
    var btn = document.getElementById('chSubmitBtn');
    btn.disabled = true;
    btn.textContent = '判定中...';

    var elapsed = Math.round((Date.now() - challengeStartTime) / 1000);
    try {
        var result = await safeFetch('/api/challenge/submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                wrong_answer_id: item.id,
                user_answer: challengeAnswer,
                confidence: challengeConfidence,
                time_spent_seconds: elapsed,
                is_variant: challengeIsVariant
            })
        }, '请求失败');
        result._user_answer = challengeAnswer;
        result._confidence = challengeConfidence;
        result._item = item;
        challengeResults.push(result);
        renderChallengeResult(result);
    } catch(e) {
        btn.textContent = '提交失败，请重试';
        btn.disabled = false;
    }
}

function renderChallengeResult(result) {
    var content = document.getElementById('challengeContent');
    var isCorrect = result.is_correct;
    var confLabels = { sure: 'Q 确定', unsure: 'W 模糊', no: 'E 不会' };

    var html = '<div class="text-center mb-6">';
    if (isCorrect) {
        html += '<div class="text-5xl mb-2">✅</div><div class="text-xl font-bold text-green-600">回答正确</div>';
    } else {
        html += '<div class="text-5xl mb-2">❌</div><div class="text-xl font-bold text-red-600">回答错误</div>';
    }
    html += '</div>';

    // 答案对比
    html += '<div class="grid grid-cols-2 gap-4 mb-4">' +
        '<div class="p-3 rounded-lg ' + (isCorrect ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200') + '">' +
        '<div class="text-xs text-gray-500 mb-1">你的选择</div>' +
        '<div class="text-xl font-bold ' + (isCorrect ? 'text-green-600' : 'text-red-600') + '">' + result._user_answer + ' (' + (confLabels[result._confidence] || '') + ')</div></div>' +
        '<div class="p-3 rounded-lg bg-green-50 border border-green-200">' +
        '<div class="text-xs text-gray-500 mb-1">正确答案</div>' +
        '<div class="text-xl font-bold text-green-600">' + result.correct_answer + '</div></div></div>';

    // SM-2 状态
    var sm2Html = '<div class="bg-gray-50 rounded-lg p-3 mb-4"><div class="flex items-center justify-between flex-wrap gap-2">';
    sm2Html += '<span class="text-xs text-gray-500">SM-2 状态</span>';
    sm2Html += '<div class="flex items-center space-x-3 text-xs">';
    sm2Html += '<span>连对 <b>' + (result.sm2_repetitions || 0) + '</b>/3</span>';
    sm2Html += '<span>间隔 <b>' + (result.sm2_interval || 0) + '</b>天</span>';
    sm2Html += '<span>EF <b>' + (result.sm2_ef || 2.5) + '</b></span>';
    if (result.next_review_date) sm2Html += '<span>下次 <b>' + result.next_review_date + '</b></span>';
    sm2Html += '</div></div>';

    // 进度（repetitions / 3）
    var reps = Math.min(result.sm2_repetitions || 0, 3);
    var pct = Math.round(reps / 3 * 100);
    var barColor = pct >= 100 ? 'bg-green-500' : (pct >= 66 ? 'bg-yellow-500' : 'bg-orange-500');
    sm2Html += '<div class="w-full bg-gray-200 rounded-full h-2 mt-2"><div class="' + barColor + ' h-2 rounded-full" style="width:' + pct + '%"></div></div>';

    if (result.auto_archived) {
        sm2Html += '<div class="text-center mt-2 text-green-600 font-bold text-sm"> 连续3次正确，已自动归档！</div>';
    }
    sm2Html += '</div>';
    html += sm2Html;

    // 解析
    var explanation = result.variant_explanation || result.explanation;
    if (explanation) {
        html += buildExplanationSection(
            explanation,
            ' 解析',
            isCorrect,
            result._confidence,
            'challenge'
        );
    }

    // 按钮
    html += '<div class="flex space-x-3">';
    if (challengeIdx < challengeQueue.length - 1) {
        html += '<button onclick="nextChallengeQuestion()" class="flex-1 bg-orange-500 text-white py-3 rounded-lg font-bold hover:bg-orange-600">→下一</button>';
    } else {
        html += '<button onclick="renderChallengeSummary()" class="flex-1 bg-orange-500 text-white py-3 rounded-lg font-bold hover:bg-orange-600"> 查看总结</button>';
    }
    html += '<button onclick="closeChallenge()" class="flex-1 bg-gray-400 text-white py-3 rounded-lg font-bold hover:bg-gray-500">退</button>';
    html += '</div>';

    content.innerHTML = html;
}

function nextChallengeQuestion() {
    challengeIdx++;
    if (challengeIdx < challengeQueue.length) {
        renderChallengeQuestion();
    } else {
        renderChallengeSummary();
    }
}

function renderChallengeSummary() {
    var content = document.getElementById('challengeContent');
    document.getElementById('challengeProgress').textContent = '闯关完成';

    var total = challengeResults.length;
    var correct = challengeResults.filter(function(r) { return r.is_correct; }).length;
    var wrong = total - correct;
    var accuracy = total > 0 ? Math.round(correct / total * 100) : 0;
    var archived = challengeResults.filter(function(r) { return r.auto_archived; }).length;

    var html = '<div class="text-center mb-6">';
    if (accuracy >= 80) {
        html += '<div class="text-5xl mb-2"></div><div class="text-2xl font-bold text-green-600">表现优秀</div>';
    } else if (accuracy >= 60) {
        html += '<div class="text-5xl mb-2"></div><div class="text-2xl font-bold text-yellow-600">继续加油</div>';
    } else {
        html += '<div class="text-5xl mb-2"></div><div class="text-2xl font-bold text-red-600">需要加强复</div>';
    }
    html += '</div>';

    // 统计卡片
    html += '<div class="grid grid-cols-4 gap-3 mb-6">' +
        '<div class="bg-blue-50 rounded-lg p-3 text-center"><div class="text-2xl font-bold text-blue-600">' + total + '</div><div class="text-xs text-gray-500">总题</div></div>' +
        '<div class="bg-green-50 rounded-lg p-3 text-center"><div class="text-2xl font-bold text-green-600">' + correct + '</div><div class="text-xs text-gray-500">正确</div></div>' +
        '<div class="bg-red-50 rounded-lg p-3 text-center"><div class="text-2xl font-bold text-red-600">' + wrong + '</div><div class="text-xs text-gray-500">错误</div></div>' +
        '<div class="bg-purple-50 rounded-lg p-3 text-center"><div class="text-2xl font-bold text-purple-600">' + archived + '</div><div class="text-xs text-gray-500">已掌</div></div>' +
    '</div>';

    // 正确率条
    var barColor = accuracy >= 80 ? 'bg-green-500' : (accuracy >= 60 ? 'bg-yellow-500' : 'bg-red-500');
    html += '<div class="mb-6"><div class="flex justify-between text-sm mb-1"><span>正确</span><span class="font-bold">' + accuracy + '%</span></div>' +
        '<div class="w-full bg-gray-200 rounded-full h-3"><div class="' + barColor + ' h-3 rounded-full" style="width:' + accuracy + '%"></div></div></div>';

    // 逐题回顾
    html += '<div class="mb-4"><div class="text-sm font-bold text-gray-700 mb-2">逐题回顾</div><div class="space-y-2">';
    challengeResults.forEach(function(r, i) {
        var icon = r.is_correct ? '✅' : '❌';
        var kp = r.key_point || '未标注';
        var archiveTag = r.auto_archived ? ' <span class="text-green-500 text-xs">🎉已掌握</span>' : '';
        var nextTag = r.next_review_date ? ' <span class="text-gray-400 text-xs">下次:' + r.next_review_date + '</span>' : '';
        html += '<div class="flex items-center justify-between bg-gray-50 rounded p-2 text-sm">' +
            '<span>' + icon + ' ' + (i + 1) + '. ' + kp + archiveTag + '</span>' +
            '<span class="text-xs text-gray-400">连对' + (r.sm2_repetitions || 0) + '/3 | 间隔' + (r.sm2_interval || 0) + '天' + nextTag + '</span></div>';
    });
    html += '</div></div>';

    html += '<button onclick="closeChallenge()" class="w-full bg-gray-500 text-white py-3 rounded-lg font-bold hover:bg-gray-600">关闭</button>';

    content.innerHTML = html;
}

function closeChallenge() {
    document.getElementById('challengeModal').classList.add('hidden');
    challengeQueue = [];
    challengeIdx = 0;
    challengeVariantCache = {};
    challengeResults = [];
    challengeAnswer = '';
    challengeConfidence = 'unsure';
    loadStats();
    loadList();
}

// ========== Keyboard Shortcuts ==========
function isModalVisible(modalId) {
    var modal = document.getElementById(modalId);
    return !!modal && !modal.classList.contains('hidden');
}

function isTypingTarget(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toUpperCase();
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!el.isContentEditable;
}

function resolveOptionByKey(key, labels) {
    if (!labels || labels.length === 0) return null;
    var k = (key || '').toUpperCase();

    // Option shortcuts are numeric only: 1-5
    if (/^[1-9]$/.test(k)) {
        var idx = parseInt(k, 10) - 1;
        if (idx >= 0 && idx < labels.length) {
            var optByIndex = labels[idx].dataset ? labels[idx].dataset.opt : '';
            return optByIndex ? optByIndex.toUpperCase() : null;
        }
    }

    return null;
}

function trySelectSurgeryOptionByKey(key) {
    var labels = Array.from(document.querySelectorAll('#surgeryContent .option-label[data-opt]'));
    var opt = resolveOptionByKey(key, labels);
    if (!opt) return false;

    var target = labels.find(function(label) {
        return label.dataset.opt && label.dataset.opt.toUpperCase() === opt;
    });
    if (!target) return false;

    selectOption(target, opt);
    return true;
}

function trySelectChallengeOptionByKey(key) {
    var labels = Array.from(document.querySelectorAll('#challengeContent .ch-option-label[data-opt]'));
    var opt = resolveOptionByKey(key, labels);
    if (!opt) return false;

    var target = labels.find(function(label) {
        return label.dataset.opt && label.dataset.opt.toUpperCase() === opt;
    });
    if (!target) return false;

    selectChallengeOption(target, opt);
    return true;
}

function resolveConfidenceByKey(key) {
    var k = (key || '').toLowerCase();
    if (k === 'q') return 'sure';
    if (k === 'w') return 'unsure';
    if (k === 'e') return 'no';
    return null;
}

function trySetSurgeryConfidenceByKey(key) {
    var conf = resolveConfidenceByKey(key);
    if (!conf) return false;
    if (!document.getElementById('conf-' + conf)) return false;
    setConfidence(conf);
    return true;
}

function trySetChallengeConfidenceByKey(key) {
    var conf = resolveConfidenceByKey(key);
    if (!conf) return false;
    if (!document.getElementById('ch-conf-' + conf)) return false;
    setChallengeConfidence(conf);
    return true;
}

document.addEventListener('keydown', function(e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    if (isTypingTarget(e.target)) return;

    var handled = false;
    if (isModalVisible('surgeryModal')) {
        handled = trySetSurgeryConfidenceByKey(e.key);
        if (!handled) handled = trySelectSurgeryOptionByKey(e.key);

        // Enter 提交答案
        if (!handled && e.key === 'Enter') {
            var submitBtn = document.getElementById('submitRetryBtn');
            if (submitBtn && !submitBtn.disabled) {
                submitRetry();
                handled = true;
            }
        }
    } else if (isModalVisible('challengeModal')) {
        handled = trySetChallengeConfidenceByKey(e.key);
        if (!handled) handled = trySelectChallengeOptionByKey(e.key);

        // Enter 提交答案
        if (!handled && e.key === 'Enter') {
            var chSubmitBtn = document.getElementById('chSubmitBtn');
            if (chSubmitBtn && !chSubmitBtn.disabled) {
                submitChallengeAnswer();
                handled = true;
            }
        }
    }

    if (handled) {
        e.preventDefault();
    }
});

// ========== 融合升级功能 (Merge & Upgrade) ==========

// 1. 解锁检查与苏格拉底引导
async function checkFusionUnlock(questionId) {
    try {
        var result = await safeFetch('/api/fusion/' + questionId + '/unlock-check', { method: 'POST' }, '请求失败');

        if (!result.can_unlock) {
            alert('🔒 ' + result.reason);
            return;
        }

        // 条件满足，打开苏格拉底引导
        currentFusionSourceId = questionId;
        openSocraticGuide(questionId);
    } catch (e) {
        alert('检查失败: ' + e.message);
    }
}

async function openSocraticGuide(questionId) {
    var modal = document.getElementById('socraticGuideModal');
    var content = document.getElementById('socraticGuideContent');

    modal.classList.remove('hidden');
    content.innerHTML = '<div class="text-center py-8"><div class="animate-spin w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full mx-auto mb-4"></div><div class="text-gray-500">AI导师思考中...</div></div>';

    try {
        var result = await safeFetch('/api/fusion/' + questionId + '/socratic-hint', {}, '请求失败');

        if (result.error || result.detail) {
            content.innerHTML = '<div class="text-red-500">' + escapeHtml(result.error || result.detail) + '</div>';
            return;
        }

        var html = '<div class="space-y-4">';

        // 显示引导问题
        if (result.guide_questions && result.guide_questions.length > 0) {
            html += '<div class="bg-blue-50 border border-blue-200 rounded-lg p-4">';
            html += '<div class="text-sm font-bold text-blue-700 mb-3">🤔 思考以下问题：</div>';
            html += '<div class="space-y-3">';
            result.guide_questions.forEach(function(q, i) {
                html += '<div class="flex items-start">';
                html += '<span class="flex-shrink-0 w-6 h-6 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs mr-3">' + (i + 1) + '</span>';
                html += '<div class="text-gray-700 text-sm">' + escapeHtml(q) + '</div>';  // ✅ 转义引导问题
                html += '</div>';
            });
            html += '</div>';
            html += '</div>';
        }

        // 显示提示文本
        if (result.hint_text) {
            html += '<div class="bg-yellow-50 border border-yellow-200 rounded-lg p-4">';
            html += '<div class="text-sm font-bold text-yellow-700 mb-2">💡 提示：</div>';
            html += '<div class="text-gray-700 text-sm">' + escapeHtml(result.hint_text) + '</div>';  // ✅ 转义提示文本
            html += '</div>';
        }

        // 显示完整提示文本（可折叠）
        if (result.hint_text) {
            html += '<details class="mt-4">';
            html += '<summary class="text-sm text-gray-500 cursor-pointer hover:text-gray-700">查看完整引导</summary>';
            html += '<div class="mt-2 p-3 bg-gray-50 rounded text-sm text-gray-600">' + escapeHtml(result.hint_text).replace(/\\n/g, '<br>') + '</div>';
            html += '</details>';
        }

        html += '</div>';
        content.innerHTML = html;

    } catch (e) {
        content.innerHTML = '<div class="text-red-500">加载失败: ' + e.message + '</div>';
    }
}

function closeSocraticGuide() {
    document.getElementById('socraticGuideModal').classList.add('hidden');
}

async function proceedToSelectPartners() {
    closeSocraticGuide();
    await openFusionSelect();
}

// 2. 融合伙伴选择
async function openFusionSelect() {
    var modal = document.getElementById('fusionSelectModal');
    var list = document.getElementById('fusionCandidatesList');

    modal.classList.remove('hidden');
    selectedFusionPartners = [];
    updateSelectedCount();

    list.innerHTML = '<div class="text-center py-8 text-gray-400">加载中...</div>';

    try {
        // 获取源题信息
        var sourceQuestion = null;
        if (currentFusionSourceId) {
            sourceQuestion = await safeFetch('/api/wrong-answers/' + currentFusionSourceId, {}, '操作失败');
        }

        fusionCandidates = await safeFetch('/api/fusion/archived-candidates?exclude_id=' + (currentFusionSourceId || ''), {}, '操作失败');

        renderFusionCandidates(sourceQuestion);
    } catch (e) {
        list.innerHTML = '<div class="text-center py-8 text-red-500">加载失败: ' + e.message + '</div>';
    }
}

function renderFusionCandidates(sourceQuestion) {
    var list = document.getElementById('fusionCandidatesList');

    if (fusionCandidates.length === 0) {
        list.innerHTML = '<div class="text-center py-8 text-gray-400">暂无其他可融合的已归档错题</div>';
        return;
    }

    var html = '<div class="space-y-2">';

    // 显示源题信息
    if (sourceQuestion) {
        html += '<div class="mb-4 p-4 bg-blue-50 border-2 border-blue-300 rounded-lg">';
        html += '<div class="flex items-center mb-2">';
        html += '<span class="text-xs bg-blue-500 text-white px-2 py-1 rounded font-bold mr-2">源题</span>';
        html += '<span class="text-sm font-medium text-blue-800">你选择的起点</span>';
        html += '</div>';
        html += '<div class="text-sm text-gray-800 mb-1">' + escapeHtml(sourceQuestion.question_text.substring(0, 150)) + (sourceQuestion.question_text.length > 150 ? '...' : '') + '</div>';
        if (sourceQuestion.key_point) {
            html += '<div class="text-xs text-blue-600">📌 ' + escapeHtml(sourceQuestion.key_point) + '</div>';
        }
        html += '</div>';
    }

    html += '<div class="text-sm text-gray-500 mb-2">💡 点击选择 1-3 道题与源题融合（总共 2-4 道）</div>';

    fusionCandidates.forEach(function(c) {
        var isSelected = selectedFusionPartners.indexOf(c.id) > -1;

        html += '<div onclick="toggleFusionPartner(' + c.id + ')" ';
        html += 'class="p-4 border-2 rounded-lg cursor-pointer transition ';
        html += isSelected ? 'border-orange-500 bg-orange-50 ' : 'border-gray-200 hover:border-gray-300 ';
        html += '">';

        html += '<div class="flex items-start justify-between">';
        html += '<div class="flex-1">';
        html += '<div class="text-sm text-gray-800 mb-1">' + escapeHtml(c.question_text.substring(0, 150)) + (c.question_text.length > 150 ? '...' : '') + '</div>';
        if (c.key_point) {
            html += '<div class="text-xs text-blue-600">📌 ' + escapeHtml(c.key_point) + '</div>';
        }
        html += '</div>';

        html += '<div class="ml-3 flex-shrink-0">';
        if (isSelected) {
            html += '<span class="text-2xl">✅</span>';
        }
        html += '</div>';

        html += '</div></div>';
    });

    html += '</div>';
    list.innerHTML = html;
}

function toggleFusionPartner(id) {
    if (id === currentFusionSourceId) return;

    var idx = selectedFusionPartners.indexOf(id);
    if (idx > -1) {
        selectedFusionPartners.splice(idx, 1);
    } else {
        if (selectedFusionPartners.length >= 3) {
            alert('⚠️ 最多只能选择4道题进行融合（包括源题）');
            return;
        }
        selectedFusionPartners.push(id);
    }

    renderFusionCandidates();
    updateSelectedCount();
}

function updateSelectedCount() {
    var totalCount = selectedFusionPartners.length + (currentFusionSourceId ? 1 : 0);
    var countEl = document.getElementById('selectedCount');
    var btn = document.getElementById('createFusionBtn');

    countEl.textContent = '已选择: ' + totalCount + '/4';

    if (totalCount >= 2) {
        btn.disabled = false;
        btn.className = 'bg-orange-500 text-white px-4 py-2 rounded hover:bg-orange-600';
    } else {
        btn.disabled = true;
        btn.className = 'bg-gray-300 text-white px-4 py-2 rounded cursor-not-allowed';
    }
}

function closeFusionSelect() {
    document.getElementById('fusionSelectModal').classList.add('hidden');
    selectedFusionPartners = [];
}

async function createFusion() {
    var parentIds = [currentFusionSourceId].concat(selectedFusionPartners);

    var btn = document.getElementById('createFusionBtn');
    btn.disabled = true;
    btn.textContent = '创建中...';

    try {
        var result = await safeFetch('/api/fusion/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ parent_ids: parentIds })
        }, '请求失败');

        if (result.detail) {
            alert('❌ ' + result.detail);
            btn.disabled = false;
            btn.textContent = '创建融合题';
            return;
        }

        closeFusionSelect();
        openFusionAnswer(result.fusion_id, result.fusion_question, result.fusion_level, parentIds);

    } catch (e) {
        alert('创建失败: ' + e.message);
        btn.disabled = false;
        btn.textContent = '创建融合题';
    }
}

// 3. 融合题答题
function openFusionAnswer(fusionId, question, level, parentIds) {
    currentFusionId = fusionId;
    fusionJudgePending = false;

    document.getElementById('fusionLevelBadge').textContent = '🔥 L' + level;
    document.getElementById('fusionQuestionText').textContent = question;
    document.getElementById('fusionUserAnswer').value = '';
    document.getElementById('fusionAnswerCount').textContent = '0 字';
    document.getElementById('metacognitiveHint').classList.add('hidden');

    var parentsHtml = '';
    parentIds.forEach(function(pid, i) {
        parentsHtml += '<span class="bg-gray-200 text-gray-700 px-2 py-1 rounded text-xs">原题' + (i + 1) + '</span>';
    });
    document.getElementById('fusionParentsList').innerHTML = parentsHtml;

    document.getElementById('fusionAnswerModal').classList.remove('hidden');
}

function closeFusionAnswer() {
    document.getElementById('fusionAnswerModal').classList.add('hidden');
    // 注意：不在这里清空 currentFusionId，因为评判/诊断/归档还需要它
    // currentFusionId 在 closeFusionJudge() 和 closeDiagnosis() 中清空
}

async function submitFusionAnswer() {
    var answer = document.getElementById('fusionUserAnswer').value.trim();

    if (answer.length < 10) {
        alert('请至少输入10字');
        return;
    }

    try {
        var result = await safeFetch('/api/fusion/' + currentFusionId + '/submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_answer: answer })
        }, '请求失败');

        fusionJudgePending = true;
        document.getElementById('metacognitiveHint').classList.remove('hidden');

        alert('✅ ' + result.message);

    } catch (e) {
        alert('提交失败: ' + e.message);
    }
}

async function requestFusionJudge() {
    if (!fusionJudgePending) {
        alert('请先提交答案');
        return;
    }

    if (!confirm('🤔 在请求AI评判前，请再次审视你的答案。\n\n逻辑是否严密？概念使用是否准确？\n\n确定要请求评判吗？')) {
        return;
    }

    var btn = document.getElementById('fusionJudgeBtn');
    btn.disabled = true;
    btn.textContent = '评判中...';

    try {
        var result = await safeFetch('/api/fusion/' + currentFusionId + '/judge', { method: 'POST' }, '请求失败');

        if (result.detail) {
            alert('❌ ' + result.detail);
            btn.disabled = false;
            btn.textContent = '🔍 请求评判';
            return;
        }

        closeFusionAnswer();
        showFusionJudgeResult(result);

    } catch (e) {
        alert('评判失败: ' + e.message);
        btn.disabled = false;
        btn.textContent = '🔍 请求评判';
    }
}

// 4. 评判结果展示
function showFusionJudgeResult(result) {
    var modal = document.getElementById('fusionJudgeModal');
    var header = document.getElementById('judgeHeader');
    var content = document.getElementById('judgeContent');
    var actions = document.getElementById('judgeActions');

    modal.classList.remove('hidden');

    var isCorrect = result.verdict === 'correct';
    var isPartial = result.verdict === 'partial';

    if (isCorrect) {
        header.className = 'px-6 py-4 border-b bg-green-50';
        header.innerHTML = '<h3 class="font-bold text-xl text-green-700">✅ 逻辑闭环！</h3>';
    } else if (isPartial) {
        header.className = 'px-6 py-4 border-b bg-yellow-50';
        header.innerHTML = '<h3 class="font-bold text-xl text-yellow-700">⚠️ 部分正确</h3>';
    } else {
        header.className = 'px-6 py-4 border-b bg-red-50';
        header.innerHTML = '<h3 class="font-bold text-xl text-red-700">❌ 需要改进</h3>';
    }

    var html = '<div class="space-y-4">';

    var scoreColor = result.score >= 70 ? 'bg-green-500' : (result.score >= 40 ? 'bg-yellow-500' : 'bg-red-500');
    html += '<div class="bg-gray-50 rounded-lg p-4">';
    html += '<div class="flex justify-between items-center mb-2">';
    html += '<span class="text-sm font-medium text-gray-700">推理评分</span>';
    html += '<span class="text-2xl font-bold ' + (result.score >= 70 ? 'text-green-600' : (result.score >= 40 ? 'text-yellow-600' : 'text-red-600')) + '">' + result.score + '<span class="text-sm text-gray-400">/100</span></span>';
    html += '</div>';
    html += '<div class="w-full bg-gray-200 rounded-full h-3">';
    html += '<div class="' + scoreColor + ' h-3 rounded-full transition-all" style="width:' + result.score + '%"></div>';
    html += '</div></div>';

    if (result.feedback) {
        html += '<div class="bg-blue-50 rounded-lg p-4">';
        html += '<div class="text-sm font-bold text-blue-700 mb-2">📝 AI反馈</div>';
        html += '<div class="text-sm text-gray-700 leading-relaxed">' + escapeHtml(result.feedback).replace(/\\n/g, '<br>') + '</div>';
        html += '</div>';
    }

    if (result.weak_links && result.weak_links.length > 0) {
        html += '<div><div class="text-sm font-bold text-gray-700 mb-2">🎯 薄弱环节</div>';
        html += '<div class="flex flex-wrap gap-2">';
        result.weak_links.forEach(function(w) {
            html += '<span class="bg-red-100 text-red-700 px-3 py-1 rounded-full text-xs">' + escapeHtml(w) + '</span>';
        });
        html += '</div></div>';
    }

    html += '</div>';
    content.innerHTML = html;

    var btnHtml = '<button onclick="closeFusionJudge()" class="text-gray-600 hover:text-gray-800">关闭</button>';

    if (result.needs_diagnosis) {
        btnHtml += '<button onclick="startDiagnosis()" class="bg-orange-500 text-white px-6 py-2 rounded hover:bg-orange-600 font-bold">🩺 进入诊断模式</button>';
    } else if (isCorrect) {
        btnHtml += '<button onclick="archiveFusion()" class="bg-green-500 text-white px-6 py-2 rounded hover:bg-green-600 font-bold">✅ 标记已掌握</button>';
    }

    actions.innerHTML = btnHtml;
}

function closeFusionJudge() {
    document.getElementById('fusionJudgeModal').classList.add('hidden');
    currentFusionId = null;  // 评判流程结束，清空
}

// 5. 诊断模式
function startDiagnosis() {
    closeFusionJudge();

    var modal = document.getElementById('diagnosisModal');
    var summary = document.getElementById('diagnosisAnswerSummary');

    var answer = document.getElementById('fusionUserAnswer')?.value || '（答案已缓存）';
    summary.textContent = answer.substring(0, 200) + (answer.length > 200 ? '...' : '');

    document.getElementById('diagnosisReflection').value = '';
    modal.classList.remove('hidden');
}

function closeDiagnosis() {
    document.getElementById('diagnosisModal').classList.add('hidden');
    currentFusionId = null;  // 诊断流程结束，清空
}

async function submitDiagnosis() {
    var reflection = document.getElementById('diagnosisReflection').value.trim();

    if (reflection.length < 20) {
        alert('请至少输入20字');
        return;
    }

    var answer = document.getElementById('fusionUserAnswer')?.value || '';

    try {
        var result = await safeFetch('/api/fusion/' + currentFusionId + '/diagnose', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_answer: answer,
                reflection: reflection
            })
        }, '请求失败');

        if (result.detail) {
            alert('❌ ' + result.detail);
            return;
        }

        closeDiagnosis();

        var diagnosisText = '';
        if (result.diagnosis_type === 'concept_forgot') {
            diagnosisText = '📚 诊断结果：概念遗忘';
        } else if (result.diagnosis_type === 'relation_error') {
            diagnosisText = '🔗 诊断结果：关系理解错误';
        } else {
            diagnosisText = '🔍 诊断结果：两者皆有';
        }

        diagnosisText += '\n\n' + result.analysis;
        diagnosisText += '\n\n💡 建议：' + result.recommendation;

        if (result.affected_parent_ids && result.affected_parent_ids.length > 0) {
            diagnosisText += '\n\n⚠️ 已恢复 ' + result.affected_parent_ids.length + ' 道原题为活跃状态，需要重新复习';
        }

        alert(diagnosisText);
        loadList();

    } catch (e) {
        alert('诊断失败: ' + e.message);
    }
}

async function archiveFusion() {
    try {
        var result = await safeFetch('/api/fusion/' + currentFusionId + '/archive', { method: 'POST' }, '请求失败');

        alert('✅ ' + result.message);
        currentFusionId = null;
        closeFusionJudge();
        loadList();
    } catch (e) {
        alert('归档失败: ' + e.message);
    }
}

// 6. 融合升级入口按钮（在已归档题目中显示）
function renderFusionButton(wrongAnswer) {
    if (wrongAnswer.mastery_status !== 'archived') return '';
    if (wrongAnswer.is_fusion) return '';

    return '<button onclick="event.stopPropagation(); checkFusionUnlock(' + wrongAnswer.id + ')" ' +
           'class="ml-2 text-xs bg-orange-100 text-orange-700 px-2 py-1 rounded hover:bg-orange-200 transition" ' +
           'title="将已掌握的概念与其他概念融合">🔍 寻找融合伙伴</button>';
}

// ========== 添加点击外部关闭弹窗 ==========
document.getElementById('socraticGuideModal')?.addEventListener('click', function(e) {
    if (e.target === this) closeSocraticGuide();
});
document.getElementById('fusionSelectModal')?.addEventListener('click', function(e) {
    if (e.target === this) closeFusionSelect();
});
document.getElementById('fusionAnswerModal')?.addEventListener('click', function(e) {
    if (e.target === this) closeFusionAnswer();
});
document.getElementById('fusionJudgeModal')?.addEventListener('click', function(e) {
    if (e.target === this) closeFusionJudge();
});
document.getElementById('diagnosisModal')?.addEventListener('click', function(e) {
    if (e.target === this) closeDiagnosis();
});