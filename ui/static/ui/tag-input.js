(function() {
  'use strict';
  
  function setupTagInput(input) {
    if (!input || input.dataset.tagInputInit) return;
    input.dataset.tagInputInit = '1';
    
    const datalistId = input.getAttribute('list');
    if (!datalistId) return;
    const datalist = document.getElementById(datalistId);
    if (!datalist) return;
    
    // Store all options
    const allOptions = Array.from(datalist.querySelectorAll('option')).map(opt => opt.value);
    
    input.addEventListener('input', function() {
      const value = input.value;
      // Find the current tag being typed (after last comma)
      const lastCommaIndex = value.lastIndexOf(',');
      let currentToken = '';
      let prefix = '';
      if (lastCommaIndex >= 0) {
        prefix = value.substring(0, lastCommaIndex + 1);
        // Keep space if there was one
        const afterComma = value.substring(lastCommaIndex + 1);
        if (afterComma.match(/^\s/)) {
          prefix += afterComma.match(/^\s*/)[0];
        }
        currentToken = value.substring(lastCommaIndex + 1).trim();
      } else {
        currentToken = value.trim();
      }
      
      // Clear and rebuild datalist
      datalist.innerHTML = '';
      if (currentToken === '' && lastCommaIndex >= 0) {
        // User just typed comma - show all options not already used
        const usedTags = value.split(',').map(t => t.trim().toLowerCase()).filter(t => t);
        allOptions.forEach(opt => {
          if (!usedTags.includes(opt.toLowerCase())) {
            const option = document.createElement('option');
            option.value = prefix + opt;
            datalist.appendChild(option);
          }
        });
      } else if (currentToken === '') {
        // No comma yet, show all unused
        const usedTags = value.split(',').map(t => t.trim().toLowerCase()).filter(t => t);
        allOptions.forEach(opt => {
          if (!usedTags.includes(opt.toLowerCase())) {
            const option = document.createElement('option');
            option.value = opt;
            datalist.appendChild(option);
          }
        });
      } else {
        // Filter options that match current token and not already used
        const usedTags = value.split(',').map(t => t.trim().toLowerCase()).filter(t => t && t !== currentToken.toLowerCase());
        allOptions.forEach(opt => {
          if (opt.toLowerCase().indexOf(currentToken.toLowerCase()) === 0 || 
              opt.toLowerCase().includes(currentToken.toLowerCase())) {
            if (!usedTags.includes(opt.toLowerCase())) {
              const option = document.createElement('option');
              option.value = prefix + opt;
              datalist.appendChild(option);
            }
          }
        });
      }
    });
  }
  
  function init() {
    document.querySelectorAll('input[list]').forEach(setupTagInput);
  }
  
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  
  document.addEventListener('htmx:afterSwap', init);
})();
