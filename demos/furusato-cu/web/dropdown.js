// ブラウザ標準の <select> は、開いた選択肢がスクリーンショットに写らない（AIから見えない）ため、
// 画面内に描画される自作のプルダウンに置き換える。値は元の <select> に書き戻すので、フォーム側の処理はそのまま使える。
(function () {
  const css = `
    .dd { position: relative; }
    .dd-btn { width: 100%; text-align: left; padding: 7px 30px 7px 9px; border: 1px solid #9aa5b1; border-radius: 5px; background: #fff; font: inherit; font-size: 14px; cursor: pointer; position: relative; }
    .dd-btn::after { content: "▾"; position: absolute; right: 10px; color: #52606d; }
    .dd-list { position: absolute; left: 0; right: 0; top: calc(100% + 2px); background: #fff; border: 1px solid #9aa5b1; border-radius: 5px; box-shadow: 0 6px 18px rgba(0,0,0,.15); z-index: 20; display: none; }
    .dd.open .dd-list { display: block; }
    .dd-opt { padding: 7px 10px; font-size: 14px; cursor: pointer; }
    .dd-opt:hover { background: #e8f0fe; }`;
  document.head.insertAdjacentHTML("beforeend", `<style>${css}</style>`);

  function enhance(select) {
    if (select.dataset.dd) return;
    select.dataset.dd = "1";
    select.style.display = "none";
    const wrap = document.createElement("div");
    wrap.className = "dd";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dd-btn";
    const list = document.createElement("div");
    list.className = "dd-list";
    [...select.options].forEach(o => {
      if (!o.value) return;
      const d = document.createElement("div");
      d.className = "dd-opt";
      d.textContent = o.textContent;
      d.addEventListener("click", () => {
        select.value = o.value;
        select.dispatchEvent(new Event("change"));
        sync();
        wrap.classList.remove("open");
      });
      list.appendChild(d);
    });
    const sync = () => { btn.textContent = select.value || select.options[0].textContent; };
    btn.addEventListener("click", e => { e.stopPropagation(); wrap.classList.toggle("open"); });
    document.addEventListener("click", e => { if (!wrap.contains(e.target)) wrap.classList.remove("open"); });
    select._ddSync = sync;
    sync();
    wrap.append(btn, list);
    select.after(wrap);
  }

  window.enhanceSelects = (root = document) => root.querySelectorAll("select").forEach(enhance);
  window.syncSelects = (root = document) => root.querySelectorAll("select").forEach(s => s._ddSync && s._ddSync());
})();
