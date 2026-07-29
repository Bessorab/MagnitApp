// ============================================================================
// MagnitApp - основна логіка фронтенду (без фреймворків, звичайний JS)
// ============================================================================

const API = "/api";
let TOKEN = localStorage.getItem("magnit_token") || null;

// Фото тепер віддаються лише авторизованим (див. backend: /api/photos/... вимагає
// login_required). Тег <img> не може надіслати заголовок Authorization, тому
// передаємо токен як query-параметр, який login_required теж приймає.
function authedPhotoUrl(url) {
  if (!url || !TOKEN) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(TOKEN)}`;
}
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
function getDeviceId() {
  let id = localStorage.getItem("magnit_device_id");
  if (!id) {
    id = "dev-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    localStorage.setItem("magnit_device_id", id);
  }
  return id;
}

async function doLogin(username, password) {
  const data = await api("/login", {
    method: "POST",
    json: { username, password, device_id: getDeviceId(), device_info: navigator.userAgent },
  });
  if (data.status === "pending") {
    showLoginPendingMessage();
    return;
  }
  TOKEN = data.token;
  localStorage.setItem("magnit_token", TOKEN);
  ME = data;
  showMainApp();
  setupPushNotifications(false);
  const pendingShare = sessionStorage.getItem("magnit_pending_share");
  if (pendingShare && ME.role === "seller") {
    sessionStorage.removeItem("magnit_pending_share");
    const shared = JSON.parse(pendingShare);
    openQuickPartModal(shared.link, shared.note);
  }
}

function showLoginPendingMessage() {
  const screen = document.getElementById("loginScreen");
  screen.innerHTML = `
    <h1>🔧 MagnitApp</h1>
    <div class="card">
      <p>🔐 Це новий пристрій для вашого акаунту. Надіслано запит головному адміну на підтвердження.</p>
      <p style="color:#93a3b8;font-size:13px;">Зачекайте, поки адмін підтвердить, потім натисніть «Спробувати ще раз».</p>
      <button class="btn" id="retryLoginBtn" style="margin-top:10px;">Спробувати ще раз</button>
    </div>`;
  document.getElementById("retryLoginBtn").addEventListener("click", () => location.reload());
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
  { id: "repairs", label: "🔧 Ремонти" },
  { id: "parts", label: "🔗 Запчастини" },
];
const ADMIN_TABS = [
  { id: "sellers", label: "👥 Продавці" },
  { id: "requests", label: "🔔 Запити" },
  { id: "locations", label: "🏷️ Точки" },
  { id: "repairs_admin", label: "🔧 Ремонти" },
];

let currentTab = null;

function tabsForRole() {
  if (ME.role === "parts_admin") return [{ id: "requests", label: "🔔 Запити" }];
  const tabs = ME.role === "seller" ? SELLER_TABS.slice() : ADMIN_TABS.slice();
  if (ME.role === "head_admin") tabs.push({ id: "admins", label: "👑 Адміни" });
  return tabs;
}

function renderBottomNav() {
  const nav = document.getElementById("bottomNav");
  const tabs = tabsForRole();
  nav.innerHTML = tabs.map(t =>
    `<button data-tab="${t.id}" class="${t.id === currentTab ? "active" : ""}">${t.label}</button>`
  ).join("") + `<button data-tab="__logout" class="logout-btn">🚪 Вихід</button>`;
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
    repairs: renderRepairsSellerView,
    sellers: renderSellersView, locations: renderLocationsView,
    repairs_admin: renderRepairsAdminView, admins: renderAdminsView,
    requests: renderRequestsView, parts: renderPartsView,
  };
  (renderers[tabId] || renderRepairsSellerView)();
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
  const pwdBtn = document.createElement("span");
  pwdBtn.innerHTML = ` <button class="btn small secondary" onclick="openChangePasswordModal()" style="margin-left:6px;">🔑 Змінити пароль</button>`;
  document.getElementById("meLabel").appendChild(pwdBtn);
  if (ME.role !== "seller") {
    const testBtn = document.createElement("span");
    testBtn.innerHTML = ` <button class="btn small secondary" onclick="sendTestPush()" style="margin-left:6px;">🔔 Тест сповіщення</button>`;
    document.getElementById("meLabel").appendChild(testBtn);
  }
  document.getElementById("partsFab").classList.toggle("hidden", ME.role !== "seller");
  const defaultTab = ME.role === "seller" ? "repairs" : (ME.role === "parts_admin" ? "requests" : "sellers");
  goTab(defaultTab);
}

function roleLabel(role) {
  return { seller: "продавець", admin: "адмін", head_admin: "головний адмін", parts_admin: "адмін запчастин" }[role] || role;
}

window.openChangePasswordModal = () => {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-sheet">
      <h3>🔑 Змінити пароль</h3>
      <p style="color:#93a3b8;font-size:13px;">Після зміни відповідальність за збереження нового пароля - ваша.</p>
      <label>Поточний пароль</label><input id="oldPassword" type="password">
      <label>Новий пароль (мінімум 6 символів)</label><input id="newPassword" type="password">
      <label>Повторіть новий пароль</label><input id="newPassword2" type="password">
      <div class="grid2">
        <button class="btn secondary" id="pwdCancel">Скасувати</button>
        <button class="btn" id="pwdSave">Змінити</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  document.getElementById("pwdCancel").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.getElementById("pwdSave").addEventListener("click", async () => {
    const oldPassword = document.getElementById("oldPassword").value;
    const newPassword = document.getElementById("newPassword").value;
    const newPassword2 = document.getElementById("newPassword2").value;
    if (newPassword !== newPassword2) { toast("Паролі не збігаються", "error"); return; }
    if (newPassword.length < 6) { toast("Новий пароль має містити щонайменше 6 символів", "error"); return; }
    try {
      await api("/me/change-password", { method: "POST", json: { old_password: oldPassword, new_password: newPassword } });
      toast("✅ Пароль змінено");
      close();
    } catch (err) { toast(err.message, "error"); }
  });
};

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

async function getLocations() {
  return api("/locations");
}

function locationSelectHtml(selectedId, locations) {
  return `<select id="${selectedId}">` +
    locations.map(l => `<option value="${l}">${l}</option>`).join("") +
    `</select>`;
}

const NO_LOCATIONS_HTML = `<div class="card"><p>⚠️ Ще немає жодної зареєстрованої точки. Спершу додайте продавця з точкою у вкладці «👥 Продавці».</p></div>`;

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
      await offerLinkUnlinkedParts(res.id, area);
    } catch (err) { toast(err.message, "error"); }
  });
}

async function offerLinkUnlinkedParts(repairId, area) {
  let unlinked = [];
  try { unlinked = await api("/part-requests/unlinked"); } catch (err) { /* ігноруємо */ }
  if (!unlinked.length) { renderRepairsSellerView(); return; }
  area.innerHTML = `
    <div class="card">
      <h3>🔩 Прив'язати раніше надіслані запчастини?</h3>
      <p style="color:#93a3b8;font-size:13px;">Оберіть, які з ваших уже надісланих запчастин стосуються цієї квитанції.</p>
      ${unlinked.map(p => `
        <label style="display:flex;align-items:center;gap:8px;font-size:14px;margin-bottom:8px;">
          <input type="checkbox" class="unlinked-part-cb" value="${p.id}" style="width:auto;margin:0;">
          <span>${p.note || p.link}</span>
        </label>`).join("")}
      <button class="btn" id="linkPartsBtn" style="margin-top:8px;">Прив'язати обрані</button>
      <button class="btn secondary" id="skipLinkBtn">Пропустити</button>
    </div>`;
  document.getElementById("skipLinkBtn").addEventListener("click", renderRepairsSellerView);
  document.getElementById("linkPartsBtn").addEventListener("click", async () => {
    const checked = Array.from(document.querySelectorAll(".unlinked-part-cb:checked")).map(cb => cb.value);
    try {
      await Promise.all(checked.map(id => api(`/part-requests/${id}/link-repair`, { method: "POST", json: { repair_id: repairId } })));
      toast(`✅ Прив'язано ${checked.length} запчастин(и)`);
    } catch (err) { toast(err.message, "error"); }
    renderRepairsSellerView();
  });
}

async function renderRepairIssueList() {
  const area = document.getElementById("repairArea");
  area.innerHTML = "<p>Завантаження...</p>";
  try {
    const pending = await api("/repairs/pending");
    if (!pending.length) { area.innerHTML = "<p>Немає квитанцій в очікуванні видачі.</p>"; return; }
    area.innerHTML = pending.map(r => {
      const partsLine = (r.parts && r.parts.length)
        ? `<div class="meta">🔩 ` + r.parts.map(p =>
            `<a href="${p.link}" style="color:#5b9bd5;">${p.note || p.link}</a>${p.status === "done" ? " ✅" : " ⏳"}`
          ).join(", ") + `</div>`
        : "";
      const photoImg = r.photo_url ? `<img src="${authedPhotoUrl(r.photo_url)}" style="width:56px;height:56px;object-fit:cover;border-radius:8px;margin-right:10px;">` : "";
      return `<div class="item-row">
         ${photoImg}
         <div class="info" style="cursor:pointer;" onclick="issueRepair(${r.id})">
           <div class="name">№${r.receipt_number}</div><div class="meta">Прийнято: ${r.intake_date} - торкніться, щоб видати</div>
           ${partsLine}
         </div>
         <button class="btn small danger" onclick="deleteRepair(${r.id})">🗑️</button>
       </div>`;
    }).join("");
  } catch (err) { toast(err.message, "error"); }
}

window.issueRepair = async (repairId) => {
  const date = prompt("Дата видачі (РІК-МІСЯЦЬ-ДЕНЬ):", new Date().toISOString().slice(0, 10));
  if (date === null) return;

  const cost = prompt("Сума ремонту (грн):", "");
  if (cost === null) return;
  if (cost.trim() && isNaN(parseFloat(cost))) { toast("Сума має бути числом", "error"); return; }

  let paymentMethod = prompt("Спосіб оплати - введіть «готівка» або «безготівка»:", "готівка");
  if (paymentMethod === null) return;
  paymentMethod = paymentMethod.trim().toLowerCase().startsWith("безгот") ? "Безготівка" : "Готівка";

  try {
    await api(`/repairs/${repairId}/complete`, {
      method: "POST",
      json: { completion_date: date, cost: cost.trim() ? parseFloat(cost) : null, payment_method: paymentMethod },
    });
    toast("✅ Видано клієнту");
    const sellerArea = document.getElementById("repairArea");
    const adminPendingBtn = document.getElementById("repPendingBtn");
    if (sellerArea) renderRepairIssueList();
    else if (adminPendingBtn) adminPendingBtn.click();
  } catch (err) { toast(err.message, "error"); }
};

window.deleteRepair = async (repairId) => {
  if (!confirm("Надіслати адміну запит на видалення цієї квитанції?")) return;
  try {
    const res = await api(`/repairs/${repairId}`, { method: "DELETE" });
    toast(res.direct ? "🗑️ Квитанцію видалено" : "📨 Запит на видалення надіслано адміну");
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
    <div class="card" id="vapidStatusCard">Перевірка стабільності push-сповіщень...</div>
    <div class="card">
      <h3>⏱️ Термін зберігання запчастин</h3>
      <p style="color:#93a3b8;font-size:13px;">Оброблені посилання на запчастини старші за вказану кількість днів видалятимуться автоматично. Залиште порожнім, щоб не видаляти взагалі.</p>
      <label>Днів зберігати після обробки</label><input id="partRetentionDays" type="number" placeholder="наприклад 30">
      <button class="btn secondary" id="saveRetentionBtn">Зберегти</button>
    </div>
    <div class="card">
      <label>Логін</label><input id="newAdminLogin">
      <label>Пароль</label><input id="newAdminPass" type="password">
      <label>Роль</label>
      <select id="newAdminRole">
        <option value="admin">Повний адмін</option>
        <option value="parts_admin">Адмін запчастин (лише запити)</option>
      </select>
      <button class="btn" id="addAdminBtn">➕ Додати адміна</button>
    </div>
    <div id="adminsList">Завантаження...</div>`;

  try {
    const status = await api("/push/public-key");
    const cardEl = document.getElementById("vapidStatusCard");
    if (status.usingEnvKeys) {
      cardEl.innerHTML = `<p>✅ Ключі push-сповіщень закріплені надійно - сповіщення не зникнуть після перезапуску сервера.</p>`;
    } else {
      cardEl.innerHTML = `
        <h3>⚠️ Push-сповіщення можуть перестати працювати</h3>
        <p style="color:#93a3b8;font-size:13px;">Ключі сповіщень зараз зберігаються тимчасово. Якщо сервер перезапуститься (наприклад, на Render) - усі підписки на сповіщення перестануть працювати непомітно. Щоб цього уникнути, закріпіть ключі один раз:</p>
        <button class="btn secondary" id="genVapidBtn">Згенерувати ключі для Render</button>
        <div id="vapidKeysResult" style="margin-top:8px;"></div>`;
      document.getElementById("genVapidBtn").addEventListener("click", async () => {
        try {
          const keys = await api("/settings/generate-vapid-keys", { method: "POST" });
          document.getElementById("vapidKeysResult").innerHTML = `
            <p style="font-size:12px;color:#93a3b8;">Додайте ці дві змінні середовища в Render (Environment) і передеплойте:</p>
            <input readonly value="VAPID_PRIVATE_KEY=${keys.VAPID_PRIVATE_KEY}" onclick="this.select()">
            <input readonly value="VAPID_PUBLIC_KEY=${keys.VAPID_PUBLIC_KEY}" onclick="this.select()">`;
        } catch (err) { toast(err.message, "error"); }
      });
    }
  } catch (err) { document.getElementById("vapidStatusCard").innerHTML = ""; }

  try {
    const current = await api("/settings/part-retention");
    if (current.days) document.getElementById("partRetentionDays").value = current.days;
  } catch (err) { /* ігноруємо, поле лишиться порожнім */ }
  document.getElementById("saveRetentionBtn").addEventListener("click", async () => {
    const days = document.getElementById("partRetentionDays").value.trim();
    try {
      await api("/settings/part-retention", { method: "POST", json: { days: days || null } });
      toast("✅ Збережено");
    } catch (err) { toast(err.message, "error"); }
  });

  document.getElementById("addAdminBtn").addEventListener("click", async () => {
    try {
      await api("/admins", {
        method: "POST",
        json: {
          username: document.getElementById("newAdminLogin").value,
          password: document.getElementById("newAdminPass").value,
          role: document.getElementById("newAdminRole").value,
        },
      });
      toast("✅ Адміна додано");
      renderAdminsView();
    } catch (err) { toast(err.message, "error"); }
  });
  const admins = await api("/admins");
  document.getElementById("adminsList").innerHTML = admins.map(a =>
    `<div class="item-row"><div class="info"><div class="name">${a.username}</div><div class="meta">${roleLabel(a.role)}</div></div>
     <button class="btn small danger" onclick="removeAdmin(${a.id})">Видалити</button></div>`
  ).join("") || "<p>Немає доданих адмінів.</p>";
}

window.removeAdmin = async (id) => {
  if (!confirm("Видалити цього адміна?")) return;
  try { await api(`/admins/${id}`, { method: "DELETE" }); toast("Видалено"); renderAdminsView(); }
  catch (err) { toast(err.message, "error"); }
};

// ----------------------------------------------------------------------------
// АДМІН: точки (перейменувати / очистити)
// ----------------------------------------------------------------------------
async function renderLocationsView() {
  const view = document.getElementById("view");
  const locations = await getLocations();
  if (!locations.length) { view.innerHTML = `<h2>🏷️ Торгові точки</h2>` + NO_LOCATIONS_HTML; return; }
  view.innerHTML = `
    <h2>🏷️ Торгові точки</h2>
    <div class="card">
      <h3>Перейменувати</h3>
      <label>Стара назва</label>${locationSelectHtml("renameOld", locations)}
      <label>Нова назва</label><input id="renameNew">
      <button class="btn" id="renameBtn">Перейменувати</button>
    </div>`;
  document.getElementById("renameBtn").addEventListener("click", async () => {
    try {
      const res = await api("/locations/rename", { method: "POST", json: { old_location: document.getElementById("renameOld").value, new_location: document.getElementById("renameNew").value } });
      toast(`✅ Перейменовано (продавців: ${res.users_updated}, ремонтів: ${res.repairs_updated})`);
      renderLocationsView();
    } catch (err) { toast(err.message, "error"); }
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

// ----------------------------------------------------------------------------
// АДМІН: ремонти (перегляд по точці)
// ----------------------------------------------------------------------------
async function renderRepairsAdminView() {
  const view = document.getElementById("view");
  const locations = await getLocations();
  if (!locations.length) { view.innerHTML = `<h2>🔧 Ремонти</h2>` + NO_LOCATIONS_HTML; return; }
  view.innerHTML = `
    <h2>🔧 Ремонти</h2>
    <div class="card">
      ${locationSelectHtml("repAdminLoc", locations)}
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
    const isPending = pendingOnly || !r.completion_date;
    const status = r.completion_date
      ? `<span class="badge done">видано ${r.completion_date}</span>`
      : `<span class="badge pending">в ремонті</span>`;
    const costLine = (!isPending && r.cost != null)
      ? `<div class="meta">Сума: ${r.cost} грн | ${r.payment_method || "?"}</div>` : "";
    const partsLine = (r.parts && r.parts.length)
      ? `<div class="meta">🔩 Запчастини: ` + r.parts.map(p =>
          `<a href="${p.link}" style="color:#5b9bd5;">${p.note || p.link}</a>${p.status === "done" ? " ✅" : " ⏳"}`
        ).join(", ") + `</div>`
      : "";
    const actions = isPending
      ? `<div class="grid2" style="margin-top:6px;">
           <button class="btn small" onclick="issueRepair(${r.id})">✅ Видати</button>
           <button class="btn small danger" onclick="deleteRepairAdmin(${r.id})">🗑️ Видалити</button>
         </div>`
      : "";
    const photoImg = r.photo_url ? `<img src="${authedPhotoUrl(r.photo_url)}" style="width:56px;height:56px;object-fit:cover;border-radius:8px;margin-right:10px;">` : "";
    return `<div class="card"><div class="item-row">${photoImg}<div class="info"><div class="name">№${r.receipt_number}</div><div class="meta">Прийнято: ${r.intake_date}</div>${costLine}${partsLine}</div>${status}</div>${actions}</div>`;
  }).join("");
}

window.deleteRepairAdmin = async (repairId) => {
  if (!confirm("Видалити цю квитанцію з обліку? Дію не можна скасувати.")) return;
  const loc = document.getElementById("repAdminLoc").value;
  try {
    await api(`/repairs/${repairId}?location=${encodeURIComponent(loc)}`, { method: "DELETE" });
    toast("🗑️ Квитанцію видалено");
    document.getElementById("repPendingBtn").click();
  } catch (err) { toast(err.message, "error"); }
};

// ----------------------------------------------------------------------------
// АДМІН: запити на зміну залишку (потребують підтвердження)
// ----------------------------------------------------------------------------
async function renderRequestsView() {
  const view = document.getElementById("view");
  view.innerHTML = `<h2>🔔 Запити</h2><div id="requestsList">Завантаження...</div>`;
  await loadRequestsList();
}

async function loadRequestsList() {
  const listEl = document.getElementById("requestsList");
  try {
    const canSeeFullHistory = ME.role === "admin" || ME.role === "head_admin" || ME.role === "parts_admin";
    const canEditDelete = ME.role === "admin" || ME.role === "head_admin";
    const isHeadAdmin = ME.role === "head_admin";
    const [repairRows, partRows, loginRows] = await Promise.all([
      api("/repair-delete-requests/pending"),
      canSeeFullHistory ? api("/part-requests/all") : api("/part-requests/pending"),
      isHeadAdmin ? api("/login-requests/pending") : Promise.resolve([]),
    ]);
    let html = "";
    if (repairRows.length) {
      html += `<h3>🗑️ Видалення квитанцій ремонту</h3>` + repairRows.map(r => `
        <div class="card">
          <div class="name">№${r.receipt_number} (${r.location})</div>
          <div class="grid2" style="margin-top:8px;">
            <button class="btn" onclick="decideRepairDeleteRequest(${r.id}, true)">✅ Підтвердити</button>
            <button class="btn danger" onclick="decideRepairDeleteRequest(${r.id}, false)">❌ Відхилити</button>
          </div>
        </div>`).join("");
    }
    if (partRows.length) {
      const title = canSeeFullHistory ? "🔩 Запчастини (уся історія, всі точки)" : "🔩 Запчастини на замовлення";
      html += `<h3>${title}</h3>` + partRows.map(r => {
        const status = canSeeFullHistory
          ? (r.status === "done" ? `<span class="badge done">оброблено</span>` : `<span class="badge pending">очікує</span>`)
          : "";
        const repairLine = r.receipt_number ? `<div class="meta">Для квитанції №${r.receipt_number}</div>` : "";
        const whenLine = canSeeFullHistory ? `<div class="meta">${r.location} | ${r.requested_at}</div>` : "";
        let actions = "";
        if (r.status !== "done") {
          actions += `<button class="btn" style="margin-top:8px;" onclick="markPartRequestDone(${r.id})">✅ Замовлено / оброблено</button>`;
        }
        if (canEditDelete) {
          actions += `<div class="grid2" style="margin-top:8px;">
               <button class="btn small" onclick="editPartRequest(${r.id}, '${(r.link || "").replace(/'/g, "&#39;")}', '${(r.note || "").replace(/'/g, "&#39;")}', ${r.repair_id || "null"})">✏️ Редагувати</button>
               <button class="btn small danger" onclick="deletePartRequest(${r.id})">🗑️ Видалити</button>
             </div>`;
        }
        return `<div class="card">
          <div class="name">${r.location}${r.note ? " — " + r.note : ""} ${status}</div>
          <div class="meta"><a href="${r.link}" style="color:#5b9bd5;">${r.link}</a></div>
          ${repairLine}
          ${whenLine}
          ${actions}
        </div>`;
      }).join("");
    }
    if (loginRows.length) {
      html += `<h3>🔐 Нові пристрої продавців</h3>` + loginRows.map(r => `
        <div class="card">
          <div class="name">${r.username}</div>
          <div class="meta">${r.device_info || "невідомий пристрій"} | ${r.created_at}</div>
          <div class="grid2" style="margin-top:8px;">
            <button class="btn" onclick="decideLoginRequest(${r.id}, true)">✅ Підтвердити</button>
            <button class="btn danger" onclick="decideLoginRequest(${r.id}, false)">❌ Відхилити</button>
          </div>
        </div>`).join("");
    }
    listEl.innerHTML = html || "<p>Немає запитів, що очікують підтвердження.</p>";
  } catch (err) { toast(err.message, "error"); }
}

window.markPartRequestDone = async (id) => {
  try {
    await api(`/part-requests/${id}/done`, { method: "POST" });
    toast("✅ Позначено як оброблено");
    loadRequestsList();
  } catch (err) { toast(err.message, "error"); }
};

window.editPartRequest = async (id, currentLink, currentNote, currentRepairId) => {
  let repairOptions = `<option value="">— не вказано —</option>`;
  try {
    const pending = await api("/repairs/pending");
    repairOptions += pending.map(r =>
      `<option value="${r.id}" ${r.id === currentRepairId ? "selected" : ""}>№${r.receipt_number} (прийнято ${r.intake_date})</option>`
    ).join("");
  } catch (err) { /* без вибору, якщо не вдалось завантажити */ }

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.id = "editPartOverlay";
  overlay.innerHTML = `
    <div class="modal-sheet">
      <h3>✏️ Редагувати запчастину</h3>
      <label>Посилання</label><input id="editPartLink" value="${currentLink.replace(/"/g, "&quot;")}">
      <label>Коментар</label><input id="editPartNote" value="${currentNote.replace(/"/g, "&quot;")}">
      <label>Для якої квитанції в ремонті</label>
      <select id="editPartRepair">${repairOptions}</select>
      <div class="grid2">
        <button class="btn secondary" id="editPartCancel">Скасувати</button>
        <button class="btn" id="editPartSave">Зберегти</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  document.getElementById("editPartCancel").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.getElementById("editPartSave").addEventListener("click", async () => {
    const link = document.getElementById("editPartLink").value.trim();
    const note = document.getElementById("editPartNote").value.trim();
    const repairId = document.getElementById("editPartRepair").value || null;
    if (!link) { toast("Посилання не може бути порожнім", "error"); return; }
    try {
      await api(`/part-requests/${id}`, { method: "PATCH", json: { link, note, repair_id: repairId } });
      toast("✅ Збережено");
      close();
      loadRequestsList();
    } catch (err) { toast(err.message, "error"); }
  });
};

window.deletePartRequest = (id) => {
  showConfirmModal("Видалити цей запис про запчастину? Дію не можна скасувати.", async () => {
    try {
      await api(`/part-requests/${id}`, { method: "DELETE" });
      toast("🗑️ Видалено");
      loadRequestsList();
    } catch (err) { toast(err.message, "error"); }
  });
};

function showConfirmModal(message, onConfirm) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-sheet">
      <p>${message}</p>
      <div class="grid2">
        <button class="btn secondary" id="confirmCancelBtn">Скасувати</button>
        <button class="btn danger" id="confirmOkBtn">Так, видалити</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  document.getElementById("confirmCancelBtn").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.getElementById("confirmOkBtn").addEventListener("click", async () => {
    close();
    await onConfirm();
  });
}

window.decideRepairDeleteRequest = async (id, approve) => {
  try {
    await api(`/repair-delete-requests/${id}/${approve ? "approve" : "reject"}`, { method: "POST" });
    toast(approve ? "✅ Квитанцію видалено" : "Відхилено");
    loadRequestsList();
  } catch (err) { toast(err.message, "error"); }
};

window.decideLoginRequest = async (id, approve) => {
  try {
    await api(`/login-requests/${id}/${approve ? "approve" : "reject"}`, { method: "POST" });
    toast(approve ? "✅ Пристрій підтверджено" : "Відхилено");
    loadRequestsList();
  } catch (err) { toast(err.message, "error"); }
};



// ----------------------------------------------------------------------------
// ПРОДАВЕЦЬ: швидкий перехід на сайти замовлення запчастин
// ----------------------------------------------------------------------------
const PARTS_SITES = [
  { name: "FixUp.ua", url: "https://fixup.ua/uk" },
  { name: "Welcome Mobi", url: "https://welcome-mobi.com.ua/" },
  { name: "ArtMobile", url: "https://artmobile.ua/" },
  { name: "M112", url: "https://m112.com.ua/" },
  { name: "GSM Server", url: "https://gsmserver.com.ua/uk/" },
];

window.openQuickPartModal = async (prefillLink, prefillNote) => {
  let repairOptions = `<option value="">— не вказано —</option>`;
  try {
    const pending = await api("/repairs/pending");
    repairOptions += pending.map(r => `<option value="${r.id}">№${r.receipt_number} (прийнято ${r.intake_date})</option>`).join("");
  } catch (err) { /* якщо не вдалось завантажити - просто без вибору */ }

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.id = "quickPartOverlay";
  overlay.innerHTML = `
    <div class="modal-sheet">
      <h3>🔩 Надіслати посилання на запчастину</h3>
      <p style="color:#93a3b8;font-size:13px;">Вставте скопійоване посилання на потрібну деталь.</p>
      <label>Посилання</label><input id="quickPartLink" placeholder="https://..." value="${(prefillLink || "").replace(/"/g, "&quot;")}">
      <label>Коментар (необов'язково)</label><input id="quickPartNote" placeholder="Наприклад: екран для iPhone 12" value="${(prefillNote || "").replace(/"/g, "&quot;")}">
      <label>Для якої квитанції в ремонті (необов'язково)</label>
      <select id="quickPartRepair">${repairOptions}</select>
      <div class="grid2">
        <button class="btn secondary" id="quickPartCancel">Скасувати</button>
        <button class="btn" id="quickPartSend">Надіслати адміну</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("quickPartLink").focus();
  document.getElementById("quickPartCancel").addEventListener("click", closeQuickPartModal);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeQuickPartModal(); });
  document.getElementById("quickPartSend").addEventListener("click", async () => {
    const link = document.getElementById("quickPartLink").value.trim();
    const note = document.getElementById("quickPartNote").value.trim();
    const repairId = document.getElementById("quickPartRepair").value || null;
    if (!link) { toast("Вставте посилання на запчастину", "error"); return; }
    try {
      await api("/part-requests", { method: "POST", json: { link, note, repair_id: repairId } });
      toast("📨 Надіслано адміну");
      closeQuickPartModal();
    } catch (err) { toast(err.message, "error"); }
  });
};

function closeQuickPartModal() {
  const overlay = document.getElementById("quickPartOverlay");
  if (overlay) overlay.remove();
}

async function renderPartsView() {
  const view = document.getElementById("view");
  view.innerHTML = `<h2>🔗 Замовлення запчастин</h2>
    <p style="color:#93a3b8;font-size:13px;margin-bottom:10px;">Знайшли потрібну деталь на сайті? Натисніть «Поділитися» в браузері й оберіть MagnitApp — посилання одразу підставиться в форму, без потреби повертатись вручну.</p>` +
    PARTS_SITES.map(s =>
      `<a href="${s.url}" class="btn secondary" style="text-decoration:none;display:block;">${s.name} ↗</a>`
    ).join("") + `
    <button class="btn" style="margin-top:14px;" onclick="openQuickPartModal()">🔩 Надіслати посилання на запчастину адміну</button>
    <h3 style="margin-top:16px;">📜 Мої надіслані запчастини</h3>
    <div id="myPartsList">Завантаження...</div>`;

  try {
    const mine = await api("/part-requests/mine");
    document.getElementById("myPartsList").innerHTML = mine.length ? mine.map(r => {
      const status = r.status === "done" ? `<span class="badge done">оброблено</span>` : `<span class="badge pending">очікує</span>`;
      const repairLine = r.receipt_number ? `<div class="meta">Для квитанції №${r.receipt_number}</div>` : "";
      const linkBtn = !r.repair_id
        ? `<button class="btn small secondary" style="margin-top:8px;" onclick="openLinkRepairModal(${r.id})">🔗 Прив'язати до квитанції</button>`
        : "";
      return `<div class="card">
        <div class="name">${r.note || "(без коментаря)"} ${status}</div>
        <div class="meta"><a href="${r.link}" style="color:#5b9bd5;">${r.link}</a></div>
        ${repairLine}
        <div class="grid2" style="margin-top:8px;">
          ${linkBtn}
          <button class="btn small danger" onclick="deleteMyPartRequest(${r.id})">🗑️ Видалити</button>
        </div>
      </div>`;
    }).join("") : "<p style='color:#93a3b8;'>Ще нічого не надсилали.</p>";
  } catch (err) { toast(err.message, "error"); }
}

window.openLinkRepairModal = async (requestId) => {
  let pending = [];
  try { pending = await api("/repairs/pending"); } catch (err) { toast(err.message, "error"); return; }
  if (!pending.length) { toast("Немає квитанцій в очікуванні на вашій точці", "error"); return; }

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-sheet">
      <h3>🔗 Прив'язати до квитанції</h3>
      <select id="linkRepairSelect">
        ${pending.map(r => `<option value="${r.id}">№${r.receipt_number} (прийнято ${r.intake_date})</option>`).join("")}
      </select>
      <div class="grid2">
        <button class="btn secondary" id="linkRepairCancel">Скасувати</button>
        <button class="btn" id="linkRepairSave">Прив'язати</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  document.getElementById("linkRepairCancel").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.getElementById("linkRepairSave").addEventListener("click", async () => {
    const repairId = document.getElementById("linkRepairSelect").value;
    try {
      await api(`/part-requests/${requestId}/link-repair`, { method: "POST", json: { repair_id: repairId } });
      toast("✅ Прив'язано");
      close();
      renderPartsView();
    } catch (err) { toast(err.message, "error"); }
  });
};

window.deleteMyPartRequest = (id) => {
  showConfirmModal("Видалити це своє посилання на запчастину?", async () => {
    try {
      await api(`/part-requests/${id}`, { method: "DELETE" });
      toast("🗑️ Видалено");
      renderPartsView();
    } catch (err) { toast(err.message, "error"); }
  });
};

// ----------------------------------------------------------------------------
// Web Push - підписка на сповіщення (для адмінів)
// ----------------------------------------------------------------------------
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

async function setupPushNotifications(silentIfNotAsked) {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    if (!silentIfNotAsked) toast("Цей браузер не підтримує push-сповіщення", "error");
    return;
  }
  if (ME.role === "seller") return; // сповіщення потрібні лише адмінам

  if (Notification.permission === "denied") {
    if (!silentIfNotAsked) toast("Сповіщення заблоковано в налаштуваннях браузера для цього сайту - дозвольте вручну (значок 🔒 біля адреси сайту)", "error");
    return;
  }

  if (Notification.permission === "default") {
    if (silentIfNotAsked) { showPushBanner(); return; } // не питаємо дозвіл автоматично - лише по кліку
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      toast("Дозвіл на сповіщення не надано", "error");
      return;
    }
  }

  try {
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
    hidePushBanner();
    if (!silentIfNotAsked) toast("🔔 Сповіщення увімкнено");
  } catch (err) {
    console.error("Push-підписка не вдалась:", err);
    if (!silentIfNotAsked) toast("Не вдалось увімкнути сповіщення: " + err.message, "error");
  }
}

function showPushBanner() {
  const banner = document.getElementById("pushBanner");
  if (!banner) return;
  banner.classList.remove("hidden");
  banner.innerHTML = `<button class="btn small" id="enablePushBtn">🔔 Увімкнути сповіщення</button>`;
  document.getElementById("enablePushBtn").addEventListener("click", () => setupPushNotifications(false));
}

function hidePushBanner() {
  const banner = document.getElementById("pushBanner");
  if (banner) { banner.classList.add("hidden"); banner.innerHTML = ""; }
}

window.sendTestPush = async () => {
  try {
    const res = await api("/push/test", { method: "POST" });
    toast(`Надіслано: ${res.sent}, помилок: ${res.failed} (усього підписок: ${res.total_subscriptions})`);
  } catch (err) { toast(err.message, "error"); }
};

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

function getSharedPartFromUrl() {
  const params = new URLSearchParams(location.search);
  const url = params.get("shared_url") || "";
  const text = params.get("shared_text") || "";
  // Деякі застосунки кладуть посилання в text, а не url - шукаємо http(s):// там теж.
  const urlFromText = (text.match(/https?:\/\/\S+/) || [])[0];
  const link = url || urlFromText || "";
  const note = (url ? text : text.replace(link, "")).trim();
  if (!link) return null;
  history.replaceState(null, "", location.pathname); // приберемо параметри з адресного рядка
  return { link, note };
}

(async function init() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  }
  const shared = getSharedPartFromUrl();
  const restored = await tryRestoreSession();
  if (restored) {
    showMainApp();
    setupPushNotifications(true);
    if (shared && ME.role === "seller") openQuickPartModal(shared.link, shared.note);
  } else {
    if (shared) sessionStorage.setItem("magnit_pending_share", JSON.stringify(shared));
    showLoginScreen();
  }
})();
