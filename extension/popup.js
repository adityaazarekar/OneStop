/**
 * OneStop Popup — Card Wallet Manager + Page Check Trigger
 */
(function () {
  "use strict";

  const API_BASE = "http://localhost:8000/api/v1";
  const cardList = document.getElementById("cardList");
  const savedCount = document.getElementById("savedCount");
  const btnAnalyze = document.getElementById("btnAnalyze");
  const statusEl = document.getElementById("status");

  async function loadCards() {
    try {
      const resp = await fetch(`${API_BASE}/cards`, {
        signal: AbortSignal.timeout(5000),
      });
      if (!resp.ok) throw new Error(`Backend returned ${resp.status}`);
      const cards = await resp.json();

      if (cards.length === 0) {
        cardList.innerHTML = '<div class="status">No cards found. Run seed.py first.</div>';
        return;
      }

      const storage = await chrome.storage.local.get(["userCardIds"]);
      const savedIds = new Set(storage.userCardIds || []);

      cardList.innerHTML = "";
      for (const card of cards) {
        const item = document.createElement("label");
        item.className = "card-item";
        item.innerHTML = `
          <input type="checkbox" value="${card.id}" ${savedIds.has(card.id) ? "checked" : ""}>
          <div class="card-info">
            <div class="card-name">${card.card_name}</div>
            <div class="card-bank">${card.bank_name} · ${card.card_network.toUpperCase()}</div>
          </div>
          <div class="card-reward">${card.base_reward_pct}%</div>
        `;
        cardList.appendChild(item);
      }

      updateSavedCount();
      cardList.addEventListener("change", onCardChange);
    } catch (err) {
      cardList.innerHTML = `<div class="status error" style="padding:10px 0;">
        Cannot reach backend at ${API_BASE}
      </div>`;
    }
  }

  function onCardChange() {
    const checked = cardList.querySelectorAll('input[type="checkbox"]:checked');
    const ids = Array.from(checked).map((el) => el.value);
    chrome.storage.local.set({ userCardIds: ids });
    updateSavedCount();
  }

  function updateSavedCount() {
    const count = cardList.querySelectorAll('input[type="checkbox"]:checked').length;
    savedCount.textContent = count > 0 ? `${count} selected` : "None selected";
  }

  btnAnalyze.addEventListener("click", async () => {
    btnAnalyze.disabled = true;
    btnAnalyze.textContent = "Checking...";
    statusEl.textContent = "";
    statusEl.className = "status";

    try {
      const response = await chrome.runtime.sendMessage({ type: "ONESTOP_ANALYZE_TAB" });
      if (response && response.success) {
        statusEl.textContent = "Ready. Open the OneStop button on the page.";
        statusEl.className = "status success";
      } else {
        statusEl.textContent = response?.error || "Could not check this page.";
        statusEl.className = "status error";
      }
    } catch (err) {
      statusEl.textContent = err.message || "Could not check this page.";
      statusEl.className = "status error";
    } finally {
      btnAnalyze.disabled = false;
      btnAnalyze.textContent = "Check this page";
    }
  });

  loadCards();
})();
