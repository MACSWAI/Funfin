const tg = window.Telegram.WebApp;
tg.expand(); tg.setHeaderColor('#0f172a');

let isPremium = false;
let globalData = []; // Hanya menyimpan riwayat transaksi (recents)
let dashboardData = null; // FIX: Menyimpan seluruh data API untuk refresh instan tanpa reload
let mediaRecorder = null;
let audioChunks = [];
let userBudget = 0; 
let currentInputMode = 'ai'; 
let currentManualType = 'OUT'; 
let currentCashBal = 0;
let currentWalletBal = 0;
let currentBankBal = 0;
let privacyMode = localStorage.getItem('privacyMode') === 'true';

function showError(msg) {
    if(tg && tg.showPopup) tg.showPopup({title: "Info", message: msg});
    else {
        const box = document.getElementById('errorBox');
        const txt = document.getElementById('errorText');
        if(box && txt) { txt.innerText = msg; box.classList.remove('hidden'); setTimeout(() => box.classList.add('hidden'), 5000); } 
        else alert(msg);
    }
}

function showPremiumLock(featureName) {
    tg.showPopup({
        title: "🔒 Fitur Pro",
        message: `Fitur ${featureName} khusus akun Pro.\n\nAkses Scan Bon, Voice Note, Grafik Tren, dan Export Unlimited.`,
        buttons: [{type: "default", text: "Nanti"}, {type: "ok", text: "Upgrade", id: "upgrade"}]
    }, (btnId) => { if(btnId === "upgrade") tg.sendData("upgrade_request"); });
}

// === PRIVACY MODE LOGIC (AC 2.1 Fixed) ===
function togglePrivacy() {
    privacyMode = !privacyMode;
    localStorage.setItem('privacyMode', privacyMode);
    
    const icon = document.getElementById('privacyIcon');
    if(icon) icon.className = privacyMode ? "fas fa-eye-slash" : "fas fa-eye";
    
    updatePrivacyUI();
}

function formatCurrency(amount, forceReveal = false) {
    if(privacyMode && !forceReveal) return "Rp ••••••";
    let num = typeof amount === 'string' ? parseInt(amount.replace(/[^0-9-]/g, '')) : amount;
    if(isNaN(num)) num = 0;
    return `Rp ${num.toLocaleString('id-ID')}`;
}

function updatePrivacyUI() {
    // FIX AC 2.1.1: Refresh Instan Seluruh UI
    // Menggunakan data yang di-cache (dashboardData) untuk me-render ulang SEMUA tab (termasuk Analisis)
    if (dashboardData) {
        renderUI(dashboardData);
    }

    // Refresh List Riwayat (menggunakan globalData yang sudah ada di memori)
    const activeFilterBtn = document.querySelector('.filter-btn.active');
    const activeFilter = activeFilterBtn ? activeFilterBtn.getAttribute('data-type') : 'ALL';
    filterHistory(activeFilter);
    
    // Refresh Goals (tetap perlu fetch atau re-render jika data goals disimpan global)
    loadGoals(); 
}

// AC 3.1.3 REVEAL LOGIC
window.revealAiAmount = function(btn, amount) {
    const container = btn.previousElementSibling; // The <strong> tag
    container.innerText = `Rp ${amount.toLocaleString('id-ID')}`;
    btn.style.display = 'none';
    setTimeout(() => {
        container.innerText = 'Rp ••••••';
        btn.style.display = 'inline-block';
    }, 5000);
}

async function initApp() {
    if(!tg.initData) console.warn("Not in Telegram.");
    const icon = document.getElementById('privacyIcon');
    if(icon) icon.className = privacyMode ? "fas fa-eye-slash" : "fas fa-eye";

    try {
        const loginReq = await fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ initData: tg.initData || "" }) });
        if (!loginReq.ok) throw new Error("Gagal terhubung ke server.");
        const loginRes = await loginReq.json();
        if(loginRes.status === 'success') loadDashboardData();
        else { document.getElementById('loader').style.display = 'none'; showError("Gagal Login: " + loginRes.message); }
    } catch (e) { document.getElementById('loader').style.display = 'none'; showError("Koneksi Error: " + e.message); }
}

async function loadDashboardData() {
    try {
        const res = await fetch('/api/get_data');
        const d = await res.json();
        document.getElementById('loader').style.display = 'none';
        
        if(d.status === 'success') {
            globalData = d.recents; 
            dashboardData = d; // FIX: Simpan seluruh data respons untuk keperluan refresh privasi
            
            currentCashBal = parseInt(d.cash_balance.replace(/[^0-9-]/g, '')) || 0;
            currentWalletBal = parseInt(d.ewallet_balance.replace(/[^0-9-]/g, '')) || 0;
            currentBankBal = parseInt(d.bank_balance.replace(/[^0-9-]/g, '')) || 0;
            
            renderUI(d);
            loadGoals();
            
            // Force Render Mini List & History saat load awal
            const miniList = document.getElementById('miniList');
            if(miniList) {
                miniList.innerHTML = globalData.length > 0 
                    ? globalData.slice(0,5).map(r => createRow(r)).join('') 
                    : '<div class="flex flex-col items-center justify-center py-6 text-slate-500 gap-2"><i class="fas fa-box-open text-2xl"></i><span class="text-xs">Belum ada data</span></div>';
            }
            filterHistory('ALL');

            document.getElementById('mainContent').classList.remove('hidden');
            document.getElementById('mainContent').classList.add('flex');
        } else showError(d.message);
    } catch (e) { document.getElementById('loader').style.display = 'none'; showError("Gagal memuat data dashboard."); }
}

function renderUI(d) {
    isPremium = d.is_prem;
    const picElem = document.getElementById('profilePic');
    const defaultPic = document.getElementById('defaultProfile');
    if (tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.photo_url) {
        picElem.src = tg.initDataUnsafe.user.photo_url;
        picElem.classList.remove('hidden'); defaultPic.classList.add('hidden');
        picElem.onerror = function() { picElem.classList.add('hidden'); defaultPic.classList.remove('hidden'); };
    } else { picElem.classList.add('hidden'); defaultPic.classList.remove('hidden'); }

    const typeElem = document.getElementById('accountType');
    const expElem = document.getElementById('accountExp');
    const btnExt = document.getElementById('btnExtend');
    const btnAdm = document.getElementById('btnAdminFeedback');
    const fbContainer = document.getElementById('feedbackFormContainer');

    if (typeElem && expElem) {
        if(d.is_admin) {
            typeElem.innerHTML = '<i class="fas fa-user-shield text-rose-500 mr-2"></i> Administrator'; typeElem.className = "text-lg font-bold text-rose-500"; expElem.innerHTML = '<i class="fas fa-bolt text-yellow-400"></i> God Mode';
            if(btnAdm) btnAdm.classList.remove('hidden'); if(fbContainer) fbContainer.classList.add('hidden'); if(btnExt) btnExt.classList.add('hidden');
        } else if(d.is_vip) {
            typeElem.innerHTML = '<i class="fas fa-crown text-purple-400 mr-2"></i> VIP'; typeElem.className = "text-lg font-bold text-purple-400"; expElem.innerHTML = '<i class="fas fa-infinity"></i> Lifetime';
            if(btnAdm) btnAdm.classList.add('hidden'); if(fbContainer) fbContainer.classList.add('hidden'); if(btnExt) btnExt.classList.add('hidden');
        } else if(d.is_prem) {
            typeElem.innerHTML = '<i class="fas fa-star text-amber-400 mr-2"></i> Pro'; typeElem.className = "text-lg font-bold text-amber-400"; expElem.innerHTML = '<i class="fas fa-clock"></i> Exp: ' + (d.expiry_date || "Unknown");
            if(btnAdm) btnAdm.classList.add('hidden'); if(fbContainer) fbContainer.classList.remove('hidden'); if(btnExt) btnExt.classList.remove('hidden');
        } else {
            typeElem.innerHTML = '<i class="fas fa-user text-emerald-400 mr-2"></i> Free Plan'; typeElem.className = "text-lg font-bold text-emerald-400"; expElem.innerHTML = '<i class="fas fa-circle-check"></i> Basic';
            if(btnAdm) btnAdm.classList.add('hidden'); if(fbContainer) fbContainer.classList.remove('hidden'); if(btnExt) btnExt.classList.add('hidden');
        }
    }

    const premBadge = document.getElementById('premBadge');
    if(d.is_prem || d.is_vip || d.is_admin) { premBadge.classList.remove('hidden'); premBadge.innerHTML = '<i class="fas fa-crown"></i> PRO'; }
    else premBadge.classList.add('hidden');

    // UPDATE SEMUA ANGKA DENGAN FORMAT CURRENCY (SENSOR/TIDAK)
    const totalBal = currentCashBal + currentWalletBal + currentBankBal;
    document.getElementById('headerBalanceDisplay').innerText = formatCurrency(totalBal);
    document.getElementById('valBalance').innerText = formatCurrency(totalBal);
    document.getElementById('balCash').innerText = formatCurrency(currentCashBal);
    document.getElementById('balWallet').innerText = formatCurrency(currentWalletBal);

    const proLock = document.getElementById('proLockScreen');
    const proContent = document.getElementById('proContent');
    const ctxTrend = document.getElementById('trendChart');
    
    if(d.is_prem || d.is_vip || d.is_admin) {
        if(proLock) proLock.classList.add('hidden'); if(proContent) proContent.classList.remove('hidden');
        
        userBudget = d.budget_limit || 0;
        let expenseTotal = 0;
        if(d.expense) expenseTotal = parseInt(String(d.expense).replace(/[^0-9]/g, '')) || 0;
        
        const targetElem = document.getElementById('targetAmount');
        const usedElem = document.getElementById('usedAmount');
        const barElem = document.getElementById('budgetBar');
        const pctElem = document.getElementById('budgetPercent');
        
        // FIX: Update angka di tab analisis juga
        if(targetElem) {
            targetElem.innerText = formatCurrency(userBudget); 
            usedElem.innerText = formatCurrency(expenseTotal);
            let pct = userBudget > 0 ? (expenseTotal / userBudget) * 100 : 0;
            pctElem.innerText = `${Math.round(pct)}%`;
            barElem.style.width = `${pct}%`;
            if(pct >= 100) barElem.className = "shadow-none flex flex-col text-center whitespace-nowrap text-white justify-center bg-rose-600 transition-all duration-500 animate-pulse";
            else if (pct >= 80) barElem.className = "shadow-none flex flex-col text-center whitespace-nowrap text-white justify-center bg-amber-500 transition-all duration-500";
            else barElem.className = "shadow-none flex flex-col text-center whitespace-nowrap text-white justify-center bg-purple-500 transition-all duration-500";
        }
        if(d.chart_values) {
            let maxVal = -1; let maxCat = "-";
            d.chart_values.forEach((v, i) => { if(v > maxVal) { maxVal = v; maxCat = d.chart_labels[i]; } });
            // FIX: Update Insight Angka
            document.getElementById('topCategory').innerText = maxCat; 
            document.getElementById('topCatAmount').innerText = formatCurrency(parseInt(maxVal));
            
            let monthlyExp = 0;
            if(d.monthly_exp && d.monthly_exp.length > 0) monthlyExp = d.monthly_exp[d.monthly_exp.length - 1];
            const today = new Date().getDate(); const avg = monthlyExp / (today || 1);
            // FIX: Update Insight Angka
            document.getElementById('dailyAvg').innerText = formatCurrency(parseInt(avg));
        }
        if(ctxTrend && d.monthly_labels && d.monthly_labels.length > 0) {
            if(window.myTrend) window.myTrend.destroy();
            window.myTrend = new Chart(ctxTrend, { type: 'bar', data: { labels: d.monthly_labels, datasets: [{ label: 'Masuk', data: d.monthly_inc, backgroundColor: '#10b981', borderRadius: 4 }, { label: 'Keluar', data: d.monthly_exp, backgroundColor: '#ef4444', borderRadius: 4 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: '#94a3b8', font:{size:10} } }, y: { display: false } } } });
        }
    } else { if(proLock) proLock.classList.remove('hidden'); if(proContent) proContent.classList.add('hidden'); }

    const ctx = document.getElementById('expenseChart');
    if(ctx) {
        if(d.chart_values && d.chart_values.length > 0) {
            if(window.myPie) window.myPie.destroy();
            // Format tooltip chart mungkin tetap angka asli (opsional, biasanya chart library punya format sendiri)
            // Di sini kita hanya format legend di bawahnya
            window.myPie = new Chart(ctx, { type: 'doughnut', data: { labels: d.chart_labels, datasets: [{ data: d.chart_values, backgroundColor: ['#a855f7','#3b82f6','#ef4444','#f59e0b','#10b981','#6366f1','#ec4899'], borderWidth: 0 }] }, options: { responsive:true, cutout:'70%', plugins:{legend:{display:false}} } });
            document.getElementById('chartLegend').innerHTML = d.chart_labels.map((l,i)=>`<div class="flex justify-between items-center mb-1"><div class="flex items-center gap-2"><span class="w-2 h-2 rounded-full" style="background-color:${['#a855f7','#3b82f6','#ef4444','#f59e0b','#10b981','#6366f1','#ec4899'][i%7]}"></span><span class="text-slate-300 text-xs">${l}</span></div><span class="font-mono text-slate-400 text-xs font-bold">${formatCurrency(parseInt(d.chart_values[i]))}</span></div>`).join('');
            document.getElementById('noDataChart').classList.add('hidden');
        } else { if(window.myPie) window.myPie.destroy(); document.getElementById('noDataChart').classList.remove('hidden'); }
    }
}

window.openBalanceModal = function() {
    document.getElementById('modalBalCash').innerText = formatCurrency(currentCashBal);
    document.getElementById('modalBalEwallet').innerText = formatCurrency(currentWalletBal);
    document.getElementById('modalBalBank').innerText = formatCurrency(currentBankBal);
    document.getElementById('modalBalTotal').innerText = formatCurrency(currentCashBal + currentWalletBal + currentBankBal);
    document.getElementById('balanceDetailsModal').classList.remove('hidden');
}

window.openInputPage = function() {
    const p = document.getElementById('inputOverlay'); p.classList.remove('hidden'); setTimeout(() => p.classList.remove('translate-y-full'), 10); switchPageMode('manual');
}
window.closeInputPage = function() {
    const p = document.getElementById('inputOverlay'); p.classList.add('translate-y-full'); setTimeout(() => p.classList.add('hidden'), 300);
}
window.switchPageMode = function(mode) {
    const btnMan = document.getElementById('tab-mode-manual'); const btnAi = document.getElementById('tab-mode-ai'); const pageMan = document.getElementById('page-manual'); const pageAi = document.getElementById('page-ai');
    if(mode === 'manual') {
        btnMan.className = "flex-1 py-2.5 rounded-lg text-sm font-medium transition bg-purple-600 text-white shadow-lg flex items-center justify-center gap-2";
        btnAi.className = "flex-1 py-2.5 rounded-lg text-sm font-medium text-slate-400 transition hover:text-white flex items-center justify-center gap-2";
        pageMan.classList.remove('hidden'); pageAi.classList.add('hidden');
    } else {
        btnMan.className = "flex-1 py-2.5 rounded-lg text-sm font-medium text-slate-400 transition hover:text-white flex items-center justify-center gap-2";
        btnAi.className = "flex-1 py-2.5 rounded-lg text-sm font-medium transition bg-indigo-600 text-white shadow-lg flex items-center justify-center gap-2";
        pageMan.classList.add('hidden'); pageAi.classList.remove('hidden');
    }
}
window.setManualType = function(type) {
    currentManualType = type;
    const btnOut = document.getElementById('btn-type-out'); const btnIn = document.getElementById('btn-type-in'); const catWrapper = document.getElementById('catWrapper'); const catSelect = document.getElementById('manualCat');
    if(type === 'OUT') {
        btnOut.className = "py-3 border border-rose-500/50 bg-rose-500/10 text-rose-500 rounded-xl text-sm font-bold flex items-center justify-center gap-2 transition ring-2 ring-rose-500 shadow-lg shadow-rose-900/20";
        btnIn.className = "py-3 border border-slate-700 bg-slate-800 text-slate-400 rounded-xl text-sm font-bold flex items-center justify-center gap-2 transition";
        catWrapper.classList.remove('hidden'); catSelect.value = "Makanan";
    } else {
        btnOut.className = "py-3 border border-slate-700 bg-slate-800 text-slate-400 rounded-xl text-sm font-bold flex items-center justify-center gap-2 transition";
        btnIn.className = "py-3 border border-emerald-500/50 bg-emerald-500/10 text-emerald-500 rounded-xl text-sm font-bold flex items-center justify-center gap-2 transition ring-2 ring-emerald-500";
        catWrapper.classList.add('hidden'); 
    }
}

window.submitManual = function() {
    const amt = parseInt(document.getElementById('manualAmount').value); const desc = document.getElementById('manualDesc').value; let cat = document.getElementById('manualCat').value; const wal = document.getElementById('manualWallet').value;
    if(!amt || !desc) return alert("Lengkapi data!");
    if (currentManualType === 'IN') { cat = 'Pemasukan'; } 
    else { if ((wal === 'Cash' && amt > currentCashBal) || (wal === 'E-Wallet' && amt > currentWalletBal) || (wal === 'Bank' && amt > currentBankBal)) { showError(`Saldo ${wal} tidak cukup!`); return; } }
    const fd = new FormData(); fd.append('amount', amt); fd.append('description', desc); fd.append('category', cat); fd.append('wallet', wal); fd.append('type', currentManualType); fd.append('mode', 'manual');
    const btn = document.querySelector('#page-manual button[onclick="submitManual()"]'); const oldTxt = btn.innerHTML; btn.innerHTML = '<i class="fas fa-circle-notch fa-spin mr-2"></i> MENYIMPAN...'; btn.disabled = true;
    fetch('/add_transaction', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{
        btn.innerHTML = oldTxt; btn.disabled = false;
        if(d.status==='success') { document.getElementById('manualAmount').value = ''; document.getElementById('manualDesc').value = ''; closeInputPage(); loadDashboardData(); tg.HapticFeedback.notificationOccurred('success'); tg.showPopup({title:"Sukses", message:"Tersimpan!"}); } else showError(d.message);
    });
}

window.sendText = function() {
    const input = document.getElementById('textInput'); if(!input || !input.value) return;
    const btn = document.getElementById('sendBtn'); const oldIcon = btn.innerHTML; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    const fd = new FormData(); fd.append('text_input', input.value); fd.append('mode', 'text');
    fetch('/add_transaction', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{
        btn.innerHTML = oldIcon;
        if(d.status==='success') { input.value=''; closeInputPage(); loadDashboardData(); tg.showPopup({title:"Sukses", message:"Tersimpan!"}); tg.HapticFeedback.notificationOccurred('success'); } 
        else showError(d.message);
    }).catch(e => { btn.innerHTML = oldIcon; showError("Gagal kirim: " + e.message); });
}

window.toggleRecording = function() {
    if(!isPremium) { showPremiumLock("Voice Note"); return; }
    const recInd = document.getElementById('recordingIndicator'); const micBtn = document.getElementById('micBtn');
    if(mediaRecorder && mediaRecorder.state === "recording") { mediaRecorder.stop(); if(recInd) recInd.classList.add('hidden'); micBtn.classList.remove('bg-rose-500/10', 'border-rose-500'); } 
    else {
        navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
            mediaRecorder = new MediaRecorder(stream); audioChunks = [];
            mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
            mediaRecorder.onstop = () => {
                const blob = new Blob(audioChunks, { type: 'audio/ogg; codecs=opus' }); const fd = new FormData(); fd.append('media_file', blob, 'voice.ogg'); fd.append('mode', 'voice');
                document.getElementById('loader').style.display = 'flex'; document.getElementById('loaderText').innerText = "AI sedang mendengarkan...";
                fetch('/add_transaction', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{
                    document.getElementById('loader').style.display = 'none';
                    if(d.status==='success') { closeInputPage(); loadDashboardData(); tg.HapticFeedback.notificationOccurred('success'); } else showError(d.message);
                });
            }; mediaRecorder.start(); if(recInd) recInd.classList.remove('hidden'); micBtn.classList.add('bg-rose-500/10', 'border-rose-500');
        }).catch(e => showError("Mikrofon tidak diizinkan."));
    }
}

window.handleFileUpload = function(input) {
    if(!isPremium) { showPremiumLock("Scan Bon"); input.value=''; return; }
    if(input.files.length > 0) {
        const fd = new FormData(); fd.append('media_file', input.files[0]); fd.append('mode', 'image');
        document.getElementById('loader').style.display = 'flex'; document.getElementById('loaderText').innerText = "AI menganalisis gambar...";
        fetch('/add_transaction', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{
            document.getElementById('loader').style.display = 'none'; input.value = '';
            if(d.status==='success') { closeInputPage(); loadDashboardData(); tg.HapticFeedback.notificationOccurred('success'); } else showError(d.message);
        });
    }
}

function createRow(r) {
    const color = r.type === 'IN' ? 'text-emerald-400' : 'text-rose-400'; const sign = r.type === 'IN' ? '+' : '-'; const icon = r.type === 'IN' ? 'fa-arrow-down' : 'fa-arrow-up';
    return `<div onclick="openEditModal(${r.id})" class="cursor-pointer bg-slate-800 p-3 rounded-xl border border-slate-700/50 flex items-center gap-3 active:scale-95 transition hover:bg-slate-700"><div class="w-10 h-10 rounded-full bg-slate-900 flex items-center justify-center shrink-0 border border-slate-700"><i class="fas ${icon} text-sm ${color}"></i></div><div class="flex-1 min-w-0"><p class="font-bold text-sm truncate text-white">${r.description}</p><div class="flex items-center gap-2 text-[10px] text-slate-400 mt-1"><span class="bg-slate-900 px-1.5 py-0.5 rounded border border-slate-700">${r.category}</span><span>•</span><span>${r.wallet}</span></div></div><p class="font-bold text-sm ${color} whitespace-nowrap privacy-blur">${sign} ${formatCurrency(parseInt(r.amount))}</p></div>`;
}

function filterHistory(type) {
    document.querySelectorAll('.filter-btn').forEach(b => { b.className = "filter-btn px-4 py-2 border border-slate-700 rounded-full text-xs font-bold whitespace-nowrap bg-slate-800 text-slate-400 transition hover:bg-slate-700 normal-case"; b.setAttribute('data-type', 'ALL'); });
    if(event && event.target) { let target = event.target.closest('button'); if(target) { target.className = "filter-btn active px-4 py-2 bg-purple-600 text-white rounded-full text-xs font-bold whitespace-nowrap transition shadow-md normal-case"; target.setAttribute('data-type', type); } }
    let data = globalData; if(type !== 'ALL') data = globalData.filter(i => i.type === type);
    const container = document.getElementById('fullHistoryList'); if(container) container.innerHTML = data.length ? data.map(r => createRow(r)).join('') : '<div class="flex flex-col items-center justify-center py-10 text-slate-500 gap-2"><i class="fas fa-clipboard-list text-3xl opacity-50"></i><span class="text-xs">Data Kosong</span></div>';
}

window.switchTab = function(id) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active')); document.getElementById('tab-'+id).classList.add('active');
    const fab = document.getElementById('fabAdd'); if(fab) { if(id === 'beranda') { fab.classList.remove('hidden'); fab.classList.add('flex'); } else { fab.classList.add('hidden'); fab.classList.remove('flex'); } }
    const header = document.getElementById('headerSubtitle'); if(header) { const titles = {'beranda': 'Dashboard', 'analisis': 'Analisis', 'riwayat': 'Riwayat', 'lainnya': 'Menu Lainnya', 'goals': 'Rencana'}; header.innerText = titles[id] || "Menu"; }
    const tabOrder = ['beranda', 'analisis', 'goals', 'riwayat', 'lainnya'];
    document.querySelectorAll('.nav-item').forEach((el, i) => { el.classList.remove('text-purple-500'); el.classList.add('text-slate-500'); if(i === tabOrder.indexOf(id)) { el.classList.remove('text-slate-500'); el.classList.add('text-purple-500'); } });
}

async function loadGoals() {
    const res = await fetch('/api/goals'); const d = await res.json(); const list = document.getElementById('goalsList');
    if(d.status === 'success' && d.data.length > 0) {
        list.innerHTML = d.data.map(g => {
            const pct = Math.min(100, Math.round((g.current/g.target)*100));
            let pColor = g.priority === 'P1' ? 'text-rose-500 bg-rose-500/10' : (g.priority === 'P2' ? 'text-amber-500 bg-amber-500/10' : 'text-blue-500 bg-blue-500/10');
            const currentFmt = formatCurrency(parseInt(g.current)); const targetFmt = formatCurrency(parseInt(g.target));
            return `<div class="bg-slate-800 p-4 rounded-xl border border-slate-700 flex flex-col gap-3 group relative overflow-hidden"><div class="flex justify-between items-start z-10"><div><span class="text-[10px] font-bold px-2 py-0.5 rounded ${pColor} mb-1 inline-block">${g.priority}</span><h4 class="font-bold text-sm text-white">${g.title}</h4></div><div class="flex gap-1"><button onclick="openEditGoal(${g.id}, '${g.title}', ${g.target}, '${g.deadline}', '${g.priority}')" class="w-7 h-7 rounded-lg bg-blue-500/10 text-blue-500 flex items-center justify-center hover:bg-blue-500 hover:text-white transition"><i class="fas fa-pen text-[10px]"></i></button><button onclick="openGoalDeposit(${g.id}, '${g.title}')" class="w-7 h-7 rounded-lg bg-emerald-500/10 text-emerald-500 flex items-center justify-center hover:bg-emerald-500 hover:text-white transition shadow-sm"><i class="fas fa-plus text-[10px]"></i></button><button onclick="deleteGoal(${g.id})" class="w-7 h-7 rounded-lg bg-slate-700 text-slate-400 flex items-center justify-center hover:bg-rose-500 hover:text-white transition"><i class="fas fa-trash text-[10px]"></i></button></div></div><div class="w-full bg-slate-900 h-2 rounded-full overflow-hidden z-10 border border-slate-700/50"><div class="bg-gradient-to-r from-purple-600 to-indigo-500 h-full transition-all duration-1000" style="width:${pct}%"></div></div><div class="flex justify-between text-[10px] text-slate-400 z-10 font-medium"><span>${currentFmt} / ${targetFmt}</span><span class="${pct >= 100 ? 'text-emerald-400 font-bold' : ''}">${pct}%</span></div><div class="absolute -bottom-6 -right-6 w-24 h-24 bg-purple-500/5 rounded-full blur-2xl z-0 group-hover:bg-purple-500/10 transition"></div></div>`;
        }).join('');
    } else list.innerHTML = '<div class="text-center py-10 text-slate-500 text-xs flex flex-col items-center gap-2"><i class="fas fa-bullseye text-2xl opacity-20"></i><span>Belum ada tujuan</span></div>';
}

window.openEditGoal = function(id, title, target, deadline, priority) { document.getElementById('editGoalId').value = id; document.getElementById('editGoalTitle').value = title; document.getElementById('editGoalTarget').value = target; document.getElementById('editGoalDeadline').value = deadline; document.getElementById('editGoalPriority').value = priority; document.getElementById('editGoalModal').classList.remove('hidden'); }
window.submitEditGoal = function() {
    const id = document.getElementById('editGoalId').value; const title = document.getElementById('editGoalTitle').value; const target = document.getElementById('editGoalTarget').value; const deadline = document.getElementById('editGoalDeadline').value; const priority = document.getElementById('editGoalPriority').value;
    if(!title || !target || !deadline) return alert("Lengkapi data!");
    const btn = document.querySelector('#editGoalModal button[onclick="submitEditGoal()"]'); const oldHtml = btn.innerHTML; btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Saving...'; btn.disabled = true;
    fetch('/api/edit_goal', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id, title, target, deadline, priority }) }).then(r => r.json()).then(d => { btn.innerHTML = oldHtml; btn.disabled = false; if(d.status === 'success') { document.getElementById('editGoalModal').classList.add('hidden'); loadGoals(); tg.showPopup({title:"Sukses", message:"Tujuan diperbarui!"}); } else { showError(d.message); } }).catch(e => { btn.innerHTML = oldHtml; btn.disabled = false; showError("Error"); });
}

window.openGoalDeposit = function(id, title) { document.getElementById('depGoalId').value = id; document.getElementById('goalDepositTitle').innerText = `Target: ${title}`; document.getElementById('depAmount').value = ''; document.getElementById('goalDepositModal').classList.remove('hidden'); }
window.submitGoalDeposit = function() {
    const id = document.getElementById('depGoalId').value; const wallet = document.getElementById('depWallet').value; const amount = document.getElementById('depAmount').value;
    if(!amount || amount <= 0) return alert("Masukkan nominal yang valid!");
    const btn = document.querySelector('#goalDepositModal button[onclick="submitGoalDeposit()"]'); const oldHtml = btn.innerHTML; btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Proses...'; btn.disabled = true;
    const fd = new FormData(); fd.append('goal_id', id); fd.append('wallet_source', wallet); fd.append('amount', amount);
    fetch('/api/goal_deposit', {method:'POST', body:fd}).then(r => r.json()).then(d => { btn.innerHTML = oldHtml; btn.disabled = false; if(d.status === 'success') { document.getElementById('goalDepositModal').classList.add('hidden'); loadGoals(); loadDashboardData(); tg.showPopup({title:"Berhasil", message:"Saldo berhasil ditambahkan!"}); tg.HapticFeedback.notificationOccurred('success'); } else { showError(d.message); } }).catch(e => { btn.innerHTML = oldHtml; btn.disabled = false; showError("Koneksi error"); });
}

window.optimizeGoals = function() {
    const btnOpt = document.querySelector('button[onclick="optimizeGoals(); event.stopPropagation()"]'); const oldIcon = btnOpt.innerHTML; btnOpt.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Analyzing...'; btnOpt.disabled = true;
    fetch('/api/optimize_goals').then(r=>r.json()).then(d=>{
        btnOpt.innerHTML = oldIcon; btnOpt.disabled = false;
        if(d.status==='success') {
            const content = document.getElementById('resultContent'); const actions = document.getElementById('resultActions'); const note = document.getElementById('resultNote');
            content.innerHTML = d.advice.map(txt => `<p class="flex items-start gap-2">${txt}</p>`).join('');
            note.innerText = d.calculation_note || ""; 
            if(d.action) {
                const rawAmt = parseInt(d.action.amount);
                let displayAmt = formatCurrency(rawAmt);
                let revealBtn = privacyMode ? `<button onclick="revealAiAmount(this, ${rawAmt})" class="ml-2 text-xs bg-slate-700 px-2 py-1 rounded hover:bg-slate-600 transition"><i class="fas fa-eye"></i></button>` : '';
                actions.innerHTML = `<div class="bg-slate-700/50 p-3 rounded-xl mb-3 text-center"><span class="text-xs text-slate-400">Saran Tabungan:</span><br><strong class="text-white text-lg">${displayAmt}</strong> ${revealBtn}</div><button onclick="executeAiAction(${d.action.goal_id}, '${d.action.wallet}', ${d.action.amount})" class="w-full bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-white py-3.5 rounded-xl text-sm font-bold shadow-lg shadow-emerald-900/20 active:scale-95 transition flex items-center justify-center gap-2 animate-pulse"><i class="fas fa-coins"></i> Tabung Sekarang</button><button onclick="closeResultModal()" class="w-full text-slate-400 py-2 text-xs hover:text-white transition">Nanti Saja</button>`;
            } else actions.innerHTML = `<button onclick="closeResultModal()" class="w-full bg-slate-700 hover:bg-slate-600 text-white py-3 rounded-xl font-bold transition">Tutup</button>`;
            const m = document.getElementById('resultModal'); m.classList.remove('hidden'); setTimeout(() => { m.classList.remove('opacity-0'); m.querySelector('div').classList.remove('scale-90'); m.querySelector('div').classList.add('scale-100'); }, 10);
        }
    }).catch(e => { btnOpt.innerHTML = oldIcon; btnOpt.disabled = false; showError("Gagal menghubungi AI"); });
}

window.executeAiAction = function(goalId, wallet, amount) {
    const fd = new FormData(); fd.append('goal_id', goalId); fd.append('wallet_source', wallet); fd.append('amount', amount);
    const btn = document.querySelector('#resultActions button'); btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Memproses...'; btn.disabled = true;
    fetch('/api/goal_deposit', {method:'POST', body:fd}).then(r => r.json()).then(d => {
        if(d.status === 'success') { closeResultModal(); loadGoals(); loadDashboardData(); tg.showPopup({title:"Hebat!", message:`Berhasil menabung ${formatCurrency(parseInt(amount), true)}!`}); tg.HapticFeedback.notificationOccurred('success'); } 
        else { closeResultModal(); showError(d.message); }
    });
}

window.openGoalModal = function() { document.getElementById('goalModal').classList.remove('hidden'); }
window.submitGoal = function() {
    const title = document.getElementById('goalTitle').value; const target = document.getElementById('goalTarget').value; const deadline = document.getElementById('goalDeadline').value; const priority = document.getElementById('goalPriority').value;
    if(!title || !target || !deadline) return alert("Lengkapi data!");
    fetch('/api/goals', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({title, target: parseInt(target), deadline, priority}) }).then(r=>r.json()).then(d=>{ if(d.status==='success') { document.getElementById('goalModal').classList.add('hidden'); loadGoals(); } else alert("Gagal"); });
}
window.deleteGoal = function(id) { if(confirm("Hapus tujuan ini?")) fetch(`/api/goals?id=${id}`, {method:'DELETE'}).then(r=>r.json()).then(d=>loadGoals()); }
window.closeResultModal = function() { const m = document.getElementById('resultModal'); m.classList.add('opacity-0'); m.querySelector('div').classList.remove('scale-100'); m.querySelector('div').classList.add('scale-90'); setTimeout(() => m.classList.add('hidden'), 300); }
window.openTransferModal = function() { document.getElementById('transferModal').classList.remove('hidden'); }
window.submitTransfer = function() {
    const src = document.getElementById('trfSource').value; const tgt = document.getElementById('trfTarget').value; const amt = document.getElementById('trfAmount').value;
    if(!amt) return alert("Isi nominal!"); if(src === tgt) return alert("Dompet sama!");
    const btn = document.querySelector('#transferModal button'); const old = btn.innerText; btn.innerText="Kirim..."; btn.disabled=true;
    const fd = new FormData(); fd.append('source', src); fd.append('target', tgt); fd.append('amount', amt);
    fetch('/api/transfer_balance', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{ btn.innerText=old; btn.disabled=false; if(d.status==='success') { document.getElementById('transferModal').classList.add('hidden'); loadDashboardData(); tg.showPopup({title:"Berhasil", message:"Transfer sukses!"}); } else alert(d.message); });
}
window.openEditModal = function(id) {
    const item = globalData.find(x => x.id === id); if(!item) return;
    document.getElementById('editId').value = id; document.getElementById('editDesc').value = item.description; document.getElementById('editAmount').value = item.amount; document.getElementById('editCategory').value = item.category; document.getElementById('editWallet').value = item.wallet;
    const m = document.getElementById('editModal'); const c = document.getElementById('editModalContent'); if(m) { m.classList.remove('hidden'); setTimeout(() => { m.classList.remove('opacity-0'); if(c) { c.classList.remove('scale-95'); c.classList.add('scale-100'); } }, 10); }
}
window.closeEditModal = function() { const m = document.getElementById('editModal'); const c = document.getElementById('editModalContent'); if(m) { m.classList.add('opacity-0'); if(c) { c.classList.remove('scale-100'); c.classList.add('scale-95'); } setTimeout(() => m.classList.add('hidden'), 300); } }
window.saveEdit = function() {
    const fd = new FormData(); fd.append('id', document.getElementById('editId').value); fd.append('description', document.getElementById('editDesc').value); fd.append('amount', document.getElementById('editAmount').value); fd.append('category', document.getElementById('editCategory').value); fd.append('wallet', document.getElementById('editWallet').value);
    fetch('/api/edit_transaction', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{ if(d.status==='success') { closeEditModal(); loadDashboardData(); } });
}
window.deleteTransaction = function() { if(confirm("Hapus?")) { const fd = new FormData(); fd.append('id', document.getElementById('editId').value); fetch('/api/delete_transaction', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{ if(d.status==='success') { closeEditModal(); loadDashboardData(); } }); } }
window.downloadExcel = function() {
    const loader = document.getElementById('loader'); if(loader) { loader.style.display='flex'; document.getElementById('loaderText').innerText="Export Excel..."; }
    fetch('/api/download_excel').then(async r => { if (!r.ok) throw new Error((await r.json()).message || "Gagal download"); return r.blob(); }).then(blob=>{ loader.style.display='none'; if(navigator.share) { const file = new File([blob], "Laporan.xlsx", {type: blob.type}); navigator.share({files:[file], title:"Laporan Keuangan"}).catch(e=>console.log(e)); } else { const url = window.URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `Laporan.xlsx`; document.body.appendChild(a); a.click(); a.remove(); } }).catch(e=>{ loader.style.display='none'; alert(e.message); });
}
window.openHelpModal = function() { const m = document.getElementById('helpModal'); if(m) { m.classList.remove('hidden'); setTimeout(()=>m.classList.remove('opacity-0'),10); } }
window.closeHelpModal = function() { const m = document.getElementById('helpModal'); if(m) { m.classList.add('opacity-0'); setTimeout(()=>m.classList.add('hidden'),300); } }
window.resetDataConfirm = function() { if(confirm("Hapus SEMUA data?")) fetch('/api/reset_data', {method:'POST'}).then(r=>r.json()).then(d=>{ if(d.status==='success') loadDashboardData(); }); }
window.sendFeedback = function() { const msg = document.getElementById('feedbackText').value; if(!msg) return; const fd = new FormData(); fd.append('message', msg); fetch('/api/send_feedback', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{ if(d.status==='success') { document.getElementById('feedbackText').value=''; alert("Terkirim!"); } }); }
window.downloadFeedback = function() { window.location.href = '/api/download_feedback'; }
window.editBudget = function() { if(!isPremium) { showPremiumLock("Budget"); return; } const amt = prompt("Target (Rp):", userBudget); if(amt) { const fd = new FormData(); fd.append('amount', amt); fetch('/api/set_budget', {method:'POST', body:fd}).then(r=>r.json()).then(d=>{ if(d.status==='success') loadDashboardData(); }); } }

document.addEventListener('DOMContentLoaded', initApp);