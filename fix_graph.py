"""
修复graph.html的Mermaid渲染问题
"""

html_content = '''{% extends "base.html" %}

{% block title %}知识图谱 - True Learning System{% endblock %}

{% block content %}
<!-- 页面头部 -->
<div class="mb-6">
    <div class="flex items-center text-sm text-gray-500 mb-2">
        <a href="/" class="hover:text-blue-500">仪表盘</a>
        <span class="mx-2">/</span>
        <span>知识图谱</span>
    </div>
    
    <h1 class="text-2xl font-bold">🕸️ 知识图谱</h1>
    <p class="text-gray-500">可视化你的学习进度和知识连接</p>
</div>

<div class="grid grid-cols-1 lg:grid-cols-4 gap-6">
    <!-- 左侧控制面板 -->
    <div class="lg:col-span-1">
        <!-- 学科选择 -->
        <div class="bg-white rounded-lg shadow p-4 mb-4">
            <h3 class="font-semibold mb-3">选择学科</h3>
            
            <select id="bookSelect" onchange="loadGraph()" 
                    class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-purple-500">
                <option value="">-- 请选择 --</option>
                {% for book in books %}
                <option value="{{ book }}">{{ book }}</option>
                {% endfor %}
            </select>
        </div>

        <!-- 图例 -->
        <div class="bg-white rounded-lg shadow p-4 mb-4">
            <h3 class="font-semibold mb-3">图例</h3>
            
            <div class="space-y-2">
                <div class="flex items-center">
                    <div class="w-4 h-4 rounded-full bg-green-500 mr-2"></div>
                    <span class="text-sm">已掌握 (&gt;80%)</span>
                </div>
                <div class="flex items-center">
                    <div class="w-4 h-4 rounded-full bg-yellow-500 mr-2"></div>
                    <span class="text-sm">学习中 (50-80%)</span>
                </div>
                <div class="flex items-center">
                    <div class="w-4 h-4 rounded-full bg-red-500 mr-2"></div>
                    <span class="text-sm">薄弱 (&lt;50%)</span>
                </div>
                <div class="flex items-center">
                    <div class="w-4 h-4 rounded-full bg-gray-300 mr-2"></div>
                    <span class="text-sm">未学习</span>
                </div>
            </div>
        </div>

        <!-- 节点信息 (点击后显示) -->
        <div id="nodeInfo" class="bg-white rounded-lg shadow p-4 hidden">
            <h3 class="font-semibold mb-3">节点信息</h3>
            
            <div id="nodeDetails">
                <!-- 动态填充 -->
            </div>
        </div>

        <!-- 统计信息 -->
        <div class="bg-white rounded-lg shadow p-4">
            <h3 class="font-semibold mb-3">统计</h3>
            
            <div class="space-y-2 text-sm">
                <div class="flex justify-between">
                    <span>总节点:</span>
                    <span id="totalNodes" class="font-medium">0</span>
                </div>
                <div class="flex justify-between">
                    <span>连接数:</span>
                    <span id="totalLinks" class="font-medium">0</span>
                </div>
            </div>
        </div>
    </div>

    <!-- 右侧图谱区域 -->
    <div class="lg:col-span-3">
        <div class="bg-white rounded-lg shadow p-4">
            <!-- 工具栏 -->
            <div class="flex justify-between items-center mb-4">
                <div class="flex space-x-2">
                    <button onclick="resetZoom()" class="px-3 py-1 bg-gray-100 rounded hover:bg-gray-200 transition text-sm">
                        重置视图
                    </button>
                    <button onclick="exportGraph()" class="px-3 py-1 bg-gray-100 rounded hover:bg-gray-200 transition text-sm">
                        导出图片
                    </button>
                </div>
                
                <div id="loadingIndicator" class="hidden text-gray-500 text-sm">
                    加载中...
                </div>
            </div>

            <!-- Mermaid 图谱容器 -->
            <div id="graphContainer" class="border rounded-lg overflow-hidden min-h-[400px]">
                <div class="p-8 text-center text-gray-400">
                    <p>请从左侧选择一个学科来查看知识图谱</p>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
    let currentGraphData = null;
    let mermaidInitialized = false;
    let idMapping = {};  // 原始ID到安全ID的映射
    let nodeData = {};   // 安全ID到节点数据的映射

    // 初始化 Mermaid
    document.addEventListener('DOMContentLoaded', function() {
        mermaid.initialize({
            startOnLoad: false,
            theme: 'default',
            flowchart: {
                useMaxWidth: true,
                htmlLabels: true,
                curve: 'basis'
            }
        });
        mermaidInitialized = true;
    });

    // 加载图谱
    async function loadGraph() {
        const bookSelect = document.getElementById('bookSelect');
        const book = bookSelect.value;
        
        if (!book) return;
        
        // 显示加载指示器
        document.getElementById('loadingIndicator').classList.remove('hidden');
        
        try {
            const response = await fetch(`/api/graph/${encodeURIComponent(book)}`);
            
            if (!response.ok) throw new Error('加载失败');
            
            const data = await response.json();
            currentGraphData = data;
            
            // 更新统计
            document.getElementById('totalNodes').textContent = data.nodes.length;
            document.getElementById('totalLinks').textContent = data.links.length;
            
            // 渲染图谱
            renderGraph(data);
            
        } catch (error) {
            console.error('加载图谱失败:', error);
            document.getElementById('graphContainer').innerHTML = 
                '<div class="p-8 text-center text-red-500">加载失败: ' + error.message + '</div>';
        } finally {
            document.getElementById('loadingIndicator').classList.add('hidden');
        }
    }

    // 渲染图谱 - 使用安全ID
    function renderGraph(data) {
        const container = document.getElementById('graphContainer');
        
        // 重置映射
        idMapping = {};
        nodeData = {};
        
        // 生成安全的Mermaid定义
        let graphDef = 'graph TD\\n';
        
        // 添加节点 - 使用数字ID避免特殊字符问题
        data.nodes.forEach((node, index) => {
            const safeId = 'n' + index;  // n0, n1, n2...
            idMapping[node.id] = safeId;
            
            // 清理节点名称 - 转义特殊字符
            let safeName = node.name
                .replace(/"/g, '#quot;')
                .replace(/\\\\/g, '\\\\')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/\\|/g, '\\|')
                .replace(/\\[/g, '\\[')
                .replace(/\\]/g, '\\]')
                .replace(/\\(/g, '\\(')
                .replace(/\\)/g, '\\)');
            
            // 限制长度
            if (safeName.length > 25) {
                safeName = safeName.substring(0, 22) + '...';
            }
            
            // 确定颜色
            const mastery = node.mastery || 0;
            let color;
            if (mastery >= 0.8) color = '#4ade80';
            else if (mastery >= 0.5) color = '#facc15';
            else if (mastery > 0) color = '#f87171';
            else color = '#d1d5db';
            
            // 保存节点数据
            nodeData[safeId] = node;
            
            // 节点定义
            graphDef += `    ${safeId}["${safeName}"]\\n`;
            graphDef += `    style ${safeId} fill:${color},stroke:#374151,stroke-width:2px\\n`;
        });
        
        // 添加连接
        data.links.forEach(link => {
            const source = idMapping[link.source];
            const target = idMapping[link.target];
            
            if (source && target) {
                graphDef += `    ${source} --> ${target}\\n`;
            }
        });
        
        console.log('Mermaid定义:', graphDef);  // 调试用
        
        // 渲染
        container.innerHTML = '<div class="mermaid">' + graphDef + '</div>';
        
        // 触发Mermaid渲染
        if (mermaidInitialized) {
            try {
                mermaid.init(undefined, container.querySelectorAll('.mermaid'));
            } catch (e) {
                console.error('Mermaid渲染错误:', e);
                container.innerHTML = '<div class="p-8 text-center text-red-500">图谱渲染失败，请刷新重试</div>';
                return;
            }
        }
        
        // 添加点击事件
        setTimeout(() => {
            addNodeClickHandlers();
        }, 600);
    }

    // 添加节点点击处理
    function addNodeClickHandlers() {
        // 查找所有节点元素
        const nodeElements = document.querySelectorAll('.mermaid .node');
        
        nodeElements.forEach(el => {
            el.style.cursor = 'pointer';
            el.addEventListener('click', function(e) {
                e.stopPropagation();
                
                // 从class或id中提取安全ID
                const idAttr = el.id || '';
                const classAttr = el.getAttribute('class') || '';
                
                // 尝试匹配安全ID
                let safeId = null;
                for (const [sid, data] of Object.entries(nodeData)) {
                    if (idAttr.includes(sid) || classAttr.includes(sid)) {
                        safeId = sid;
                        break;
                    }
                }
                
                if (safeId && nodeData[safeId]) {
                    showNodeInfo(nodeData[safeId]);
                }
            });
        });
    }

    // 显示节点信息
    function showNodeInfo(node) {
        const nodeInfo = document.getElementById('nodeInfo');
        const nodeDetails = document.getElementById('nodeDetails');
        
        const masteryPercent = Math.round((node.mastery || 0) * 100);
        
        let color;
        if (masteryPercent >= 80) color = '#4ade80';
        else if (masteryPercent >= 50) color = '#facc15';
        else if (masteryPercent > 0) color = '#f87171';
        else color = '#d1d5db';
        
        nodeDetails.innerHTML = `
            <div class="mb-3">
                <div class="font-medium text-lg">${node.name}</div>
                <div class="text-sm text-gray-500">${node.chapter || ''}</div>
            </div>
            
            <div class="mb-3">
                <div class="flex justify-between text-sm mb-1">
                    <span>掌握度</span>
                    <span>${masteryPercent}%</span>
                </div>
                <div class="w-full bg-gray-200 rounded-full h-2">
                    <div class="h-2 rounded-full" 
                         style="width: ${masteryPercent}%; background-color: ${color}"></div>
                </div>
            </div>
            
            <div class="flex space-x-2">
                <a href="/quiz/${node.id}" 
                   class="flex-1 bg-blue-500 text-white text-center py-2 rounded text-sm hover:bg-blue-600 transition">
                    测试
                </a>
                <a href="/feynman/${node.id}" 
                   class="flex-1 bg-purple-500 text-white text-center py-2 rounded text-sm hover:bg-purple-600 transition">
                    费曼
                </a>
            </div>
        `;
        
        nodeInfo.classList.remove('hidden');
    }

    // 重置视图
    function resetZoom() {
        if (currentGraphData) {
            renderGraph(currentGraphData);
        }
    }

    // 导出图片
    function exportGraph() {
        const svg = document.querySelector('.mermaid svg');
        if (!svg) {
            alert('请先加载图谱');
            return;
        }
        
        // 创建 Canvas
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const svgData = new XMLSerializer().serializeToString(svg);
        
        const img = new Image();
        img.onload = function() {
            canvas.width = img.width;
            canvas.height = img.height;
            ctx.drawImage(img, 0, 0);
            
            // 下载
            const link = document.createElement('a');
            link.download = 'knowledge-graph.png';
            link.href = canvas.toDataURL();
            link.click();
        };
        
        img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgData)));
    }
</script>

<style>
    .mermaid {
        font-family: 'Inter', sans-serif;
    }
    
    .mermaid .node rect,
    .mermaid .node circle,
    .mermaid .node ellipse,
    .mermaid .node polygon {
        stroke-width: 2px;
    }
    
    .mermaid .edgePath .path {
        stroke: #666;
        stroke-width: 1.5px;
    }
</style>
{% endblock %}
'''

with open('templates/graph.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print('✅ graph.html 已修复')
