document.getElementById('year').textContent = new Date().getFullYear();

document.querySelectorAll('a[href^="https://pay.kiwify.com.br/"]').forEach((link) => {
  link.addEventListener('click', () => {
    try {
      localStorage.setItem('zapvenda_checkout_click', new Date().toISOString());
    } catch (_) {}
  });
});
