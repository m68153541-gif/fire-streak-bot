const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

let currentPartnerId = null;
let fireData = { fire_name: 'Огонек', hat: 'none', scarf: 'none', shirt: 'none' };

// Словарь эмодзи для слоев
const EMOJIS = {
    hat: { cap: '🧢', crown: '👑', beanie: '🧶' },
    scarf: { red: '🧣', blue: '🧣' }, // В идеале нужны разные цвета, но пока так
    shirt: { tshirt: '👕', hoodie: '🧥' }
};

async function init() {
    const userId = tg.initDataUnsafe.user.id;
    const res = await fetch(`/api/get_partners?user_id=${userId}`);
    const data = await res.json();
    
    const partnerList = document.getElementById('partnerList');
    if (data.partners.length === 0) {
        partnerList.innerHTML = '<div style="padding:10px; color:#888;">Нет активных огоньков.</div>';
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
    
    // Шапка
    const hatLayer = document.getElementById('layer-hat');
    hatLayer.textContent = (fireData.hat !== 'none' && EMOJIS.hat[fireData.hat]) ? EMOJIS.hat[fireData.hat] : '';
    
    // Шарф
    const scarfLayer = document.getElementById('layer-scarf');
    scarfLayer.textContent = (fireData.scarf !== 'none' && EMOJIS.scarf[fireData.scarf]) ? EMOJIS.scarf[fireData.scarf] : '';
    // Если синий шарф, меняем цвет через фильтр
    scarfLayer.style.filter = fireData.scarf === 'blue' ? 'hue-rotate(180deg)' : 'none';
    
    // Футболка
    const shirtLayer = document.getElementById('layer-shirt');
    shirtLayer.textContent = (fireData.shirt !== 'none' && EMOJIS.shirt[fireData.shirt]) ? EMOJIS.shirt[fireData.shirt] : '';
    
    // Подсветка активных кнопок
    document.querySelectorAll('.item-btn').forEach(btn => {
        const type = btn.dataset.type;
        const value = btn.dataset.value;
        if (fireData[type] === value) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
}

// Переключение вкладок
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.onclick = () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.target).classList.add('active');
    };
});

// Одевание/снятие одежды
document.querySelectorAll('.item-btn').forEach(btn => {
    btn.onclick = () => {
        const type = btn.dataset.type;
        const value = btn.dataset.value;
        
        // Если уже надето - снимаем, иначе надеваем
        if (fireData[type] === value) {
            fireData[type] = 'none';
            sendUpdate(`снял ${type === 'hat' ? 'шапку' : type === 'scarf' ? 'шарф' : 'футболку'}`);
        } else {
            fireData[type] = value;
            sendUpdate(`надел ${type === 'hat' ? 'шапку' : type === 'scarf' ? 'шарф' : 'футболку'}`);
        }
        updateUI();
    };
});

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
