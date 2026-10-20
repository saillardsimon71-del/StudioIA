const form = document.querySelector('#subscribe-form');
const status = document.querySelector('#form-status');
const submit = form.querySelector('button[type=submit]');

fetch('/api/funnel/config')
  .then(response => {
    if (!response.ok) throw new Error('Configuration indisponible.');
    return response.json();
  })
  .then(config => {
    if (config.privacy_url) document.querySelector('#privacy-link').href = config.privacy_url;
    if (config.checkout_url?.startsWith('https://')) {
      const link = document.querySelector('#checkout-link');
      link.href = config.checkout_url;
      link.hidden = false;
      document.querySelector('#checkout-status').hidden = true;
    }
    if (!config.signup_enabled) {
      submit.disabled = true;
      status.textContent = "L'inscription ouvrira prochainement.";
    }
  })
  .catch(() => {
    status.textContent = "Le formulaire est momentanément indisponible. Réessaie plus tard.";
    status.dataset.error = 'true';
    submit.disabled = true;
  });

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  submit.disabled = true;
  status.dataset.error = 'false';
  status.textContent = 'Envoi en cours...';
  const data = new FormData(form);
  try {
    const response = await fetch('/api/funnel/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: data.get('email'),
        consent: data.get('consent') === 'on',
        website: data.get('website') || ''
      })
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(typeof result.detail === 'string' ? result.detail : 'Vérifie ton adresse email et ton consentement.');
    }
    status.textContent = result.status === 'enrolled'
      ? 'Ton inscription est confirmée. La bible arrive par email.'
      : 'Vérifie ta boîte mail et confirme ton inscription pour recevoir la bible.';
    form.reset();
  } catch (error) {
    status.dataset.error = 'true';
    status.textContent = error.message || "L'envoi n'a pas été confirmé. Réessaie dans quelques minutes.";
  } finally {
    submit.disabled = false;
  }
});
