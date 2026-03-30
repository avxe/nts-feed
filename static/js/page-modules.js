(function () {
  'use strict';

  const modules = new Map();

  function register(name, definition) {
    if (!name || typeof name !== 'string') {
      throw new Error('Page module name is required');
    }
    if (!definition || typeof definition.init !== 'function') {
      throw new Error(`Page module "${name}" must define an init() function`);
    }

    const normalizedDefinition = {
      init: definition.init,
      cleanup: typeof definition.cleanup === 'function' ? definition.cleanup : () => {},
    };

    modules.set(name, normalizedDefinition);
    return normalizedDefinition;
  }

  window.NTSPageModules = {
    register,
    get(name) {
      return modules.get(name) || null;
    },
    has(name) {
      return modules.has(name);
    },
  };
})();
