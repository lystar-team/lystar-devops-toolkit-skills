#!/usr/bin/env python3
"""从已经打开的浏览器页面提取组件的 CSS、变量、字体和动画。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Frame, Page, sync_playwright
except ImportError as exc:  # pragma: no cover - 运行环境提示
    print(
        "缺少 Python Playwright。请先在当前环境安装 Playwright，"
        "或使用 agent-browser eval --stdin 执行 CSSOM 采集。",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


THEME_JS = r"""
(theme) => {
  const normalized = theme === 'dark' || theme === 'light' ? theme : null;
  if (!normalized) {
    return { requested: theme || 'current', changed: false, mechanisms: [] };
  }

  const targets = [document.documentElement, document.body].filter(Boolean);
  const themeAttributes = ['data-theme', 'data-color-scheme', 'data-color-mode', 'data-mode'];
  const changes = [];

  for (const element of targets) {
    const beforeClass = typeof element.className === 'string' ? element.className : '';
    const beforeAttributes = Object.fromEntries(themeAttributes
      .filter((name) => element.hasAttribute(name))
      .map((name) => [name, element.getAttribute(name)]));
    const classTokens = beforeClass.split(/\s+/).filter(Boolean);
    const hasPlainThemeClass = classTokens.includes('dark') || classTokens.includes('light');
    const hasNamedThemeClass = classTokens.includes('theme-dark') || classTokens.includes('theme-light');
    const nextClassTokens = classTokens.filter((token) => ![
      'dark', 'light', 'theme-dark', 'theme-light',
    ].includes(token));

    // v0 常把主题写在 html.dark；21st 的 Bundle 常把主题写在 html.light/html.dark。
    // 如果页面没有现成主题 class，也给 html 加 plain class，兼容公开 CSS 中的 .dark 规则。
    if (element === document.documentElement || hasPlainThemeClass || hasNamedThemeClass) {
      nextClassTokens.push(hasNamedThemeClass ? `theme-${normalized}` : normalized);
    }
    if (typeof element.className === 'string') element.className = nextClassTokens.join(' ');

    for (const name of themeAttributes) {
      if (element.hasAttribute(name)) element.setAttribute(name, normalized);
    }

    const afterAttributes = Object.fromEntries(themeAttributes
      .filter((name) => element.hasAttribute(name))
      .map((name) => [name, element.getAttribute(name)]));
    const afterClass = typeof element.className === 'string' ? element.className : '';
    if (beforeClass !== afterClass || JSON.stringify(beforeAttributes) !== JSON.stringify(afterAttributes)) {
      changes.push({
        tag: element.tagName.toLowerCase(),
        beforeClass,
        afterClass,
        beforeAttributes,
        afterAttributes,
      });
    }
  }

  return {
    requested: normalized,
    changed: changes.length > 0,
    mechanisms: [
      'prefers-color-scheme',
      ...(changes.some((item) => item.afterClass.includes('dark') || item.afterClass.includes('light')) ? ['class'] : []),
      ...(changes.some((item) => Object.keys(item.afterAttributes).length) ? ['data-attribute'] : []),
    ],
    changes,
  };
}
"""


THEME_DISCOVERY_JS = r"""
() => {
  const root = document.documentElement;
  const body = document.body;
  const controls = [...document.querySelectorAll('button, [role="button"], [data-theme-toggle], a[href="#"]')]
    .map((element) => ({
      text: (element.innerText || '').trim().slice(0, 80),
      ariaLabel: element.getAttribute('aria-label') || '',
      value: element.getAttribute('data-theme') || element.getAttribute('data-mode') || '',
    }))
    .filter((item) => /dark|light|system|theme/i.test(`${item.text} ${item.ariaLabel} ${item.value}`));
  const cssThemeMedia = [...document.styleSheets].some((sheet) => {
    try {
      return [...sheet.cssRules].some((rule) => /prefers-color-scheme/i.test(rule.cssText || ''));
    } catch (_) {
      return false;
    }
  });
  const themeTargets = [root, body, ...document.querySelectorAll('[data-theme], [data-color-scheme], [data-color-mode], [data-mode]')]
    .filter(Boolean)
    .slice(0, 12)
    .map((element) => ({
      tag: element.tagName.toLowerCase(),
      className: typeof element.className === 'string' ? element.className : '',
      attributes: Object.fromEntries([...element.attributes]
        .filter((attribute) => /^(data-theme|data-color-scheme|data-color-mode|data-mode)$/.test(attribute.name))
        .map((attribute) => [attribute.name, attribute.value])),
    }));
  const urlHasTheme = /(?:^|[?&])theme=(?:light|dark)(?:&|$)/i.test(location.search);
  const classTheme = /(^|\\s)(dark|light|theme-dark|theme-light)(?=\\s|$)/i.test(root.className || '')
    || /(^|\\s)(dark|light|theme-dark|theme-light)(?=\\s|$)/i.test(body?.className || '');
  const attrTheme = themeTargets.some((item) => Object.keys(item.attributes).length > 0);
  const supportsThemes = Boolean(cssThemeMedia || urlHasTheme || classTheme || attrTheme || controls.length);
  return {
    supportsThemes,
    themes: supportsThemes ? ['light', 'dark'] : ['current'],
    mechanisms: [
      ...(cssThemeMedia ? ['media-query'] : []),
      ...(urlHasTheme ? ['url-query'] : []),
      ...(classTheme ? ['class'] : []),
      ...(attrTheme ? ['data-attribute'] : []),
      ...(controls.length ? ['control'] : []),
    ],
    controls,
    themeTargets,
    url: location.href,
  };
}
"""

GENERIC_THEME_JS = r"""
(theme) => {
  const normalized = theme === 'dark' || theme === 'light' ? theme : null;
  if (!normalized) return { changed: false, mechanisms: [] };
  const changes = [];
  const targets = [document.documentElement, document.body, ...document.querySelectorAll('[data-theme], [data-color-scheme], [data-color-mode], [data-mode]')]
    .filter(Boolean)
    .slice(0, 12);
  const controls = [...document.querySelectorAll('button, [role="button"], [data-theme-toggle], a[href="#"]')];
  const control = controls.find((element) => {
    const value = `${element.innerText || ''} ${element.getAttribute('aria-label') || ''} ${element.getAttribute('data-theme') || ''} ${element.getAttribute('data-mode') || ''}`.toLowerCase();
    return value.includes(normalized);
  });
  if (control) {
    try {
      control.click();
      changes.push({ mechanism: 'control-click', tag: control.tagName.toLowerCase(), text: (control.innerText || '').trim().slice(0, 80) });
    } catch (_) {}
  }
  for (const element of targets) {
    const beforeClass = typeof element.className === 'string' ? element.className : '';
    const beforeAttrs = Object.fromEntries([...element.attributes]
      .filter((attribute) => /^(data-theme|data-color-scheme|data-color-mode|data-mode)$/.test(attribute.name))
      .map((attribute) => [attribute.name, attribute.value]));
    const tokens = beforeClass.split(/\\s+/).filter(Boolean);
    const hasThemeClass = tokens.some((token) => ['dark', 'light', 'theme-dark', 'theme-light'].includes(token));
    const nextTokens = tokens.filter((token) => !['dark', 'light', 'theme-dark', 'theme-light'].includes(token));
    if (element === document.documentElement || hasThemeClass) {
      nextTokens.push(hasThemeClass && tokens.some((token) => token.startsWith('theme-')) ? `theme-${normalized}` : normalized);
      if (typeof element.className === 'string') element.className = nextTokens.join(' ');
    }
    for (const name of ['data-theme', 'data-color-scheme', 'data-color-mode', 'data-mode']) {
      if (element.hasAttribute(name)) element.setAttribute(name, normalized);
    }
    const afterClass = typeof element.className === 'string' ? element.className : '';
    const afterAttrs = Object.fromEntries([...element.attributes]
      .filter((attribute) => /^(data-theme|data-color-scheme|data-color-mode|data-mode)$/.test(attribute.name))
      .map((attribute) => [attribute.name, attribute.value]));
    if (beforeClass !== afterClass || JSON.stringify(beforeAttrs) !== JSON.stringify(afterAttrs)) {
      changes.push({ tag: element.tagName.toLowerCase(), beforeClass, afterClass, beforeAttributes: beforeAttrs, afterAttributes: afterAttrs });
    }
  }
  return {
    changed: changes.length > 0,
    mechanisms: [...new Set(changes.map((item) => item.mechanism || 'dom'))],
    changes,
  };
}
"""

EXTRACT_JS = r"""
(args) => {
  const selector = args.selector || 'body';
  const maxElements = Number(args.maxElements || 1000);
  const fullComputed = Boolean(args.fullComputed);
  const includeHtml = Boolean(args.includeHtml);
  const requestedTheme = args.theme || null;
  const root = document.querySelector(selector);
  if (!root) throw new Error(`找不到目标选择器: ${selector}`);

  const splitDeclarations = (value) => {
    const result = [];
    let current = '';
    let round = 0;
    let square = 0;
    let quote = '';
    for (const char of value || '') {
      if (quote) {
        current += char;
        if (char === quote) quote = '';
        continue;
      }
      if (char === '"' || char === "'") {
        quote = char;
        current += char;
      } else if (char === '(') {
        round += 1;
        current += char;
      } else if (char === ')') {
        round = Math.max(0, round - 1);
        current += char;
      } else if (char === '[') {
        square += 1;
        current += char;
      } else if (char === ']') {
        square = Math.max(0, square - 1);
        current += char;
      } else if (char === ';' && round === 0 && square === 0) {
        if (current.trim()) result.push(current.trim());
        current = '';
      } else {
        current += char;
      }
    }
    if (current.trim()) result.push(current.trim());
    return result;
  };

  const styleDeclarations = (style) => {
    const result = {};
    if (!style) return result;

    // CSSOM 在部分浏览器里会把 background、animation 等 shorthand 展开成空的
    // longhand 名字。先从 cssText 读取原始声明，才能保留真正写在页面里的属性。
    for (const declaration of splitDeclarations(style.cssText || '')) {
      let round = 0;
      let square = 0;
      let quote = '';
      let colon = -1;
      for (let index = 0; index < declaration.length; index += 1) {
        const char = declaration[index];
        if (quote) {
          if (char === quote) quote = '';
          continue;
        }
        if (char === '"' || char === "'") quote = char;
        else if (char === '(') round += 1;
        else if (char === ')') round = Math.max(0, round - 1);
        else if (char === '[') square += 1;
        else if (char === ']') square = Math.max(0, square - 1);
        else if (char === ':' && round === 0 && square === 0) {
          colon = index;
          break;
        }
      }
      if (colon < 0) continue;
      const property = declaration.slice(0, colon).trim();
      const value = declaration.slice(colon + 1).trim();
      if (property && value) result[property] = value;
    }

    // 补上 cssText 没有展示、但 CSSStyleDeclaration 能直接读到的属性；不写入空值。
    for (let i = 0; i < style.length; i += 1) {
      const property = style[i];
      const value = style.getPropertyValue(property).trim();
      if (value && !(property in result)) result[property] = value;
    }
    return result;
  };

  const stylePropertyNames = (style) => {
    const result = [];
    if (!style) return result;
    for (let index = 0; index < Number(style.length || 0); index += 1) {
      const property = style[index];
      if (property) result.push(property);
    }
    return result;
  };

  const splitSelectorList = (value) => {
    const result = [];
    let current = '';
    let round = 0;
    let square = 0;
    let quote = '';
    for (const char of value || '') {
      if (quote) {
        current += char;
        if (char === quote) quote = '';
        continue;
      }
      if (char === '"' || char === "'") {
        quote = char;
        current += char;
      } else if (char === '(') {
        round += 1;
        current += char;
      } else if (char === ')') {
        round = Math.max(0, round - 1);
        current += char;
      } else if (char === '[') {
        square += 1;
        current += char;
      } else if (char === ']') {
        square = Math.max(0, square - 1);
        current += char;
      } else if (char === ',' && round === 0 && square === 0) {
        if (current.trim()) result.push(current.trim());
        current = '';
      } else {
        current += char;
      }
    }
    if (current.trim()) result.push(current.trim());
    return result;
  };

  const combineSelectors = (parent, child) => {
    if (!parent) return child;
    const parents = splitSelectorList(parent);
    const children = splitSelectorList(child);
    return parents.flatMap((parentSelector) => children.map((childSelector) => {
      if (childSelector.includes('&')) return childSelector.replaceAll('&', parentSelector);
      if (childSelector.startsWith(':') || childSelector.startsWith('[')) {
        return `${parentSelector}${childSelector}`;
      }
      return `${parentSelector} ${childSelector}`;
    })).join(', ');
  };

  const rules = [];
  const fontFaces = [];
  const keyframes = [];
  const styleSheets = [];

  const walkRules = (ruleList, contexts = [], parentSelector = null, source = '[inline]') => {
    for (const rule of ruleList || []) {
      const constructorName = rule.constructor?.name || '';
      if (rule.type === 1) {
        const selectorText = combineSelectors(parentSelector, rule.selectorText);
        const declarations = styleDeclarations(rule.style);
        if (Object.keys(declarations).length) {
          rules.push({
            kind: 'style',
            selector: selectorText,
            declarations,
            css: rule.style.cssText,
            conditions: contexts,
            source,
          });
        }
        if (rule.cssRules) walkRules(rule.cssRules, contexts, selectorText, source);
        continue;
      }

      if (constructorName === 'CSSNestedDeclarations') {
        const declarations = styleDeclarations(rule.style);
        if (Object.keys(declarations).length) {
          rules.push({
            kind: 'style',
            selector: parentSelector,
            declarations,
            css: rule.style.cssText,
            conditions: contexts,
            source,
          });
        }
        continue;
      }

      if (rule.type === 7 || constructorName === 'CSSKeyframesRule') {
        const frames = [...(rule.cssRules || [])].map((frame) => ({
          key: frame.keyText,
          declarations: styleDeclarations(frame.style),
          css: frame.style?.cssText || '',
        }));
        const item = { kind: 'keyframes', name: rule.name, frames, conditions: contexts, source };
        rules.push(item);
        keyframes.push(item);
        continue;
      }

      if (rule.type === 5 || constructorName === 'CSSFontFaceRule') {
        const item = {
          kind: 'font-face',
          declarations: styleDeclarations(rule.style),
          css: rule.cssText,
          conditions: contexts,
          source,
        };
        rules.push(item);
        fontFaces.push(item);
        continue;
      }

      if (!rule.cssRules && rule.cssText && [
        'CSSLayerStatementRule',
        'CSSPropertyRule',
      ].includes(constructorName)) {
        rules.push({
          kind: 'raw',
          css: rule.cssText,
          conditions: contexts,
          source,
        });
        continue;
      }

      if (rule.cssRules) {
        let label = '';
        if (rule.type === 4) label = `@media ${rule.conditionText}`;
        else if (rule.type === 12) label = `@supports ${rule.conditionText}`;
        else if (rule.type === 0 && constructorName === 'CSSLayerBlockRule') {
          label = `@layer ${rule.name || ''}`;
        } else if (constructorName === 'CSSContainerRule') {
          label = `@container ${rule.conditionText || ''}`;
        } else if (rule.conditionText) {
          label = `@group ${rule.conditionText}`;
        } else {
          label = `@group type=${rule.type}`;
        }
        walkRules(rule.cssRules, label ? contexts.concat(label) : contexts, parentSelector, source);
      }
    }
  };

  for (const sheet of document.styleSheets) {
    const item = { href: sheet.href || '[inline]', disabled: sheet.disabled, readable: true };
    try {
      item.ruleCount = sheet.cssRules.length;
      walkRules(sheet.cssRules, [], null, sheet.href || '[inline]');
    } catch (error) {
      item.readable = false;
      item.error = String(error);
    }
    styleSheets.push(item);
  }

  const ancestorElements = [];
  for (let current = root.parentElement; current && current !== document.documentElement; current = current.parentElement) {
    ancestorElements.unshift(current);
  }

  // 镜像 HTML 会保留祖先上下文；祖先的 class、伪元素和背景同样会影响目标组件。
  // 把 root 放在第一位，保证 maxElements 较小时目标本身不会被截掉。
  const orderedElements = [root, document.documentElement, ...ancestorElements, ...root.querySelectorAll('*')];
  const targetElements = [...new Set(orderedElements)].slice(0, maxElements);
  const classTokens = [...new Set(targetElements.flatMap((element) =>
    (element.getAttribute('class') || '').split(/\s+/).filter(Boolean)
  ))];

  const hasExactClassSelector = (selectorText, token) => {
    const needle = `.${CSS.escape(token)}`;
    let position = selectorText.indexOf(needle);
    while (position >= 0) {
      const after = selectorText[position + needle.length] || '';
      if (!/[A-Za-z0-9_\\-]/.test(after)) return true;
      position = selectorText.indexOf(needle, position + 1);
    }
    return false;
  };

  const styleRules = rules.filter((rule) => rule.kind === 'style');
  const classes = Object.fromEntries(classTokens.map((token) => [
    token,
    styleRules.filter((rule) => hasExactClassSelector(rule.selector || '', token)),
  ]));

  // getPropertyValue() 使用 CSS 属性名；这里不能写成 zIndex、backgroundColor 这种 JS 属性名，
  // 否则普通模式会得到空值。完整模式仍然直接遍历 CSSStyleDeclaration。
  const importantProperties = [
    'display', 'position', 'inset', 'top', 'right', 'bottom', 'left', 'z-index',
    'width', 'height', 'min-height', 'max-height', 'min-width', 'max-width',
    'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
    'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'gap', 'row-gap', 'column-gap', 'grid-template-columns', 'grid-template-rows',
    'align-items', 'align-content', 'justify-content', 'justify-items',
    'overflow', 'overflow-x', 'overflow-y', 'cursor', 'isolation',
    'border-radius', 'border-width', 'border-style', 'border-color',
    'background', 'background-color', 'background-image', 'color', 'font-family', 'font-size',
    'font-weight', 'line-height', 'letter-spacing', 'white-space', 'object-fit',
    'box-sizing', 'border', 'box-shadow', 'filter', 'backdrop-filter', 'transition-property',
    'transition-duration', 'transition-delay', 'transition-timing-function',
    'animation-name', 'animation-duration', 'animation-delay',
    'animation-iteration-count', 'animation-direction', 'animation-fill-mode',
    'animation-timing-function', 'animation-play-state', 'transform', 'transform-origin',
    'translate', 'rotate', 'scale', 'opacity', 'visibility', 'pointer-events',
    'content', 'clip-path', 'mask-image', 'will-change',
  ];

  const cleanValue = (value) => {
    if (value === Infinity) return 'infinite';
    if (typeof value === 'number' && Number.isNaN(value)) return 'nan';
    if (Array.isArray(value)) return value.map(cleanValue);
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cleanValue(item)]));
    }
    return value;
  };

  const animationData = (element, computed, pseudoElement = null) => {
    const animationNames = computed.getPropertyValue('animation-name')
      .split(',')
      .map((name) => name.trim())
      .filter((name) => name && name !== 'none');
    const animations = pseudoElement
      ? element.getAnimations({ subtree: true }).filter((animation) => animationNames.includes(animation.animationName))
      : element.getAnimations().filter((animation) => !animation.pseudoElement);
    return animations.map((animation) => ({
      type: animation.constructor?.name || 'Animation',
      pseudoElement: pseudoElement || animation.pseudoElement || null,
      name: animation.animationName || computed.animationName || null,
      playState: animation.playState,
      currentTime: cleanValue(animation.currentTime),
      timing: cleanValue(animation.effect?.getTiming?.() || null),
      keyframes: cleanValue(animation.effect?.getKeyframes?.() || []),
    }));
  };

  const domPath = (element) => {
    const parts = [];
    let current = element;
    while (current && current.nodeType === 1 && current !== document.documentElement) {
      let index = 1;
      let sibling = current.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === current.tagName) index += 1;
        sibling = sibling.previousElementSibling;
      }
      parts.unshift(`${current.tagName.toLowerCase()}:nth-of-type(${index})`);
      current = current.parentElement;
    }
    return parts.join(' > ') || 'html';
  };

  const customPropertyData = (element, computedStyle) => {
    const inlineCustomProperties = {};
    for (const property of stylePropertyNames(element.style)) {
      if (property.startsWith('--')) {
        inlineCustomProperties[property] = element.style.getPropertyValue(property).trim();
      }
    }

    const customProperties = {};
    const parentStyle = element.parentElement ? getComputedStyle(element.parentElement) : null;
    for (const property of stylePropertyNames(computedStyle)) {
      if (!property.startsWith('--')) continue;
      const value = computedStyle.getPropertyValue(property).trim();
      const inheritedValue = parentStyle ? parentStyle.getPropertyValue(property).trim() : null;
      // 根节点给出完整变量；子元素只保留相对父级发生变化的变量，避免把继承变量重复写几百遍。
      if (!parentStyle || value !== inheritedValue || property in inlineCustomProperties) {
        customProperties[property] = value;
      }
    }
    return { customProperties, inlineCustomProperties };
  };

  const attributesData = (element) => Object.fromEntries(
    [...element.attributes].map((attribute) => [attribute.name, attribute.value])
  );

  const ancestorData = (element) => {
    const result = [];
    let current = element.parentElement;
    while (current && current !== document.documentElement) {
      result.unshift({
        tag: current.tagName.toLowerCase(),
        attributes: attributesData(current),
      });
      current = current.parentElement;
    }
    return result;
  };

  const allCustomProperties = (computedStyle) => Object.fromEntries(
    stylePropertyNames(computedStyle)
      .filter((property) => property.startsWith('--'))
      .map((property) => [property, computedStyle.getPropertyValue(property).trim()])
  );

  const pseudoData = (element) => {
    const result = {};
    const hostStyle = getComputedStyle(element);
    for (const pseudoElement of ['::before', '::after']) {
      const computedStyle = getComputedStyle(element, pseudoElement);
      const animations = animationData(element, computedStyle, pseudoElement);
      const content = computedStyle.getPropertyValue('content').trim();
      const backgroundImage = computedStyle.getPropertyValue('background-image').trim();
      const backgroundColor = computedStyle.getPropertyValue('background-color').trim();
      const borderWidth = computedStyle.getPropertyValue('border-width').trim();
      const hasGeneratedContent = content && content !== 'none' && content !== 'normal';
      const hasPaint = backgroundImage !== 'none'
        || (backgroundColor && backgroundColor !== 'transparent' && backgroundColor !== 'rgba(0, 0, 0, 0)')
        || (borderWidth && borderWidth !== '0px');
      const hasLayout = computedStyle.getPropertyValue('position') !== 'static'
        && computedStyle.getPropertyValue('display') !== 'inline';
      if (!hasGeneratedContent && !hasPaint && !hasLayout && !animations.length) continue;

      const computed = {};
      const properties = fullComputed ? stylePropertyNames(computedStyle) : importantProperties;
      for (const property of properties) computed[property] = computedStyle.getPropertyValue(property).trim();

      const customProperties = Object.fromEntries(
        stylePropertyNames(computedStyle)
          .filter((property) => property.startsWith('--'))
          .map((property) => [property, computedStyle.getPropertyValue(property).trim()])
          .filter(([property, value]) => value !== hostStyle.getPropertyValue(property).trim())
      );

      result[pseudoElement.slice(2)] = {
        computed,
        customProperties,
        animations,
      };
    }
    return result;
  };

  const elementData = (element, index) => {
    const computedStyle = getComputedStyle(element);
    const computed = {};
    const properties = fullComputed ? stylePropertyNames(computedStyle) : importantProperties;
    for (const property of properties) computed[property] = computedStyle.getPropertyValue(property).trim();

    const { customProperties, inlineCustomProperties } = customPropertyData(element, computedStyle);

    const rect = element.getBoundingClientRect();
    return {
      index,
      domPath: domPath(element),
      tag: element.tagName.toLowerCase(),
      text: (element.innerText || '').trim().slice(0, 160),
      class: element.getAttribute('class') || '',
      attributes: attributesData(element),
      inlineStyle: element.getAttribute('style') || '',
      inlineDeclarations: styleDeclarations(element.style),
      rect: {
        x: Number(rect.x.toFixed(3)),
        y: Number(rect.y.toFixed(3)),
        width: Number(rect.width.toFixed(3)),
        height: Number(rect.height.toFixed(3)),
      },
      computed,
      customProperties,
      inlineCustomProperties,
      animations: animationData(element, computedStyle),
      pseudo: pseudoData(element),
    };
  };

  const renderModeData = () => {
    const canvases = [...document.querySelectorAll('canvas')];
    const svgs = [...document.querySelectorAll('svg')];
    const iframes = [...document.querySelectorAll('iframe')];
    const webgl = canvases.some((canvas) => {
      try {
        return Boolean(canvas.getContext('webgl') || canvas.getContext('webgl2') || canvas.getContext('experimental-webgl'));
      } catch (_) {
        return false;
      }
    });
    const hasDom = Boolean(document.body?.children.length);
    let renderMode = 'dom-css';
    if (webgl) renderMode = 'webgl';
    else if (canvases.length) renderMode = 'canvas';
    else if (svgs.length) renderMode = 'dom-css-svg';
    if (iframes.length && renderMode === 'dom-css') renderMode = 'iframe';
    if ((canvases.length || iframes.length) && hasDom && renderMode !== 'dom-css-svg') renderMode = 'hybrid';
    return {
      renderMode,
      dom: { hasBody: Boolean(document.body), elementCount: document.querySelectorAll('*').length },
      frames: iframes.map((element) => ({ src: element.src || element.getAttribute('src') || '', title: element.title || '' })),
      scripts: [...document.scripts].map((script) => script.src).filter(Boolean),
      stylesheets: [...document.styleSheets].map((sheet) => sheet.href || '[inline]'),
      svgCount: svgs.length,
      canvasCount: canvases.length,
      webgl,
      url: location.href,
      readyState: document.readyState,
    };
  };

  const discovery = renderModeData();
  const assets = [];
  for (const element of targetElements) {
    if (element instanceof HTMLImageElement) {
      assets.push({ type: 'image', url: element.currentSrc || element.src, alt: element.alt, domPath: domPath(element) });
    } else if (element instanceof HTMLVideoElement) {
      assets.push({ type: 'video', url: element.currentSrc || element.src, domPath: domPath(element) });
    }
    const background = getComputedStyle(element).backgroundImage;
    if (background && background !== 'none' && /url\(/i.test(background)) {
      assets.push({ type: 'background-image', value: background, domPath: domPath(element) });
    }
  }

  const media = {
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
    hover: matchMedia('(hover: hover)').matches,
    anyHover: matchMedia('(any-hover: hover)').matches,
    pointer: matchMedia('(pointer: fine)').matches ? 'fine' : matchMedia('(pointer: coarse)').matches ? 'coarse' : 'none',
    anyPointer: matchMedia('(any-pointer: fine)').matches ? 'fine' : matchMedia('(any-pointer: coarse)').matches ? 'coarse' : 'none',
    prefersColorScheme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
    prefersReducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
  };

  const fonts = [...document.fonts].map((font) => ({
    family: font.family,
    style: font.style,
    weight: font.weight,
    stretch: font.stretch,
    status: font.status,
  }));

  const result = {
    discovery,
    renderMode: discovery.renderMode,
    theme: {
      requested: requestedTheme,
      resolved: media.prefersColorScheme,
      url: location.href,
      htmlClass: document.documentElement.className || '',
      htmlAttributes: attributesData(document.documentElement),
    },
    target: {
      selector,
      tag: root.tagName.toLowerCase(),
      class: root.getAttribute('class') || '',
      text: (root.innerText || '').trim().slice(0, 160),
      resolvedCustomProperties: allCustomProperties(getComputedStyle(root)),
      documentElement: {
        tag: 'html',
        attributes: attributesData(document.documentElement),
      },
      ancestors: ancestorData(root),
    },
    media,
    styleSheets,
    cssRules: rules,
    classes,
    coverage: {
      classTokenCount: classTokens.length,
      matchedRuleCount: classTokens.filter((token) => classes[token].length > 0).length,
      unmatchedTokens: classTokens.filter((token) => classes[token].length === 0),
    },
    elements: targetElements.map(elementData),
    keyframes,
    fontFaces,
    fonts,
    assets,
  };
  if (includeHtml) result.html = root.outerHTML;
  return result;
}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get("AGENT_BROWSER_CDP_URL", "http://127.0.0.1:9222"),
        help="已有浏览器的 CDP HTTP/WS 地址",
    )
    parser.add_argument("--page-url", required=True, help="要连接的页面 URL 子串")
    parser.add_argument("--frame-url", help="优先选择 URL 包含此子串的 frame")
    parser.add_argument(
        "--source",
        default="auto",
        choices=["auto", "generic", "v0", "21st", "runtime"],
        help="来源处理模式；默认自动发现",
    )
    parser.add_argument("--selector", default="body", help="目标组件的 CSS 选择器")
    parser.add_argument(
        "--states",
        default="base",
        help="要采集的状态，逗号分隔：base、hover、focus、active、auto",
    )
    parser.add_argument(
        "--themes",
        default="light,dark",
        help="要采集的主题，逗号分隔：light、dark、current、auto",
    )
    parser.add_argument(
        "--viewports",
        default="current",
        help="视口：current、desktop、tablet、mobile 或 WIDTHxHEIGHT，逗号分隔",
    )
    parser.add_argument("--max-elements", type=int, default=1000)
    parser.add_argument("--full-computed", action="store_true", help="保存全部 computed style 属性")
    parser.add_argument("--include-html", action="store_true", help="把目标根节点 outerHTML 写入报告")
    parser.add_argument("--wait-ms", type=int, default=0, help="打开页面后额外等待毫秒数")
    parser.add_argument("--out", required=True, help="输出 JSON 文件")
    return parser.parse_args()


def parse_themes(value: str) -> list[str]:
    themes = []
    for item in value.split(","):
        theme = item.strip().lower()
        if not theme:
            continue
        if theme not in {"light", "dark", "current", "auto"}:
            raise ValueError(f"不支持的主题: {theme}，只能使用 light、dark、current 或 auto")
        if theme not in themes:
            themes.append(theme)
    return themes or ["light", "dark"]


def parse_viewports(value: str) -> list[dict[str, Any]]:
    presets = {
        "current": {"name": "current", "width": None, "height": None},
        "desktop": {"name": "desktop", "width": 1280, "height": 720},
        "tablet": {"name": "tablet", "width": 768, "height": 1024},
        "mobile": {"name": "mobile", "width": 390, "height": 844},
    }
    result: list[dict[str, Any]] = []
    for item in value.split(","):
        token = item.strip().lower()
        if not token:
            continue
        if token in presets:
            candidate = dict(presets[token])
        else:
            match = re.fullmatch(r"(\\d+)x(\\d+)", token)
            if not match:
                raise ValueError(f"不支持的视口: {token}，可使用 current、desktop、tablet、mobile 或 WIDTHxHEIGHT")
            candidate = {"name": token, "width": int(match.group(1)), "height": int(match.group(2))}
        if candidate not in result:
            result.append(candidate)
    return result or [dict(presets["current"])]


def discover_theme_info(frame: Frame) -> dict[str, Any]:
    try:
        return frame.evaluate(THEME_DISCOVERY_JS)
    except PlaywrightError as exc:
        return {"supportsThemes": False, "themes": ["current"], "mechanisms": [], "error": str(exc)}


def resolve_states(frame: Frame, selector: str, value: str) -> list[str]:
    requested = [item.strip().lower() for item in value.split(",") if item.strip()]
    if "auto" not in requested:
        result = []
        for item in requested:
            if item not in {"base", "hover", "focus", "active"}:
                raise ValueError(f"不支持的状态: {item}，只能使用 base、hover、focus、active 或 auto")
            if item not in result:
                result.append(item)
        return result or ["base"]
    try:
        interactive = frame.locator(selector).first.evaluate("""(element) => {
          const tag = element.tagName.toLowerCase();
          return ['a', 'button', 'input', 'select', 'textarea', 'summary'].includes(tag)
            || Boolean(element.getAttribute('role'))
            || element.hasAttribute('tabindex');
        }""")
    except Exception:
        interactive = False
    return ["base", "hover", "focus", "active"] if interactive else ["base"]


def apply_viewport(page: Page, viewport: dict[str, Any]) -> dict[str, Any]:
    result = dict(viewport)
    if not viewport.get("width") or not viewport.get("height"):
        result["applied"] = False
        result["reason"] = "使用当前浏览器视口"
        return result
    try:
        page.set_viewport_size({"width": viewport["width"], "height": viewport["height"]})
        result["applied"] = True
    except Exception as exc:
        result["applied"] = False
        result["reason"] = str(exc)
    return result


def resolve_themes(frame: Frame, requested: list[str]) -> tuple[list[str], dict[str, Any]]:
    info = discover_theme_info(frame)
    if "auto" not in requested:
        return requested, info
    resolved = []
    for item in requested:
        if item == "auto":
            for theme in info.get("themes", ["current"]):
                if theme not in resolved:
                    resolved.append(theme)
        elif item not in resolved:
            resolved.append(item)
    return resolved or ["current"], info


def frame_href(frame: Frame) -> str:
    try:
        return str(frame.evaluate("location.href"))
    except Exception:
        return frame.url or ""


def detect_platform(*urls: str) -> str:
    value = " ".join(urls).lower()
    if "v0.app" in value or "vusercontent.net" in value:
        return "v0"
    if "21st.dev" in value:
        return "21st"
    return "unknown"


def theme_url(url: str, theme: str, platform: str = "21st", allow_new_param: bool = False) -> str:
    """为公开 Bundle 或明确使用 theme 参数的页面设置主题参数。"""
    if theme not in {"light", "dark"} or not url:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    changed = False
    found_theme = False
    next_query = []
    for key, value in query:
        if key.lower() == "theme":
            next_query.append((key, theme))
            changed = True
            found_theme = True
        else:
            next_query.append((key, value))
    if not found_theme and (platform == "21st" and "cdn.21st.dev" in parts.netloc.lower() or allow_new_param):
        next_query.append(("theme", theme))
        changed = True
    if not changed:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(next_query), parts.fragment))


def apply_theme(page: Page, frame: Frame, theme: str) -> dict[str, Any]:
    """按平台和页面已发现的主题入口切换主题。"""
    platform = detect_platform(page.url, frame_href(frame))
    theme_info = discover_theme_info(frame)
    mechanisms = set(theme_info.get("mechanisms", []))
    strategy = {
        "21st": "public-bundle-query",
        "v0": "preview-html-class",
    }.get(platform, "generic-discovery")
    result: dict[str, Any] = {
        "captured": True,
        "name": theme,
        "platform": platform,
        "strategy": strategy,
        "discovered": theme_info,
        "urlBefore": frame_href(frame),
        "urlAfter": frame_href(frame),
        "mediaEmulated": False,
        "urlThemeApplied": False,
        "domThemeApplied": False,
    }
    if theme == "current":
        result["mechanisms"] = ["current"]
        return result

    try:
        page.emulate_media(color_scheme=theme)
        result["mediaEmulated"] = True
    except PlaywrightError as exc:
        result["mediaError"] = str(exc)

    before_url = frame_href(frame)
    if platform == "21st" or "url-query" in mechanisms:
        desired_url = theme_url(
            before_url,
            theme,
            platform,
            allow_new_param=("url-query" in mechanisms and platform != "21st"),
        )
        if desired_url != before_url:
            try:
                frame.goto(desired_url, wait_until="networkidle", timeout=30000)
            except PlaywrightError:
                try:
                    frame.goto(desired_url, wait_until="load", timeout=30000)
                except PlaywrightError as exc:
                    result["urlError"] = str(exc)
            result["urlThemeApplied"] = frame_href(frame) == desired_url
            if not result["urlThemeApplied"]:
                result["captured"] = False
                result["reason"] = "公开 Bundle 或主题 URL 没有成功加载"
                result["urlAfter"] = frame_href(frame)
                return result
            try:
                page.emulate_media(color_scheme=theme)
                result["mediaEmulated"] = True
            except PlaywrightError as exc:
                result["mediaErrorAfterNavigation"] = str(exc)

    try:
        if platform in {"v0", "21st"}:
            dom_result = frame.evaluate(THEME_JS, theme)
        else:
            dom_result = frame.evaluate(GENERIC_THEME_JS, theme)
            page.wait_for_timeout(150)
        result["domThemeApplied"] = bool(dom_result.get("changed"))
        result["mechanisms"] = dom_result.get("mechanisms", []) or sorted(mechanisms)
        result["domChanges"] = dom_result.get("changes", [])
    except PlaywrightError as exc:
        result["domError"] = str(exc)
        result["mechanisms"] = sorted(mechanisms)
    result["urlBefore"] = before_url
    result["urlAfter"] = frame_href(frame)
    return result


def page_url_matches(requested: str, actual: str) -> bool:
    if requested in actual:
        return True
    requested_parts = urlsplit(requested)
    actual_parts = urlsplit(actual)
    requested_host = requested_parts.netloc.lower().removeprefix("www.")
    actual_host = actual_parts.netloc.lower().removeprefix("www.")
    aliases = {
        frozenset({"v0.dev", "v0.app"}),
    }
    same_host = requested_host == actual_host or frozenset({requested_host, actual_host}) in aliases
    if not same_host:
        return False
    requested_path = requested_parts.path.rstrip("/") or "/"
    actual_path = actual_parts.path.rstrip("/") or "/"
    return requested_path == actual_path or requested_path == "/"


def find_page(pages: list[Page], page_url: str) -> Page:
    matches = [page for page in pages if page_url_matches(page_url, page.url)]
    if not matches:
        available = "\n".join(f"- {page.url}" for page in pages)
        raise RuntimeError(f"找不到 --page-url={page_url} 的页面。当前页面：\n{available}")
    return matches[0]


def find_frame(page: Page, selector: str, frame_url: str | None) -> Frame:
    frames = page.frames
    if frame_url:
        matches = [frame for frame in frames if frame_url in frame_href(frame)]
        if matches:
            return matches[0]
        raise RuntimeError(f"找不到 URL 包含 {frame_url} 的 frame。当前 frame：\n" + "\n".join(frame_href(f) for f in frames))

    if not frame_url and selector.strip() == "body" and detect_platform(page.url) not in {"v0", "21st"}:
        main_frame = page.main_frame
        try:
            if main_frame.locator("body").count():
                return main_frame
        except Exception:
            pass

    candidates: list[tuple[int, int, Frame]] = []
    for index, frame in enumerate(frames):
        try:
            count = frame.locator(selector).count()
        except Exception:
            count = 0
        if count:
            # 优先子 frame；同一层时保留 page.frames 的顺序。
            depth = 0
            parent = frame.parent_frame
            while parent:
                depth += 1
                parent = parent.parent_frame
            candidates.append((depth, -index, frame))
    if candidates:
        return max(candidates, key=lambda item: (item[0], item[1]))[2]
    raise RuntimeError(f"在页面的 frame 中找不到目标选择器: {selector}")


def capture_state(frame: Frame, args: argparse.Namespace) -> dict[str, Any]:
    return frame.evaluate(
        EXTRACT_JS,
        {
            "selector": args.selector,
            "maxElements": args.max_elements,
            "fullComputed": args.full_computed,
            "includeHtml": args.include_html,
            "theme": getattr(args, "theme", None),
        },
    )


def reset_interaction(page: Page, frame: Frame) -> None:
    """让每个状态从干净的鼠标/焦点状态开始，避免 hover 泄漏到 focus。"""
    try:
        page.mouse.up()
    except Exception:
        pass
    try:
        page.mouse.move(0, 0)
    except Exception:
        pass
    try:
        frame.evaluate("document.activeElement?.blur()")
    except Exception:
        pass


def apply_state(page: Page, frame: Frame, selector: str, state: str) -> dict[str, Any]:
    target = frame.locator(selector).first
    if state == "base":
        return {"captured": True}
    try:
        reset_interaction(page, frame)
        if state == "hover":
            target.hover(force=True, timeout=5000)
        elif state == "focus":
            target.focus(timeout=5000)
        elif state == "active":
            target.hover(force=True, timeout=5000)
            page.mouse.down()
        else:
            return {"captured": False, "reason": f"不支持的状态: {state}"}
        return {"captured": True}
    except PlaywrightError as exc:
        return {"captured": False, "reason": str(exc)}


def capture_args_for_theme(args: argparse.Namespace, theme: str) -> argparse.Namespace:
    return argparse.Namespace(
        selector=args.selector,
        max_elements=args.max_elements,
        full_computed=args.full_computed,
        include_html=args.include_html,
        theme=theme,
    )


def json_signature(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def merge_unique(items: list[Any]) -> list[Any]:
    seen = set()
    result = []
    for item in items:
        key = json_signature(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def merge_capture_data(captures: list[dict[str, Any]]) -> dict[str, Any]:
    if not captures:
        return {}
    classes: dict[str, list[dict[str, Any]]] = {}
    for capture in captures:
        for token, rules in capture.get("classes", {}).items():
            classes.setdefault(token, [])
            classes[token] = merge_unique(classes[token] + rules)

    css_rules = merge_unique([rule for capture in captures for rule in capture.get("cssRules", [])])
    keyframes = merge_unique([item for capture in captures for item in capture.get("keyframes", [])])
    font_faces = merge_unique([item for capture in captures for item in capture.get("fontFaces", [])])
    fonts = merge_unique([item for capture in captures for item in capture.get("fonts", [])])
    assets = merge_unique([item for capture in captures for item in capture.get("assets", [])])
    style_sheets = merge_unique([item for capture in captures for item in capture.get("styleSheets", [])])
    return {
        "classes": classes,
        "cssRules": css_rules,
        "keyframes": keyframes,
        "fontFaces": font_faces,
        "fonts": fonts,
        "assets": assets,
        "styleSheets": style_sheets,
        "coverage": {
            "classTokenCount": len(classes),
            "matchedRuleCount": sum(bool(rules) for rules in classes.values()),
            "unmatchedTokens": [token for token, rules in classes.items() if not rules],
        },
    }


def main() -> int:
    args = parse_args()
    states = [item.strip() for item in args.states.split(",") if item.strip()]
    if "base" not in states:
        states.insert(0, "base")
    try:
        themes = parse_themes(args.themes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            print(f"无法连接 CDP: {args.cdp_url}\n{exc}", file=sys.stderr)
            return 2

        pages = [page for context in browser.contexts for page in context.pages]
        try:
            page = find_page(pages, args.page_url)
            frame = find_frame(page, args.selector, args.frame_url)
            page_url = page.url
            frame_url = frame_href(frame)
            theme_records: list[dict[str, Any]] = []
            all_captures: list[dict[str, Any]] = []
            base: dict[str, Any] | None = None
            viewport_records: list[dict[str, Any]] = []
            requested_viewports = parse_viewports(args.viewports)
            requested_themes = parse_themes(args.themes)
            states = resolve_states(frame, args.selector, args.states)

            for viewport in requested_viewports:
                viewport_result = apply_viewport(page, viewport)
                viewport_records.append(viewport_result)
                frame = find_frame(page, args.selector, args.frame_url)
                resolved_themes, _ = resolve_themes(frame, requested_themes)
                for theme in resolved_themes:
                    reset_interaction(page, frame)
                    controller = apply_theme(page, frame, theme)
                    if args.wait_ms:
                        page.wait_for_timeout(args.wait_ms)
                    if not controller.get("captured"):
                        theme_records.append({"name": theme, "viewport": viewport_result, **controller})
                        continue
                    reset_interaction(page, frame)

                    theme_args = capture_args_for_theme(args, theme)
                    theme_base = capture_state(frame, theme_args)
                    if base is None:
                        base = theme_base
                    theme_captures = [theme_base]
                    state_records = [{
                        "name": "base",
                        "captured": True,
                        "viewport": viewport_result,
                        "media": theme_base.get("media"),
                        "elements": theme_base.get("elements", []),
                    }]

                    for state in states:
                        if state == "base":
                            continue
                        state_result = apply_state(page, frame, args.selector, state)
                        if not state_result.get("captured"):
                            state_records.append({"name": state, "viewport": viewport_result, **state_result})
                            continue
                        current = capture_state(frame, theme_args)
                        theme_captures.append(current)
                        state_records.append({
                            "name": state,
                            "captured": True,
                            "viewport": viewport_result,
                            "media": current.get("media"),
                            "elements": current.get("elements", []),
                        })
                        if state == "active":
                            page.mouse.up()

                    all_captures.extend(theme_captures)
                    reset_interaction(page, frame)
                    theme_records.append({
                        "name": theme,
                        "viewport": viewport_result,
                        "captured": True,
                        "controller": controller,
                        "theme": theme_base.get("theme"),
                        "states": state_records,
                    })

            if base is None:
                raise RuntimeError("没有成功采集任何主题。")

            captured_theme_names = []
            for item in theme_records:
                name = item.get("name")
                if name and name not in captured_theme_names:
                    captured_theme_names.append(name)

            merged = merge_capture_data(all_captures)
            report: dict[str, Any] = {
                "schemaVersion": "3.0",
                "source": {
                    "platform": detect_platform(page_url, frame_url),
                    "sourceType": base.get("renderMode", "dom-css"),
                    "pageUrl": page_url,
                    "frameUrl": frame_url,
                    "selector": args.selector,
                },
                "capture": {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "cdpUrl": args.cdp_url,
                    "fullComputed": args.full_computed,
                    "source": args.source,
                    "themes": captured_theme_names,
                    "requestedThemes": requested_themes,
                    "states": states,
                    "viewports": viewport_records,
                },
                "renderMode": base.get("renderMode", "dom-css"),
                "discovery": base.get("discovery", {}),
                **base,
                **merged,
                "themes": theme_records,
                "states": theme_records[0].get("states", []) if theme_records else [],
                "viewports": viewport_records,
            }

            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({
                "out": str(out_path),
                "pageUrl": page.url,
                "frameUrl": frame_href(frame),
                "selector": args.selector,
                "classTokens": report.get("coverage", {}).get("classTokenCount", 0),
                "matchedRules": report.get("coverage", {}).get("matchedRuleCount", 0),
                "themes": [item["name"] for item in report["themes"]],
                "states": [item["name"] for item in report["states"]],
            }, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(f"提取失败：{exc}", file=sys.stderr)
            return 1

if __name__ == "__main__":
    raise SystemExit(main())
