// Local JSON Exporter — runs inside Figma desktop using the Plugin API.
// No REST API, no token, NOT subject to HTTP 429 rate limits.

figma.showUI(__html__, { width: 440, height: 600, themeColors: true });

try {
  figma.ui.postMessage({
    type: "init",
    fileName: figma.root ? figma.root.name : "",
    fileTitle: figma.root ? figma.root.name : "",
    pageName: figma.currentPage ? figma.currentPage.name : ""
  });
} catch (e) {}

var DEFAULT_OPTIONS = {
  includeAssets: true,
  pngScale: 2,
  maxAssets: 3000,
  assetFormat: "auto",
  assetScope: "library_only",
  assetDetection: "smart",
  screenshotScope: "all",
  maxScreenshots: 100,
  screenshotScale: 1
};

function round(n) {
  if (typeof n !== "number" || !isFinite(n)) return n;
  return Math.round(n * 100) / 100;
}

function parseScreenName(name) {
  var clean = name.trim();

  // Try matching slash: "Group / State"
  var parts = clean.split("/");
  if (parts.length > 1) {
    return {
      group: parts[0].trim(),
      state: parts.slice(1).join("/").trim()
    };
  }

  // Try matching hyphen: "Group - State"
  parts = clean.split("-");
  if (parts.length > 1) {
    return {
      group: parts[0].trim(),
      state: parts.slice(1).join("-").trim()
    };
  }

  // Try matching parentheses: "Group (State)"
  var parenMatch = clean.match(/^([^(]+)\(([^)]+)\)$/);
  if (parenMatch) {
    return {
      group: parenMatch[1].trim(),
      state: parenMatch[2].trim()
    };
  }

  return {
    group: clean,
    state: "default"
  };
}

function toHex(c, opacity) {
  if (!c) return null;
  var r = Math.max(0, Math.min(255, Math.round((c.r || 0) * 255)));
  var g = Math.max(0, Math.min(255, Math.round((c.g || 0) * 255)));
  var b = Math.max(0, Math.min(255, Math.round((c.b || 0) * 255)));
  var a = c.a === undefined ? 1 : c.a;
  if (opacity !== undefined && opacity !== null) a = a * opacity;
  function h(x) {
    var s = x.toString(16).toUpperCase();
    return s.length === 1 ? "0" + s : s;
  }
  if (a >= 0.999) return "#" + h(r) + h(g) + h(b);
  return "#" + h(r) + h(g) + h(b) + h(Math.round(a * 255));
}

function plainMatrix(m) {
  if (!m) return m;
  try {
    return [
      [round(m[0][0]), round(m[0][1]), round(m[0][2])],
      [round(m[1][0]), round(m[1][1]), round(m[1][2])]
    ];
  } catch (e) {
    return m;
  }
}

function resolveMixed(val) {
  if (val === figma.mixed) return "MIXED";
  return val;
}

function extractPaint(p) {
  var out = { type: p.type, visible: p.visible !== false, opacity: p.opacity };
  if (p.type === "SOLID") {
    var c = p.color || {};
    out.color = { r: c.r || 0, g: c.g || 0, b: c.b || 0, a: c.a === undefined ? 1 : c.a };
    out.hex = toHex(p.color, p.opacity);
  } else if (p.type && p.type.indexOf("GRADIENT") === 0) {
    out.gradientStops = (p.gradientStops || []).map(function (s) {
      var c = s.color || {};
      return { 
        position: round(s.position), 
        color: { r: c.r || 0, g: c.g || 0, b: c.b || 0, a: c.a === undefined ? 1 : c.a }, 
        hex: toHex(s.color) 
      };
    });
    out.gradientTransform = plainMatrix(p.gradientTransform);
  } else if (p.type === "IMAGE") {
    out.scaleMode = p.scaleMode;
    out.imageHash = p.imageHash;
    out.imageTransform = plainMatrix(p.imageTransform);
    out.scalingFactor = p.scalingFactor;
    out.rotation = p.rotation;
  }
  return out;
}

function extractEffect(e) {
  var out = {
    type: e.type,
    visible: e.visible !== false,
    radius: e.radius,
    spread: e.spread,
    blendMode: e.blendMode
  };
  if (e.color) {
    var c = e.color;
    out.color = { r: c.r || 0, g: c.g || 0, b: c.b || 0, a: c.a === undefined ? 1 : c.a };
    out.hex = toHex(e.color);
  }
  if (e.offset) {
    out.offset = { x: e.offset.x || 0, y: e.offset.y || 0 };
  }
  return out;
}

function safe(fn, fallback) {
  try {
    var v = fn();
    return v === undefined ? fallback : v;
  } catch (e) {
    return fallback;
  }
} function sanitizeFileName(value) {
  var s = String(value || "layer").trim();
  // Keep original if it already starts with ic_ or img_
  if (/^(ic_|img_)/i.test(s)) {
    return s.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  }
  return s.toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48) || "layer";
}

function bytesToBase64(bytes) {
  var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  var out = "";
  for (var i = 0; i < bytes.length; i += 3) {
    var b1 = bytes[i];
    var b2 = i + 1 < bytes.length ? bytes[i + 1] : 0;
    var b3 = i + 2 < bytes.length ? bytes[i + 2] : 0;
    var triplet = (b1 << 16) | (b2 << 8) | b3;
    out += chars[(triplet >> 18) & 63];
    out += chars[(triplet >> 12) & 63];
    out += i + 1 < bytes.length ? chars[(triplet >> 6) & 63] : "=";
    out += i + 2 < bytes.length ? chars[triplet & 63] : "=";
  }
  return out;
}

function isExportable(node) {
  return !!node && typeof node.exportAsync === "function";
}

function hasVisibleImageFill(node) {
  var fills = safe(function () { return node.fills; }, []);
  if (!fills || fills === figma.mixed || fills.length === undefined) return false;
  for (var i = 0; i < fills.length; i++) {
    if (fills[i] && fills[i].type === "IMAGE" && fills[i].visible !== false) return true;
  }
  return false;
}

function isVectorLike(node) {
  return [
    "VECTOR",
    "BOOLEAN_OPERATION",
    "STAR",
    "LINE",
    "ELLIPSE",
    "POLYGON"
  ].indexOf(node.type) >= 0;
}

function containsOnlyVectors(node) {
  if (node.type === "TEXT") return false;
  if ("children" in node && node.children) {
    for (var i = 0; i < node.children.length; i++) {
      if (!containsOnlyVectors(node.children[i])) return false;
    }
  }
  return true;
}

function shouldIgnoreNodeSelf(node) {
  // A variant such as Selected=false is still visible. Names and variant
  // values are not visibility flags.
  return !!node && node.visible === false;
}

function shouldIgnoreNode(node) {
  var curr = node;
  while (curr && curr.type !== "DOCUMENT") {
    if (shouldIgnoreNodeSelf(curr)) return true;
    curr = curr.parent;
  }
  return false;
}

function isAbsolutelyVisible(node) {
  return !shouldIgnoreNode(node);
}

async function loadLibraryAssetsIndex() {
  var index = {
    byNormalizedName: Object.create(null),
    byExactName: Object.create(null),
    byId: Object.create(null),
    byKey: Object.create(null),
    list: [],
    _mainComponentCache: Object.create(null)
  };

  try {
    if (typeof figma.loadAllPagesAsync === "function") {
      await figma.loadAllPagesAsync();
    }
  } catch (e) {
    console.log("loadAllPagesAsync info: " + e.message);
  }

  function register(node) {
    if (!node || node.visible === false) return;
    var name = (node.name || "").trim();
    if (!name) return;

    var norm = sanitizeFileName(name);
    var exact = name.toLowerCase();

    if (!index.byNormalizedName[norm]) index.byNormalizedName[norm] = node;
    if (!index.byExactName[exact]) index.byExactName[exact] = node;
    index.byId[node.id] = node;
    if (node.key) index.byKey[node.key] = node;

    if (node.type === "COMPONENT_SET" && node.children) {
      for (var i = 0; i < node.children.length; i++) {
        register(node.children[i]);
      }
    } else {
      index.list.push(node);
    }
  }

  // 1. All COMPONENT and COMPONENT_SET in the file (Local library)
  var found = [];
  if (figma.root && typeof figma.root.findAllWithCriteria === "function") {
    found = figma.root.findAllWithCriteria({ types: ["COMPONENT", "COMPONENT_SET"] });
  } else if (figma.root && figma.root.children) {
    function walkDoc(curr) {
      if (curr.type === "COMPONENT" || curr.type === "COMPONENT_SET") found.push(curr);
      if (curr.children) {
        for (var i = 0; i < curr.children.length; i++) walkDoc(curr.children[i]);
      }
    }
    walkDoc(figma.root);
  }

  for (var i = 0; i < found.length; i++) register(found[i]);

  // 2. Any dedicated asset / library pages (Assets, Libraries, Components, Icons)
  if (figma.root && figma.root.children) {
    function walkAssetContainer(node) {
      if (!node || node.visible === false) return;
      if (node.type === "SECTION" || node.type === "GROUP") {
        if (node.children) {
          for (var i = 0; i < node.children.length; i++) walkAssetContainer(node.children[i]);
        }
      } else if (node.type === "FRAME") {
        register(node);
        if (node.children && (node.width > 200 || node.height > 200 || !isVectorLike(node))) {
          for (var j = 0; j < node.children.length; j++) walkAssetContainer(node.children[j]);
        }
      } else {
        register(node);
      }
    }

    for (var p = 0; p < figma.root.children.length; p++) {
      var page = figma.root.children[p];
      var pName = (page.name || "").toLowerCase();
      var isAssetPage = /assets|librar|components|icons|design\s*system/i.test(pName);
      if (isAssetPage && page.children) {
        for (var c = 0; c < page.children.length; c++) {
          walkAssetContainer(page.children[c]);
        }
      }
    }
  }

  return index;
}

async function findMatchedLibraryAsset(node, libraryIndex, options) {
  if (!node || shouldIgnoreNodeSelf(node) || isStatusBar(node)) return null;
  if (node.parent && node.parent.type === "PAGE") return null;

  var name = (node.name || "").trim();
  var norm = sanitizeFileName(name);
  var exact = name.toLowerCase();

  // If node is an INSTANCE, inspect its mainComponent asynchronously (dynamic-page safe)
  if (node.type === "INSTANCE") {
    var main = null;
    var mainCache = libraryIndex && libraryIndex._mainComponentCache;
    if (mainCache && mainCache[node.id] !== undefined) {
      main = mainCache[node.id];
    } else {
      try {
        if (typeof node.getMainComponentAsync === "function") {
          main = await node.getMainComponentAsync();
        } else {
          main = safe(function () { return node.mainComponent; }, null);
        }
      } catch (e) {
        main = null;
      }
      if (mainCache) mainCache[node.id] = main;
    }

    if (main) {
      if (libraryIndex && libraryIndex.byId[main.id]) return libraryIndex.byId[main.id];
      if (main.key && libraryIndex && libraryIndex.byKey[main.key]) return libraryIndex.byKey[main.key];

      var mainExact = (main.name || "").toLowerCase().trim();
      var mainNorm = sanitizeFileName(main.name);
      if (libraryIndex && libraryIndex.byExactName[mainExact]) return libraryIndex.byExactName[mainExact];
      if (libraryIndex && libraryIndex.byNormalizedName[mainNorm]) return libraryIndex.byNormalizedName[mainNorm];

      var mainParent = safe(function () { return main.parent; }, null);
      if (mainParent && mainParent.type === "COMPONENT_SET") {
        var parentExact = (mainParent.name || "").toLowerCase().trim();
        var parentNorm = sanitizeFileName(mainParent.name);
        if (libraryIndex && libraryIndex.byExactName[parentExact]) return main;
        if (libraryIndex && libraryIndex.byNormalizedName[parentNorm]) return main;
        if (libraryIndex && libraryIndex.byId[mainParent.id]) return main;
      }

      if (/^ic_/i.test(mainNorm) || /^img_/i.test(mainNorm)) {
        return main;
      }
    }
  }

  // Check matching by layer name against library assets
  var isGenericName = /^(frame|group|rectangle|ellipse|vector|line|text)\s*\d*$/i.test(name);
  if (!isGenericName && libraryIndex) {
    if (libraryIndex.byExactName[exact]) return libraryIndex.byExactName[exact];
    if (libraryIndex.byNormalizedName[norm]) return libraryIndex.byNormalizedName[norm];
  }

  // Fallback 1: Explicit naming prefix (ic_* or img_*)
  if (/^ic_/i.test(norm) || /^img_/i.test(norm)) {
    return node;
  }

  // Fallback 2: If user chose "all_artwork" scope, also allow image fills and vector icons
  if (options && options.assetScope === "all_artwork") {
    if (hasVisibleImageFill(node)) return node;
    if (node.exportSettings && node.exportSettings.length > 0) return node;
    if (containsOnlyVectors(node) || isVectorLike(node)) {
      var box = safe(function () { return node.absoluteBoundingBox; }, null);
      if (!box || (box.width <= 320 && box.height <= 320)) return node;
    }
  }

  return null;
}

async function isScreenAsset(node, options, libraryIndex) {
  return await findMatchedLibraryAsset(node, libraryIndex, options);
}

async function shouldExportAsset(node) {
  return !!(await findMatchedLibraryAsset(node, null, null));
}

function relativeBox(box, rootBox) {
  if (!box || !rootBox) return null;
  return {
    x: round(box.x - rootBox.x),
    y: round(box.y - rootBox.y),
    width: round(box.width),
    height: round(box.height)
  };
}

function extractNode(node, parentChain, rootBox) {
  var abb = safe(function () { return node.absoluteBoundingChain ? null : node.absoluteBoundingBox; }, null);
  var bbox = null;
  try {
    if (node.absoluteBoundingBox) {
      bbox = {
        x: round(node.absoluteBoundingBox.x),
        y: round(node.absoluteBoundingBox.y),
        width: round(node.absoluteBoundingBox.width),
        height: round(node.absoluteBoundingBox.height)
      };
    }
  } catch (e) { }

  var data = {
    id: node.id,
    name: node.name,
    type: node.type,
    visible: resolveMixed(node.visible !== false),
    locked: resolveMixed(safe(function () { return node.locked; }, false)),
    opacity: resolveMixed(safe(function () { return node.opacity; }, undefined)),
    blendMode: resolveMixed(safe(function () { return node.blendMode; }, undefined)),
    rotation: resolveMixed(safe(function () { return round(node.rotation); }, undefined)),
    // Relative position to parent + size
    x: resolveMixed(safe(function () { return round(node.x); }, undefined)),
    y: resolveMixed(safe(function () { return round(node.y); }, undefined)),
    width: resolveMixed(safe(function () { return round(node.width); }, undefined)),
    height: resolveMixed(safe(function () { return round(node.height); }, undefined)),
    // Absolute frame on canvas
    absoluteBoundingBox: bbox,
    rootRelativeBoundingBox: relativeBox(bbox, rootBox),
    depth: parentChain.length,
    childIds: safe(function () {
      return "children" in node ? node.children.map(function (c) { return c.id; }) : [];
    }, []),
    clipsContent: resolveMixed(safe(function () { return node.clipsContent; }, undefined)),
    cornerRadius: resolveMixed(safe(function () { return node.cornerRadius; }, undefined)),
    topLeftRadius: resolveMixed(safe(function () { return node.topLeftRadius; }, undefined)),
    topRightRadius: resolveMixed(safe(function () { return node.topRightRadius; }, undefined)),
    bottomLeftRadius: resolveMixed(safe(function () { return node.bottomLeftRadius; }, undefined)),
    bottomRightRadius: resolveMixed(safe(function () { return node.bottomRightRadius; }, undefined)),
    strokeWeight: resolveMixed(safe(function () { return node.strokeWeight; }, undefined)),
    strokeAlign: resolveMixed(safe(function () { return node.strokeAlign; }, undefined)),
    strokeCap: resolveMixed(safe(function () { return node.strokeCap; }, undefined)),
    strokeJoin: resolveMixed(safe(function () { return node.strokeJoin; }, undefined)),
    dashPattern: safe(function () {
      var dp = node.dashPattern;
      if (!dp || dp === figma.mixed) return dp === figma.mixed ? "MIXED" : undefined;
      var arr = [];
      for (var i = 0; i < dp.length; i++) arr.push(round(dp[i]));
      return arr;
    }, undefined),
    parentChain: parentChain.map(function (p) { return { id: p.id, name: p.name, type: p.type }; })
  };

  var fills = safe(function () { return node.fills; }, []);
  if (fills && fills !== figma.mixed && fills.length !== undefined) {
    data.fills = fills.map(extractPaint);
  } else if (fills === figma.mixed) {
    data.fills = "MIXED";
  }

  var strokes = safe(function () { return node.strokes; }, []);
  if (strokes && strokes.length !== undefined) {
    data.strokes = strokes.map(extractPaint);
  }

  var effects = safe(function () { return node.effects; }, []);
  if (effects && effects.length !== undefined) {
    data.effects = effects.map(extractEffect);
  }

  if (node.type === "TEXT") {
    var fSize = safe(function () { return node.fontSize; }, undefined);
    var fName = safe(function () { return node.fontName; }, undefined);
    var fWeight = safe(function () { return node.fontWeight; }, undefined);
    var lSpacing = safe(function () { return node.letterSpacing; }, undefined);
    var lHeight = safe(function () { return node.lineHeight; }, undefined);

    var plainSize = fSize === figma.mixed ? "MIXED" : fSize;
    var plainFont = (fName && fName !== figma.mixed) ? { family: fName.family, style: fName.style } : (fName === figma.mixed ? "MIXED" : fName);
    var plainWeight = fWeight === figma.mixed ? "MIXED" : fWeight;
    var plainSpacing = (lSpacing && lSpacing !== figma.mixed) ? { value: lSpacing.value, unit: lSpacing.unit } : (lSpacing === figma.mixed ? "MIXED" : lSpacing);
    var plainHeight = (lHeight && lHeight !== figma.mixed) ? { value: lHeight.value, unit: lHeight.unit } : (lHeight === figma.mixed ? "MIXED" : lHeight);

    data.text = {
      characters: safe(function () { return node.characters; }, ""),
      fontSize: plainSize,
      fontName: plainFont,
      fontWeight: plainWeight,
      textAlignHorizontal: safe(function () { return node.textAlignHorizontal; }, undefined),
      textAlignVertical: safe(function () { return node.textAlignVertical; }, undefined),
      letterSpacing: plainSpacing,
      lineHeight: plainHeight,
      textCase: safe(function () { return node.textCase; }, undefined),
      textDecoration: safe(function () { return node.textDecoration; }, undefined),
      paragraphSpacing: safe(function () { return node.paragraphSpacing; }, undefined)
    };
  }

  if (node.type === "INSTANCE" || node.type === "COMPONENT" || node.type === "COMPONENT_SET") {
    data.component = {
      componentProperties: {},
      variantProperties: null,
      mainComponentId: undefined
    };
  }

  return data;
}

function isStatusBar(node) {
  if (!node || !node.name) return false;
  var name = node.name.toLowerCase().replace(/[-_]/g, " ").trim();
  return name.indexOf("status bar") >= 0 || name.indexOf("statusbar") >= 0;
}

async function flatten(node, parentChain, out, rootBox, options, libraryIndex) {
  if (shouldIgnoreNodeSelf(node)) return out;
  if (isStatusBar(node)) return out;
  var nodeData = extractNode(node, parentChain, rootBox);
  out.push(nodeData);

  var matchedAsset = libraryIndex ? await findMatchedLibraryAsset(node, libraryIndex, options) : null;
  var isAsset = !!matchedAsset;

  if (isAsset && matchedAsset) {
    nodeData.isAsset = true;
    nodeData.assetName = sanitizeFileName(matchedAsset.name);
  }

  if (!isAsset && "children" in node && node.children && node.children.length) {
    var nextChain = parentChain.concat([node]);
    for (var i = 0; i < node.children.length; i++) {
      await flatten(node.children[i], nextChain, out, rootBox, options, libraryIndex);
    }
  }
  return out;
}

async function exportNodeAsset(node, kind, options, filename) {
  var isSvg = kind === "vector_svg";
  var ext = isSvg ? "svg" : "png";

  var nodeName = node.name || "";
  var sanitizedNodeName = sanitizeFileName(nodeName);

  var assetName = filename || sanitizedNodeName + "." + ext;

  var settings = isSvg ? { format: "SVG" } : { format: "PNG", constraint: { type: "SCALE", value: options.pngScale || 2 } };

  // Export the selected artwork intact, including text and image children.
  // Do not mutate the user's document, even temporarily.
  var bytes = await node.exportAsync(settings);

  var mime = isSvg ? "image/svg+xml" : "image/png";
  return {
    nodeId: node.id,
    nodeName: nodeName,
    nodeType: node.type,
    mainComponentId: undefined,
    kind: kind,
    filename: assetName,
    mimeType: mime,
    byteLength: bytes.length,
    base64: bytesToBase64(bytes)
  };
}

async function collectPendingAssets(roots, options, libraryIndex) {
  var assets = [];
  var seenAssetIds = {};

  async function walk(node) {
    if (shouldIgnoreNodeSelf(node)) return;
    if (isStatusBar(node)) return;

    var matchedAsset = await findMatchedLibraryAsset(node, libraryIndex, options);
    if (matchedAsset) {
      var assetId = matchedAsset.id;
      if (!seenAssetIds[assetId]) {
        seenAssetIds[assetId] = true;
        var kind = assetKind(matchedAsset, options);
        assets.push({ pendingNode: matchedAsset, kind: kind });
      }
      return; // Stop traversal at the asset boundary
    }

    if ("children" in node && node.children) {
      for (var c = 0; c < node.children.length; c++) {
        await walk(node.children[c]);
      }
    }
  }
  for (var j = 0; j < roots.length; j++) {
    await walk(roots[j]);
  }
  return assets;
}

function assetKind(node, options) {
  if (options && options.assetFormat === "svg") return "vector_svg";
  if (options && options.assetFormat === "png") return "image_png";
  var name = (node.name || "").toLowerCase();
  if (/^ic_/i.test(name)) return "vector_svg";
  if (/^img_/i.test(name)) return "image_png";
  if (hasVisibleImageFill(node)) return "image_png";
  if (isVectorLike(node) || node.type === "BOOLEAN_OPERATION") return "vector_svg";
  if (containsOnlyVectors(node)) return "vector_svg";
  if (/icon|vector|svg/i.test(name)) return "vector_svg";
  return "image_png";
}

function assetPage(node) {
  var parent = node;
  while (parent && parent.type !== "PAGE") {
    parent = safe(function () { return parent.parent; }, null);
  }
  return parent ? { id: parent.id, name: parent.name } : null;
}

function prepareAssetPlan(pending, options) {
  var counts = Object.create(null);
  var used = Object.create(null);
  var seen = Object.create(null);
  var unique = pending.filter(function (entry) {
    var id = entry.pendingNode.id;
    if (seen[id]) return false;
    seen[id] = true;
    entry.kind = (typeof entry.kind === "string" ? entry.kind : null) || assetKind(entry.pendingNode, options);
    var base = sanitizeFileName(entry.pendingNode.name);
    // Android drawable names must begin with a letter or underscore.
    if (!/^[a-z]/.test(base)) base = "asset_" + base;
    entry.baseName = base;
    var key = base + (entry.kind === "vector_svg" ? ".svg" : ".png");
    counts[key] = (counts[key] || 0) + 1;
    return true;
  });
  return unique.map(function (entry, index) {
    var node = entry.pendingNode;
    var ext = entry.kind === "vector_svg" ? ".svg" : ".png";
    var base = entry.baseName;
    if (counts[base + ext] > 1) base += "__" + sanitizeFileName(node.id);
    var filename = base + ext;
    var suffix = 2;
    while (used[filename]) filename = base + "_" + suffix++ + ext;
    used[filename] = true;
    entry.record = {
      nodeId: node.id,
      nodeName: node.name,
      nodeType: node.type,
      page: assetPage(node),
      componentKey: node.type === "COMPONENT" ? node.key : undefined,
      variantProperties: node.variantProperties || undefined,
      width: round(node.width),
      height: round(node.height),
      kind: entry.kind,
      scale: entry.kind === "image_png" ? options.pngScale : 1,
      filename: filename,
      status: index < options.maxAssets ? "pending" : "skipped",
      reason: index < options.maxAssets ? undefined : "max-assets-limit"
    };
    return entry;
  });
}

async function buildPayload(roots, options) {
  var screens = [];
  var allFlattened = [];
  var screenshots = [];

  figma.ui.postMessage({ type: "progress", message: "Scanning asset libraries across file..." });
  var libraryIndex = await loadLibraryAssetsIndex();

  // Flatten screens and export screenshots
  var validScreens = roots.filter(function (r) { return !shouldIgnoreNode(r); });
  var maxScreenshots = options.screenshotScope === "none" ? 0 :
    options.screenshotScope === "first" ? 1 :
    options.screenshotScope === "custom" ? (options.maxScreenshots || 10) :
    validScreens.length;

  var screenIndex = 0;
  for (var i = 0; i < roots.length; i++) {
    if (shouldIgnoreNode(roots[i])) continue;
    var flattened = [];
    var rootBox = safe(function () { return roots[i].absoluteBoundingBox; }, null);
    figma.ui.postMessage({
      type: "progress",
      message: "Processing screen " + (screenIndex + 1) + "/" + validScreens.length + ": " + roots[i].name
    });
    await flatten(roots[i], [], flattened, rootBox, options, libraryIndex);

    var parsed = parseScreenName(roots[i].name);
    var cleanGroup = sanitizeFileName(parsed.group);
    var cleanState = sanitizeFileName(parsed.state);
    var suffix = roots[i].id.replace(/[:;]/g, "_");
    var fileName = cleanGroup + "_" + cleanState + "_" + suffix + ".json";

    screens.push({
      fileName: fileName,
      screenGroup: parsed.group,
      screenState: parsed.state,
      root: { id: roots[i].id, name: roots[i].name, type: roots[i].type },
      flattened: flattened
    });
    allFlattened = allFlattened.concat(flattened);

    try {
      if (screenIndex < maxScreenshots) {
        figma.ui.postMessage({
          type: "progress",
          message: "Capturing screenshot " + (screenIndex + 1) + "/" + Math.min(validScreens.length, maxScreenshots) + " (" + roots[i].name + ")..."
        });
        var cleanPngName = cleanGroup + "_" + cleanState + "_" + suffix + ".png";
        var screenPng = await roots[i].exportAsync({ format: "PNG", constraint: { type: "SCALE", value: options.screenshotScale || 1 } });
        screenshots.push({
          filename: cleanPngName,
          base64: bytesToBase64(screenPng)
        });
      }
    } catch (e) {
      console.log("Error exporting screen screenshot (" + roots[i].name + "): " + e.message);
    }
    screenIndex++;
  }

  var pending = options.includeAssets ? await collectPendingAssets(roots, options, libraryIndex) : [];
  var assetExports = pending.map(function (a) {
    return {
      nodeId: a.pendingNode.id,
      nodeName: a.pendingNode.name,
      nodeType: a.pendingNode.type,
      kind: a.kind,
      filename: sanitizeFileName(a.pendingNode.name) + (a.kind === "image_png" ? ".png" : ".svg")
    };
  });

  return {
    meta: {
      tool: "figma-local-export-plugin",
      exportedAt: new Date().toISOString(),
      fileName: figma.root.name,
      fileTitle: figma.root.name,
      page: figma.currentPage.name,
      rootCount: roots.length,
      nodeCount: allFlattened.length,
      assetCount: pending.length,
      pngScale: options.pngScale || 2,
      failedCount: 0,
      errors: []
    },
    screens: screens,
    assetExports: assetExports,
    __assetsForDownload: pending,
    __screenshots: screenshots
  };
}

function resolveRoots(nodes) {
  var resolved = [];
  var seen = {};
  function process(node) {
    if (!node) return;
    if (shouldIgnoreNode(node)) return;
    if (seen[node.id]) return;
    seen[node.id] = true;

    if (node.type === "SECTION" || node.type === "GROUP") {
      if (node.children) {
        for (var i = 0; i < node.children.length; i++) {
          process(node.children[i]);
        }
      }
    } else if (node.type === "FRAME" || node.type === "COMPONENT" || node.type === "INSTANCE") {
      resolved.push(node);
    }
  }
  for (var i = 0; i < nodes.length; i++) {
    process(nodes[i]);
  }
  return resolved;
}

function resolveAssetRoots(nodes) {
  var roots = [];
  var seen = Object.create(null);
  function visit(node) {
    if (!node || seen[node.id] || shouldIgnoreNode(node)) return;
    seen[node.id] = true;
    // A variant set is a collection; each variant is its own export.
    if (node.type === "SECTION" || node.type === "COMPONENT_SET") {
      (node.children || []).forEach(visit);
    } else if (isExportable(node)) {
      roots.push(node);
    }
  }
  nodes.forEach(visit);
  var selected = Object.create(null);
  roots.forEach(function (node) { selected[node.id] = true; });
  return roots.filter(function (node) {
    var parent = node.parent;
    while (parent && parent.type !== "DOCUMENT") {
      if (selected[parent.id]) return false;
      parent = parent.parent;
    }
    return true;
  });
}

function normalizeOptions(input) {
  var options = Object.assign({}, DEFAULT_OPTIONS, input || {});
  var scale = Number(options.pngScale);
  var limit = Number(options.maxAssets);
  options.pngScale = isFinite(scale) && scale > 0 ? Math.min(4, Math.max(1, scale)) : 2;
  options.maxAssets = isFinite(limit) && limit > 0 ? Math.min(5000, Math.max(1, Math.floor(limit))) : 3000;
  if (["auto", "png", "svg"].indexOf(options.assetFormat) < 0) options.assetFormat = "auto";
  options.assetScope = input && input.assetScope === "all_artwork" ? "all_artwork" : "library_only";
  options.assetDetection = input && input.assetDetection === "strict" ? "strict" : "smart";
  if (["all", "first", "custom", "none"].indexOf(options.screenshotScope) < 0) options.screenshotScope = "all";
  var maxScreenshots = Number(options.maxScreenshots);
  options.maxScreenshots = isFinite(maxScreenshots) && maxScreenshots > 0 ? Math.min(1000, Math.max(1, Math.floor(maxScreenshots))) : 100;
  var sScale = Number(options.screenshotScale);
  options.screenshotScale = isFinite(sScale) && sScale > 0 ? Math.min(2, Math.max(0.5, sScale)) : 1;
  return options;
}

var exportInProgress = false;

async function runExport(mode, input) {
  if (exportInProgress) return;
  exportInProgress = true;
  var options = normalizeOptions(input);
  figma.ui.postMessage({ type: "progress", message: "Preparing export..." });
  try {
    var assetsOnly = mode === "selected-assets" || mode === "local-components";
    var roots;
    var hiddenCount = 0;
    if (mode === "local-components") {
      figma.ui.postMessage({ type: "progress", message: "Loading all pages to find local components..." });
      await figma.loadAllPagesAsync();
      var components = figma.root.findAllWithCriteria({ types: ["COMPONENT"] });
      roots = components.filter(function (node) {
        if (node.remote) return false;
        if (shouldIgnoreNode(node)) { hiddenCount++; return false; }
        return true;
      });
    } else if (mode === "selected-assets") {
      roots = resolveAssetRoots(figma.currentPage.selection.slice());
    } else {
      roots = resolveRoots(mode === "page" ? figma.currentPage.children.slice() : figma.currentPage.selection.slice());
    }
    if (!roots.length) {
      throw new Error(mode === "local-components"
        ? "No visible local components found in this file. Hidden components skipped: " + hiddenCount + "."
        : "No exportable layers found. Select artwork on the canvas for Export selected assets, or use Export local components (file).");
    }

    var payload;
    if (assetsOnly) {
      payload = {
        meta: { tool: "figma-local-export-plugin", exportedAt: new Date().toISOString(),
          fileName: figma.root.name, fileTitle: figma.root.name, page: figma.currentPage.name, rootCount: roots.length,
          nodeCount: 0, pngScale: options.pngScale, errors: [] },
        screens: [],
        __assetsForDownload: roots.map(function (node) { return { pendingNode: node }; }),
        __screenshots: []
      };
    } else {
      payload = await buildPayload(roots, options);
    }
    var plan = prepareAssetPlan(payload.__assetsForDownload || [], options);
    var screenshots = payload.__screenshots || [];
    delete payload.__assetsForDownload;
    delete payload.__screenshots;
    payload.schemaVersion = 2;
    payload.assetExports = plan.map(function (entry) { return entry.record; });
    payload.meta.scope = mode;
    payload.meta.screenshotCount = screenshots.length;
    payload.meta.hiddenComponentCount = hiddenCount;
    payload.meta.candidateAssetCount = plan.length;
    payload.meta.assetCount = 0;
    payload.meta.failedCount = 0;
    payload.meta.skippedCount = plan.filter(function (entry) { return entry.record.status === "skipped"; }).length;
    payload.meta.errors = [];
    figma.ui.postMessage({ type: "result-start", json: JSON.stringify(payload, null, 2), screenshots: screenshots });

    // Sequential export bounds Figma's export workload and keeps errors per asset.
    for (var i = 0; i < plan.length; i++) {
      var item = plan[i];
      if (item.record.status === "skipped") continue;
      figma.ui.postMessage({ type: "progress", message: "Exporting asset " + (i + 1) + "/" + Math.min(plan.length, options.maxAssets) + ": " + item.pendingNode.name });
      try {
        var exported = await exportNodeAsset(item.pendingNode, item.kind, options, item.record.filename);
        item.record.status = "exported";
        item.record.mimeType = exported.mimeType;
        item.record.byteLength = exported.byteLength;
        payload.meta.assetCount++;
        figma.ui.postMessage({ type: "result-asset", asset: exported });
      } catch (e) {
        item.record.status = "failed";
        item.record.error = String(e && e.message ? e.message : e);
        payload.meta.failedCount++;
        payload.meta.errors.push({ nodeId: item.record.nodeId, filename: item.record.filename, message: item.record.error });
      }
    }
    payload.meta.complete = payload.meta.failedCount === 0 && payload.meta.skippedCount === 0 && hiddenCount === 0;
    figma.ui.postMessage({ type: "result-end", json: JSON.stringify(payload, null, 2), summary: payload.meta });
  } catch (e) {
    figma.ui.postMessage({ type: "error", message: String(e && e.message ? e.message : e) });
  } finally {
    exportInProgress = false;
  }
}

figma.ui.onmessage = function (msg) {
  if (!msg || !msg.type) return;
  if (msg.type === "export-selection") return runExport("selection", msg.options);
  if (msg.type === "export-page") return runExport("page", msg.options);
  if (msg.type === "export-selected-assets") return runExport("selected-assets", msg.options);
  if (msg.type === "export-local-components") return runExport("local-components", msg.options);
  if (msg.type === "close") figma.closePlugin();
};
