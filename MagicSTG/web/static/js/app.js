// 全局变量
let allStrategiesList = [];
let currentDetailRunData = [];
let currentDetailCategory = 'stock';
let globalTaskRunning = false;
let currentTaskName = '';
let activeDetailStgDbId = null;
let activeExecStgDbId = null;
let activeExecMode = 'RUN'; // 'RUN' or 'BACKTEST'
let allWorkflowStrategiesList = [];
let allBacktestRecords = [];
let currentDetailSignalDate = null;
let currentDetailStrategyId = null;
let currentDetailStrategyName = null;
let portfolioChartInstance = null;

// 切换主 Tab 导航
function switchMainTab(tabName) {
    document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-section').forEach(sec => sec.style.display = 'none');

    if (tabName === 'strategies') {
        document.getElementById('tabNavStrategies').classList.add('active');
        document.getElementById('sectionStrategies').style.display = 'block';
        loadStrategies();
    } else if (tabName === 'workflow') {
        document.getElementById('tabNavWorkflow').classList.add('active');
        document.getElementById('sectionWorkflow').style.display = 'block';
        loadWorkflowStrategies();
    } else if (tabName === 'portfolio') {
        document.getElementById('tabNavPortfolio').classList.add('active');
        document.getElementById('sectionPortfolio').style.display = 'block';
        loadPortfolioStrategyOptions().then(() => loadPortfolioData());
    } else if (tabName === 'backtests') {
        document.getElementById('tabNavBacktests').classList.add('active');
        document.getElementById('sectionBacktests').style.display = 'block';
        loadBacktests();
    } else if (tabName === 'recommendations') {
        document.getElementById('tabNavRecommendations').classList.add('active');
        document.getElementById('sectionRecommendations').style.display = 'block';
        loadRecommendationRuns();
    }
}

// 辅助函数: 获取排序因子中文名称
function getSortByLabel(key) {
    const map = {
        'db_low_value': '双低值优先',
        'cb_price': '转债价格优先',
        'convert_premium_rate': '转股溢价率优先',
        'ytm': 'YTM到期收益率优先',
        'roe': '高ROE优先',
        'pe': '低估值PE优先',
        'pb': '低PB市净率优先',
        'growth': '净利润增长率优先',
        'price': '低股价优先'
    };
    return map[key] || key || '综合因子';
}

// 1. 加载策略卡片列表
async function loadStrategies() {
    const search = document.getElementById('searchStrategyInput').value;
    const category = document.getElementById('filterCategorySelect').value;
    const grid = document.getElementById('strategyCardGrid');

    try {
        const res = await fetch(`/api/strategies?search=${encodeURIComponent(search)}&category=${category}`);
        const result = await res.json();

        if (result.status !== 'success') {
            grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:#df4b5e; padding:2rem;">${result.message}</div>`;
            return;
        }

        allStrategiesList = result.data;
        if (allStrategiesList.length === 0) {
            grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:#8e95b2; padding:2.5rem;">暂无匹配自定义策略</div>`;
            return;
        }

        grid.innerHTML = allStrategiesList.map(stg => {
            const isCb = stg.category === 'convertible_bond';
            const catBadge = isCb 
                ? `<span class="badge-tag badge-cb"><i class="fa-solid fa-link"></i> 可转债</span>` 
                : `<span class="badge-tag badge-stock"><i class="fa-solid fa-chart-line"></i> 股票</span>`;

            const activeBadge = stg.is_active
                ? `<span class="badge-tag badge-active"><i class="fa-solid fa-circle-play"></i> 盘后推演中</span>`
                : `<span class="badge-tag badge-inactive"><i class="fa-solid fa-circle-pause"></i> 草稿/关闭</span>`;

            const cfg = typeof stg.factors_config === 'object' ? stg.factors_config : (JSON.parse(stg.factors_config || '{}'));
            let factorTags = [];
            
            if (isCb) {
                if (cfg.max_price) factorTags.push(`最高债价 ≤ ${cfg.max_price}`);
                if (cfg.min_convert_premium !== undefined) factorTags.push(`溢价率 [${cfg.min_convert_premium}%, ${cfg.max_convert_premium}%]`);
                if (cfg.sort_by) factorTags.push(getSortByLabel(cfg.sort_by));
            } else {
                if (cfg.stock_scope) factorTags.push(`标的池: ${cfg.stock_scope === 'csi300' ? '沪深300' : '全市场'}`);
                if (cfg.financial?.roe_min || cfg.min_roe) {
                    const val = cfg.financial?.roe_min || cfg.min_roe;
                    factorTags.push(`ROE ≥ ${(val * 100).toFixed(0)}%`);
                }
                if (cfg.financial?.pe_max || cfg.pe_range) {
                    const pMax = cfg.financial?.pe_max || (cfg.pe_range ? cfg.pe_range[1] : null);
                    if (pMax) factorTags.push(`PE ≤ ${pMax}`);
                }
                if (cfg.sort_by) factorTags.push(getSortByLabel(cfg.sort_by));
            }
            if (cfg.top_n) factorTags.push(`Top ${cfg.top_n} 选中`);

            const tagsHtml = factorTags.slice(0, 4).map(t => `<span class="factor-summary-badge">${t}</span>`).join(' ');

            return `
                <div class="stg-card ${isCb ? 'cb-stg-card' : ''}" style="border: 1px solid ${stg.is_active ? 'rgba(0, 210, 196, 0.4)' : 'rgba(255, 255, 255, 0.08)'};" onclick="openStrategyDetailModal(${stg.id})">
                    <div>
                        <div class="stg-card-header">
                            <div>
                                ${catBadge}
                                ${activeBadge}
                            </div>
                            <code style="font-size: 0.78rem; color: #8e95b2;">${stg.strategy_id}</code>
                        </div>
                        <div class="stg-card-title">${stg.name}</div>
                        <div class="stg-card-desc">${stg.description || '无策略描述说明'}</div>
                        <div class="stg-card-tags">
                            ${tagsHtml}
                        </div>
                    </div>

                    <div class="stg-card-footer" style="display: flex; justify-content: space-between; align-items: center;" onclick="event.stopPropagation();">
                        <div class="switch-box" title="开启后此策略将在每日 18:31 盘后工作流中自动运行并发送邮件到您的邮箱">
                            <label class="switch">
                                <input type="checkbox" ${stg.is_active ? 'checked' : ''} onchange="toggleStrategyActiveAction(${stg.id})">
                                <span class="slider round"></span>
                            </label>
                            <span style="font-size: 0.78rem; color: ${stg.is_active ? '#00d2c4' : '#8e95b2'}; font-weight: 600;">
                                ${stg.is_active ? '盘后推演: 开启' : '盘后推演: 关闭'}
                            </span>
                        </div>
                        <button class="btn btn-primary" style="padding: 0.3rem 0.75rem; font-size: 0.78rem;" onclick="openStrategyDetailModal(${stg.id})">
                            <i class="fa-solid fa-folder-open"></i> 策略详情
                        </button>
                    </div>
                </div>
            `;
        }).join('');

    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:#df4b5e; padding:2rem;">加载失败: ${e.message}</div>`;
    }
}

// 1.5 加载每日盘后工作流策略配置池
async function loadWorkflowStrategies() {
    const searchInput = document.getElementById('searchWorkflowStrategyInput');
    const search = searchInput ? searchInput.value.trim() : '';
    const catSelect = document.getElementById('filterWorkflowCategorySelect');
    const category = catSelect ? catSelect.value : 'all';
    const grid = document.getElementById('workflowStrategyCardGrid');
    const badge = document.getElementById('workflowActiveStatsBadge');

    try {
        const res = await fetch(`/api/strategies?search=${encodeURIComponent(search)}&category=${category}`);
        const result = await res.json();

        if (result.status !== 'success') {
            grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:#df4b5e; padding:2rem;">${result.message}</div>`;
            return;
        }

        allWorkflowStrategiesList = result.data;
        const activeCount = allWorkflowStrategiesList.filter(s => s.is_active).length;
        if (badge) {
            badge.innerHTML = `<i class="fa-solid fa-circle-check"></i> 盘后工作流激活策略: <strong>${activeCount}</strong> / ${allWorkflowStrategiesList.length}`;
        }

        if (allWorkflowStrategiesList.length === 0) {
            grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:#8e95b2; padding:2.5rem;">暂无自定义策略记录</div>`;
            return;
        }

        grid.innerHTML = allWorkflowStrategiesList.map(stg => {
            const isCb = stg.category === 'convertible_bond';
            const catBadge = isCb 
                ? `<span class="badge-tag badge-cb"><i class="fa-solid fa-link"></i> 可转债</span>` 
                : `<span class="badge-tag badge-stock"><i class="fa-solid fa-chart-line"></i> 股票</span>`;

            const activeBadge = stg.is_active
                ? `<span class="badge-tag badge-active"><i class="fa-solid fa-circle-play"></i> 盘后推演中</span>`
                : `<span class="badge-tag badge-inactive"><i class="fa-solid fa-circle-pause"></i> 仅作为草稿</span>`;

            const cfg = typeof stg.factors_config === 'object' ? stg.factors_config : (JSON.parse(stg.factors_config || '{}'));
            let factorTags = [];
            
            if (isCb) {
                if (cfg.max_price) factorTags.push(`最高债价 ≤ ${cfg.max_price}`);
                if (cfg.min_convert_premium !== undefined) factorTags.push(`溢价率 [${cfg.min_convert_premium}%, ${cfg.max_convert_premium}%]`);
                if (cfg.sort_by) factorTags.push(getSortByLabel(cfg.sort_by));
            } else {
                if (cfg.stock_scope) factorTags.push(`标的池: ${cfg.stock_scope === 'csi300' ? '沪深300' : '全市场'}`);
                if (cfg.financial?.roe_min || cfg.min_roe) {
                    const val = cfg.financial?.roe_min || cfg.min_roe;
                    factorTags.push(`ROE ≥ ${(val * 100).toFixed(0)}%`);
                }
                if (cfg.financial?.pe_max || cfg.pe_range) {
                    const pMax = cfg.financial?.pe_max || (cfg.pe_range ? cfg.pe_range[1] : null);
                    if (pMax) factorTags.push(`PE ≤ ${pMax}`);
                }
                if (cfg.sort_by) factorTags.push(getSortByLabel(cfg.sort_by));
            }
            if (cfg.top_n) factorTags.push(`Top ${cfg.top_n} 选中`);

            const tagsHtml = factorTags.slice(0, 4).map(t => `<span class="factor-summary-badge">${t}</span>`).join(' ');

            return `
                <div class="stg-card ${isCb ? 'cb-stg-card' : ''}" style="border: 1px solid ${stg.is_active ? 'rgba(0, 210, 196, 0.4)' : 'rgba(255, 255, 255, 0.08)'};" onclick="openStrategyDetailModal(${stg.id})">
                    <div>
                        <div class="stg-card-header">
                            <div>
                                ${catBadge}
                                ${activeBadge}
                            </div>
                            <code style="font-size: 0.78rem; color: #8e95b2;">${stg.strategy_id}</code>
                        </div>
                        <div class="stg-card-title" style="display: flex; justify-content: space-between; align-items: center;">
                            <span>${stg.name}</span>
                        </div>
                        <div class="stg-card-desc">${stg.description || '无策略描述说明'}</div>
                        <div class="stg-card-tags">
                            ${tagsHtml}
                        </div>
                    </div>

                    <div class="stg-card-footer" style="display: flex; justify-content: space-between; align-items: center;" onclick="event.stopPropagation();">
                        <div class="switch-box" title="开启后此策略将在每日 18:31 盘后工作流中自动运行并发送邮件到您的邮箱">
                            <label class="switch">
                                <input type="checkbox" ${stg.is_active ? 'checked' : ''} onchange="toggleStrategyActiveAction(${stg.id})">
                                <span class="slider round"></span>
                            </label>
                            <span style="font-size: 0.78rem; color: ${stg.is_active ? '#00d2c4' : '#8e95b2'}; font-weight: 600;">
                                ${stg.is_active ? '盘后自动推演: 开启' : '盘后自动推演: 关闭'}
                            </span>
                        </div>
                        <button class="btn btn-secondary" style="padding: 0.25rem 0.65rem; font-size: 0.78rem;" onclick="openStrategyDetailModal(${stg.id})">
                            <i class="fa-solid fa-sliders"></i> 因子配置
                        </button>
                    </div>
                </div>
            `;
        }).join('');

    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:#df4b5e; padding:2rem;">加载工作流策略失败: ${e.message}</div>`;
    }
}

// 一键切换策略的 is_active 工作流状态
async function toggleStrategyActiveAction(stgId) {
    try {
        const res = await fetch(`/api/strategies/${stgId}/toggle-active`, { method: 'POST' });
        const result = await res.json();
        if (result.status === 'success') {
            loadWorkflowStrategies();
            loadStrategies();
        } else {
            alert('❌ 操作失败: ' + result.message);
        }
    } catch (e) {
        alert('网络异常: ' + e.message);
    }
}

// 打开策略详情模态框
function openStrategyDetailModal(stgDbId) {
    let stg = allStrategiesList.find(s => s.id === stgDbId);
    if (!stg) stg = allWorkflowStrategiesList.find(s => s.id === stgDbId);
    if (!stg) return;

    activeDetailStgDbId = stgDbId;
    const isCb = stg.category === 'convertible_bond';

    document.getElementById('stgDetailCategoryBadge').className = isCb ? 'badge-tag badge-cb' : 'badge-tag badge-stock';
    document.getElementById('stgDetailCategoryBadge').textContent = isCb ? '可转债策略' : '股票策略';
    document.getElementById('stgDetailIdCode').textContent = stg.strategy_id;
    document.getElementById('stgDetailName').textContent = stg.name;
    document.getElementById('stgDetailDesc').textContent = stg.description || '暂无详细描述';
    document.getElementById('stgDetailBuyRule').textContent = stg.buy_signals_rule || '基于组合因子综合多维筛选';
    document.getElementById('stgDetailSellRule').textContent = stg.sell_signals_rule || '触发止盈/止损风控规则离场';

    const cfg = typeof stg.factors_config === 'object' ? stg.factors_config : (JSON.parse(stg.factors_config || '{}'));
    const factorsContainer = document.getElementById('stgDetailFactorsList');
    
    let factorItems = [];
    if (isCb) {
        if (cfg.max_price !== undefined) factorItems.push({ label: '最高转债价格', val: `≤ ${cfg.max_price} 元` });
        if (cfg.min_convert_premium !== undefined) factorItems.push({ label: '转股溢价率范围', val: `[${cfg.min_convert_premium}%, ${cfg.max_convert_premium}%]` });
        if (cfg.max_pure_bond_premium !== null && cfg.max_pure_bond_premium !== undefined) factorItems.push({ label: '纯债溢价率限制', val: `≤ ${cfg.max_pure_bond_premium}%` });
        if (cfg.min_ytm !== null && cfg.min_ytm !== undefined) factorItems.push({ label: '最低到期收益率(YTM)', val: `≥ ${cfg.min_ytm}%` });
        if (cfg.sort_by) factorItems.push({ label: '核心排序算法', val: getSortByLabel(cfg.sort_by) });
        if (cfg.stock_linkage) {
            const l = cfg.stock_linkage;
            if (l.enable_stock_roe) factorItems.push({ label: '正股最低 ROE', val: `≥ ${(l.stock_roe_min * 100).toFixed(1)}%` });
            if (l.enable_stock_pe) factorItems.push({ label: '正股 PE 范围', val: `[${l.stock_pe_min}, ${l.stock_pe_max}]` });
        }
    } else {
        if (cfg.stock_scope) factorItems.push({ label: '标的股票池', val: cfg.stock_scope === 'csi300' ? '沪深300' : '全市场' });
        if (cfg.sort_by) factorItems.push({ label: '主因子排序算法', val: getSortByLabel(cfg.sort_by) });
        if (cfg.tech) {
            if (cfg.tech.enable_golden_cross) factorItems.push({ label: '均线金叉 (MA)', val: `MA${cfg.tech.short_ma} > MA${cfg.tech.long_ma}` });
            if (cfg.tech.enable_volume_surge) factorItems.push({ label: '放量因子', val: `> ${cfg.tech.volume_surge_factor} 倍均量` });
            if (cfg.tech.enable_di_ratio) factorItems.push({ label: 'DMI 动量比', val: `≥ ${cfg.tech.buy_di_threshold}` });
            if (cfg.tech.enable_turnover) factorItems.push({ label: '换手率区间', val: `[${cfg.tech.min_turnover}%, ${cfg.tech.max_turnover}%]` });
            if (cfg.tech.enable_rsi) factorItems.push({ label: 'RSI 动量区间', val: `[${cfg.tech.rsi_min}, ${cfg.tech.rsi_max}]` });
            if (cfg.tech.enable_macd) factorItems.push({ label: 'MACD', val: '金叉多头' });
            if (cfg.tech.enable_boll) factorItems.push({ label: 'BOLL', val: '突破上轨' });
            if (cfg.tech.enable_kdj) factorItems.push({ label: 'KDJ', val: '低位金叉' });
        }
        if (cfg.financial) {
            if (cfg.financial.enable_roe) factorItems.push({ label: '最低 ROE 盈利', val: `≥ ${(cfg.financial.roe_min * 100).toFixed(1)}%` });
            if (cfg.financial.enable_pe) factorItems.push({ label: 'PE 估值区间', val: `[${cfg.financial.pe_min}, ${cfg.financial.pe_max}]` });
            if (cfg.financial.enable_pb) factorItems.push({ label: 'PB 市净率区间', val: `[${cfg.financial.pb_min}, ${cfg.financial.pb_max}]` });
            if (cfg.financial.enable_growth) factorItems.push({ label: '净利润增长 YoY', val: `≥ ${(cfg.financial.growth_min * 100).toFixed(1)}%` });
            if (cfg.financial.enable_debt_limit) factorItems.push({ label: '资产负债率上限', val: `≤ ${(cfg.financial.debt_max * 100).toFixed(0)}%` });
            if (cfg.financial.enable_cash_quality) factorItems.push({ label: '经营现金流/净利润', val: `≥ ${cfg.financial.cfo_np_min}` });
        }
    }
    if (cfg.top_n) factorItems.push({ label: 'Top N 推荐数量', val: `${cfg.top_n} 标的` });

    factorsContainer.innerHTML = factorItems.map(item => `
        <div style="background: rgba(255,255,255,0.04); padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <div style="font-size: 0.72rem; color: #8e95b2;">${item.label}</div>
            <div style="font-size: 0.85rem; font-weight: 700; color: #00d2c4; margin-top: 0.15rem;">${item.val}</div>
        </div>
    `).join('');

    document.getElementById('strategyDetailModal').classList.add('active');
}

function closeStrategyDetailModal() {
    document.getElementById('strategyDetailModal').classList.remove('active');
}

// 按钮触发关联操作
function triggerRunFromDetail() {
    if (globalTaskRunning) {
        alert(`⚠️ 当前已有策略任务在运行中（${currentTaskName}）！\n系统全局只允许同一时间运行一个任务，请等待当前任务计算完成。`);
        return;
    }
    closeStrategyDetailModal();
    openExecutionLogModal('RUN', activeDetailStgDbId);
}

function triggerBacktestFromDetail() {
    if (globalTaskRunning) {
        alert(`⚠️ 当前已有策略任务在运行中（${currentTaskName}）！\n系统全局只允许同一时间运行一个任务，请等待当前任务计算完成。`);
        return;
    }
    closeStrategyDetailModal();
    openExecutionLogModal('BACKTEST', activeDetailStgDbId);
}

function openEditFromDetailModal() {
    closeStrategyDetailModal();
    openEditStrategyModal(activeDetailStgDbId);
}

function deleteFromDetailModal() {
    const stg = allStrategiesList.find(s => s.id === activeDetailStgDbId);
    if (!stg) return;
    deleteStrategyAction(stg.id, stg.name);
    closeStrategyDetailModal();
}

// 打开统一控制台
function openExecutionLogModal(mode, stgDbId) {
    const stg = allStrategiesList.find(s => s.id === stgDbId);
    if (!stg) return;

    activeExecStgDbId = stgDbId;
    activeExecMode = mode;

    const modal = document.getElementById('executionLogModal');
    const titleElem = document.getElementById('execModalTitle');
    const controlPanel = document.getElementById('execControlPanel');
    const consoleBox = document.getElementById('logConsoleOutput');
    const goToBtn = document.getElementById('btnGoToResultTab');

    goToBtn.style.display = 'none';
    modal.classList.add('active');

    if (mode === 'RUN') {
        titleElem.innerHTML = `<i class="fa-solid fa-play"></i> 策略盘后选股运行控制台 - ${stg.name}`;
        
        const todayStr = new Date().toISOString().split('T')[0];
        controlPanel.innerHTML = `
            <span style="font-size: 0.82rem; color: #8e95b2; font-weight: 600;"><i class="fa-regular fa-calendar"></i> 行情目标日:</span>
            <input type="date" id="runTargetDateInput" class="form-control" style="width: auto; padding: 0.2rem 0.5rem; font-size: 0.82rem;" value="${todayStr}">
            <div style="display:flex; align-items:center; gap:0.3rem;">
                <input type="checkbox" id="runForceCheckbox" style="accent-color:#00d2c4;">
                <label for="runForceCheckbox" style="font-size: 0.82rem; color: #f0f3fa; cursor:pointer;">强制重新计算覆盖</label>
            </div>
            <button class="btn btn-primary" id="btnStartExecAction" style="padding: 0.25rem 0.85rem; font-size: 0.82rem;" onclick="startRunStrategyExecution()">
                <i class="fa-solid fa-play"></i> 开始策略运行 (选股/推荐)
            </button>
        `;

        consoleBox.textContent = `[System Ready] 已就绪！准备对策略 『${stg.name} (${stg.strategy_id})』 执行盘后选股推荐算法...\n` +
                                 `[Notice] 正在检测系统行情最新有效交易日...\n`;

        fetch('/api/latest-date').then(r => r.json()).then(res => {
            if (res.status === 'success' && res.date) {
                document.getElementById('runTargetDateInput').value = res.date;
                consoleBox.textContent = `[System Ready] 已就绪！自动匹配数据库最新有效行情日 (${res.date})\n` +
                                         `[Notice] 点击上方【开始策略运行】按钮启动计算，引擎将基于该日行情生成股票/可转债推荐数据。\n`;
            }
        }).catch(() => {});

    } else {
        titleElem.innerHTML = `<i class="fa-solid fa-flask"></i> 策略历史回测运行控制台 - ${stg.name}`;

        const todayObj = new Date();
        const defaultEndDateStr = todayObj.toISOString().split('T')[0];
        const startDateObj = new Date();
        startDateObj.setFullYear(startDateObj.getFullYear() - 1);
        const defaultStartDateStr = startDateObj.toISOString().split('T')[0];

        controlPanel.innerHTML = `
            <span style="font-size: 0.82rem; color: #8e95b2; font-weight: 600;"><i class="fa-regular fa-calendar-days"></i> 回测时间区间:</span>
            <input type="date" id="backtestStartDateInput" class="form-control" style="width: auto; padding: 0.2rem 0.5rem; font-size: 0.82rem;" value="${defaultStartDateStr}">
            <span style="color: #8e95b2;">至</span>
            <input type="date" id="backtestEndDateInput" class="form-control" style="width: auto; padding: 0.2rem 0.5rem; font-size: 0.82rem;" value="${defaultEndDateStr}">
            <button class="btn btn-primary" id="btnStartExecAction" style="padding: 0.25rem 0.85rem; font-size: 0.82rem;" onclick="startBacktestExecution()">
                <i class="fa-solid fa-play"></i> 开始计算历史回测
            </button>
        `;

        consoleBox.textContent = `[System Ready] 已就绪！策略 『${stg.name} (${stg.strategy_id})』 回测引擎环境初始化完毕...\n` +
                                 `[Params] 默认近 1 年动态时间段 (${defaultStartDateStr} 至 ${defaultEndDateStr})\n` +
                                 `[Notice] 支持自由调整起止日期，准备完毕后点击【开始计算历史回测】。\n`;
    }
}

function closeExecutionLogModal() {
    if (globalTaskRunning) {
        if (!confirm('⚠️ 当前后台策略任务仍在计算中，确定要隐藏控制台视图吗？（计算仍将在后台继续）')) return;
    }
    document.getElementById('executionLogModal').classList.remove('active');
}

// 统一控制台下的 【策略运行】 触发
async function startRunStrategyExecution() {
    if (globalTaskRunning) {
        alert(`⚠️ 当前已有策略任务在运行中（${currentTaskName}）！`);
        return;
    }
    const stg = allStrategiesList.find(s => s.id === activeExecStgDbId);
    if (!stg) return;

    const targetDate = document.getElementById('runTargetDateInput').value;
    const force = document.getElementById('runForceCheckbox').checked;

    globalTaskRunning = true;
    currentTaskName = `策略运行: ${stg.name}`;

    const runBtn = document.getElementById('btnStartExecAction');
    if (runBtn) {
        runBtn.disabled = true;
        runBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 策略运行调度中...`;
    }

    const consoleBox = document.getElementById('logConsoleOutput');
    consoleBox.textContent = `[System Init] 启动策略 『${stg.name} (${stg.strategy_id})』 盘后选股引擎...\n` +
                             `[Target Date] 行情目标日: ${targetDate} (强制重新计算: ${force})\n` +
                             `[Dispatching...] 正在向离线算力引擎提交策略运行任务...\n`;

    try {
        const res = await fetch(`/api/strategies/${stg.id}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: targetDate, force: force })
        });

        if (!res.ok) {
            const text = await res.text();
            throw new Error(`服务器 HTTP ${res.status} 响应异常: ${text || '请求超时或 Worker 正在重启'}`);
        }

        const result = await res.json();

        if (result.status === 'already_run') {
            consoleBox.textContent += `\n[NOTICE] ${result.message}\n` +
                                     `[Action Required] 若需覆盖已有数据重新计算，请勾选【强制重新计算】后再次点击运行。\n`;
            globalTaskRunning = false;
            currentTaskName = '';
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.innerHTML = `<i class="fa-solid fa-play"></i> 开始策略运行 (选股/推荐)`;
            }
            return;
        } else if (result.status === 'processing') {
            const engineLabel = result.engine === 'github_actions' ? 'GitHub Actions (7GB 算力引擎)' : '后台计算线程';
            consoleBox.textContent += `\n[Status 200 OK] ⚡ 0.1s 秒级响应成功！\n` +
                                     `[Engine Hub] 算力引擎: ${engineLabel}\n` +
                                     `[Notice] ${result.message}\n` +
                                     `-------------------------------------------------\n` +
                                     `[Monitor] 正在持续监测数据落库状态 (每 3 秒自动轮询)...\n`;

            let pollCount = 0;
            const pollInterval = setInterval(async () => {
                pollCount++;
                try {
                    const statusRes = await fetch(`/api/strategies/${stg.id}/task-status?date=${targetDate}`);
                    const statusData = await statusRes.json();

                    if (statusData.completed) {
                        clearInterval(pollInterval);
                        globalTaskRunning = false;
                        currentTaskName = '';
                        if (runBtn) {
                            runBtn.disabled = false;
                            runBtn.innerHTML = `<i class="fa-solid fa-play"></i> 开始策略运行 (选股/推荐)`;
                        }

                        if (statusData.status === 'success') {
                            consoleBox.textContent += `\n================== 策略运行结果 ==================\n` +
                                                     `🎉 计算成功完成！生成 ${statusData.count} 条最新选股/可转债推荐记录。\n` +
                                                     `行情数据日期: ${statusData.date}\n` +
                                                     `=================================================\n`;

                            const goToBtn = document.getElementById('btnGoToResultTab');
                            if (goToBtn) {
                                goToBtn.style.display = 'inline-flex';
                                goToBtn.onclick = () => {
                                    closeExecutionLogModal();
                                    switchMainTab('recommendations');
                                };
                            }
                            if (typeof loadStrategies === 'function') loadStrategies();
                        } else {
                            consoleBox.textContent += `\n❌ [ERROR] 策略运行中途异常: ${statusData.message}\n`;
                        }
                    } else {
                        if (pollCount >= 60) {
                            clearInterval(pollInterval);
                            globalTaskRunning = false;
                            currentTaskName = '';
                            if (runBtn) {
                                runBtn.disabled = false;
                                runBtn.innerHTML = `<i class="fa-solid fa-play"></i> 开始策略运行 (选股/推荐)`;
                            }
                            consoleBox.textContent += `\n⚠️ [TIMEOUT] 轮询超过 3 分钟上限，后台重算已被放弃。建议将此策略在【工作流策略配置】中开启，由 GitHub Actions 每日盘后自动离线扫描！\n`;
                        } else {
                            consoleBox.textContent += `[Progress ${pollCount * 3}s] ⚙️ 算力引擎计算中... (状态: ${statusData.status})\n`;
                            consoleBox.scrollTop = consoleBox.scrollHeight;
                        }
                    }
                } catch (pollErr) {
                    consoleBox.textContent += `[Progress ${pollCount * 3}s] ⚠️ 轮询状态探针检测中: ${pollErr.message}\n`;
                }
            }, 3000);

        } else {
            consoleBox.textContent += `\n❌ [ERROR] 策略调度失败: ${result.message}\n`;
            globalTaskRunning = false;
            currentTaskName = '';
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.innerHTML = `<i class="fa-solid fa-play"></i> 开始策略运行 (选股/推荐)`;
            }
        }
    } catch (e) {
        consoleBox.textContent += `\n❌ [NET ERROR] 网络异常: ${e.message}\n`;
        globalTaskRunning = false;
        currentTaskName = '';
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.innerHTML = `<i class="fa-solid fa-play"></i> 开始策略运行 (选股/推荐)`;
        }
    }
}

// 统一控制台下的 【历史回测】 触发
async function startBacktestExecution() {
    if (globalTaskRunning) {
        alert(`⚠️ 当前已有策略任务在运行中（${currentTaskName}）！`);
        return;
    }
    const stg = allStrategiesList.find(s => s.id === activeExecStgDbId);
    if (!stg) return;

    const startDateStr = document.getElementById('backtestStartDateInput').value;
    const endDateStr = document.getElementById('backtestEndDateInput').value;

    if (!startDateStr || !endDateStr) {
        alert('请先选择完整的回测开始与结束日期！');
        return;
    }

    globalTaskRunning = true;
    currentTaskName = `策略历史回测: ${stg.name}`;

    const runBtn = document.getElementById('btnStartExecAction');
    if (runBtn) {
        runBtn.disabled = true;
        runBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 回测调度中...`;
    }

    const consoleBox = document.getElementById('logConsoleOutput');
    const baseLogsText = `[System Init] 开始初始化策略 『${stg.name} (${stg.strategy_id})』 回测引擎...\n` +
                         `[Params] 回测时间区间: ${startDateStr} 至 ${endDateStr}\n` +
                         `[Params] 因子约束: ${JSON.stringify(stg.factors_config)}\n` +
                         `[Engine Hub] 正在调度算力节点...\n`;
    consoleBox.textContent = baseLogsText;

    try {
        const res = await fetch('/api/backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                strategy_name: stg.strategy_id,
                universe: stg.category === 'convertible_bond' ? 'all' : (stg.factors_config?.stock_scope || 'csi300'),
                start_date: startDateStr,
                end_date: endDateStr,
                save_db: true,
                config: stg.factors_config
            })
        });

        if (!res.ok) {
            const text = await res.text();
            throw new Error(`服务器 HTTP ${res.status} 响应异常: ${text || '请求超时'}`);
        }

        const result = await res.json();

        if (result.status === 'processing') {
            const lastId = result.dispatch_id || 0;
            const stgId = stg.strategy_id;
            const dispatchTime = Math.floor(Date.now() / 1000);
            let elapsedSec = 0;

            consoleBox.textContent += `\n[Status 200 OK] ⚡ 0.1s 秒级响应成功！\n` +
                                     `[Engine Hub] 算力引擎: GitHub Actions (7GB 离线高性能节点)\n` +
                                     `[Notice] ${result.message}\n` +
                                     `-------------------------------------------------\n` +
                                     `[Polling ⏳] 正在实时监听算力节点进度...\n`;

            const goToBtn = document.getElementById('btnGoToResultTab');
            if (goToBtn) {
                goToBtn.style.display = 'inline-flex';
                goToBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 算力节点计算中...`;
                goToBtn.onclick = () => {
                    closeExecutionLogModal();
                    switchMainTab('backtests');
                };
            }

            // 开启 3 秒轮询监听引擎计算状态
            const pollTimer = setInterval(async () => {
                elapsedSec += 3;
                try {
                    const statusRes = await fetch(`/api/backtest/status?strategy=${encodeURIComponent(stgId)}&last_id=${lastId}&dispatch_time=${dispatchTime}`);
                    const statusData = await statusRes.json();

                    if (statusData.completed) {
                        clearInterval(pollTimer);
                        globalTaskRunning = false;
                        if (runBtn) {
                            runBtn.disabled = false;
                            runBtn.innerHTML = `<i class="fa-solid fa-play"></i> 重新运行`;
                        }

                        if (statusData.status === 'success' && statusData.data) {
                            const d = statusData.data;
                            const retSign = d.total_return >= 0 ? '+' : '';
                            const annSign = d.annual_return >= 0 ? '+' : '';

                            let finalLogs = baseLogsText + 
                                           `\n[Status 200 OK] ⚡ 秒级响应成功！\n` +
                                           `[Engine Hub] 算力引擎: GitHub Actions (7GB 离线高性能节点)\n` +
                                           `-------------------------------------------------\n` +
                                           `================== 🎉 回测计算成功完成 ==================\n` +
                                           `回测档案 ID: #${d.id}\n` +
                                           `策略名称:    ${stg.name} (${stgId})\n` +
                                           `回测区间:    ${d.date_range}\n` +
                                           `初始资金:    ¥${d.initial_equity.toLocaleString()}\n` +
                                           `期末总资产:  ¥${d.final_equity.toLocaleString()}\n` +
                                           `累计收益率:  ${retSign}${d.total_return}%\n` +
                                           `年化收益率:  ${annSign}${d.annual_return}%\n` +
                                           `最大回撤:    ${d.max_drawdown}%\n` +
                                           `胜率:        ${d.win_rate}%\n` +
                                           `=======================================================\n` +
                                           `[Success ✅] 数据已完整持久化保存至 TiDB 数据库！\n`;

                            consoleBox.textContent = finalLogs;

                            if (goToBtn) {
                                goToBtn.style.display = 'inline-flex';
                                goToBtn.style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';
                                goToBtn.innerHTML = `<i class="fa-solid fa-chart-line"></i> 查看最新回测报告档案`;
                                goToBtn.onclick = () => {
                                    closeExecutionLogModal();
                                    switchMainTab('backtests');
                                    loadBacktests();
                                };
                            }

                            if (typeof loadBacktests === 'function') loadBacktests();
                        } else {
                            consoleBox.textContent += `\n================== ⚠️ 回测计算中断/报错 ==================\n` +
                                                     `❌ [ERROR] ${statusData.message || '算力节点发生程序异常，未写入回测报告'}\n` +
                                                     `=======================================================\n`;
                            if (goToBtn) goToBtn.style.display = 'none';
                        }
                    } else {
                        if (elapsedSec >= 300) {
                            clearInterval(pollTimer);
                            globalTaskRunning = false;
                            if (runBtn) {
                                runBtn.disabled = false;
                                runBtn.innerHTML = `<i class="fa-solid fa-play"></i> 重新运行`;
                            }
                            consoleBox.textContent += `\n================== ⚠️ 回测超时中断 ==================\n` +
                                                     `❌ [TIMEOUT] 轮询超过 5 分钟上限。离线算力任务可能遭遇报错或中断，请检查 GitHub Actions 执行日志！\n` +
                                                     `=======================================================\n`;
                            if (goToBtn) goToBtn.style.display = 'none';
                        } else {
                            // 正在计算中，追加更新倒计时日志
                            const currentText = consoleBox.textContent;
                            const lines = currentText.split('\n');
                            const lastLine = lines[lines.length - 1] || '';
                            if (lastLine.includes('[Polling ⏳]')) {
                                lines[lines.length - 1] = `[Polling ⏳] 正在实时监听算力节点进度... (已等待 ${elapsedSec} 秒)`;
                                consoleBox.textContent = lines.join('\n');
                            } else {
                                consoleBox.textContent += `[Polling ⏳] 正在实时监听算力节点进度... (已等待 ${elapsedSec} 秒)\n`;
                            }
                        }
                    }
                } catch (pollErr) {
                    console.warn('轮询回测状态异常:', pollErr);
                }
            }, 3000);

        } else if (result.status === 'success') {
            const s = result.summary;
            let logsText = baseLogsText;
            logsText += `\n================== 回测统计汇总 ==================\n` +
                        `初始资金: ¥${s.initial_equity}  | 最终资产: ¥${s.final_equity}\n` +
                        `总回报率: ${s.total_return.toFixed(2)}% | 年化收益率: ${s.annual_return.toFixed(2)}%\n` +
                        `最大回撤: ${s.max_drawdown.toFixed(2)}% | 胜率: ${s.win_rate.toFixed(2)}%\n` +
                        `成交笔数: ${s.total_buys + s.total_sells} (买入: ${s.total_buys}, 卖出: ${s.total_sells})\n` +
                        `================== 交易明细 Log ==================\n`;

            (result.trade_log || []).forEach((t) => {
                logsText += `[${t.trade_date || t.date}] ${t.action} ${t.stock_code || t.code} | 价格: ¥${t.price} | 数量: ${t.shares} | 盈亏: ${t.pnl !== null ? '¥' + t.pnl.toFixed(2) : '--'} | 原因: ${t.reason || '-'}\n`;
            });

            consoleBox.textContent = logsText;

            const goToBtn = document.getElementById('btnGoToResultTab');
            if (goToBtn) {
                goToBtn.style.display = 'inline-flex';
                goToBtn.onclick = () => {
                    closeExecutionLogModal();
                    switchMainTab('backtests');
                };
            }
            if (typeof loadBacktests === 'function') loadBacktests();
        } else {
            consoleBox.textContent += `\n❌ [ERROR] 回测出错: ${result.message}\n`;
        }
    } catch (e) {
        consoleBox.textContent += `\n❌ [NET ERROR] 网络异常: ${e.message}\n`;
    } finally {
        globalTaskRunning = false;
        currentTaskName = '';
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.innerHTML = `<i class="fa-solid fa-play"></i> 开始计算历史回测`;
        }
    }
}

function copyConsoleLog() {
    const text = document.getElementById('logConsoleOutput').textContent;
    navigator.clipboard.writeText(text).then(() => {
        alert('📋 日志明细已成功复制到剪贴板！');
    }).catch(e => alert('复制失败: ' + e));
}

// 当前因子编辑模式 ('form' 或 'json')
let currentFactorEditMode = 'form';

// 切换因子编辑模式 (可视化表单 VS JSON 代码文本)
function switchFactorEditMode(mode) {
    currentFactorEditMode = mode;
    const btnForm = document.getElementById('btnModeForm');
    const btnJson = document.getElementById('btnModeJson');
    const panelStock = document.getElementById('factorPanelStock');
    const panelCB = document.getElementById('factorPanelCB');
    const panelJson = document.getElementById('factorPanelJson');
    const jsonMsg = document.getElementById('jsonFormatStatusMsg');

    if (mode === 'json') {
        // 如果 textarea 目前为空，才从表单组装填入
        if (!document.getElementById('stgFactorsJsonInput').value.trim()) {
            const currentConfig = buildConfigFromForm();
            document.getElementById('stgFactorsJsonInput').value = JSON.stringify(currentConfig, null, 4);
        }

        btnForm.classList.remove('active');
        btnJson.classList.add('active');
        panelStock.style.display = 'none';
        panelCB.style.display = 'none';
        panelJson.style.display = 'block';
        jsonMsg.innerHTML = `<span style="color:#00d2c4;"><i class="fa-solid fa-check"></i> 正在编辑原生 JSON 源代码</span>`;
    } else {
        // 从 JSON textarea 解析并反填回表单控件
        const jsonText = document.getElementById('stgFactorsJsonInput').value.trim();
        if (jsonText) {
            try {
                const parsedCfg = JSON.parse(jsonText);
                applyConfigToForm(parsedCfg);
                jsonMsg.innerHTML = `<span style="color:#2ebd85;"><i class="fa-solid fa-circle-check"></i> JSON 验证合法并已同步至可视化控件</span>`;
            } catch (err) {
                alert(`⚠️ 无法切换回表单配置: 输入的 JSON 语法错误！\n错误详情: ${err.message}\n请修正 JSON 语法后再试。`);
                return;
            }
        }

        btnJson.classList.remove('active');
        btnForm.classList.add('active');
        panelJson.style.display = 'none';
        onCategoryChange();
    }
}

// 格式化与校验 textarea 中的 JSON 文本
function formatJsonTextareaInput() {
    const jsonInput = document.getElementById('stgFactorsJsonInput');
    const jsonMsg = document.getElementById('jsonFormatStatusMsg');
    const text = jsonInput.value.trim();

    if (!text) {
        jsonMsg.innerHTML = `<span style="color:#ffb86c;">⚠️ 内容为空</span>`;
        return false;
    }

    try {
        const obj = JSON.parse(text);
        jsonInput.value = JSON.stringify(obj, null, 4);
        jsonMsg.innerHTML = `<span style="color:#2ebd85; font-weight:bold;"><i class="fa-solid fa-circle-check"></i> JSON 语法校验无误！格式化成功。</span>`;
        return true;
    } catch (err) {
        jsonMsg.innerHTML = `<span style="color:#df4b5e; font-weight:bold;"><i class="fa-solid fa-triangle-exclamation"></i> 语法错误: ${err.message}</span>`;
        return false;
    }
}

function getPortfolioConfigFromForm() {
    const portType = document.getElementById('stg_portfolio_type') ? document.getElementById('stg_portfolio_type').value : 'equal_slot';
    const initCapital = document.getElementById('stg_initial_capital') ? parseFloat(document.getElementById('stg_initial_capital').value || 100000.0) : 100000.0;
    const maxHoldings = document.getElementById('stg_max_holdings') ? parseInt(document.getElementById('stg_max_holdings').value || 5) : 5;
    const execTiming = document.getElementById('stg_execution_timing') ? document.getElementById('stg_execution_timing').value : 'T+1_OPEN';
    const stopLoss = document.getElementById('stg_stop_loss_pct') ? parseFloat(document.getElementById('stg_stop_loss_pct').value || -8.0) : -8.0;
    const trailingStop = document.getElementById('stg_trailing_stop_pct') ? parseFloat(document.getElementById('stg_trailing_stop_pct').value || -5.0) : -5.0;

    return {
        strategy_type: portType,
        initial_capital: initCapital,
        max_holdings: maxHoldings,
        allocation_mode: portType === 'compounding' ? 'COMPOUNDING' : 'EQUAL_SLOT',
        stop_loss_pct: stopLoss,
        trailing_stop_pct: trailingStop,
        execution_timing: execTiming,
        slippage: execTiming === 'T+1_OPEN' ? 0.001 : 0.0
    };
}

// 从当前 HTML 表单控件读取构建 JSON 字典
function buildConfigFromForm() {
    const category = document.getElementById('stgFormCategory').value;
    const topN = parseInt(document.getElementById('stgFormTopN').value || 10);
    const portfolioConfig = getPortfolioConfigFromForm();

    if (category === 'convertible_bond') {
        const enablePureBond = document.getElementById('stg_cb_enable_pure_bond').checked;
        const enableYTM = document.getElementById('stg_cb_enable_ytm').checked;

        return {
            max_price: parseFloat(document.getElementById('stg_cb_max_price').value || 130.0),
            min_convert_premium: parseFloat(document.getElementById('stg_cb_min_premium').value || -20.0),
            max_convert_premium: parseFloat(document.getElementById('stg_cb_max_premium').value || 50.0),
            max_pure_bond_premium: enablePureBond ? parseFloat(document.getElementById('stg_cb_max_pure_bond_premium').value || 30.0) : null,
            min_ytm: enableYTM ? parseFloat(document.getElementById('stg_cb_min_ytm').value || 0.0) : null,
            sort_by: document.getElementById('stg_cb_primary_factor').value,
            stock_scope: document.getElementById('stg_cb_universe').value,
            top_n: topN,
            portfolio_config: portfolioConfig,
            stock_linkage: {
                enable_stock_roe: document.getElementById('stg_cb_enable_stock_roe').checked,
                stock_roe_min: parseFloat(document.getElementById('stg_cb_stock_roe_min').value || 5.0) / 100.0,
                enable_stock_pe: document.getElementById('stg_cb_enable_stock_pe').checked,
                stock_pe_min: parseFloat(document.getElementById('stg_cb_stock_pe_min').value || 0),
                stock_pe_max: parseFloat(document.getElementById('stg_cb_stock_pe_max').value || 35),
                enable_stock_ma: document.getElementById('stg_cb_enable_stock_ma').checked,
                short_ma: parseInt(document.getElementById('stg_cb_stock_short_ma').value || 5),
                long_ma: parseInt(document.getElementById('stg_cb_stock_long_ma').value || 20)
            }
        };
    } else {
        const primaryFactor = document.getElementById('stg_primary_factor').value;
        return {
            top_n: topN,
            stock_scope: document.getElementById('stg_universe').value,
            sort_by: primaryFactor,
            portfolio_config: portfolioConfig,
            tech: {
                enable_golden_cross: document.getElementById('stg_enable_golden_cross').checked,
                short_ma: parseInt(document.getElementById('stg_short_ma').value || 5),
                long_ma: parseInt(document.getElementById('stg_long_ma').value || 20),
                enable_volume_surge: document.getElementById('stg_enable_volume_surge').checked,
                volume_surge_factor: parseFloat(document.getElementById('stg_volume_surge_factor').value || 1.2),
                enable_di_ratio: document.getElementById('stg_enable_di_ratio').checked,
                buy_di_threshold: parseFloat(document.getElementById('stg_buy_di_threshold').value || 0.7),
                enable_turnover: document.getElementById('stg_enable_turnover') ? document.getElementById('stg_enable_turnover').checked : false,
                min_turnover: document.getElementById('stg_turnover_min') ? parseFloat(document.getElementById('stg_turnover_min').value || 1.0) : 1.0,
                max_turnover: document.getElementById('stg_turnover_max') ? parseFloat(document.getElementById('stg_turnover_max').value || 15.0) : 15.0,
                enable_rsi: document.getElementById('stg_enable_rsi') ? document.getElementById('stg_enable_rsi').checked : false,
                rsi_min: document.getElementById('stg_rsi_min') ? parseFloat(document.getElementById('stg_rsi_min').value || 30.0) : 30.0,
                rsi_max: document.getElementById('stg_rsi_max') ? parseFloat(document.getElementById('stg_rsi_max').value || 70.0) : 70.0,
                enable_macd: document.getElementById('stg_enable_macd') ? document.getElementById('stg_enable_macd').checked : false,
                enable_boll: document.getElementById('stg_enable_boll') ? document.getElementById('stg_enable_boll').checked : false,
                enable_kdj: document.getElementById('stg_enable_kdj') ? document.getElementById('stg_enable_kdj').checked : false
            },
            financial: {
                enable_roe: document.getElementById('stg_enable_roe').checked,
                roe_min: parseFloat(document.getElementById('stg_roe_min').value || 5.0) / 100.0,
                enable_pe: document.getElementById('stg_enable_pe').checked,
                pe_min: parseFloat(document.getElementById('stg_pe_min').value || 0),
                pe_max: parseFloat(document.getElementById('stg_pe_max').value || 35),
                enable_pb: document.getElementById('stg_enable_pb') ? document.getElementById('stg_enable_pb').checked : false,
                pb_min: document.getElementById('stg_pb_min') ? parseFloat(document.getElementById('stg_pb_min').value || 0.5) : 0.5,
                pb_max: document.getElementById('stg_pb_max') ? parseFloat(document.getElementById('stg_pb_max').value || 5.0) : 5.0,
                enable_growth: document.getElementById('stg_enable_growth').checked,
                growth_min: parseFloat(document.getElementById('stg_growth_min').value || 10.0) / 100.0,
                enable_debt_limit: document.getElementById('stg_enable_debt_limit').checked,
                debt_max: parseFloat(document.getElementById('stg_debt_max').value || 0.7),
                enable_cash_quality: document.getElementById('stg_enable_cash_quality').checked,
                cfo_np_min: parseFloat(document.getElementById('stg_cfo_np_min').value || 0.8)
            },
            ranking: {
                primary_factor: primaryFactor,
                reverse: ['roe', 'growth'].includes(primaryFactor)
            }
        };
    }
}

// 将传入的 config 对象解包同步反填回 HTML 表单控件
function applyConfigToForm(cfg) {
    if (!cfg || typeof cfg !== 'object') return;

    if (cfg.top_n) document.getElementById('stgFormTopN').value = cfg.top_n;

    if (cfg.portfolio_config) {
        const p = cfg.portfolio_config;
        if (document.getElementById('stg_portfolio_type')) document.getElementById('stg_portfolio_type').value = p.strategy_type || (p.allocation_mode === 'COMPOUNDING' ? 'compounding' : 'equal_slot');
        if (document.getElementById('stg_initial_capital')) document.getElementById('stg_initial_capital').value = p.initial_capital || 100000;
        if (document.getElementById('stg_max_holdings')) document.getElementById('stg_max_holdings').value = p.max_holdings || 5;
        if (document.getElementById('stg_execution_timing')) document.getElementById('stg_execution_timing').value = p.execution_timing || 'T+1_OPEN';
        if (document.getElementById('stg_stop_loss_pct')) document.getElementById('stg_stop_loss_pct').value = p.stop_loss_pct !== undefined ? p.stop_loss_pct : -8.0;
        if (document.getElementById('stg_trailing_stop_pct')) document.getElementById('stg_trailing_stop_pct').value = p.trailing_stop_pct !== undefined ? p.trailing_stop_pct : -5.0;
    }

    const category = document.getElementById('stgFormCategory').value;
    if (category === 'convertible_bond') {
        if (cfg.max_price !== undefined) document.getElementById('stg_cb_max_price').value = cfg.max_price;
        if (cfg.min_convert_premium !== undefined) document.getElementById('stg_cb_min_premium').value = cfg.min_convert_premium;
        else if (cfg.min_premium_rate !== undefined) document.getElementById('stg_cb_min_premium').value = cfg.min_premium_rate;

        if (cfg.max_convert_premium !== undefined) document.getElementById('stg_cb_max_premium').value = cfg.max_convert_premium;
        else if (cfg.max_premium_rate !== undefined) document.getElementById('stg_cb_max_premium').value = cfg.max_premium_rate;

        document.getElementById('stg_cb_enable_pure_bond').checked = cfg.max_pure_bond_premium !== undefined && cfg.max_pure_bond_premium !== null;
        if (cfg.max_pure_bond_premium !== undefined && cfg.max_pure_bond_premium !== null) {
            document.getElementById('stg_cb_max_pure_bond_premium').value = cfg.max_pure_bond_premium;
        }

        document.getElementById('stg_cb_enable_ytm').checked = cfg.min_ytm !== undefined && cfg.min_ytm !== null;
        if (cfg.min_ytm !== undefined && cfg.min_ytm !== null) {
            document.getElementById('stg_cb_min_ytm').value = cfg.min_ytm;
        }

        if (cfg.sort_by) document.getElementById('stg_cb_primary_factor').value = cfg.sort_by;
        if (cfg.stock_scope) document.getElementById('stg_cb_universe').value = cfg.stock_scope;

        if (cfg.stock_linkage) {
            const link = cfg.stock_linkage;
            document.getElementById('stg_cb_enable_stock_roe').checked = !!link.enable_stock_roe;
            if (link.stock_roe_min !== undefined) document.getElementById('stg_cb_stock_roe_min').value = link.stock_roe_min * 100;
            document.getElementById('stg_cb_enable_stock_pe').checked = !!link.enable_stock_pe;
            if (link.stock_pe_min !== undefined) document.getElementById('stg_cb_stock_pe_min').value = link.stock_pe_min;
            if (link.stock_pe_max !== undefined) document.getElementById('stg_cb_stock_pe_max').value = link.stock_pe_max;
            document.getElementById('stg_cb_enable_stock_ma').checked = !!link.enable_stock_ma;
            if (link.short_ma !== undefined) document.getElementById('stg_cb_stock_short_ma').value = link.short_ma;
            if (link.long_ma !== undefined) document.getElementById('stg_cb_stock_long_ma').value = link.long_ma;
        }
    } else {
        if (cfg.stock_scope) document.getElementById('stg_universe').value = cfg.stock_scope;
        if (cfg.sort_by || cfg.ranking?.primary_factor) document.getElementById('stg_primary_factor').value = cfg.sort_by || cfg.ranking?.primary_factor;

        if (cfg.tech) {
            document.getElementById('stg_enable_golden_cross').checked = !!cfg.tech.enable_golden_cross;
            document.getElementById('stg_short_ma').value = cfg.tech.short_ma || 5;
            document.getElementById('stg_long_ma').value = cfg.tech.long_ma || 20;
            document.getElementById('stg_enable_volume_surge').checked = !!cfg.tech.enable_volume_surge;
            document.getElementById('stg_volume_surge_factor').value = cfg.tech.volume_surge_factor || 1.2;
            document.getElementById('stg_enable_di_ratio').checked = !!cfg.tech.enable_di_ratio;
            document.getElementById('stg_buy_di_threshold').value = cfg.tech.buy_di_threshold || 0.70;

            if (document.getElementById('stg_enable_turnover')) document.getElementById('stg_enable_turnover').checked = !!cfg.tech.enable_turnover;
            if (document.getElementById('stg_turnover_min')) document.getElementById('stg_turnover_min').value = cfg.tech.min_turnover || 1.0;
            if (document.getElementById('stg_turnover_max')) document.getElementById('stg_turnover_max').value = cfg.tech.max_turnover || 15.0;
            if (document.getElementById('stg_enable_rsi')) document.getElementById('stg_enable_rsi').checked = !!cfg.tech.enable_rsi;
            if (document.getElementById('stg_rsi_min')) document.getElementById('stg_rsi_min').value = cfg.tech.rsi_min || 30.0;
            if (document.getElementById('stg_rsi_max')) document.getElementById('stg_rsi_max').value = cfg.tech.rsi_max || 70.0;
            if (document.getElementById('stg_enable_macd')) document.getElementById('stg_enable_macd').checked = !!cfg.tech.enable_macd;
            if (document.getElementById('stg_enable_boll')) document.getElementById('stg_enable_boll').checked = !!cfg.tech.enable_boll;
            if (document.getElementById('stg_enable_kdj')) document.getElementById('stg_enable_kdj').checked = !!cfg.tech.enable_kdj;
        }
        if (cfg.financial) {
            document.getElementById('stg_enable_roe').checked = !!cfg.financial.enable_roe;
            document.getElementById('stg_roe_min').value = (cfg.financial.roe_min || 0.05) * 100;
            document.getElementById('stg_enable_pe').checked = !!cfg.financial.enable_pe;
            document.getElementById('stg_pe_min').value = cfg.financial.pe_min || 0;
            document.getElementById('stg_pe_max').value = cfg.financial.pe_max || 35;

            if (document.getElementById('stg_enable_pb')) document.getElementById('stg_enable_pb').checked = !!cfg.financial.enable_pb;
            if (document.getElementById('stg_pb_min')) document.getElementById('stg_pb_min').value = cfg.financial.pb_min || 0.5;
            if (document.getElementById('stg_pb_max')) document.getElementById('stg_pb_max').value = cfg.financial.pb_max || 5.0;

            document.getElementById('stg_enable_growth').checked = !!cfg.financial.enable_growth;
            document.getElementById('stg_growth_min').value = (cfg.financial.growth_min || 0.10) * 100;
            document.getElementById('stg_enable_debt_limit').checked = !!cfg.financial.enable_debt_limit;
            document.getElementById('stg_debt_max').value = cfg.financial.debt_max || 0.70;
            document.getElementById('stg_enable_cash_quality').checked = !!cfg.financial.enable_cash_quality;
            document.getElementById('stg_cfo_np_min').value = cfg.financial.cfo_np_min || 0.80;
        }
    }
}

// 动态因子面板切换 (股票策略 vs 可转债策略)
function onCategoryChange() {
    if (currentFactorEditMode === 'json') return;

    const cat = document.getElementById('stgFormCategory').value;
    const stockPanel = document.getElementById('factorPanelStock');
    const cbPanel = document.getElementById('factorPanelCB');

    if (cat === 'convertible_bond') {
        stockPanel.style.display = 'none';
        cbPanel.style.display = 'block';
    } else {
        stockPanel.style.display = 'block';
        cbPanel.style.display = 'none';
    }
}

// 打开/关闭新建模态框
function openCreateStrategyModal() {
    document.getElementById('modalStrategyTitle').textContent = '新建自定义因子策略';
    document.getElementById('editStrategyDbId').value = '';
    
    const autoId = 'stg_' + Math.floor(Date.now() / 1000);
    const stgFormIdElem = document.getElementById('stgFormId');
    stgFormIdElem.value = autoId;
    stgFormIdElem.readOnly = true;
    stgFormIdElem.style.background = 'rgba(255,255,255,0.05)';
    stgFormIdElem.title = '策略 ID 由系统自动生成';

    document.getElementById('stgFormName').value = '';
    document.getElementById('stgFormCategory').value = 'stock';
    document.getElementById('stgFormDesc').value = '';
    document.getElementById('stgFormBuyRule').value = '';
    document.getElementById('stgFormSellRule').value = '';
    
    document.getElementById('stg_enable_golden_cross').checked = true;
    document.getElementById('stg_short_ma').value = 5;
    document.getElementById('stg_long_ma').value = 20;
    document.getElementById('stg_enable_volume_surge').checked = true;
    document.getElementById('stg_volume_surge_factor').value = 1.2;
    document.getElementById('stg_enable_di_ratio').checked = true;
    document.getElementById('stg_buy_di_threshold').value = 0.70;
    
    if (document.getElementById('stg_enable_turnover')) document.getElementById('stg_enable_turnover').checked = false;
    if (document.getElementById('stg_turnover_min')) document.getElementById('stg_turnover_min').value = 1.0;
    if (document.getElementById('stg_turnover_max')) document.getElementById('stg_turnover_max').value = 15.0;
    if (document.getElementById('stg_enable_rsi')) document.getElementById('stg_enable_rsi').checked = false;
    if (document.getElementById('stg_rsi_min')) document.getElementById('stg_rsi_min').value = 30.0;
    if (document.getElementById('stg_rsi_max')) document.getElementById('stg_rsi_max').value = 70.0;
    if (document.getElementById('stg_enable_macd')) document.getElementById('stg_enable_macd').checked = false;
    if (document.getElementById('stg_enable_boll')) document.getElementById('stg_enable_boll').checked = false;
    if (document.getElementById('stg_enable_kdj')) document.getElementById('stg_enable_kdj').checked = false;

    document.getElementById('stg_enable_roe').checked = false;
    document.getElementById('stg_roe_min').value = 5.0;
    document.getElementById('stg_enable_pe').checked = false;
    document.getElementById('stg_pe_min').value = 0;
    document.getElementById('stg_pe_max').value = 35;

    if (document.getElementById('stg_enable_pb')) document.getElementById('stg_enable_pb').checked = false;
    if (document.getElementById('stg_pb_min')) document.getElementById('stg_pb_min').value = 0.5;
    if (document.getElementById('stg_pb_max')) document.getElementById('stg_pb_max').value = 5.0;

    document.getElementById('stg_enable_growth').checked = false;
    document.getElementById('stg_growth_min').value = 10.0;
    document.getElementById('stg_enable_debt_limit').checked = false;
    document.getElementById('stg_debt_max').value = 0.70;
    document.getElementById('stg_enable_cash_quality').checked = false;
    document.getElementById('stg_cfo_np_min').value = 0.80;
    document.getElementById('stg_primary_factor').value = 'price';
    document.getElementById('stg_universe').value = 'csi300';
    document.getElementById('stgFormTopN').value = 10;

    document.getElementById('stg_cb_max_price').value = 130.0;
    document.getElementById('stg_cb_min_premium').value = -20.0;
    document.getElementById('stg_cb_max_premium').value = 50.0;
    document.getElementById('stg_cb_enable_pure_bond').checked = false;
    document.getElementById('stg_cb_max_pure_bond_premium').value = 30.0;
    document.getElementById('stg_cb_enable_ytm').checked = false;
    document.getElementById('stg_cb_min_ytm').value = 0.0;
    document.getElementById('stg_cb_primary_factor').value = 'db_low_value';
    document.getElementById('stg_cb_universe').value = 'all';

    document.getElementById('stg_cb_enable_stock_roe').checked = false;
    document.getElementById('stg_cb_stock_roe_min').value = 5.0;
    document.getElementById('stg_cb_enable_stock_pe').checked = false;
    document.getElementById('stg_cb_stock_pe_min').value = 0;
    document.getElementById('stg_cb_stock_pe_max').value = 35;
    document.getElementById('stg_cb_enable_stock_ma').checked = false;
    document.getElementById('stg_cb_stock_short_ma').value = 5;
    document.getElementById('stg_cb_stock_long_ma').value = 20;

    onCategoryChange();
    document.getElementById('strategyModal').classList.add('active');
}

function openEditStrategyModal(dbId) {
    let stg = allStrategiesList.find(s => s.id === dbId);
    if (!stg) stg = allWorkflowStrategiesList.find(s => s.id === dbId);
    if (!stg) return;

    document.getElementById('modalStrategyTitle').textContent = `编辑策略: ${stg.name}`;
    document.getElementById('editStrategyDbId').value = stg.id;
    
    const stgFormIdElem = document.getElementById('stgFormId');
    stgFormIdElem.value = stg.strategy_id;
    stgFormIdElem.readOnly = true;
    stgFormIdElem.style.background = 'rgba(255,255,255,0.05)';

    document.getElementById('stgFormName').value = stg.name;
    document.getElementById('stgFormCategory').value = stg.category || 'stock';
    document.getElementById('stgFormDesc').value = stg.description || '';
    document.getElementById('stgFormBuyRule').value = stg.buy_signals_rule || '';
    document.getElementById('stgFormSellRule').value = stg.sell_signals_rule || '';

    const cfg = typeof stg.factors_config === 'object' ? stg.factors_config : (JSON.parse(stg.factors_config || '{}'));
    
    // 直接将数据库中的原生 JSON 填入源码编辑器
    document.getElementById('stgFactorsJsonInput').value = JSON.stringify(cfg, null, 4);

    if (cfg) {
        applyConfigToForm(cfg);
    }

    // 重置编辑模式为表单模式
    currentFactorEditMode = 'form';
    document.getElementById('btnModeForm').classList.add('active');
    document.getElementById('btnModeJson').classList.remove('active');
    document.getElementById('factorPanelJson').style.display = 'none';

    onCategoryChange();
    document.getElementById('strategyModal').classList.add('active');
}

function closeStrategyModal() {
    document.getElementById('strategyModal').classList.remove('active');
}

// 保存策略
async function saveStrategySubmit() {
    const dbId = document.getElementById('editStrategyDbId').value;
    const category = document.getElementById('stgFormCategory').value;

    let factorsConfig = {};

    if (currentFactorEditMode === 'json') {
        const jsonText = document.getElementById('stgFactorsJsonInput').value.trim();
        if (!jsonText) {
            alert('⚠️ 因子配置 JSON 不能为空！');
            return;
        }
        try {
            factorsConfig = JSON.parse(jsonText);
            // 校验根节点是否为对象
            if (typeof factorsConfig !== 'object' || Array.isArray(factorsConfig) || factorsConfig === null) {
                alert('⚠️ 因子配置 JSON 格式不合法: 根节点必须是一个字典对象 {...}！');
                return;
            }
        } catch (err) {
            alert(`❌ 无法保存: JSON 格式语法不合法！\n错误信息: ${err.message}\n请修正文本框中的 JSON 格式后再试。`);
            return;
        }
    } else {
        factorsConfig = buildConfigFromForm();
    }

    const payload = {
        strategy_id: document.getElementById('stgFormId').value.trim(),
        name: document.getElementById('stgFormName').value.trim(),
        category: category,
        description: document.getElementById('stgFormDesc').value.trim(),
        buy_signals_rule: document.getElementById('stgFormBuyRule').value.trim(),
        sell_signals_rule: document.getElementById('stgFormSellRule').value.trim(),
        factors_config: factorsConfig
    };

    const method = dbId ? 'PUT' : 'POST';
    const url = dbId ? `/api/strategies/${dbId}` : '/api/strategies';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();

        if (result.status === 'success') {
            alert('✅ 策略保存成功！');
            closeStrategyModal();
            loadStrategies();
        } else {
            alert('❌ 保存失败: ' + result.message);
        }
    } catch (e) {
        alert('网络异常: ' + e.message);
    }
}

// 删除策略
async function deleteStrategyAction(dbId, name) {
    if (!confirm(`确定要删除策略『${name}』吗？系统将同步清理该策略历史推荐记录。`)) return;

    try {
        const res = await fetch(`/api/strategies/${dbId}?purge_results=true`, { method: 'DELETE' });
        const result = await res.json();
        if (result.status === 'success') {
            alert('✅ 策略已删除');
            loadStrategies();
        } else {
            alert('❌ 删除失败: ' + result.message);
        }
    } catch (e) {
        alert('网络异常: ' + e.message);
    }
}

// 2. 加载历史回测记录
async function loadBacktests() {
    const grid = document.getElementById('backtestCardGrid');
    if (!grid) return;

    const searchInput = document.getElementById('searchBacktestInput');
    const search = (searchInput ? searchInput.value : '').toLowerCase().trim();

    try {
        const res = await fetch('/api/backtests');
        const result = await res.json();

        if (result.status !== 'success') {
            grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #df4b5e;">${result.message}</div>`;
            return;
        }

        allBacktestRecords = result.data || [];
        let filtered = allBacktestRecords;

        if (search) {
            filtered = filtered.filter(b => 
                (b.strategy_name && b.strategy_name.toLowerCase().includes(search)) ||
                (b.strategy && b.strategy.toLowerCase().includes(search)) ||
                (b.date_range && b.date_range.includes(search)) ||
                (b.run_date && b.run_date.includes(search))
            );
        }

        if (filtered.length === 0) {
            grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #8e95b2; padding: 2.5rem;">暂无历史回测档案数据</div>`;
            return;
        }

        grid.innerHTML = filtered.map(b => {
            const retClass = b.total_return >= 0 ? 'up-val' : 'down-val';
            const retSign = b.total_return >= 0 ? '+' : '';
            const annSign = b.annual_return >= 0 ? '+' : '';
            const sharpeVal = (b.sharpe_ratio !== undefined && b.sharpe_ratio !== null) ? b.sharpe_ratio.toFixed(2) : '0.00';
            const stgName = b.strategy_name || b.strategy;

            return `
                <div class="stg-card">
                    <div>
                        <div class="stg-card-header">
                            <div class="stg-card-title">${stgName}</div>
                            <span class="stg-card-id" title="策略 ID">ID: ${b.strategy}</span>
                        </div>

                        <div style="font-size: 0.78rem; color: #8e95b2; margin-bottom: 0.75rem; display: flex; flex-direction: column; gap: 0.2rem;">
                            <div><i class="fa-regular fa-calendar"></i> 回测区间: <strong style="color: #f0f3fa;">${b.date_range}</strong></div>
                            <div><i class="fa-regular fa-clock"></i> 运行时间: ${b.run_date || '-'} (ID: #${b.id})</div>
                        </div>

                        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.5rem; background: rgba(15, 17, 32, 0.6); padding: 0.65rem 0.85rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06); margin-bottom: 0.85rem;">
                            <div>
                                <div style="font-size: 0.72rem; color: #8e95b2;">累计回报率</div>
                                <div style="font-size: 1rem; font-weight: 700;" class="${retClass}">${retSign}${b.total_return.toFixed(2)}%</div>
                            </div>
                            <div>
                                <div style="font-size: 0.72rem; color: #8e95b2;">年化收益率</div>
                                <div style="font-size: 1rem; font-weight: 700;" class="${b.annual_return >= 0 ? 'up-val' : 'down-val'}">${annSign}${b.annual_return.toFixed(2)}%</div>
                            </div>
                            <div>
                                <div style="font-size: 0.72rem; color: #8e95b2;">夏普比率 (Sharpe)</div>
                                <div style="font-size: 1rem; font-weight: 700; color: #00d2c4;">${sharpeVal}</div>
                            </div>
                            <div>
                                <div style="font-size: 0.72rem; color: #8e95b2;">最大回撤 / 胜率</div>
                                <div style="font-size: 0.85rem; font-weight: 600;"><span class="down-val">${b.max_drawdown.toFixed(2)}%</span> / <span style="color:#ffb86c;">${b.win_rate.toFixed(1)}%</span></div>
                            </div>
                        </div>
                    </div>

                    <div class="stg-card-footer" style="display: flex; gap: 0.5rem; justify-content: flex-end;">
                        <button class="btn btn-secondary" style="padding: 0.25rem 0.65rem; font-size: 0.8rem;" onclick="openBacktestDetailModal(${b.id})">
                            <i class="fa-solid fa-file-lines"></i> 详情
                        </button>
                        <button class="btn btn-danger" style="padding: 0.25rem 0.65rem; font-size: 0.8rem;" onclick="deleteBacktestAction(${b.id})">
                            <i class="fa-solid fa-trash"></i> 删除
                        </button>
                    </div>
                </div>
            `;
        }).join('');

    } catch (e) {
        grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #df4b5e;">加载失败: ${e.message}</div>`;
    }
}

// 删除特定回测记录
async function deleteBacktestAction(backtestId) {
    const b = allBacktestRecords.find(item => item.id == backtestId);
    const stgName = b ? (b.strategy_name || b.strategy) : `ID #${backtestId}`;

    if (!confirm(`⚠️ 确定要从数据库中彻底删除次回测记录（ID: #${backtestId} - ${stgName}）及其包含的所有成交明细数据吗？此操作无法撤销。`)) {
        return;
    }

    try {
        const res = await fetch(`/api/backtests/${backtestId}`, { method: 'DELETE' });
        const result = await res.json();

        if (result.status === 'success') {
            alert(`✅ ${result.message}`);
            loadBacktests();
        } else {
            alert(`❌ 删除失败: ${result.message}`);
        }
    } catch (e) {
        alert(`❌ 网络异常: ${e.message}`);
    }
}

// 弹框显示回测成交明细
async function openBacktestDetailModal(backtestId) {
    const b = allBacktestRecords.find(item => item.id == backtestId);
    if (!b) {
        alert('未从缓存记录中查找到对应回测 ID: ' + backtestId);
        return;
    }

    document.getElementById('btModalStgName').textContent = b.strategy_name || b.strategy;
    document.getElementById('btModalStgCode').textContent = b.strategy;
    document.getElementById('btModalDateRange').textContent = b.date_range;

    const totSign = b.total_return >= 0 ? '+' : '';
    const annSign = b.annual_return >= 0 ? '+' : '';
    document.getElementById('btModalTotalReturn').textContent = `${totSign}${b.total_return.toFixed(2)}%`;
    document.getElementById('btModalTotalReturn').className = b.total_return >= 0 ? 'up-val' : 'down-val';

    document.getElementById('btModalAnnualReturn').textContent = `${annSign}${b.annual_return.toFixed(2)}%`;
    document.getElementById('btModalAnnualReturn').className = b.annual_return >= 0 ? 'up-val' : 'down-val';

    const sharpeVal = (b.sharpe_ratio !== undefined && b.sharpe_ratio !== null) ? b.sharpe_ratio.toFixed(2) : '0.00';
    document.getElementById('btModalSharpe').textContent = sharpeVal;
    document.getElementById('btModalMaxDrawdown').textContent = `${b.max_drawdown.toFixed(2)}%`;
    document.getElementById('btModalWinRate').textContent = `${b.win_rate.toFixed(2)}%`;

    const tbody = document.getElementById('backtestModalTradesBody');
    tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: #8e95b2;"><i class="fa-solid fa-spinner fa-spin"></i> 加载交易明细中...</td></tr>`;

    document.getElementById('backtestDetailModal').classList.add('active');

    try {
        const res = await fetch(`/api/backtest/trades/${backtestId}`);
        const result = await res.json();
        const trades = result.data || [];

        if (trades.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#8e95b2;">无成交记录</td></tr>`;
            return;
        }

        tbody.innerHTML = trades.map(t => `
            <tr>
                <td data-label="交易日期">${t.date}</td>
                <td data-label="标的代码"><code>${t.code}</code></td>
                <td data-label="动作"><span class="badge-tag ${t.action === 'BUY' ? 'badge-buy' : 'badge-sell'}">${t.action === 'BUY' ? '买入' : '卖出'}</span></td>
                <td data-label="成交价格">¥${t.price.toFixed(2)}</td>
                <td data-label="成交数量">${t.shares}</td>
                <td data-label="手续费">¥${t.fee.toFixed(2)}</td>
                <td data-label="盈亏金额" class="${t.pnl >= 0 ? 'up-val' : 'down-val'}">${t.pnl !== null ? '¥' + t.pnl.toFixed(2) : '--'}</td>
                <td data-label="盈亏比例" class="${t.pnl_pct >= 0 ? 'up-val' : 'down-val'}">${t.pnl_pct !== null ? (t.pnl_pct >= 0 ? '+' : '') + t.pnl_pct.toFixed(2) + '%' : '--'}</td>
                <td data-label="原因" style="color:#8e95b2;">${t.reason || '-'}</td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#df4b5e;">加载成交明细失败: ${e.message}</td></tr>`;
    }
}

function closeBacktestDetailModal() {
    document.getElementById('backtestDetailModal').classList.remove('active');
}

// 3. 策略推荐结果
async function loadRecommendationRuns() {
    const search = document.getElementById('recRunSearchInput').value;
    const category = document.getElementById('recRunCategorySelect').value;
    const grid = document.getElementById('recRunsCardGrid');

    try {
        const res = await fetch(`/api/recommendation-runs?search=${encodeURIComponent(search)}&category=${category}`);
        const result = await res.json();

        if (result.status !== 'success') {
            grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #df4b5e;">${result.message}</div>`;
            return;
        }

        const runs = result.data || [];
        if (runs.length === 0) {
            grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #8e95b2; padding: 2rem;">暂无策略执行历史履历</div>`;
            return;
        }

        grid.innerHTML = runs.map(run => {
            const isCB = run.category === 'convertible_bond';
            const catBadge = isCB 
                ? `<span class="badge-tag badge-cb"><i class="fa-solid fa-coins"></i> 可转债</span>` 
                : `<span class="badge-tag badge-stock"><i class="fa-solid fa-layer-group"></i> 股票</span>`;

            return `
                <div class="run-card ${isCB ? 'cb-card' : ''}" onclick="openRecRunDetail('${run.signal_date}', '${run.strategy_id}', '${run.strategy_name}', '${run.category}')">
                    <div>
                        <div class="run-card-header">
                            <span class="run-card-date"><i class="fa-regular fa-calendar"></i> ${run.signal_date}</span>
                            ${catBadge}
                        </div>
                        <div class="run-card-title">${run.strategy_name}</div>
                        <div class="run-card-stats">
                            <span class="badge-tag badge-buy"><i class="fa-solid fa-arrow-up"></i> 买入 ${run.buy_count}</span>
                            <span class="badge-tag badge-sell"><i class="fa-solid fa-arrow-down"></i> 卖出 ${run.sell_count}</span>
                            <span style="color:#8e95b2; margin-left:auto;">共 ${run.total_count} 条信号</span>
                        </div>
                    </div>
                    <div class="run-card-footer">
                        <span><i class="fa-regular fa-clock"></i> ${run.run_time || '-'}</span>
                        <div style="display: flex; gap: 0.4rem; align-items: center;">
                            <button class="btn btn-danger" style="padding: 0.2rem 0.55rem; font-size: 0.78rem;" onclick="event.stopPropagation(); deleteRecommendationRunAction('${run.signal_date}', '${run.strategy_id}', '${run.strategy_name}')">
                                <i class="fa-solid fa-trash"></i> 删除
                            </button>
                            <span class="run-card-action">查看明细 <i class="fa-solid fa-chevron-right"></i></span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

    } catch (e) {
        grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #df4b5e;">加载失败: ${e.message}</div>`;
    }
}

// 点击卡片下钻进入详情视图
async function openRecRunDetail(signalDate, strategyId, strategyName, category) {
    currentDetailSignalDate = signalDate;
    currentDetailStrategyId = strategyId;
    currentDetailStrategyName = strategyName;
    currentDetailCategory = category;

    document.getElementById('recRunsMasterContainer').style.display = 'none';
    document.getElementById('recRunDetailContainer').style.display = 'block';

    document.getElementById('recDetailTitleHeader').textContent = `${signalDate} ${strategyName} 盘后推荐明细`;
    document.getElementById('recDetailDateBadge').textContent = signalDate;
    document.getElementById('recDetailDateBadge').className = category === 'convertible_bond' ? 'badge-tag badge-cb' : 'badge-tag badge-stock';

    const tbody = document.getElementById('recDetailTableBody');
    tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: #8e95b2;"><i class="fa-solid fa-spinner fa-spin"></i> 加载明细中...</td></tr>`;

    try {
        const res = await fetch(`/api/recommendations?strategy=${strategyId}&date=${signalDate}`);
        const result = await res.json();

        if (result.status !== 'success') {
            tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#df4b5e;">${result.message}</td></tr>`;
            return;
        }

        currentDetailRunData = result.data || [];
        renderRecDetailTable();

    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#df4b5e;">加载失败: ${e.message}</td></tr>`;
    }
}

// 删除特定策略和特定日期的推荐结果记录
async function deleteRecommendationRunAction(signalDate, strategyId, strategyName) {
    const displayName = strategyName || strategyId;
    if (!confirm(`⚠️ 确定要从数据库中彻底删除策略『${displayName}』在 ${signalDate} 生成的所有推荐结果记录吗？此操作无法撤销。`)) {
        return;
    }

    try {
        const res = await fetch(`/api/recommendations?strategy=${encodeURIComponent(strategyId)}&date=${encodeURIComponent(signalDate)}`, {
            method: 'DELETE'
        });
        const result = await res.json();

        if (result.status === 'success') {
            alert(`✅ ${result.message}`);
            if (document.getElementById('recRunDetailContainer').style.display !== 'none') {
                backToRecRunsMaster();
            } else {
                loadRecommendationRuns();
            }
        } else {
            alert(`❌ 删除失败: ${result.message}`);
        }
    } catch (e) {
        alert(`❌ 网络异常: ${e.message}`);
    }
}

function deleteCurrentRecRunFromDetail() {
    if (currentDetailSignalDate && currentDetailStrategyId) {
        deleteRecommendationRunAction(currentDetailSignalDate, currentDetailStrategyId, currentDetailStrategyName);
    }
}

function backToRecRunsMaster() {
    document.getElementById('recRunDetailContainer').style.display = 'none';
    document.getElementById('recRunsMasterContainer').style.display = 'block';
    loadRecommendationRuns();
}

function filterRecDetailTable() {
    renderRecDetailTable();
}

// 渲染推荐明细表格
function renderRecDetailTable() {
    const header = document.getElementById('recDetailTableHeader');
    const tbody = document.getElementById('recDetailTableBody');
    const actionFilter = document.getElementById('recDetailActionSelect').value;

    const filteredData = currentDetailRunData.filter(d => actionFilter === 'ALL' || d.action === actionFilter);

    if (currentDetailCategory === 'convertible_bond') {
        header.innerHTML = `
            <tr>
                <th>转债代码</th>
                <th>转债名称</th>
                <th>动作</th>
                <th>当前价格</th>
                <th>转股价值</th>
                <th>纯债价值</th>
                <th>到期收益率(YTM)</th>
                <th>正股代码</th>
                <th>正股名称</th>
                <th>正股价格</th>
                <th>推荐原因</th>
            </tr>
        `;

        if (filteredData.length === 0) {
            tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#8e95b2;">暂无推荐结果</td></tr>`;
            return;
        }

        tbody.innerHTML = filteredData.map(d => `
            <tr>
                <td data-label="转债代码"><code>${d.code}</code></td>
                <td data-label="转债名称" style="font-weight:700; color:#00d2c4;">${d.name || '-'}</td>
                <td data-label="信号动作"><span class="badge-tag ${d.action === 'BUY' ? 'badge-buy' : 'badge-sell'}">${d.action === 'BUY' ? '买入' : '卖出'}</span></td>
                <td data-label="当前价格">¥${d.price !== null ? d.price.toFixed(2) : '-'}</td>
                <td data-label="转股价值">${d.convert_value !== null ? '¥' + d.convert_value.toFixed(2) : '-'}</td>
                <td data-label="纯债价值" style="color:#8e95b2;">${d.pure_bond_value !== null ? '¥' + d.pure_bond_value.toFixed(2) : '-'}</td>
                <td data-label="到期收益率(YTM)" style="color:#ffb86c;">${d.ytm !== null ? d.ytm.toFixed(2) + '%' : '-'}</td>
                <td data-label="正股代码"><code>${d.stock_code || '-'}</code></td>
                <td data-label="正股名称">${d.stock_name || '-'}</td>
                <td data-label="正股价格">¥${d.stock_price !== null ? d.stock_price.toFixed(2) : '-'}</td>
                <td data-label="推荐原因" style="font-size:0.8rem; color:#8e95b2;">${d.reason || '-'}</td>
            </tr>
        `).join('');
    } else {
        header.innerHTML = `
            <tr>
                <th>股票代码</th>
                <th>股票名称</th>
                <th>动作</th>
                <th>股票当天价格</th>
                <th>推荐时间</th>
                <th>推荐原因</th>
            </tr>
        `;

        if (filteredData.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#8e95b2;">暂无推荐结果</td></tr>`;
            return;
        }

        tbody.innerHTML = filteredData.map(d => `
            <tr>
                <td data-label="股票代码"><code>${d.code}</code></td>
                <td data-label="股票名称" style="font-weight:700; color:#00d2c4;">${d.name || d.code}</td>
                <td data-label="信号动作"><span class="badge-tag ${d.action === 'BUY' ? 'badge-buy' : 'badge-sell'}">${d.action === 'BUY' ? '买入' : '卖出'}</span></td>
                <td data-label="股票价格">¥${d.price !== null ? d.price.toFixed(2) : '-'}</td>
                <td data-label="推荐时间">${d.date || '-'}</td>
                <td data-label="推荐原因" style="font-size:0.8rem; color:#8e95b2;">${d.reason || '-'}</td>
            </tr>
        `).join('');
    }
}

// 模拟实盘持仓与组合收益引擎
async function loadPortfolioStrategyOptions() {
    try {
        const res = await fetch('/api/portfolio/strategies');
        const result = await res.json();
        if (result.status === 'success' && result.data && result.data.length > 0) {
            const select = document.getElementById('portfolioStrategySelect');
            const currVal = select.value;
            select.innerHTML = '';
            result.data.forEach(stg => {
                const opt = document.createElement('option');
                opt.value = stg.strategy_id;
                opt.textContent = stg.name || stg.strategy_id;
                select.appendChild(opt);
            });
            if (currVal && result.data.some(s => s.strategy_id === currVal)) {
                select.value = currVal;
            }
        }
    } catch (err) {
        console.error("加载持仓策略下拉失败:", err);
    }
}

async function loadPortfolioData() {
    const stgId = document.getElementById('portfolioStrategySelect').value || 'cb_double_low';
    try {
        const res = await fetch(`/api/portfolio?strategy_id=${stgId}`);
        const result = await res.json();
        if (result.status === 'success' && result.data) {
            renderPortfolioDashboard(result.data);
        }
    } catch (err) {
        console.error("加载模拟持仓失败:", err);
    }
}

async function syncPortfolioData() {
    const stgId = document.getElementById('portfolioStrategySelect').value || 'cb_double_low';
    const btn = document.getElementById('btnSyncPortfolio');
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 正在推演中...';

    try {
        const res = await fetch('/api/portfolio/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ strategy_id: stgId })
        });
        const result = await res.json();
        if (result.status === 'success' && result.data) {
            renderPortfolioDashboard(result.data);
            alert(`✅ 模拟实盘盘后推演成功！推演 ${result.sync_result?.days_count || 0} 个交易日。`);
        } else {
            alert(`⚠️ 同步失败: ${result.message || '未知错误'}`);
        }
    } catch (err) {
        alert(`❌ 同步失败: ${err.message}`);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> 重新同步/推演模拟盘';
    }
}

function renderPortfolioDashboard(data) {
    document.getElementById('portTotalEquity').textContent = '¥ ' + (data.total_equity || 0).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    document.getElementById('portCash').textContent = '¥ ' + (data.cash || 0).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    document.getElementById('portMarketValue').textContent = '¥ ' + (data.market_value || 0).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    document.getElementById('portHoldingCount').textContent = data.holding_count || 0;
    document.getElementById('portLatestDate').textContent = data.latest_date || '--';

    const cumPnl = data.cum_pnl || 0;
    const cumRet = data.cum_return || 0;
    const dailyPnl = data.daily_pnl || 0;

    const elCumPnl = document.getElementById('portCumPnl');
    const elCumRet = document.getElementById('portCumReturn');
    const elDailyPnl = document.getElementById('portDailyPnl');

    elCumPnl.textContent = (cumPnl >= 0 ? '+¥ ' : '-¥ ') + Math.abs(cumPnl).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    elCumPnl.className = cumPnl >= 0 ? 'up-val' : 'down-val';
    elCumRet.textContent = (cumRet >= 0 ? '+' : '') + cumRet.toFixed(2) + '%';
    elCumRet.className = cumRet >= 0 ? 'up-val' : 'down-val';

    elDailyPnl.textContent = (dailyPnl >= 0 ? '+¥ ' : '-¥ ') + Math.abs(dailyPnl).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    elDailyPnl.className = dailyPnl >= 0 ? 'up-val' : 'down-val';

    const activeTable = document.getElementById('tableActivePositions');
    const activePositions = data.active_positions || [];
    document.getElementById('countActivePositions').textContent = activePositions.length;

    if (activePositions.length === 0) {
        activeTable.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#8e95b2; padding:2rem;">当前无活跃持仓</td></tr>`;
    } else {
        activeTable.innerHTML = activePositions.map(pos => {
            const pnlClass = pos.pnl >= 0 ? 'up-val' : 'down-val';
            const pnlSign = pos.pnl >= 0 ? '+' : '';
            return `
                <tr>
                    <td><strong style="color: #00d2c4;">${pos.stock_code}</strong></td>
                    <td style="font-weight: 700;">${pos.stock_name}</td>
                    <td>${pos.buy_date}</td>
                    <td>¥ ${pos.buy_price.toFixed(2)}</td>
                    <td>¥ ${pos.current_price.toFixed(2)}</td>
                    <td>${pos.shares.toLocaleString()}</td>
                    <td>¥ ${pos.cost_total.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
                    <td>¥ ${pos.market_value.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
                    <td class="${pnlClass}">${pnlSign}¥ ${pos.pnl.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
                    <td class="${pnlClass}">${pnlSign}${pos.pnl_pct.toFixed(2)}%</td>
                    <td><span class="badge-tag" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3);">槽位 #${pos.slot_idx + 1}</span></td>
                </tr>
            `;
        }).join('');
    }

    const closedTable = document.getElementById('tableClosedTrades');
    const closedTrades = data.closed_trades || [];
    document.getElementById('countClosedTrades').textContent = closedTrades.length;

    if (closedTrades.length === 0) {
        closedTable.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#8e95b2; padding:2rem;">暂无离场成交记录</td></tr>`;
    } else {
        closedTable.innerHTML = closedTrades.map(tr => {
            const pnlClass = tr.pnl >= 0 ? 'up-val' : 'down-val';
            const pnlSign = tr.pnl >= 0 ? '+' : '';
            const reason = tr.extra_data?.exit_reason || '策略卖出平仓';
            return `
                <tr>
                    <td><strong style="color: #a855f7;">${tr.stock_code}</strong></td>
                    <td style="font-weight: 700;">${tr.stock_name}</td>
                    <td>${tr.buy_date}</td>
                    <td>¥ ${tr.buy_price.toFixed(2)}</td>
                    <td>${tr.sell_date}</td>
                    <td>¥ ${tr.sell_price.toFixed(2)}</td>
                    <td>${tr.shares.toLocaleString()}</td>
                    <td>¥ ${tr.cost_total.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
                    <td class="${pnlClass}">${pnlSign}¥ ${tr.pnl.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
                    <td class="${pnlClass}">${pnlSign}${tr.pnl_pct.toFixed(2)}%</td>
                    <td style="font-size: 0.85rem; color: #94a3b8;">${reason}</td>
                </tr>
            `;
        }).join('');
    }

    renderPortfolioChart(data.equity_curve || []);
}

function renderPortfolioChart(equityCurve) {
    const ctx = document.getElementById('portfolioEquityChart').getContext('2d');
    if (portfolioChartInstance) {
        portfolioChartInstance.destroy();
    }

    const labels = equityCurve.map(item => item.date);
    const returns = equityCurve.map(item => item.cum_return);
    const equities = equityCurve.map(item => (item.total_equity / 10000).toFixed(2));

    portfolioChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '累计收益率 (%)',
                    data: returns,
                    borderColor: '#00d2c4',
                    backgroundColor: 'rgba(0, 210, 196, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3,
                    pointRadius: equityCurve.length > 50 ? 0 : 3,
                    pointHoverRadius: 6,
                    yAxisID: 'y'
                },
                {
                    label: '总资产 (万元)',
                    data: equities,
                    borderColor: '#a855f7',
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    fill: false,
                    tension: 0.3,
                    pointRadius: 0,
                    yAxisID: 'y1'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    labels: { color: '#f0f3fa', font: { family: 'Outfit' } }
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            if (context.dataset.yAxisID === 'y') {
                                return `累计收益率: ${context.parsed.y.toFixed(2)}%`;
                            } else {
                                return `账户总资产: ${context.parsed.y} 万元`;
                            }
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#8e95b2' }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#00d2c4',
                        callback: function(val) { return val.toFixed(1) + '%'; }
                    }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    ticks: {
                        color: '#a855f7',
                        callback: function(val) { return val + '万'; }
                    }
                }
            }
        }
    });
}

function switchPortfolioSubTab(tabName) {
    if (tabName === 'active') {
        document.getElementById('subTabNavActiveHoldings').classList.add('active');
        document.getElementById('subTabNavClosedTrades').classList.remove('active');
        document.getElementById('subSectionActiveHoldings').style.display = 'block';
        document.getElementById('subSectionClosedTrades').style.display = 'none';
    } else {
        document.getElementById('subTabNavActiveHoldings').classList.remove('active');
        document.getElementById('subTabNavClosedTrades').classList.add('active');
        document.getElementById('subSectionActiveHoldings').style.display = 'none';
        document.getElementById('subSectionClosedTrades').style.display = 'block';
    }
}

// 初始化DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    setInterval(() => {
        const now = new Date();
        const liveElem = document.getElementById('liveTime');
        if (liveElem) liveElem.textContent = now.toLocaleTimeString('zh-CN');
    }, 1000);

    loadStrategies();
});
