// Stand-in for the Campaign Cart SDK, for offline tests only. It mimics the
// behaviour the launch check looks at: a ready signal, SDK-bound prices that
// follow the browser locale, dataLayer events, checkout validation, an order
// call, and upsell accept/decline.
(function () {
  var html = document.documentElement;
  var params = new URLSearchParams(location.search);
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : null; };
  var type = meta('next-page-type');
  window.dataLayer = window.dataLayer || [];
  var fmt = new Intl.NumberFormat(navigator.language, { style: 'currency', currency: 'USD' });
  function render() {
    document.querySelectorAll('[data-next-display]').forEach(function (el) {
      var v = parseFloat(el.getAttribute('data-test-amount') || '0');
      el.textContent = fmt.format(v);
    });
  }
  function carry(url) {
    var u = new URL(url, location.href);
    params.forEach(function (v, k) { if (!u.searchParams.has(k)) u.searchParams.set(k, v); });
    return u.href;
  }
  function ready() {
    render();
    html.classList.add('next-display-ready');
    if (type === 'checkout') window.dataLayer.push({ event: 'dl_begin_checkout' });
    if (type === 'upsell') {
      window.dataLayer.push({ event: 'dl_viewed_upsell' });
      if (params.get('ref_id') && !sessionStorage.getItem('purchased')) {
        sessionStorage.setItem('purchased', '1');
        window.dataLayer.push({ event: 'dl_purchase', ecommerce: { value: 49.95, currency: 'USD' } });
      }
    }
    var form = document.querySelector('form[data-next-checkout-step]');
    if (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var validate = form.getAttribute('data-test-validate') !== 'off';
        var bad = 0;
        if (validate) {
          form.querySelectorAll('input[data-next-checkout-field]').forEach(function (f) {
            var ok = f.value.trim() !== '' && (f.type !== 'email' || /.+@.+\..+/.test(f.value));
            f.classList.toggle('next-error-field', !ok);
            f.setAttribute('aria-invalid', ok ? 'false' : 'true');
            if (!ok) bad++;
          });
        }
        if (bad) return;
        fetch('/api/v1/orders/', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ lines: [{ package_id: 1, quantity: 1 }], shipping_method: 1, attribution: { utm_source: params.get('utm_source'), affid: params.get('affid') }, payment_detail: { payment_method: 'card_token', card_token: 'tok_test', test_card_last4: (form.querySelector('[data-next-checkout-field="cc-number"]') || {}).value ? form.querySelector('[data-next-checkout-field="cc-number"]').value.slice(-4) : null }, user: { email: form.querySelector('[type=email]').value, language: 'en' }, success_url: meta('next-success-url') }) })
          .then(function (r) { return r.json(); })
          .then(function (j) { location.href = carry(meta('next-success-url') + '?ref_id=' + j.ref_id); });
      });
    }
    document.querySelectorAll('[data-next-upsell-action]').forEach(function (b) {
      b.addEventListener('click', function () {
        var accept = b.getAttribute('data-next-upsell-action') === 'add';
        var go = function () { location.href = carry(meta(accept ? 'next-upsell-accept-url' : 'next-upsell-decline-url')); };
        if (accept) fetch('/api/v1/orders/' + params.get('ref_id') + '/upsells/', { method: 'POST', body: '{"lines":[{"package_id":2,"quantity":1}]}' }).then(go);
        else go();
      });
    });
    if (params.get('autotest') === '1' && form) {
      form.querySelectorAll('input[data-next-checkout-field]').forEach(function (f) { f.value = f.type === 'email' ? 'qa@example.com' : 'Test'; });
      setTimeout(function () { form.requestSubmit(); }, 300);
    }
    if (params.get('autotest') === '1' && type === 'upsell') setTimeout(function () { document.querySelector('[data-next-upsell-action="add"]').click(); }, 500);
  }
  setTimeout(ready, 200);
})();
