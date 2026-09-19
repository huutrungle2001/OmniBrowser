(() => {
  const sentinel = Symbol.for("__OMNI_DOM_AGENT__");
  if (window[sentinel]) return;

  let nextNodeToken = 1;
  let revision = 0;
  let context = null;
  const nodeTokens = new WeakMap();
  const refs = new Map();
  const fingerprints = new Map();

  const interactiveRoles = new Set([
    "button", "checkbox", "combobox", "link", "menuitem", "slider",
    "switch", "tab", "textbox", "dialog", "alert", "alertdialog", "status"
  ]);

  const roleFor = (element) => {
    if (element.getAttribute("role")) return element.getAttribute("role");
    if (element.hasAttribute("aria-live")) return "status";
    if (element.tagName === "A") return "link";
    if (element.tagName === "BUTTON") return "button";
    if (element.tagName === "SELECT") return "combobox";
    if (element.tagName === "TEXTAREA") return "textbox";
    if (element.tagName === "INPUT") {
      const type = element.type.toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (["button", "submit", "reset"].includes(type)) return "button";
      return "textbox";
    }
    return element.isContentEditable ? "textbox" : "generic";
  };


  const labelText = (element) => {
    const ariaLabel = element.getAttribute("aria-label");
    if (ariaLabel) return ariaLabel.trim();
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy.split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent?.trim() || "")
        .filter(Boolean)
        .join(" ");
      if (text) return text;
    }
    if (element.labels?.length) {
      const text = [...element.labels].map((label) => label.textContent?.trim() || "")
        .filter(Boolean).join(" ");
      if (text) return text;
    }
    const wrappingLabel = element.closest("label");
    if (wrappingLabel) return wrappingLabel.textContent?.trim() || "";
    return (element.placeholder || element.title || element.innerText || element.textContent || "")
      .replace(/\s+/g, " ").trim().slice(0, 160);
  };

  const isVisible = (element) => {
    if (!(element instanceof Element) || element.closest("[hidden], [inert], [aria-hidden='true']")) {
      return false;
    }
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const isCandidate = (element) => {
    if (!isVisible(element) || element.matches("script, style, svg, option, template")) return false;
    if (element.matches("button, a[href], input:not([type='hidden']), textarea, select")) return true;
    if (element.isContentEditable) return true;
    if (interactiveRoles.has(roleFor(element))) return true;
    if (element.hasAttribute("aria-live")) return true;
    return element.tabIndex >= 0 && Boolean(labelText(element));
  };


  const tokenFor = (element) => {
    let token = nodeTokens.get(element);
    if (!token) {
      token = String(nextNodeToken++);
      nodeTokens.set(element, token);
    }
    return token;
  };

  const refFor = (element) => {
    if (!context) throw new Error("OmniBrowser scanner context is not configured");
    const ref = `f${context.frame}.d${context.epoch}.n${tokenFor(element)}`;
    refs.set(ref, new WeakRef(element));
    fingerprints.set(ref, fingerprintFor(element));
    return ref;
  };

  const fingerprintFor = (element) => ({
    role: roleFor(element),
    name: labelText(element),
    tag: element.tagName,
    id: element.id || "",
    nameAttr: element.getAttribute("name") || "",
    type: element.getAttribute("type") || "",
    form: element.form?.id || ""
  });

  const fingerprintMatches = (element, fingerprint) => {
    const current = fingerprintFor(element);
    if (current.role !== fingerprint.role || current.name !== fingerprint.name || current.tag !== fingerprint.tag) {
      return false;
    }
    for (const key of ["id", "nameAttr", "type", "form"]) {
      if (fingerprint[key] && current[key] !== fingerprint[key]) return false;
    }
    return true;
  };

  const candidates = () => {
    const result = [];
    const queue = [document.documentElement];
    const seen = new Set();
    while (queue.length) {
      const element = queue.shift();
      if (!(element instanceof Element) || seen.has(element)) continue;
      seen.add(element);
      if (isCandidate(element)) result.push(element);
      for (const child of element.children) queue.push(child);
      if (element.shadowRoot?.mode === "open") {
        for (const child of element.shadowRoot.children) queue.push(child);
      }
    }
    return result;
  };

  const liveElement = (ref) => {
    const direct = refs.get(ref)?.deref();
    if (direct?.isConnected) return direct;
    const fingerprint = fingerprints.get(ref);
    if (!fingerprint) return null;
    const matches = candidates().filter((element) => fingerprintMatches(element, fingerprint));
    if (matches.length !== 1) return null;
    refs.set(ref, new WeakRef(matches[0]));
    return matches[0];
  };

  const agent = {
    setContext(frame, epoch) {
      context = { frame: String(frame), epoch: String(epoch) };
      return { ...context, revision };
    },
    scan(maxElements = 80) {
      const nodes = candidates().slice(0, maxElements).map((element) => {
        const rect = element.getBoundingClientRect();
        const isPassword = element instanceof HTMLInputElement && element.type === "password";
        return {
          ref: refFor(element),
          role: roleFor(element),
          name: labelText(element),
          bounds: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)],
          visible: true,
          interactive: true,
          disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
          value: isPassword ? undefined : ("value" in element ? String(element.value).slice(0, 256) : undefined)
        };
      });
      return { revision, nodes };
    },
    resolve(ref) {
      const element = liveElement(ref);
      return Boolean(element?.isConnected);
    },
    resolveElement(ref) {
      const element = liveElement(ref);
      return element?.isConnected ? element : null;
    }
  };

  new MutationObserver(() => { revision += 1; }).observe(document, {
    childList: true,
    subtree: true,
    attributes: true,
    characterData: true
  });
  document.addEventListener("input", () => { revision += 1; }, true);
  document.addEventListener("change", () => { revision += 1; }, true);

  window[sentinel] = agent;
})();
