// BlazeMarket Web App - JavaScript

let tg = window.Telegram.WebApp;
let currentUser = null;
let currentRole = null;
let navigationStack = [];

// Initialize app
tg.ready();
tg.expand();

// Get user data from Telegram
function initUser() {
    const user = tg.initDataUnsafe?.user;
    if (user) {
        currentUser = {
            id: user.id,
            username: user.username || user.first_name,
            firstName: user.first_name,
            lastName: user.last_name
        };
        
        document.getElementById('userInfo').innerHTML = `
            <span>👤 ${currentUser.username}</span>
        `;
    }
}

// Navigation functions
function showScreen(screenId) {
    // Hide all screens
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    
    // Show target screen
    document.getElementById(screenId).classList.add('active');
    
    // Add to navigation stack
    if (screenId !== 'startScreen') {
        navigationStack.push(screenId);
    }
}

function goBack() {
    if (navigationStack.length > 1) {
        navigationStack.pop(); // Remove current
        const previous = navigationStack.pop(); // Get previous
        
        // Hide all screens
        document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
        
        // Show previous screen
        document.getElementById(previous).classList.add('active');
        navigationStack.push(previous);
    } else {
        showScreen('startScreen');
        navigationStack = ['startScreen'];
    }
}

// Role selection
function selectRole(role) {
    currentRole = role;
    
    if (role === 'buyer') {
        showScreen('buyerScreen');
    } else {
        showScreen('sellerScreen');
    }
}

// Buyer functions
function showHaveOrder() {
    showScreen('codeScreen');
}

function showCategories() {
    showScreen('categoriesScreen');
}

function showBuyerMenu() {
    showScreen('buyerScreen');
}

function showCodeScreen() {
    showScreen('codeScreen');
}

function selectCategory(category) {
    // Load services for category
    loadServices(category);
    showScreen('servicesScreen');
}

function submitCode() {
    const code = document.getElementById('orderCodeInput').value.trim().toUpperCase();
    
    if (!code) {
        tg.showAlert('Введите код заказа');
        return;
    }
    
    // Send to bot for validation
    tg.sendData(JSON.stringify({
        action: 'validate_code',
        code: code,
        role: currentRole
    }));
    
    showScreen('commentsScreen');
}

function submitComments() {
    const text = document.getElementById('commentsText').value.trim();
    const fileInput = document.getElementById('commentsFile');
    
    if (!text && !fileInput.files.length) {
        tg.showAlert('Введите комментарии или прикрепите файл');
        return;
    }
    
    // Clean comments (remove # symbols)
    const cleanedText = text.replace(/#/g, '');
    
    // Send to bot
    tg.sendData(JSON.stringify({
        action: 'submit_comments',
        comments: cleanedText,
        hasFile: fileInput.files.length > 0
    }));
    
    tg.showAlert('✅ Комментарии отправлены!');
    showScreen('buyerScreen');
}

function handleFileSelect() {
    const fileInput = document.getElementById('commentsFile');
    const fileName = document.getElementById('fileName');
    
    if (fileInput.files.length > 0) {
        fileName.textContent = fileInput.files[0].name;
    } else {
        fileName.textContent = '';
    }
}

// Seller functions
function showSellerMenu() {
    showScreen('sellerScreen');
}

function buyBot() {
    tg.showConfirm('Купить бота за 1000₽?', (confirmed) => {
        if (confirmed) {
            // Send payment request to bot
            tg.sendData(JSON.stringify({
                action: 'buy_bot',
                amount: 1000
            }));
            
            tg.showProgress(true);
            setTimeout(() => tg.showProgress(false), 2000);
        }
    });
}

function createShop() {
    // Request shop creation from bot
    tg.sendData(JSON.stringify({
        action: 'create_shop'
    }));
    
    tg.showProgress(true);
    
    // Simulate response
    setTimeout(() => {
        tg.showProgress(false);
        const mirrorCode = generateCode(6);
        document.getElementById('mirrorCode').textContent = mirrorCode;
        showScreen('mirrorScreen');
    }, 1500);
}

function setMarkup() {
    tg.showPrompt('Введите процент наценки (например, 20):', (value) => {
        const markup = parseFloat(value);
        
        if (isNaN(markup) || markup < 0 || markup > 1000) {
            tg.showAlert('Неверное значение');
            return;
        }
        
        // Send to bot
        tg.sendData(JSON.stringify({
            action: 'set_markup',
            markup: markup
        }));
        
        document.getElementById('mirrorMarkup').textContent = markup + '%';
        tg.showAlert(`✅ Наценка установлена: ${markup}%`);
    });
}

function copyMirrorLink() {
    const code = document.getElementById('mirrorCode').textContent;
    const link = `https://t.me/BlazeMarketBot?start=mirror_${code}`;
    
    navigator.clipboard.writeText(link).then(() => {
        tg.showAlert('✅ Ссылка скопирована!');
    });
}

function editMarkup() {
    setMarkup();
}

// Services functions
function loadServices(category) {
    const servicesList = document.getElementById('servicesList');
    const servicesTitle = document.getElementById('servicesTitle');
    
    servicesTitle.textContent = `📦 Услуги: ${category.charAt(0).toUpperCase() + category.slice(1)}`;
    
    // Sample services (replace with API call)
    const sampleServices = [
        { id: 1, name: 'Подписчики Telegram', price: 0.50, min: 100, max: 10000 },
        { id: 2, name: 'Просмотры Telegram', price: 0.10, min: 1000, max: 100000 },
        { id: 3, name: 'Реакции Telegram', price: 0.30, min: 50, max: 5000 }
    ];
    
    servicesList.innerHTML = sampleServices.map(service => `
        <div class="service-item" onclick="selectService(${service.id})">
            <div class="service-info">
                <div class="service-name">${service.name}</div>
                <div class="service-details">${service.min} - ${service.max} шт.</div>
            </div>
            <div class="service-price">${service.price.toFixed(2)}₽</div>
        </div>
    `).join('');
}

function selectService(serviceId) {
    tg.showAlert(`Услуга ${serviceId} выбрана. Функция в разработке.`);
}

// Utility functions
function generateCode(length = 8) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let result = '';
    for (let i = 0; i < length; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
}

function showLoading(show) {
    const overlay = document.getElementById('loadingOverlay');
    if (show) {
        overlay.classList.add('active');
    } else {
        overlay.classList.remove('active');
    }
}

// Handle messages from bot
window.addEventListener('message', (event) => {
    const data = event.data;
    
    if (data.type === 'service_update') {
        // Update services list
        loadServices(data.category);
    }
    
    if (data.type === 'order_status') {
        tg.showAlert(`Статус заказа: ${data.status}`);
    }
});

// Initialize on load
initUser();
showScreen('startScreen');
navigationStack = ['startScreen'];

// Set theme colors
tg.setHeaderColor(getComputedStyle(document.documentElement).getPropertyValue('--graphite-dark').trim());
tg.setBackgroundColor(getComputedStyle(document.documentElement).getPropertyValue('--bg-dark').trim());
