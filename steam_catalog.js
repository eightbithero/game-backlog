(() => {
  const container = document.evaluate(
    '//*[@id="CommunityTemplate"]/div/div/div/div[1]/div[3]/div[5]',
    document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
  ).singleNodeValue;

  if (!container) {
    console.error('Контейнер не найден по XPath. Проверь, что страница полностью прогрузилась.');
    return;
  }

  const links = container.querySelectorAll(':scope > div > div > span > a');
  if (!links.length) {
    console.error('Ссылки не найдены внутри контейнера — возможно, структура чуть другая.');
    return;
  }

  const names = [...links]
    .map(a => a.textContent.trim())
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, 'ru'));

  console.log(`Найдено игр: ${names.length}`);
  const md = names.map(n => `| ${n} | STEAM |  |  |  |`).join('\n');
  copy(md);
  console.log('Markdown-таблица скопирована в буфер обмена ✅');
})();