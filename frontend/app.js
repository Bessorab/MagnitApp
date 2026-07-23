// ============================================================================
// MagnitApp - основна логіка фронтенду (без фреймворків, звичайний JS)
// ============================================================================

const API = "/api";
let TOKEN = localStorage.getItem("magnit_token") || null;
let ME = null; // { user_id, username, role, location }

function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  if (TOKEN) h["Authorization"] = "Bearer " + TOKEN;
  return h;
}

async function api(path, options) {
  options = options || {};
  const opts = { method: options.method || "GET", headers: authHeaders(options.headers) };
  if (options.json) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(options.json);
  } else if (options.form) {
    opts.body = options.form; // FormData - браузер сам виставить Content-Type
  }
  const resp = await fetch(API + path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || "Помилка сервера");
  }
  return data;
}

// ----------------------------------------------------------------------------
// Стиснення фото ПРЯМО В ТЕЛЕФОНІ перед відправкою - фото з камери зазвичай
// 3000-4000px і кілька мегабайт, що дуже повільно і завантажується, і
// обробляється на слабкому сервері. Зменшуємо до розумного розміру заздалегідь.
// ----------------------------------------------------------------------------
function resizePhotoBeforeUpload(file, maxDimension = 1280, quality = 0.82) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      let { width, height } = img;
      if (width > maxDimension || height > maxDimension) {
        if (width >= height) {
          height = Math.round(height * (maxDimension / width));
          width = maxDimension;
        } else {
          width = Math.round(width * (maxDimension / height));
          height = maxDimension;
        }
      }
      const canvas = document.createElement("canvas");
      canvas.width = width; canvas.height = height;
      canvas.getContext("2d").drawImage(img, 0, 0, width, height);
      canvas.toBlob(
        (blob) => resolve(blob ? new File([blob], file.name || "photo.jpg", { type: "image/jpeg" }) : file),
        "image/jpeg", quality
      );
    };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(file); }; // якщо щось пішло не так - шлемо як є
    img.src = url;
  });
}

function toast(msg, kind) {
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ----------------------------------------------------------------------------
// Логін / вихід
// ----------------------------------------------------------------------------
async function doLogin(username, password) {
  const data = await api("/login", { method: "POST", json: { username, password } });
  TOKEN = data.token;
  localStorage.setItem("magnit_token", TOKEN);
  ME = data;
  showMainApp();
  setupPushNotifications();
}

function doLogout() {
  TOKEN = null;
  ME = null;
  localStorage.removeItem("magnit_token");
  showLoginScreen();
}

async function tryRestoreSession() {
  if (!TOKEN) return false;
  try {
    ME = await api("/me");
    return true;
  } catch (e) {
    TOKEN = null;
    localStorage.removeItem("magnit_token");
    return false;
  }
}

// ----------------------------------------------------------------------------
// Навігація між екранами
// ----------------------------------------------------------------------------
const SELLER_TABS = [
  { id: "sale", label: "🛒 Продаж" },
  { id: "list", label: "📦 Товари" },
  { id: "today", label: "📊 Сьогодні" },
  { id: "supply", label: "📥 Прихід" },
  { id: "repairs", label: "🔧 Ремонти" },
  { id: "recount", label: "🔄 Переоблік" },
];
const ADMIN_TABS = [
  { id: "sellers", label: "👥 Продавці" },
  { id: "requests", label: "🔔 Запити" },
  { id: "price", label: "💰 Ціна" },
  { id: "locations", label: "🏷️ Точки" },
  { id: "reports", label: "📈 Звіти" },
  { id: "repairs_admin", label: "🔧 Ремонти" },
];

let currentTab = null;

function tabsForRole() {
  const tabs = ME.role === "seller" ? SELLER_TABS.slice() : ADMIN_TABS.slice();
  if (ME.role === "head_admin") tabs.push({ id: "admins", label: "👑 Адміни" });
  return tabs;
}

function renderBottomNav() {
  const nav = document.getElementById("bottomNav");
  const tabs = tabsForRole();
  nav.innerHTML = tabs.map(t =>
    `<button data-tab="${t.id}" class="${t.id === currentTab ? "active" : ""}">${t.label}</button>`
  ).join("") + `<button data-tab="__logout">🚪 Вихід</button>`;
  nav.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === "__logout") { doLogout(); return; }
      goTab(btn.dataset.tab);
    });
  });
}

function goTab(tabId) {
  currentTab = tabId;
  renderBottomNav();
  const view = document.getElementById("view");
  view.innerHTML = "<p>Завантаження...</p>";
  const renderers = {
    sale: renderSaleView, list: renderListView, today: renderTodayView,
    supply: renderSupplyView, repairs: renderRepairsSellerView, recount: renderRecountView,
    sellers: renderSellersView, price: renderPriceView, locations: renderLocationsView,
    reports: renderReportsView, repairs_admin: renderRepairsAdminView, admins: renderAdminsView,
    requests: renderRequestsView,
  };
  (renderers[tabId] || renderSaleView)();
}

function showLoginScreen() {
  document.getElementById("loginScreen").classList.remove("hidden");
  document.getElementById("mainApp").classList.add("hidden");
}

function showMainApp() {
  document.getElementById("loginScreen").classList.add("hidden");
  document.getElementById("mainApp").classList.remove("hidden");
  document.getElementById("meLabel").textContent =
    `${ME.username} (${roleLabel(ME.role)}${ME.location ? ", " + ME.location : ""})`;
  goTab(ME.role === "seller" ? "sale" : "sellers");
}

function roleLabel(role) {
  return { seller: "продавець", admin: "адмін", head_admin: "головний адмін" }[role] || role;
}

// ----------------------------------------------------------------------------
// Допоміжне: вибір фото з камери, елемент товару, вибір точки (для адміна)
// ----------------------------------------------------------------------------
function cameraInputHtml(id) {
  return `
    <div class="camera-input-wrap">
      <label for="${id}" class="camera-btn">📷</label>
      <input type="file" id="${id}" accept="image/*" capture="environment">
      <p style="margin-top:10px;color:#93a3b8;font-size:13px;">Натисніть, щоб сфотографувати</p>
    </div>`;
}

function itemRowHtml(item, actionsHtml) {
  const img = item.photo_url ? `<img src="${item.photo_url}">` : `<div style="width:56px;height:56px;background:#2a3648;border-radius:8px;"></div>`;
  return `
    <div class="item-row">
      ${img}
      <div class="info">
        <div class="name">${item.name || "(без назви)"} — ${item.color}</div>
        <div class="meta">Ціна: ${item.price} грн | Кількість: ${item.quantity}${item.barcode ? " | " + item.barcode : ""}</div>
      </div>
      ${actionsHtml || ""}
    </div>`;
}

async function locationPickerHtml(selectedId) {
  const locations = await api("/locations");
  return `<select id="${selectedId}">` +
    locations.map(l => `<option value="${l}">${l}</option>`).join("") +
    `</select>`;
}

// ----------------------------------------------------------------------------
// ПРОДАВЕЦЬ: продаж
// ----------------------------------------------------------------------------
function renderSaleView() {
  const view = document.getElementById("view");
  view.innerHTML = `<h2>🛒 Продаж / новий товар</h2>` + cameraInputHtml("salePhotoInput") + `<div id="saleResult"></div>`;
  document.getElementById("salePhotoInput").addEventListener("change", handleSalePhoto);
}

async function handleSalePhoto(e) {
  const file = e.target.files[0];
  if (!file) return;
  const resultEl = document.getElementById("saleResult");
  resultEl.innerHTML = "<p>Стискаю фото...</p>";
  const resized = await resizePhotoBeforeUpload(file);
  resultEl.innerHTML = "<p>Аналізую фото...</p>";
  const form = new FormData();
  form.append("photo", resized);
  try {
    const data = await api("/items/identify", { method: "POST", form });
    if (data.status === "exact_match") {
      renderSaleConfirm(data.item);
    } else if (data.status === "possible_matches") {
      renderSaleMatches(data.matches, data.photo_filename, data.photo_hash, data.barcode);
    } else {
      renderNewItemForm(data.photo_filename, data.photo_hash, data.barcode, "sale");
    }
  } catch (err) {
    resultEl.innerHTML = "";
    toast(err.message, "error");
  }
}

function renderSaleMatches(matches, photoFilename, photoHash, barcode) {
  const resultEl = document.getElementById("saleResult");
  resultEl.innerHTML = `<h3>Схоже на:</h3>` +
    matches.map(m => itemRowHtml(m, `<button class="btn small" onclick="selectSaleMatch(${m.id})">Продати</button>`)).join("") +
    `<button class="btn secondary" id="notMatchBtn">➕ Це новий товар</button>`;
  window.selectSaleMatch = async (itemId) => {
    const item = matches.find(m => m.id === itemId);
    renderSaleConfirm(item);
  };
  document.getElementById("notMatchBtn").addEventListener("click", () => {
    renderNewItemForm(photoFilename, photoHash, barcode, "sale");
  });
}

function renderSaleConfirm(item) {
  const resultEl = document.getElementById("saleResult");
  if (item.quantity <= 0) {
    resultEl.innerHTML = `<p>Товар ${item.name} закінчився. Продаж неможливий.</p>`;
    return;
  }
  resultEl.innerHTML = `
    <div class="card">
      ${itemRowHtml(item)}
      <p style="margin:10px 0;">Спосіб оплати:</p>
      <div class="grid2">
        <button class="btn" id="payCash">💵 Готівка</button>
        <button class="btn" id="payNoncash">💳 Безготівка</button>
      </div>
    </div>`;
  const finalize = async (method) => {
    try {
      await api("/sales", { method: "POST", json: { item_id: item.id, payment_method: method } });
      toast("✅ Продано!");
      renderSaleView();
    } catch (err) { toast(err.message, "error"); }
  };
  document.getElementById("payCash").addEventListener("click", () => finalize("Готівка"));
  document.getElementById("payNoncash").addEventListener("click", () => finalize("Безготівка"));
}

function renderNewItemForm(photoFilename, photoHash, barcode, mode) {
  const resultEl = document.getElementById("saleResult") || document.getElementById("supplyResult");
  resultEl.innerHTML = `
    <div class="card">
      <h3>➕ Новий товар</h3>
      <label>Назва</label><input id="newName" placeholder="Наприклад Кабель Type-C">
      <label>Колір</label><input id="newColor" placeholder="Наприклад Чорний">
      <label>Ціна</label><input id="newPrice" type="number" step="0.01">
      <label>Кількість</label><input id="newQty" type="number" value="1">
      <button class="btn" id="saveNewItem">Зберегти</button>
    </div>`;
  document.getElementById("saveNewItem").addEventListener("click", async () => {
    try {
      await api("/items", {
        method: "POST",
        json: {
          name: document.getElementById("newName").value,
          color: document.getElementById("newColor").value,
          price: parseFloat(document.getElementById("newPrice").value || "0"),
          quantity: parseInt(document.getElementById("newQty").value || "1", 10),
          photo_filename: photoFilename, photo_hash: photoHash, barcode: barcode,
        },
      });
      toast("✅ Товар збережено!");
      if (mode === "sale") renderSaleView(); else renderSupplyView();
    } catch (err) { toast(err.message, "error"); }
  });
}

// ----------------------------------------------------------------------------
// ПРОДАВЕЦЬ: список товарів / пошук
// ----------------------------------------------------------------------------
async function renderListView() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <h2>📦 Товари точки</h2>
    <input id="searchInput" placeholder="Пошук за назвою або кольором...">
    <div id="itemsList">Завантаження...</div>`;
  document.getElementById("searchInput").addEventListener("input", debounce(loadItemsList, 400));
  loadItemsList();
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function loadItemsList() {
  const query = document.getElementById("searchInput").value.trim();
  const listEl = document.getElementById("itemsList");
  try {
    const items = await api("/items" + (query ? "?query=" + encodeURIComponent(query) : ""));
    listEl.innerHTML = items.length
      ? items.map(it => itemRowHtml(it, `<button class="btn small" onclick="promptQtyChange(${it.id}, ${it.quantity})">✏️</button>`)).join("")
      : "<p>Нічого не знайдено.</p>";
  } catch (err) { toast(err.message, "error"); }
}

window.promptQtyChange = async (itemId, currentQty) => {
  const newQty = prompt("Нова кількість:", currentQty);
  if (newQty === null) return;
  try {
    const res = await api(`/items/${itemId}`, { method: "PATCH", json: { quantity: parseInt(newQty, 10), reason: "" } });
    if (res.direct) {
      toast("✅ Кількість оновлено");
    } else {
      toast("📨 Запит надіслано адміну на підтвердження");
    }
    loadItemsList();
  } catch (err) { toast(err.message, "error"); }
};

// ----------------------------------------------------------------------------
// ПРОДАВЕЦЬ: продажі сьогодні
// ----------------------------------------------------------------------------
async function renderTodayView() {
  const view = document.getElementById("view");
  try {
    const data = await api("/sales/today");
    view.innerHTML = `
      <h2>📊 Продажі сьогодні</h2>
      <div class="card">
        <p>💵 Готівка: ${data.cash.count} шт на ${data.cash.sum.toFixed(2)} грн</p>
        <p>💳 Безготівка: ${data.noncash.count} шт на ${data.noncash.sum.toFixed(2)} грн</p>
        <p><b>Разом: ${data.total.count} шт на ${data.total.sum.toFixed(2)} грн</b></p>
      </div>`;
  } catch (err) { view.innerHTML = ""; toast(err.message, "error"); }
}

// ----------------------------------------------------------------------------
// ПРОДАВЕЦЬ: прихід товару
// ----------------------------------------------------------------------------
function renderSupplyView() {
  const view = document.getElementById("view");
  view.innerHTML = `<h2>📥 Прихід товару</h2>` + cameraInputHtml("supplyPhotoInput") + `<div id="supplyResult"></div>`;
  document.getElementById("supplyPhotoInput").addEventListener("change", handleSupplyPhoto);
}

async function handleSupplyPhoto(e) {
  const file = e.target.files[0];
  if (!file) return;
  const resultEl = document.getElementById("supplyResult");
  resultEl.innerHTML = "<p>Стискаю фото...</p>";
  const resized = await resizePhotoBeforeUpload(file);
  resultEl.innerHTML = "<p>Аналізую фото...</p>";
  const form = new FormData();
  form.append("photo", resized);
  try {
    const data = await api("/items/identify", { method: "POST", form });
    if (data.status === "exact_match" || (data.status === "possible_matches" && data.matches.length)) {
      const item = data.status === "exact_match" ? data.item : data.matches[0];
      resultEl.innerHTML = `${itemRowHtml(item)}<label>Скільки додати?</label><input id="supplyQty" type="number" value="1"><button class="btn" id="supplyConfirm">Додати</button>`;
      document.getElementById("supplyConfirm").addEventListener("click", async () => {
        try {
          await api("/sales/supply", { method: "POST", json: { item_id: item.id, quantity: parseInt(document.getElementById("supplyQty").value, 10) } });
          toast("✅ Прихід додано!");
          renderSupplyView();
        } catch (err) { toast(err.message, "error"); }
      });
    } else {
      renderNewItemForm(data.photo_filename, data.photo_hash, data.barcode, "supply");
    }
  } catch (err) { resultEl.innerHTML = ""; toast(err.message, "error"); }
}

// ----------------------------------------------------------------------------
// ПРОДАВЕЦЬ: ремонти (прийняти / видати)
// ----------------------------------------------------------------------------
function renderRepairsSellerView() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <h2>🔧 Ремонти</h2>
    <button class="btn" id="repairNewBtn">➕ Прийняти нову квитанцію</button>
    <button class="btn secondary" id="repairIssueBtn">✅ Видати з ремонту</button>
    <div id="repairArea"></div>`;
  document.getElementById("repairNewBtn").addEventListener("click", renderRepairNewForm);
  document.getElementById("repairIssueBtn").addEventListener("click", renderRepairIssueList);
}

function renderRepairNewForm() {
  const area = document.getElementById("repairArea");
  area.innerHTML = `
    <div class="card">
      ${cameraInputHtml("repairPhotoInput")}
      <label>Номер квитанції (наприклад 3-2239)</label><input id="repairNumber">
      <label>Дата прийняття</label><input id="repairIntakeDate" type="date">
      <button class="btn" id="repairSaveBtn">Зберегти</button>
    </div>`;
  document.getElementById("repairIntakeDate").valueAsDate = new Date();
  let selectedPhoto = null;
  document.getElementById("repairPhotoInput").addEventListener("change", async (e) => {
    selectedPhoto = e.target.files[0] ? await resizePhotoBeforeUpload(e.target.files[0]) : null;
  });
  document.getElementById("repairSaveBtn").addEventListener("click", async () => {
    const form = new FormData();
    if (selectedPhoto) form.append("photo", selectedPhoto);
    form.append("receipt_number", document.getElementById("repairNumber").value);
    form.append("intake_date", document.getElementById("repairIntakeDate").value);
    try {
      const res = await api("/repairs", { method: "POST", form });
      toast("✅ Квитанцію прийнято");
      if (res.gap_warning) toast("⚠️ " + res.gap_warning, "warn");
      renderRepairsSellerView();
    } catch (err) { toast(err.message, "error"); }
  });
}

async function renderRepairIssueList() {
  const area = document.getElementById("repairArea");
  area.innerHTML = "<p>Завантаження...</p>";
  try {
    const pending = await api("/repairs/pending");
    if (!pending.length) { area.innerHTML = "<p>Немає квитанцій в очікуванні видачі.</p>"; return; }
    area.innerHTML = pending.map(r =>
      `<div class="item-row"><div class="info"><div class="name">№${r.receipt_number}</div><div class="meta">Прийнято: ${r.intake_date}</div></div>
       <button class="btn small" onclick="issueRepair(${r.id})">Видати</button></div>`
    ).join("");
  } catch (err) { toast(err.message, "error"); }
}

window.issueRepair = async (repairId) => {
  const date = prompt("Дата видачі (РІК-МІСЯЦЬ-ДЕНЬ):", new Date().toISOString().slice(0, 10));
  if (date === null) return;
  try {
    await api(`/repairs/${repairId}/complete`, { method: "POST", json: { completion_date: date } });
    toast("✅ Видано клієнту");
    renderRepairIssueList();
  } catch (err) { toast(err.message, "error"); }
};

// ----------------------------------------------------------------------------
// АДМІН: продавці
// ----------------------------------------------------------------------------
async function renderSellersView() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <h2>👥 Продавці</h2>
    <div class="card">
      <label>Логін</label><input id="newSellerLogin">
      <label>Пароль</label><input id="newSellerPass" type="password">
      <label>Точка</label><input id="newSellerLocation" placeholder="Наприклад Сокіл">
      <button class="btn" id="addSellerBtn">➕ Додати продавця</button>
    </div>
    <div id="sellersList">Завантаження...</div>`;
  document.getElementById("addSellerBtn").addEventListener("click", async () => {
    try {
      await api("/sellers", {
        method: "POST",
        json: {
          username: document.getElementById("newSellerLogin").value,
          password: document.getElementById("newSellerPass").value,
          location: document.getElementById("newSellerLocation").value,
        },
      });
      toast("✅ Продавця додано");
      renderSellersView();
    } catch (err) { toast(err.message, "error"); }
  });
  try {
    const sellers = await api("/sellers");
    document.getElementById("sellersList").innerHTML = sellers.map(s =>
      `<div class="item-row"><div class="info"><div class="name">${s.username}</div><div class="meta">${s.location}</div></div>
       <button class="btn small danger" onclick="removeSeller(${s.id})">Видалити</button></div>`
    ).join("") || "<p>Продавців ще немає.</p>";
  } catch (err) { toast(err.message, "error"); }
}

window.removeSeller = async (id) => {
  if (!confirm("Видалити цього продавця?")) return;
  try { await api(`/sellers/${id}`, { method: "DELETE" }); toast("Видалено"); renderSellersView(); }
  catch (err) { toast(err.message, "error"); }
};

// ----------------------------------------------------------------------------
// ГОЛОВНИЙ АДМІН: адміни
// ----------------------------------------------------------------------------
async function renderAdminsView() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <h2>👑 Адміни</h2>
    <div class="card">
      <label>Логін</label><input id="newAdminLogin">
      <label>Пароль</label><input id="newAdminPass" type="password">
      <button class="btn" id="addAdminBtn">➕ Додати адміна</button>
    </div>
    <div id="adminsList">Завантаження...</div>`;
  document.getElementById("addAdminBtn").addEventListener("click", async () => {
    try {
      await api("/admins", { method: "POST", json: { username: document.getElementById("newAdminLogin").value, password: document.getElementById("newAdminPass").value } });
      toast("✅ Адміна додано");
      renderAdminsView();
    } catch (err) { toast(err.message, "error"); }
  });
  const admins = await api("/admins");
  document.getElementById("adminsList").innerHTML = admins.map(a =>
    `<div class="item-row"><div class="info"><div class="name">${a.username}</div></div>
     <button class="btn small danger" onclick="removeAdmin(${a.id})">Видалити</button></div>`
  ).join("") || "<p>Немає доданих адмінів.</p>";
}

window.removeAdmin = async (id) => {
  if (!confirm("Видалити цього адміна?")) return;
  try { await api(`/admins/${id}`, { method: "DELETE" }); toast("Видалено"); renderAdminsView(); }
  catch (err) { toast(err.message, "error"); }
};

// ----------------------------------------------------------------------------
// АДМІН: зміна ціни за штрихкодом
// ----------------------------------------------------------------------------
function renderPriceView() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <h2>💰 Змінити ціну</h2>
    <div class="card">
      <label>Штрихкод</label><input id="priceBarcode">
      <label>Нова ціна</label><input id="priceValue" type="number" step="0.01">
      <button class="btn" id="priceSaveBtn">Зберегти</button>
    </div>`;
  document.getElementById("priceSaveBtn").addEventListener("click", async () => {
    try {
      const res = await api("/items/price-by-barcode", { method: "POST", json: { barcode: document.getElementById("priceBarcode").value, price: parseFloat(document.getElementById("priceValue").value) } });
      toast(`✅ Оновлено на ${res.updated_count} товар(ах)`);
    } catch (err) { toast(err.message, "error"); }
  });
}

// ----------------------------------------------------------------------------
// АДМІН: точки (перейменувати / очистити)
// ----------------------------------------------------------------------------
async function renderLocationsView() {
  const view = document.getElementById("view");
  const locations = await api("/locations");
  view.innerHTML = `
    <h2>🏷️ Торгові точки</h2>
    <div class="card">
      <h3>Перейменувати</h3>
      <label>Стара назва</label>${await locationPickerHtml("renameOld")}
      <label>Нова назва</label><input id="renameNew">
      <button class="btn" id="renameBtn">Перейменувати</button>
    </div>
    <div class="card">
      <h3>⚠️ Очистити точку (видалить УСІ товари)</h3>
      ${await locationPickerHtml("clearLoc")}
      <button class="btn danger" id="clearBtn">Очистити</button>
    </div>`;
  document.getElementById("renameBtn").addEventListener("click", async () => {
    try {
      const res = await api("/locations/rename", { method: "POST", json: { old_location: document.getElementById("renameOld").value, new_location: document.getElementById("renameNew").value } });
      toast(`✅ Перейменовано (продавців: ${res.users_updated}, товарів: ${res.items_updated})`);
      renderLocationsView();
    } catch (err) { toast(err.message, "error"); }
  });
  document.getElementById("clearBtn").addEventListener("click", async () => {
    const loc = document.getElementById("clearLoc").value;
    const count = (await api(`/locations/${encodeURIComponent(loc)}/count`)).count;
    if (!confirm(`Видалити ВСІ ${count} товар(ів) точки «${loc}»? Це незворотньо.`)) return;
    try {
      const res = await api(`/locations/${encodeURIComponent(loc)}/clear`, { method: "DELETE" });
      toast(`✅ Видалено ${res.deleted_count} товар(ів)`);
    } catch (err) { toast(err.message, "error"); }
  });
}

// ----------------------------------------------------------------------------
// АДМІН: звіти
// ----------------------------------------------------------------------------
async function renderReportsView() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <h2>📈 Звіти продажів</h2>
    <div class="card">
      <h3>По точці за період</h3>
      ${await locationPickerHtml("reportLoc")}
      <div class="grid2">
        <input id="reportStart" type="date">
        <input id="reportEnd" type="date">
      </div>
      <div class="grid2">
        <button class="btn" id="reportLocBtn">Показати</button>
        <button class="btn secondary" id="reportLocXlsxBtn">📊 Excel</button>
      </div>
    </div>
    <div class="card">
      <button class="btn secondary" id="reportAllTodayBtn">📊 Продажі сьогодні (всі точки)</button>
      <button class="btn secondary" id="reportAllXlsxBtn">📊 Excel за період (всі точки)</button>
    </div>
    <div id="reportResult"></div>`;
  document.getElementById("reportLocBtn").addEventListener("click", async () => {
    const loc = document.getElementById("reportLoc").value;
    const start = document.getElementById("reportStart").value;
    const end = document.getElementById("reportEnd").value;
    try {
      const rows = await api(`/sales/report?location=${encodeURIComponent(loc)}&start=${start}&end=${end}`);
      renderSalesRows(rows);
    } catch (err) { toast(err.message, "error"); }
  });
  document.getElementById("reportLocXlsxBtn").addEventListener("click", () => {
    const loc = document.getElementById("reportLoc").value;
    const start = document.getElementById("reportStart").value;
    const end = document.getElementById("reportEnd").value;
    downloadFile(`/sales/report.xlsx?location=${encodeURIComponent(loc)}&start=${start}&end=${end}`);
  });
  document.getElementById("reportAllTodayBtn").addEventListener("click", async () => {
    const today = new Date().toISOString().slice(0, 10);
    try {
      const rows = await api(`/sales/report-all?start=${today}&end=${today}`);
      renderSalesRows(rows);
    } catch (err) { toast(err.message, "error"); }
  });
  document.getElementById("reportAllXlsxBtn").addEventListener("click", () => {
    const start = document.getElementById("reportStart").value;
    const end = document.getElementById("reportEnd").value;
    downloadFile(`/sales/report-all.xlsx?start=${start}&end=${end}`);
  });
}

async function downloadFile(path) {
  try {
    const resp = await fetch(API + path, { headers: authHeaders() });
    if (!resp.ok) { toast("Помилка завантаження файлу", "error"); return; }
    const blob = await resp.blob();
    const disposition = resp.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : "звіт.xlsx";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) { toast("Помилка завантаження файлу", "error"); }
}

function renderSalesRows(rows) {
  const el = document.getElementById("reportResult");
  if (!rows.length) { el.innerHTML = "<p>Продажів не знайдено.</p>"; return; }
  const total = rows.reduce((s, r) => s + r.price, 0);
  el.innerHTML = `<p><b>Знайдено: ${rows.length} на суму ${total.toFixed(2)} грн</b></p>` +
    rows.map(r => `<div class="item-row"><div class="info"><div class="name">${r.name || "?"} — ${r.color}</div><div class="meta">${r.location} | ${r.sold_at} | ${r.price} грн | ${r.payment_method}</div></div></div>`).join("");
}

// ----------------------------------------------------------------------------
// АДМІН: ремонти (перегляд по точці)
// ----------------------------------------------------------------------------
async function renderRepairsAdminView() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <h2>🔧 Ремонти</h2>
    <div class="card">
      ${await locationPickerHtml("repAdminLoc")}
      <button class="btn secondary" id="repPendingBtn">📋 Ще не видані</button>
      <div class="grid2">
        <input id="repStart" type="date">
        <input id="repEnd" type="date">
      </div>
      <button class="btn" id="repPeriodBtn">📊 Звіт за період</button>
      <button class="btn secondary" id="repXlsxBtn">📊 Excel</button>
    </div>
    <div id="repairAdminResult"></div>`;
  document.getElementById("repXlsxBtn").addEventListener("click", () => {
    const loc = document.getElementById("repAdminLoc").value;
    const start = document.getElementById("repStart").value;
    const end = document.getElementById("repEnd").value;
    downloadFile(`/repairs/report.xlsx?location=${encodeURIComponent(loc)}&start=${start}&end=${end}`);
  });
  document.getElementById("repPendingBtn").addEventListener("click", async () => {
    const loc = document.getElementById("repAdminLoc").value;
    const pending = await api(`/repairs/pending?location=${encodeURIComponent(loc)}`);
    renderRepairRows(pending, true);
  });
  document.getElementById("repPeriodBtn").addEventListener("click", async () => {
    const loc = document.getElementById("repAdminLoc").value;
    const start = document.getElementById("repStart").value;
    const end = document.getElementById("repEnd").value;
    const rows = await api(`/repairs/report?location=${encodeURIComponent(loc)}&start=${start}&end=${end}`);
    renderRepairRows(rows, false);
  });
}

function renderRepairRows(rows, pendingOnly) {
  const el = document.getElementById("repairAdminResult");
  if (!rows.length) { el.innerHTML = "<p>Нічого не знайдено.</p>"; return; }
  el.innerHTML = rows.map(r => {
    const status = pendingOnly
      ? `<span class="badge pending">в ремонті</span>`
      : (r.completion_date ? `<span class="badge done">видано ${r.completion_date}</span>` : `<span class="badge pending">в ремонті</span>`);
    return `<div class="item-row"><div class="info"><div class="name">№${r.receipt_number}</div><div class="meta">Прийнято: ${r.intake_date}</div></div>${status}</div>`;
  }).join("");
}

// ----------------------------------------------------------------------------
// АДМІН: запити на зміну залишку (потребують підтвердження)
// ----------------------------------------------------------------------------
async function renderRequestsView() {
  const view = document.getElementById("view");
  view.innerHTML = `<h2>🔔 Запити на зміну залишку</h2><div id="requestsList">Завантаження...</div>`;
  await loadRequestsList();
}

async function loadRequestsList() {
  const listEl = document.getElementById("requestsList");
  try {
    const rows = await api("/qty-requests/pending");
    listEl.innerHTML = rows.length ? rows.map(r => `
      <div class="card">
        <div class="name">${r.name || "?"} — ${r.color} (${r.location})</div>
        <div class="meta">${r.old_quantity} → ${r.new_quantity}${r.reason ? " | Причина: " + r.reason : ""}</div>
        <div class="grid2" style="margin-top:8px;">
          <button class="btn" onclick="decideRequest(${r.id}, true)">✅ Підтвердити</button>
          <button class="btn danger" onclick="decideRequest(${r.id}, false)">❌ Відхилити</button>
        </div>
      </div>`).join("") : "<p>Немає запитів, що очікують підтвердження.</p>";
  } catch (err) { toast(err.message, "error"); }
}

window.decideRequest = async (id, approve) => {
  try {
    await api(`/qty-requests/${id}/${approve ? "approve" : "reject"}`, { method: "POST" });
    toast(approve ? "✅ Підтверджено" : "Відхилено");
    loadRequestsList();
  } catch (err) { toast(err.message, "error"); }
};

// ----------------------------------------------------------------------------
// ПРОДАВЕЦЬ: переоблік
// ----------------------------------------------------------------------------
async function renderRecountView() {
  const view = document.getElementById("view");
  view.innerHTML = `<h2>🔄 Переоблік точки</h2><p style="color:#93a3b8;font-size:13px;margin-bottom:10px;">Введіть фактично пораховану кількість для кожного товару.</p><div id="recountList">Завантаження...</div><button class="btn" id="recountSubmit" style="margin-top:10px;">Зберегти переоблік</button>`;
  const items = await api("/recount/items");
  document.getElementById("recountList").innerHTML = items.map(it => `
    <div class="item-row">
      <div class="info"><div class="name">${it.name || "?"} — ${it.color}</div><div class="meta">В базі: ${it.quantity}</div></div>
      <input type="number" style="width:70px;margin:0;" value="${it.quantity}" data-item-id="${it.id}" class="recount-input">
    </div>`).join("");
  document.getElementById("recountSubmit").addEventListener("click", async () => {
    const inputs = document.querySelectorAll(".recount-input");
    const payload = Array.from(inputs).map(inp => ({ id: parseInt(inp.dataset.itemId, 10), quantity: parseInt(inp.value, 10) }));
    try {
      const res = await api("/recount/apply", { method: "POST", json: { items: payload } });
      if (res.changes.length) {
        toast(`✅ Оновлено ${res.changes.length} товар(ів)`);
      } else {
        toast("Змін не знайдено — все збігається");
      }
      renderRecountView();
    } catch (err) { toast(err.message, "error"); }
  });
}

// ----------------------------------------------------------------------------
// Web Push - підписка на сповіщення (для адмінів)
// ----------------------------------------------------------------------------
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

async function setupPushNotifications() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
  if (ME.role === "seller") return; // сповіщення потрібні лише адмінам
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return;
    const reg = await navigator.serviceWorker.ready;
    const { publicKey } = await api("/push/public-key");
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }
    const json = sub.toJSON();
    await api("/push/subscribe", { method: "POST", json: { endpoint: json.endpoint, keys: json.keys } });
  } catch (err) {
    console.warn("Push-підписка не вдалась:", err);
  }
}

// ----------------------------------------------------------------------------
// Точка входу
// ----------------------------------------------------------------------------
document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await doLogin(document.getElementById("loginUsername").value, document.getElementById("loginPassword").value);
  } catch (err) {
    toast(err.message, "error");
  }
});

(async function init() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  }
  const restored = await tryRestoreSession();
  if (restored) { showMainApp(); setupPushNotifications(); } else showLoginScreen();
})();
