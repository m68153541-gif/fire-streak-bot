const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

let currentPartnerId = null;
let fireData = { fire_name: 'Огонек', hat: 'none', scarf: 'none', shirt: 'none' };

async function init() {
    const userId = tg.initDataUnsafe.user.id;
    const res = await fetch(`/api/get_partners?user_id=${userId}`);
    const data = await res.json();
    
    const partnerList = document.getElementById('partnerList');
    if (data.partners.length === 0) {
        partnerList.innerHTML = '<div style="padding:10px; color:#888;">Нет активных огоньков. Напиши /faier в боте.</div>';
    } else {
        data.partners.forEach(pid => {
            const div = document.createElement('div');
            div.className = 'partner-item';
            div.textContent = `Собеседник ${pid}`;
            div.onclick = () => selectPartner(pid);
            partnerList.appendChild(div);
        });
    }
}

async function selectPartner(pid) {
    currentPartnerId = pid;
    document.getElementById('selectPartnerBtn').textContent = `👥 Собеседник ${pid}`;
    document.getElementById('partnerList').classList.add('hidden');
    
    const userId = tg.initDataUnsafe.user.id;
    const res = await fetch(`/api/get_fire?user_id=${userId}&partner_id=${pid}`);
    const data = await res.json();
    fireData = data.fire;
    updateUI();
}

function updateUI() {
    document.getElementById('fireName').textContent = fireData.fire_name || 'Огонек';
    let emoji = '🔥';
    if (fireData.hat === 'cap') emoji = '🧢' + emoji;
    if (fireData.scarf === 'red') emoji += '🧣';
    document.getElementById('fireCharacter').textContent = emoji;
}

document.getElementById('selectPartnerBtn').onclick = () => {
    document.getElementById('partnerList').classList.toggle('hidden');
};

document.getElementById('fireCharacter').onclick = () => {
    const phrases = ['Ура!', 'Обнимашки!', 'Щекотно!', 'Я люблю тебя!'];
    const phrase = phrases[Math.floor(Math.random() * phrases.length)];
    const el = document.getElementById('fireName');
    el.textContent = phrase;
    setTimeout(() => el.textContent = fireData.fire_name || 'Огонек', 1000);
    sendUpdate(`потрогал огонька: ${phrase}`);
};

document.querySelectorAll('.option-btn').forEach(btn => {
    btn.onclick = () => {
        fireData[btn.dataset.type] = btn.dataset.value;
        updateUI();
        sendUpdate(`сменил ${btn.dataset.type}`);
    };
});

document.getElementById('saveBtn').onclick = () => {
    const newName = document.getElementById('fireNameInput').value;
    if (newName) {
        fireData.fire_name = newName;
        updateUI();
        sendUpdate(`переименовал огонька в "${newName}"`);
    }
};

async function sendUpdate(action) {
    if (!currentPartnerId) {
        tg.showAlert('Выбери собеседника!');
        return;
    }
    await fetch('/api/update_fire', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            user_id: tg.initDataUnsafe.user.id,
            partner_id: currentPartnerId,
            fire: fireData,
            action: action
        })
    });
}

init();
