(() => {
  const source = new URL('../assets/icons.svg', document.currentScript.src).href;
  window.Icon = (name, size = 20) => `<svg class="icon" width="${Number(size)}" height="${Number(size)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="${source}#${name}"></use></svg>`;
})();
